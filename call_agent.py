# call_agent.py
import os
import json
import re 
import time 
from datetime import datetime
import pytz
import redis.asyncio as redis
from dotenv import load_dotenv
from loguru import logger
from fastapi import APIRouter, Request, Form, WebSocket, Response
from fastapi.responses import HTMLResponse, JSONResponse
import base64
import aiohttp
import asyncio
import google.generativeai as genai

from pipecat.serializers.base_serializer import FrameSerializer
from pipecat.frames.frames import Frame, AudioRawFrame, CancelFrame

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams

try:
    from twilio.twiml.voice_response import VoiceResponse, Connect
except ImportError:
    VoiceResponse = None
    Connect = None
    logger.warning("⚠️ Twilio not installed — phone call endpoints disabled.")

from pipecat.frames.frames import (
    TextFrame, TranscriptionFrame,
    TTSSpeakFrame, TTSUpdateSettingsFrame, FunctionCallInProgressFrame
)
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.transports.base_transport import BaseTransport
from pipecat.transports.websocket.fastapi import FastAPIWebsocketTransport, FastAPIWebsocketParams
from pipecat.serializers.twilio import TwilioFrameSerializer

from pipecat.services.sarvam.stt import SarvamSTTService
from pipecat.services.google.tts import GoogleTTSService
from pipecat.services.google.llm import GoogleLLMService

from tools.pipecat_tools import register_all_tools, get_tools_schema
from tools.notify import handle_successful_payment
from tools.pool import get_pool

load_dotenv(override=True)

# 👇 FIXED: Global LLM Initialization for the Summarizer
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
summarizer_model = genai.GenerativeModel("gemini-2.5-flash")

router = APIRouter()
redis_client = None

async def ensure_redis_client():
    global redis_client
    if redis_client:
        return
    try:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        redis_client = redis.from_url(redis_url, decode_responses=True)
        await redis_client.ping()
        logger.info("✅ Redis client connected successfully.")
    except Exception as e:
        logger.warning(f"⚠️ Redis connection failed: {e}")
        redis_client = None

# ==========================================================
# 🧠 SYSTEM PROMPT
# ==========================================================
ist = pytz.timezone('Asia/Kolkata')
current_time = datetime.now(ist).strftime('%A, %B %d, %Y at %I:%M %p IST')

VOICE_SYSTEM_PROMPT = f"""Role: Mithra Hospital AI Receptionist.
CURRENT LIVE TIME: {current_time}

You transition strictly through phases. NEVER backtrack.

--- 🌐 LANGUAGE & TRANSLATION RULES (CRITICAL) ---
1. STARTING LANGUAGE: You start the conversation in Telugu.
2. CASUAL MIXING: If the user casually uses English or Hindi words (e.g., "book cheyandi", "10 o clock", "haan"), DO NOT switch languages. Continue replying in your current language.
3. EXPLICIT LANGUAGE SWITCHING (CRITICAL): If the user EXPLICITLY commands you to change the language (e.g., "Can you talk in English?", "Speak in Hindi"):
   - You MUST immediately switch your text output to the requested language.
   - You MUST acknowledge the switch (e.g., "Sure, I can speak in English.")
   - You MUST repeat the exact question you were just asking. DO NOT hallucinate symptoms or skip steps. DO NOT call `check_availability` unless the user actually stated their medical problem.
4. DATABASE TRANSLATION (CRITICAL): ALL data sent to your internal tools (like patient_name, reason) MUST be translated to plain ENGLISH.
5. TIME FORMATTING: Translate all digits into spelled-out phonetic words in your active language.
6. PHONE NUMBER SPELLING (CRITICAL): When repeating a phone number to confirm, spell out EACH digit individually phonetically.
7. RESPECTFUL VOCABULARY: Always use the English word "patient" (even when speaking Telugu or Hindi).
8. PACE & SPEED: Keep all your sentences extremely short, crisp, and punchy. Avoid long explanations.

--- 🛠️ FAQ & DOCUMENT LOOKUP ---
If at ANY point the user asks a general question about clinic policies, surgeries, cancellations, or doctors:
1. Immediately call `query_clinic_faq`.
2. Answer ONLY using the retrieved info.
3. After answering, re-enter the flow. Ask: "Would you like to book an appointment today?" or resume where you left off.

--- INTENT ROUTING ---
1. CANCEL/RESCHEDULE: Use `query_clinic_faq` to explain the policy. Then ask if there is anything else they need.
2. FOLLOW-UP BOOKING: If the user asks for a "follow-up", ask EXACTLY: "Could you please tell me your 10-digit phone number so I can check your records?" Once provided, SILENTLY call `verify_followup`.

--- CORE BOOKING STATES ---

PHASE 0 (Symptoms Gathering):
Check if the user has ALREADY provided symptoms. If NO symptoms given: ask "What medical problem or symptoms are you experiencing?"
CRITICAL: DO NOT call `check_availability` until symptoms are explicitly stated.

PHASE 1 (Availability):
ONLY AFTER the user gives symptoms, SILENTLY call `check_availability`. Emit ZERO text.

PHASE 2 (Offer & Negotiation):
- Initial Offer: Read the `system_directive` exactly as intended. Look at `all_available_slots` to find alternative times if asked.
- TOKEN SYSTEM RULE: If the system directive mentions a "Token System" or "Session", DO NOT ask the user what exact time they will arrive. If they agree to the session, accept it and immediately move to PHASE 3.
PHASE 3 (Details Request):
If the user agrees to a slot, ask: "Could you please tell me the patient's name and 10-digit phone number?"

PHASE 3.5 (Confirmation - CRITICAL):
Once the user provides their name and phone number, DO NOT call the booking tool immediately.
You MUST repeat the phone number back to them digit-by-digit to confirm. 
Say: "Your number is [Digit Digit Digit...]. Is that correct?"

PHASE 4 (The Silent Trigger):
ONLY AFTER the user explicitly says "Yes", "Correct", "Avunu", etc., YOU MUST STOP SPEAKING.
Immediately call `voice_book_appointment`. 
CRITICAL: Emit ZERO characters of text. DO NOT say "Okay" or repeat the name again.

PHASE 5 (Confirmation & Persistence):
ONLY AFTER the tool returns "success", inform the patient.
- For a paid appointment, say EXACTLY the native translation of: "A tentative appointment has been booked. Please click the payment link on WhatsApp and do the payment under 15 minutes."
- CRITICAL: After the confirmation, DO NOT end the call. Ask: "Is there anything else I can help you with today?"
- CLOSING THE CALL: If the user says they are done, have no more questions, or say goodbye, you MUST first say a polite thank you and goodbye in your active language, and THEN call `end_call`.
"""

# ==========================================================
# 🛠️ PROCESSORS & SERIALIZERS
# ==========================================================
class STTTextCleanerProcessor(FrameProcessor):
    def __init__(self, session_id):
        super().__init__()
        self.session_id = session_id

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame):
            text = frame.text.strip().lower()
            if len(text) <= 2:
                return
            logger.info(f"[{self.session_id}] 🎤 USER SAID [Raw STT]: {text}")
            corrections = {
                "పార్లమెంట్": "అపాయింట్మెంట్",
                "apartment": "appointment",
                "అపార్ట్మెంట్": "అపాయింట్మెంట్",
                "department": "appointment",
                "తెలుగు": "telugu",
                "हिंदी": "hindi"
            }
            for k, v in corrections.items():
                text = text.replace(k, v)
            frame.text = text
        await self.push_frame(frame, direction)

class AutoLanguageProcessor(FrameProcessor):
    def __init__(self, session_id):
        super().__init__()
        self.session_id = session_id
        self.current_language = "te-IN"

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TextFrame):
            text = frame.text
            new_lang = self.current_language
            new_voice = f"{self.current_language}-Chirp3-HD-Despina"

            has_telugu = bool(re.search(r'[\u0c00-\u0c7f]', text))
            has_hindi = bool(re.search(r'[\u0900-\u097f]', text))

            if has_telugu:
                new_lang = "te-IN"
                new_voice = "te-IN-Chirp3-HD-Despina"
            elif has_hindi:
                new_lang = "hi-IN"
                new_voice = "hi-IN-Chirp3-HD-Despina"
            elif re.search(r'[a-zA-Z]', text) and not (has_telugu or has_hindi) and len(text) > 10:
                new_lang = "en-US"
                new_voice = "en-US-Chirp3-HD-Despina"

            if new_lang != self.current_language:
                logger.info(f"[{self.session_id}] 🌐 Auto-switching TTS to: {new_lang} | Voice: {new_voice}")
                self.current_language = new_lang
                await self.push_frame(
                    TTSUpdateSettingsFrame(settings={
                        "language": new_lang,
                        "voice": new_voice,
                        "speaking_rate": 1.15
                    }),
                    direction
                )

        if isinstance(frame, FunctionCallInProgressFrame):
            filler = ""
            if frame.function_name == "voice_book_appointment":
                filler = (
                    "ఒక్క నిమిషం" if self.current_language == "te-IN"
                    else "एक मिनट" if self.current_language == "hi-IN"
                    else "One moment"
                )
            elif frame.function_name == "check_availability":
                filler = (
                    "చూస్తున్నాను" if self.current_language == "te-IN"
                    else "चेक कर रही हूँ" if self.current_language == "hi-IN"
                    else "Checking"
                )
            elif frame.function_name == "query_clinic_faq":
                filler = (
                    "చూస్తున్నాను" if self.current_language == "te-IN"
                    else "Checking"
                )
            if filler:
                logger.info(f"[{self.session_id}] ⏳ Filler: {filler}")
                await self.push_frame(TTSSpeakFrame(text=filler), direction)

        await self.push_frame(frame, direction)

class BillingTracker(FrameProcessor):
    def __init__(self, context, session_id):
        super().__init__()
        self.tts_chars = 0
        self.llm_output_tokens_est = 0
        self.start_time = time.time()
        self.context = context
        self.session_id = session_id

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TextFrame) and direction == FrameDirection.DOWNSTREAM:
            self.tts_chars += len(frame.text)
            self.llm_output_tokens_est += len(frame.text) / 4.0
        await self.push_frame(frame, direction)

    def generate_receipt(self):
        duration_seconds = time.time() - self.start_time
        duration_minutes = duration_seconds / 60.0
        history_str = json.dumps(self.context.messages)
        total_input_tokens = len(history_str) / 4.0
        stt_cost_usd = duration_minutes * 0.006
        tts_cost_usd = self.tts_chars * 0.00003
        llm_input_cost_usd = total_input_tokens * 0.0000003
        llm_output_cost_usd = self.llm_output_tokens_est * 0.0000025
        total_cost_usd = stt_cost_usd + tts_cost_usd + llm_input_cost_usd + llm_output_cost_usd
        
        # 👇 FIX: Explicitly calculate and define the USD cost per minute
        cost_per_minute_usd = total_cost_usd / duration_minutes if duration_minutes > 0 else 0
        
        exchange_rate = 93.29
        total_cost_inr = total_cost_usd * exchange_rate
        cost_per_minute_inr = cost_per_minute_usd * exchange_rate

        logger.info("\n" + "=" * 55)
        logger.info(f"[{self.session_id}] 💰 SESSION BILLING RECEIPT 💰")
        logger.info("=" * 55)
        logger.info(f"⏱️  Duration:     {duration_minutes:.2f} mins ({duration_seconds:.0f}s)")
        logger.info(f"🎙️  STT Cost:     ₹{stt_cost_usd * exchange_rate:.4f} (${stt_cost_usd:.4f})")
        logger.info(f"🧠 LLM In:       ₹{llm_input_cost_usd * exchange_rate:.4f} (~{total_input_tokens:.0f} tokens)")
        logger.info(f"🧠 LLM Out:      ₹{llm_output_cost_usd * exchange_rate:.4f} (~{self.llm_output_tokens_est:.0f} tokens)")
        logger.info(f"🗣️  TTS Cost:     ₹{tts_cost_usd * exchange_rate:.4f} ({self.tts_chars} chars)")
        logger.info("-" * 55)
        logger.info(f"💵 TOTAL:        ₹{total_cost_inr:.4f} (${total_cost_usd:.4f})")
        # 👇 FIX: Print the actual USD variable without multiplying it by the exchange rate again
        logger.info(f"📊 PER MIN:      ₹{cost_per_minute_inr:.4f} (${cost_per_minute_usd:.4f})")
        logger.info("=" * 55 + "\n")

class PipecatBugFixProcessor(FrameProcessor):
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, AudioRawFrame):
            if not hasattr(frame, 'pts'): frame.pts = None
            if not hasattr(frame, 'transport_destination'): frame.transport_destination = None
            if not hasattr(frame, 'id'): frame.id = "fixed-audio-frame-id"
            if not hasattr(frame, 'broadcast_sibling_id'): frame.broadcast_sibling_id = None
        await self.push_frame(frame, direction)

class VobizFrameSerializer(FrameSerializer):
    def __init__(self, stream_sid: str = None):
        self.stream_sid = stream_sid

    async def serialize(self, frame: Frame) -> str | None:
        if isinstance(frame, AudioRawFrame):
            payload = base64.b64encode(frame.audio).decode("utf-8")
            vobiz_msg = {
                "event": "playAudio",
                "media": {
                    "payload": payload,
                    "contentType": "audio/x-l16",
                    "sampleRate": frame.sample_rate
                }
            }
            return json.dumps(vobiz_msg)
        elif isinstance(frame, CancelFrame):
            return json.dumps({"event": "clearAudio"}) 
        return None

    async def deserialize(self, data: str | bytes) -> Frame | None:
        try:
            if isinstance(data, bytes): data = data.decode("utf-8")
            msg = json.loads(data)
            if msg.get("event") == "media":
                payload = msg.get("media", {}).get("payload")
                if payload:
                    audio_data = base64.b64decode(payload)
                    frame = AudioRawFrame(audio=audio_data, sample_rate=8000, num_channels=1)
                    if not hasattr(frame, 'id'): frame.id = "inbound-audio-id"
                    if not hasattr(frame, 'pts'): frame.pts = None
                    if not hasattr(frame, 'transport_destination'): frame.transport_destination = None
                    if not hasattr(frame, 'broadcast_sibling_id'): frame.broadcast_sibling_id = None
                    return frame
            elif msg.get("event") == "stop":
                return CancelFrame()
        except Exception as e:
            logger.error(f"Error deserializing Vobiz frame: {e}")
        return None

# ==========================================================
# 🔌 VOBIZ REST API HANGUP HELPER
# ==========================================================
async def force_vobiz_hangup(call_uuid: str):
    if not call_uuid or call_uuid == "vobiz_call": return
    auth_id = os.getenv("VOBIZ_AUTH_ID")
    auth_token = os.getenv("VOBIZ_AUTH_TOKEN")
    if not auth_id or not auth_token: return
    url = f"https://api.vobiz.ai/api/v1/Account/{auth_id}/Call/{call_uuid}/"
    headers = {"X-Auth-ID": auth_id, "X-Auth-Token": auth_token}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.delete(url, headers=headers) as resp:
                if resp.status in [200, 202, 204]: logger.info(f"☎️ Call Terminated via API: {call_uuid}")
    except Exception as e:
        logger.error(f"❌ Failed to forcefully hang up Vobiz call: {e}")

# ==========================================================
# 💾 DB SAVING HELPER
# ==========================================================
async def save_call_log(call_sid: str, caller_number: str, duration: float, messages: list):
    logger.info(f"💾 Starting DB save for call {call_sid}...")
    try:
        # 1. Improved Transcript Extraction
        lines = []
        for m in messages:
            role = "AI" if m.get('role') == 'model' else "Patient"
            content = ""
            if 'content' in m and isinstance(m['content'], str):
                content = m['content']
            elif 'parts' in m:
                for p in m['parts']:
                    if isinstance(p, dict) and 'text' in p:
                        content += p['text']
                    elif isinstance(p, str):
                        content += p
            if content.strip():
                lines.append(f"{role}: {content.strip()}")
        
        transcript = "\n".join(lines)
        
        # 2. Summarize using Global Summarizer
        if not lines:
            ai_summary = "Call connected but no speech detected."
        else:
            prompt = f"""
            Analyze the following medical receptionist transcript and provide a summary strictly following this template:
            "Patient {{Name}} booked an appointment on {{Date}} at {{Time}} with {{Doctor Name}} ({{Specialization}}) due to {{Reason}}"

            Rules:
            - If no appointment was booked, write: "Patient called to inquire but no appointment was booked."
            - Translate any Telugu details into English for the summary.
            - Keep it to one single sentence.

            Transcript:
            {transcript}
            """
            # 👇 FIXED: Using global 'summarizer_model' instead of re-initializing
            response = await asyncio.to_thread(summarizer_model.generate_content, prompt)
            ai_summary = response.text.strip() if response else "No summary generated."

        # 3. Insert into DB
        pool = get_pool()
        if not pool: return
        async with pool.acquire() as conn:
            clinic_id = await conn.fetchval("SELECT id FROM clinics LIMIT 1")
            if not clinic_id: return
            await conn.execute("""
                INSERT INTO calls (clinic_id, type, caller, agent_type, duration, ai_summary)
                VALUES ($1, 'incoming', $2, 'ai', $3, $4)
            """, clinic_id, caller_number, int(duration), ai_summary)
        logger.info(f"✅ Call Log Saved | Summary: {ai_summary}")
    except Exception as e:
        logger.error(f"❌ Failed to save call log: {e}", exc_info=True)

# ==========================================================
# 🎙️ PIPECAT RUNNER
# ==========================================================
async def run_bot(transport: BaseTransport, call_sid: str = "local_test", caller_number: str = "Unknown"):
    await ensure_redis_client()
    session_id = call_sid[:8]
    google_creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

    stt = SarvamSTTService(api_key=os.getenv("SARVAM_API_KEY"), language="unknown", model="saaras:v3", mode="transcribe")
    tts = GoogleTTSService(credentials_path=google_creds_path, voice="te-IN-Chirp3-HD-Despina", language="te-IN")
    llm = GoogleLLMService(api_key=os.getenv("GEMINI_API_KEY"), model="gemini-2.5-flash")

    register_all_tools(llm)

    context = LLMContext(messages=[{"role": "system", "content": VOICE_SYSTEM_PROMPT}], tools=get_tools_schema())
    context_aggregator = LLMContextAggregatorPair(context)
    billing_tracker = BillingTracker(context, session_id)

    pipeline = Pipeline([
        transport.input(),
        stt,
        STTTextCleanerProcessor(session_id),
        context_aggregator.user(),
        llm,
        billing_tracker,
        AutoLanguageProcessor(session_id),
        tts,
        PipecatBugFixProcessor(),
        transport.output(),
        context_aggregator.assistant()
    ])

    task = PipelineTask(pipeline, params=PipelineParams(audio_in_sample_rate=8000, audio_out_sample_rate=8000, enable_metrics=True, enable_usage_metrics=True))

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        await task.queue_frames([TTSSpeakFrame("నమస్కారం! మిత్ర హాస్పిటల్స్‌కు స్వాగతం. నేను మీకు ఎలా సహాయపడగలను?", append_to_context=True)])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        await task.cancel()

    runner = PipelineRunner(handle_sigint=False)
    await runner.run(task)
    billing_tracker.generate_receipt()

    duration = time.time() - billing_tracker.start_time
    # 👇 Keep awaiting to ensure record is written before shutdown
    await save_call_log(call_sid, caller_number, duration, context.messages)

# ==========================================================
# 📞 TWILIO ROUTES
# ==========================================================
@router.post("/incoming")
async def incoming_call(request: Request, From: str = Form("Unknown")):
    base_url = str(request.base_url).replace("http://", "wss://").replace("https://", "wss://").rstrip("/")
    xml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect><Stream url="{base_url}/media"><Parameter name="caller" value="{From}" /></Stream></Connect>
</Response>"""
    return Response(content=xml_response, media_type="text/xml")

@router.post("/voice")
async def voice_callback(request: Request):
    return await incoming_call(request)

@router.websocket("/media")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        raw = await websocket.receive_text()
        data = json.loads(raw)
        if data.get("event") == "connected":
            raw = await websocket.receive_text()
            data = json.loads(raw)

        if data.get("event") == "start":
            stream_sid = data["start"]["streamSid"]
            call_sid   = data["start"].get("callSid", "twilio_call")
            caller_number = data["start"].get("customParameters", {}).get("caller", "Unknown")
            serializer = TwilioFrameSerializer(stream_sid=stream_sid, params=TwilioFrameSerializer.InputParams(auto_hang_up=False))
            transport = FastAPIWebsocketTransport(websocket=websocket, params=FastAPIWebsocketParams(audio_in_enabled=True, audio_out_enabled=True, add_wav_header=False, serializer=serializer))
            await run_bot(transport, call_sid=call_sid, caller_number=caller_number)
    except Exception as e:
        logger.error(f"❌ Twilio WebSocket error: {e}")

# ==========================================================
# 📞 VOBIZ ROUTES
# ==========================================================
@router.post("/vobiz-events")
async def vobiz_events(request: Request):
    body_bytes = await request.body()
    body_str = body_bytes.decode('utf-8')
    if "event=hangup" in body_str.lower(): return Response(content="<Response></Response>", media_type="text/xml")
    wss_url = "wss://gymnastic-oversentimentally-marcelino.ngrok-free.dev/vobiz-media"
    xml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Speak>Connecting to AI.</Speak>
    <Stream bidirectional="true" keepCallAlive="true">{wss_url}</Stream>
</Response>"""
    return Response(content=xml_response, media_type="text/xml")

@router.websocket("/vobiz-media")
async def vobiz_websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    call_sid = "vobiz_call"
    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            if data.get("event") == "start":
                stream_sid = data["start"].get("streamId", "vobiz_stream")
                call_sid = data["start"].get("callId", "vobiz_call")
                caller_number = data["start"].get("metadata", {}).get("from", "Unknown")
                break
        serializer = VobizFrameSerializer(stream_sid=stream_sid)
        transport = FastAPIWebsocketTransport(websocket=websocket, params=FastAPIWebsocketParams(audio_in_enabled=True, audio_out_enabled=True, add_wav_header=False, serializer=serializer))
        await run_bot(transport, call_sid=call_sid, caller_number=caller_number)
    except Exception as e:
        logger.error(f"❌ Vobiz WebSocket error: {e}")
    finally:
        await websocket.close()
        await force_vobiz_hangup(call_sid)

# ==========================================================
# 💳 RAZORPAY WEBHOOK
# ==========================================================
@router.post("/razorpay-webhook")
async def razorpay_webhook(request: Request):
    try:
        payload = await request.json()
        event = payload.get("event")
        if event in ["payment_link.paid", "payment_link.cancelled", "payment_link.expired"]:
            entity = payload.get("payload", {}).get("payment_link", {}).get("entity", {})
            appointment_id = entity.get("notes", {}).get("appointment_id")
            if appointment_id:
                from tools.pool import get_pool
                pool = get_pool()
                status = "paid" if event == "payment_link.paid" else "failed"
                async with pool.acquire() as conn:
                    await conn.execute("UPDATE payments SET status = $1 WHERE appointment_id = $2", status, appointment_id)
                if event == "payment_link.paid": await handle_successful_payment(appointment_id)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"❌ Razorpay Webhook Error: {e}")
        return {"status": "error"}
    
# ==========================================================
# 🧪 LOCAL TESTING UI (FREE & BROWSER BASED)
# ==========================================================

class WebTestSerializer(FrameSerializer):
    def __init__(self):
        pass

    async def serialize(self, frame: Frame) -> str | None:
        if isinstance(frame, AudioRawFrame):
            payload = base64.b64encode(frame.audio).decode("utf-8")
            return json.dumps({"event": "media", "payload": payload})
        elif isinstance(frame, TextFrame):
            # 👇 Emits the AI's spoken text so you can read it in the chat
            return json.dumps({"event": "text", "text": frame.text, "sender": "ai"})
        elif isinstance(frame, CancelFrame):
            return json.dumps({"event": "stop"})
        return None

    async def deserialize(self, data: str | bytes) -> Frame | None:
        try:
            if isinstance(data, bytes):
                data = data.decode("utf-8")
            msg = json.loads(data)
            
            # 1. TEXT INPUT (Simulating STT)
            if msg.get("event") == "text":
                return TranscriptionFrame(
                    text=msg.get("text", ""), 
                    user_id="web_tester", 
                    timestamp=datetime.now().isoformat()
                )
                
            # 2. ACTUAL VOICE INPUT (Real STT via Microphone)
            elif msg.get("event") == "media":
                payload = msg.get("payload")
                if payload:
                    audio_data = base64.b64decode(payload)
                    frame = AudioRawFrame(audio=audio_data, sample_rate=8000, num_channels=1)
                    if not hasattr(frame, 'id'): frame.id = "inbound-audio-id"
                    if not hasattr(frame, 'pts'): frame.pts = None
                    if not hasattr(frame, 'transport_destination'): frame.transport_destination = None
                    if not hasattr(frame, 'broadcast_sibling_id'): frame.broadcast_sibling_id = None
                    return frame
                    
            elif msg.get("event") == "stop":
                return CancelFrame()
        except Exception as e:
            logger.error(f"Test UI Deserialization error: {e}")
        return None

@router.get("/test-ui")
async def get_test_ui():
    """Serves a Hybrid UI to test both Chat (Text) and Voice (STT+TTS)."""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Mithra AI Tester</title>
        <style>
            body { font-family: Arial, sans-serif; max-width: 650px; margin: 40px auto; padding: 20px; background: #f4f7f6; }
            h2 { color: #2c3e50; text-align: center; }
            #chat { background: white; height: 450px; overflow-y: auto; padding: 15px; border-radius: 8px; border: 1px solid #ddd; margin-bottom: 15px; display: flex; flex-direction: column;}
            .msg { margin-bottom: 10px; padding: 10px 14px; border-radius: 15px; max-width: 80%; line-height: 1.4; font-size: 15px; }
            .msg.user { background: #007bff; color: white; align-self: flex-end; border-bottom-right-radius: 2px;}
            .msg.ai { background: #e9ecef; color: black; align-self: flex-start; border-bottom-left-radius: 2px;}
            .controls { display: flex; gap: 10px; align-items: center;}
            input { flex: 1; padding: 12px; border: 1px solid #ddd; border-radius: 4px; font-size: 15px;}
            button { padding: 12px 20px; border: none; color: white; border-radius: 4px; cursor: pointer; font-weight: bold; font-size: 14px;}
            button:disabled { background: #ccc !important; cursor: not-allowed; }
            #sendBtn { background: #28a745; }
            #micBtn { background: #17a2b8; width: 150px; user-select: none;}
            #connectBtn { background: #007bff; width: 100%; margin-bottom: 15px; padding: 15px; font-size: 16px;}
        </style>
    </head>
    <body>
        <h2>🏥 Mithra Hospital AI - Web Tester</h2>
        <button id="connectBtn">Connect to AI (Turn on Volume & Allow Mic)</button>
        <div id="chat"></div>
        <div class="controls">
            <button id="micBtn" disabled>🎤 Hold to Speak</button>
            <input type="text" id="msgInput" placeholder="Type or speak..." disabled />
            <button id="sendBtn" disabled>Send</button>
        </div>

        <script>
            let ws;
            let audioContext;
            let nextPlayTime = 0;
            
            let globalMicStream = null;
            let globalSource = null;
            let globalProcessor = null;
            let isRecording = false;

            const chat = document.getElementById('chat');
            const msgInput = document.getElementById('msgInput');
            const sendBtn = document.getElementById('sendBtn');
            const connectBtn = document.getElementById('connectBtn');
            const micBtn = document.getElementById('micBtn');

            let lastSender = null;
            let lastBubble = null;

            function appendLog(text, sender) {
                if (sender === 'ai' && lastSender === 'ai' && lastBubble) {
                    lastBubble.innerText += " " + text;
                } else {
                    const div = document.createElement('div');
                    div.className = 'msg ' + sender;
                    div.innerText = text;
                    chat.appendChild(div);
                    lastBubble = div;
                }
                lastSender = sender;
                chat.scrollTop = chat.scrollHeight;
            }

            connectBtn.onclick = async () => {
                audioContext = new (window.AudioContext || window.webkitAudioContext)({sampleRate: 8000});

                try {
                    globalMicStream = await navigator.mediaDevices.getUserMedia({ audio: true });
                    if (audioContext.state === 'suspended') await audioContext.resume();
                    nextPlayTime = audioContext.currentTime;

                    globalSource = audioContext.createMediaStreamSource(globalMicStream);
                    globalProcessor = audioContext.createScriptProcessor(1024, 1, 1);
                    
                    globalProcessor.onaudioprocess = (e) => {
                        if (!isRecording) return; 
                        
                        const inputData = e.inputBuffer.getChannelData(0);
                        const pcm16 = new Int16Array(inputData.length);
                        for (let i = 0; i < inputData.length; i++) {
                            let s = Math.max(-1, Math.min(1, inputData[i] * 2.5));
                            pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
                        }
                        const buffer = new Uint8Array(pcm16.buffer);
                        
                        // Optimized Base64 Conversion
                        let binary = '';
                        const chunkSize = 0x8000;
                        for (let i = 0; i < buffer.length; i += chunkSize) {
                            binary += String.fromCharCode.apply(null, buffer.subarray(i, i + chunkSize));
                        }
                        if (ws && ws.readyState === WebSocket.OPEN) {
                            ws.send(JSON.stringify({event: "media", payload: window.btoa(binary)}));
                        }
                    };
                    
                    const zeroGain = audioContext.createGain();
                    zeroGain.gain.value = 0;
                    globalSource.connect(globalProcessor);
                    globalProcessor.connect(zeroGain);
                    zeroGain.connect(audioContext.destination);

                } catch (err) {
                    console.error("Mic access denied", err);
                    alert("Microphone access is required to use Voice STT.");
                    return; 
                }

                const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                ws = new WebSocket(`${protocol}//${window.location.host}/test-media`);

                ws.onopen = () => {
                    connectBtn.style.display = 'none';
                    msgInput.disabled = false;
                    sendBtn.disabled = false;
                    micBtn.disabled = false;
                    appendLog("Connected. The AI is greeting you...", "ai");
                };

                ws.onmessage = async (event) => {
                    const data = JSON.parse(event.data);
                    if (data.event === "media") {
                        playAudio(data.payload);
                    } else if (data.event === "text") {
                        appendLog(data.text, data.sender || "ai");
                    } else if (data.event === "stop") {
                        appendLog("Call ended.", "ai");
                        msgInput.disabled = true;
                        sendBtn.disabled = true;
                        micBtn.disabled = true;
                    }
                };
            };

            sendBtn.onclick = () => {
                if (msgInput.value.trim() && ws) {
                    appendLog(msgInput.value, "user");
                    ws.send(JSON.stringify({event: "text", text: msgInput.value}));
                    msgInput.value = '';
                }
            };

            msgInput.addEventListener("keypress", function(event) {
                if (event.key === "Enter") {
                    event.preventDefault();
                    sendBtn.click();
                }
            });

            micBtn.onmousedown = () => {
                if (!globalMicStream || micBtn.disabled) return;
                isRecording = true; 
                micBtn.innerText = "🎙️ Listening...";
                micBtn.style.background = "#dc3545"; 
            };

            const stopMic = () => {
                if (isRecording) {
                    isRecording = false; 
                    micBtn.innerText = "🎤 Hold to Speak";
                    micBtn.style.background = "#17a2b8";
                    appendLog("🎤 (Spoke via Microphone)", "user");

                    // 👇 THE FIX: Blast 0.5s of absolute silence to force STT to finish the sentence instantly
                    const silenceBuffer = new Uint8Array(8000); // 0.5 seconds of 8kHz 16-bit audio (all zeros)
                    let binary = '';
                    for (let i = 0; i < silenceBuffer.length; i++) binary += String.fromCharCode(silenceBuffer[i]);
                    if (ws && ws.readyState === WebSocket.OPEN) {
                        ws.send(JSON.stringify({event: "media", payload: window.btoa(binary)}));
                    }
                }
            };

            micBtn.onmouseup = stopMic;
            micBtn.onmouseleave = stopMic;

            function playAudio(base64String) {
                const binaryString = window.atob(base64String);
                const len = binaryString.length;
                const bytes = new Int16Array(len / 2);
                for (let i = 0; i < len; i += 2) {
                    let int16 = (binaryString.charCodeAt(i)) | (binaryString.charCodeAt(i + 1) << 8);
                    if (int16 & 0x8000) int16 = int16 | 0xFFFF0000;
                    bytes[i / 2] = int16;
                }

                const floatArray = new Float32Array(bytes.length);
                for (let i = 0; i < bytes.length; i++) {
                    floatArray[i] = bytes[i] / 32768.0;
                }

                const buffer = audioContext.createBuffer(1, floatArray.length, 8000);
                buffer.getChannelData(0).set(floatArray);

                const source = audioContext.createBufferSource();
                source.buffer = buffer;
                source.connect(audioContext.destination);

                if (nextPlayTime < audioContext.currentTime) {
                    nextPlayTime = audioContext.currentTime;
                }
                source.start(nextPlayTime);
                nextPlayTime += buffer.duration;
            }
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@router.websocket("/test-media")
async def test_websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    call_sid = f"web_test_{int(time.time())}"
    caller_number = "9999999999"  
    logger.info(f"🧪 Web Test Stream started | callSid={call_sid} | Mock Caller={caller_number}")

    serializer = WebTestSerializer()
    
    # 👇 FIX: Completely removed VAD injection here to stop dropping audio packets
    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=serializer
        )
    )
    
    try:
        await run_bot(transport, call_sid=call_sid, caller_number=caller_number)
    except Exception as e:
        logger.error(f"❌ Web Test WebSocket error: {e}")
# # call_agent.py
# # call_agent.py
# import os
# import json
# import re 
# import time 
# from datetime import datetime
# import pytz
# import redis.asyncio as redis
# from dotenv import load_dotenv
# from loguru import logger
# from fastapi import APIRouter, Request, Form, WebSocket, Response
# from fastapi.responses import HTMLResponse, JSONResponse
# import base64
# import aiohttp
# import asyncio  # 👇 NEW: For background DB saving
# import google.generativeai as genai  # 👇 NEW: For AI Summarization

# from pipecat.serializers.base_serializer import FrameSerializer
# from pipecat.frames.frames import Frame, AudioRawFrame, CancelFrame

# from pipecat.audio.vad.silero import SileroVADAnalyzer
# from pipecat.audio.vad.vad_analyzer import VADParams

# try:
#     from twilio.twiml.voice_response import VoiceResponse, Connect
# except ImportError:
#     VoiceResponse = None
#     Connect = None
#     logger.warning("⚠️ Twilio not installed — phone call endpoints disabled.")

# from pipecat.frames.frames import (
#     TextFrame, TranscriptionFrame,
#     TTSSpeakFrame, TTSUpdateSettingsFrame, FunctionCallInProgressFrame
# )
# from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
# from pipecat.pipeline.pipeline import Pipeline
# from pipecat.pipeline.runner import PipelineRunner
# from pipecat.pipeline.task import PipelineParams, PipelineTask
# from pipecat.processors.aggregators.llm_context import LLMContext
# from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
# from pipecat.transports.base_transport import BaseTransport
# from pipecat.transports.websocket.fastapi import FastAPIWebsocketTransport, FastAPIWebsocketParams
# from pipecat.serializers.twilio import TwilioFrameSerializer

# from pipecat.services.sarvam.stt import SarvamSTTService
# from pipecat.services.google.tts import GoogleTTSService
# from pipecat.services.google.llm import GoogleLLMService

# from tools.pipecat_tools import register_all_tools, get_tools_schema
# from tools.notify import handle_successful_payment
# from tools.pool import get_pool  # 👇 NEW: Import DB Pool

# load_dotenv(override=True)

# router = APIRouter()
# redis_client = None

# async def ensure_redis_client():
#     global redis_client
#     if redis_client:
#         return
#     try:
#         redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
#         redis_client = redis.from_url(redis_url, decode_responses=True)
#         await redis_client.ping()
#         logger.info("✅ Redis client connected successfully.")
#     except Exception as e:
#         logger.warning(f"⚠️ Redis connection failed: {e}")
#         redis_client = None


# # ==========================================================
# # 🧠 SYSTEM PROMPT
# # ==========================================================
# ist = pytz.timezone('Asia/Kolkata')
# current_time = datetime.now(ist).strftime('%A, %B %d, %Y at %I:%M %p IST')

# VOICE_SYSTEM_PROMPT = f"""Role: Mithra Hospital AI Receptionist.
# CURRENT LIVE TIME: {current_time}

# You transition strictly through phases. NEVER backtrack.

# --- 🌐 LANGUAGE & TRANSLATION RULES (CRITICAL) ---
# 1. STRICT LANGUAGE LOCK: Your default and locked language is Telugu. Even if the user speaks English words or Hindi words (e.g., "book cheyandi", "10 o clock", "haan"), YOU MUST IGNORE IT AND REPLY IN TELUGU ONLY.
# 2. EXPLICIT INTENT TO SWITCH: NEVER change languages unless the user explicitly commands you to change the language.
# 3. PURITY OF LANGUAGE: Do not write English words in your Telugu responses.
# 4. IGNORE TRANSLITERATION: The STT engine may write English words in Telugu script. Treat it as the locked language.
# 5. DATABASE TRANSLATION (CRITICAL): ALL data sent to your internal tools (like patient_name, reason) MUST be translated to plain ENGLISH.
# 6. TIME FORMATTING: Translate all digits into spelled-out phonetic words. For Telugu, say "ఉదయం తొమ్మిది గంటలకు". NEVER output raw digits like "09:00".
# 7. PHONE NUMBER SPELLING (CRITICAL): When repeating a phone number to confirm, spell out EACH digit individually phonetically. E.g., for 830, say "ఎనిమిది మూడు సున్నా...". NEVER say "ఎనభై మూడు" (eighty-three).
# 8. RESPECTFUL VOCABULARY: NEVER use the Telugu word "రోగి" (rogi). Always use the English word "patient".
# 9. PACE & SPEED: Keep all your sentences extremely short, crisp, and punchy. Avoid long explanations.

# --- 🛠️ FAQ & DOCUMENT LOOKUP ---
# If at ANY point the user asks a general question about clinic policies, surgeries, cancellations, or doctors:
# 1. Immediately call `query_clinic_faq`.
# 2. Answer ONLY using the retrieved info.
# 3. After answering, re-enter the flow. Ask: "Would you like to book an appointment today?" or resume where you left off.

# --- INTENT ROUTING ---
# 1. CANCEL/RESCHEDULE: Use `query_clinic_faq` to explain the policy. Then ask if there is anything else they need.
# 2. FOLLOW-UP BOOKING: If the user asks for a "follow-up", ask EXACTLY: "Could you please tell me your 10-digit phone number so I can check your records?" Once provided, SILENTLY call `verify_followup`.

# --- CORE BOOKING STATES ---

# PHASE 0 (Symptoms Gathering):
# Check if the user has ALREADY provided symptoms. If NO symptoms given: ask "What medical problem or symptoms are you experiencing?"
# CRITICAL: DO NOT call `check_availability` until symptoms are explicitly stated.

# PHASE 1 (Availability):
# ONLY AFTER the user gives symptoms, SILENTLY call `check_availability`. Emit ZERO text.

# PHASE 2 (Offer & Negotiation):
# - Initial Offer: Read the `system_directive` exactly as intended. Look at `all_available_slots` to find alternative times if asked.

# PHASE 3 (Details Request):
# If the user agrees to a slot, ask: "Could you please tell me the patient's name and 10-digit phone number?"

# PHASE 3.5 (Confirmation - CRITICAL):
# Once the user provides their name and phone number, DO NOT call the booking tool immediately.
# You MUST repeat the phone number back to them digit-by-digit to confirm. 
# Say: "Your number is [Digit Digit Digit...]. Is that correct?"

# PHASE 4 (The Silent Trigger):
# ONLY AFTER the user explicitly says "Yes", "Correct", "Avunu", etc., YOU MUST STOP SPEAKING.
# Immediately call `voice_book_appointment`. 
# CRITICAL: Emit ZERO characters of text. DO NOT say "Okay" or repeat the name again.

# PHASE 5 (Confirmation & Persistence):
# ONLY AFTER the tool returns "success", inform the patient.
# - For a paid appointment, say EXACTLY the native translation of: "A tentative appointment has been booked. Please click the payment link on WhatsApp and do the payment under 15 minutes."
# - CRITICAL: After the confirmation, DO NOT end the call. Ask: "Is there anything else I can help you with today?"
# - ONLY call `end_call` if the user explicitly says they are done or says goodbye.
# """


# # ==========================================================
# # 🛠️ PROCESSORS & SERIALIZERS
# # ==========================================================
# class STTTextCleanerProcessor(FrameProcessor):
#     def __init__(self, session_id):
#         super().__init__()
#         self.session_id = session_id

#     async def process_frame(self, frame: Frame, direction: FrameDirection):
#         await super().process_frame(frame, direction)
#         if isinstance(frame, TranscriptionFrame):
#             text = frame.text.strip().lower()
#             if len(text) <= 2:
#                 return
#             logger.info(f"[{self.session_id}] 🎤 USER SAID [Raw STT]: {text}")
#             corrections = {
#                 "పార్లమెంట్": "అపాయింట్మెంట్",
#                 "apartment": "appointment",
#                 "అపార్ట్మెంట్": "అపాయింట్మెంట్",
#                 "department": "appointment",
#                 "తెలుగు": "telugu",
#                 "हिंदी": "hindi"
#             }
#             for k, v in corrections.items():
#                 text = text.replace(k, v)
#             frame.text = text
#         await self.push_frame(frame, direction)


# class AutoLanguageProcessor(FrameProcessor):
#     def __init__(self, session_id):
#         super().__init__()
#         self.session_id = session_id
#         self.current_language = "te-IN"

#     async def process_frame(self, frame: Frame, direction: FrameDirection):
#         await super().process_frame(frame, direction)
#         if isinstance(frame, TextFrame):
#             text = frame.text
#             new_lang = self.current_language
#             new_voice = f"{self.current_language}-Chirp3-HD-Despina"

#             has_telugu = bool(re.search(r'[\u0c00-\u0c7f]', text))
#             has_hindi = bool(re.search(r'[\u0900-\u097f]', text))

#             if has_telugu:
#                 new_lang = "te-IN"
#                 new_voice = "te-IN-Chirp3-HD-Despina"
#             elif has_hindi:
#                 new_lang = "hi-IN"
#                 new_voice = "hi-IN-Chirp3-HD-Despina"
#             elif re.search(r'[a-zA-Z]', text) and not (has_telugu or has_hindi) and len(text) > 10:
#                 new_lang = "en-US"
#                 new_voice = "en-US-Chirp3-HD-Despina"

#             if new_lang != self.current_language:
#                 logger.info(f"[{self.session_id}] 🌐 Auto-switching TTS to: {new_lang} | Voice: {new_voice}")
#                 self.current_language = new_lang
#                 await self.push_frame(
#                     TTSUpdateSettingsFrame(settings={
#                         "language": new_lang,
#                         "voice": new_voice,
#                         "speaking_rate": 1.15
#                     }),
#                     direction
#                 )

#         if isinstance(frame, FunctionCallInProgressFrame):
#             filler = ""
#             if frame.function_name == "voice_book_appointment":
#                 filler = (
#                     "ఒక్క నిమిషం" if self.current_language == "te-IN"
#                     else "एक मिनट" if self.current_language == "hi-IN"
#                     else "One moment"
#                 )
#             elif frame.function_name == "check_availability":
#                 filler = (
#                     "చూస్తున్నాను" if self.current_language == "te-IN"
#                     else "चेक कर रही हूँ" if self.current_language == "hi-IN"
#                     else "Checking"
#                 )
#             elif frame.function_name == "query_clinic_faq":
#                 filler = (
#                     "చూస్తున్నాను" if self.current_language == "te-IN"
#                     else "Checking"
#                 )
#             if filler:
#                 logger.info(f"[{self.session_id}] ⏳ Filler: {filler}")
#                 await self.push_frame(TTSSpeakFrame(text=filler), direction)

#         await self.push_frame(frame, direction)


# class BillingTracker(FrameProcessor):
#     def __init__(self, context, session_id):
#         super().__init__()
#         self.tts_chars = 0
#         self.llm_output_tokens_est = 0
#         self.start_time = time.time()
#         self.context = context
#         self.session_id = session_id

#     async def process_frame(self, frame: Frame, direction: FrameDirection):
#         await super().process_frame(frame, direction)
#         if isinstance(frame, TextFrame) and direction == FrameDirection.DOWNSTREAM:
#             self.tts_chars += len(frame.text)
#             self.llm_output_tokens_est += len(frame.text) / 4.0
#         await self.push_frame(frame, direction)

#     def generate_receipt(self):
#         duration_seconds = time.time() - self.start_time
#         duration_minutes = duration_seconds / 60.0

#         history_str = json.dumps(self.context.messages)
#         total_input_tokens = len(history_str) / 4.0

#         stt_cost_usd = duration_minutes * 0.006
#         tts_cost_usd = self.tts_chars * 0.00003
#         llm_input_cost_usd = total_input_tokens * 0.0000003
#         llm_output_cost_usd = self.llm_output_tokens_est * 0.0000025
#         total_cost_usd = stt_cost_usd + tts_cost_usd + llm_input_cost_usd + llm_output_cost_usd
#         cost_per_minute_usd = total_cost_usd / duration_minutes if duration_minutes > 0 else 0
#         exchange_rate = 83.5
#         total_cost_inr = total_cost_usd * exchange_rate
#         cost_per_minute_inr = cost_per_minute_usd * exchange_rate

#         logger.info("\n" + "=" * 55)
#         logger.info(f"[{self.session_id}] 💰 SESSION BILLING RECEIPT 💰")
#         logger.info("=" * 55)
#         logger.info(f"⏱️  Duration:     {duration_minutes:.2f} mins ({duration_seconds:.0f}s)")
#         logger.info(f"🎙️  STT Cost:     ₹{stt_cost_usd * exchange_rate:.4f} (${stt_cost_usd:.4f})")
#         logger.info(f"🧠 LLM In:       ₹{llm_input_cost_usd * exchange_rate:.4f} (~{total_input_tokens:.0f} tokens)")
#         logger.info(f"🧠 LLM Out:      ₹{llm_output_cost_usd * exchange_rate:.4f} (~{self.llm_output_tokens_est:.0f} tokens)")
#         logger.info(f"🗣️  TTS Cost:     ₹{tts_cost_usd * exchange_rate:.4f} ({self.tts_chars} chars)")
#         logger.info("-" * 55)
#         logger.info(f"💵 TOTAL:        ₹{total_cost_inr:.4f} (${total_cost_usd:.4f})")
#         logger.info(f"📊 PER MIN:      ₹{cost_per_minute_inr:.4f} (${cost_per_minute_usd:.4f})")
#         logger.info("=" * 55 + "\n")


# class PipecatBugFixProcessor(FrameProcessor):
#     async def process_frame(self, frame: Frame, direction: FrameDirection):
#         await super().process_frame(frame, direction)
#         if isinstance(frame, AudioRawFrame):
#             if not hasattr(frame, 'pts'):
#                 frame.pts = None
#             if not hasattr(frame, 'transport_destination'):
#                 frame.transport_destination = None
#             if not hasattr(frame, 'id'):
#                 frame.id = "fixed-audio-frame-id"
#             if not hasattr(frame, 'broadcast_sibling_id'):
#                 frame.broadcast_sibling_id = None
#         await self.push_frame(frame, direction)


# class VobizFrameSerializer(FrameSerializer):
#     def __init__(self, stream_sid: str = None):
#         self.stream_sid = stream_sid

#     async def serialize(self, frame: Frame) -> str | None:
#         if isinstance(frame, AudioRawFrame):
#             payload = base64.b64encode(frame.audio).decode("utf-8")
#             vobiz_msg = {
#                 "event": "playAudio",
#                 "media": {
#                     "payload": payload,
#                     "contentType": "audio/x-l16",
#                     "sampleRate": frame.sample_rate
#                 }
#             }
#             return json.dumps(vobiz_msg)
            
#         elif isinstance(frame, CancelFrame):
#             return json.dumps({"event": "clearAudio"}) 
            
#         return None

#     async def deserialize(self, data: str | bytes) -> Frame | None:
#         try:
#             if isinstance(data, bytes):
#                 data = data.decode("utf-8")
#             msg = json.loads(data)
#             event_type = msg.get("event")
#             if event_type == "media":
#                 payload = msg.get("media", {}).get("payload")
#                 if payload:
#                     audio_data = base64.b64decode(payload)
#                     frame = AudioRawFrame(audio=audio_data, sample_rate=8000, num_channels=1)
#                     if not hasattr(frame, 'id'): frame.id = "inbound-audio-id"
#                     if not hasattr(frame, 'pts'): frame.pts = None
#                     if not hasattr(frame, 'transport_destination'): frame.transport_destination = None
#                     if not hasattr(frame, 'broadcast_sibling_id'): frame.broadcast_sibling_id = None
#                     return frame
#             elif event_type == "stop":
#                 return CancelFrame()
#         except Exception as e:
#             logger.error(f"Error deserializing Vobiz frame: {e}")
#         return None


# # ==========================================================
# # 🔌 VOBIZ REST API HANGUP HELPER
# # ==========================================================
# async def force_vobiz_hangup(call_uuid: str):
#     if not call_uuid or call_uuid == "vobiz_call":
#         return
        
#     auth_id = os.getenv("VOBIZ_AUTH_ID")
#     auth_token = os.getenv("VOBIZ_AUTH_TOKEN")
    
#     if not auth_id or not auth_token:
#         logger.warning("⚠️ Missing VOBIZ_AUTH_ID or VOBIZ_AUTH_TOKEN in .env. Cannot forcefully hang up.")
#         return
        
#     url = f"https://api.vobiz.ai/api/v1/Account/{auth_id}/Call/{call_uuid}/"
#     headers = {
#         "X-Auth-ID": auth_id,
#         "X-Auth-Token": auth_token
#     }
    
#     try:
#         async with aiohttp.ClientSession() as session:
#             async with session.delete(url, headers=headers) as resp:
#                 if resp.status in [200, 202, 204]:
#                     logger.info(f"☎️ Call Terminated via API: {call_uuid}")
#                 elif resp.status == 404:
#                     logger.debug(f"☎️ Call {call_uuid} already ended normally.")
#                 else:
#                     logger.warning(f"⚠️ Hangup API returned status {resp.status} for {call_uuid}")
#     except Exception as e:
#         logger.error(f"❌ Failed to forcefully hang up Vobiz call: {e}")


# # ==========================================================
# # 💾 DB SAVING HELPER
# # ==========================================================
# # 👇 NEW: Extracts transcript, summarizes with Gemini, and saves to Postgres DB
# async def save_call_log(call_sid: str, caller_number: str, duration: float, messages: list):
#     logger.info(f"💾 Starting DB save for call {call_sid}...")
#     try:
#         # 1. Improved Transcript Extraction
#         lines = []
#         for m in messages:
#             role = "AI" if m.get('role') == 'model' else "Patient"
#             # Extract text from different possible Pipecat context structures
#             content = ""
#             if 'content' in m and isinstance(m['content'], str):
#                 content = m['content']
#             elif 'parts' in m:
#                 for p in m['parts']:
#                     if isinstance(p, dict) and 'text' in p:
#                         content += p['text']
#                     elif isinstance(p, str):
#                         content += p
            
#             if content.strip():
#                 lines.append(f"{role}: {content.strip()}")
        
#         transcript = "\n".join(lines)
        
#         # 2. Summarize using Gemini with your specific template
#         if not lines:
#             ai_summary = "Call connected but no speech detected."
#         else:
#             # 👇 STRICT TEMPLATE ENFORCEMENT
#             prompt = f"""
#             Analyze the following medical receptionist transcript and provide a summary strictly following this template:
#             "Patient {{Name}} booked an appointment on {{Date}} at {{Time}} with {{Doctor Name}} ({{Specialization}}) due to {{Reason}}"

#             Rules:
#             - If no appointment was booked, write: "Patient called to inquire but no appointment was booked."
#             - Translate any Telugu details into English for the summary.
#             - Keep it to one single sentence.

#             Transcript:
#             {transcript}
#             """
            
#             genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
#             model = genai.GenerativeModel("gemini-2.5-flash")
#             response = await asyncio.to_thread(model.generate_content, prompt)
#             ai_summary = response.text.strip() if response else "No summary generated."

#         # 3. Insert into DB
#         pool = get_pool()
#         if not pool:
#             logger.error("⚠️ No DB pool available.")
#             return
            
#         async with pool.acquire() as conn:
#             clinic_id = await conn.fetchval("SELECT id FROM clinics LIMIT 1")
#             if not clinic_id:
#                 logger.error("⚠️ No clinic found.")
#                 return
            
#             await conn.execute("""
#                 INSERT INTO calls (clinic_id, type, caller, agent_type, duration, ai_summary)
#                 VALUES ($1, 'incoming', $2, 'ai', $3, $4)
#             """, clinic_id, caller_number, int(duration), ai_summary)
            
#         logger.info(f"✅ Call Log Saved | Summary: {ai_summary}")
#     except Exception as e:
#         logger.error(f"❌ Failed to save call log: {e}", exc_info=True)

# # ==========================================================
# # 🎙️ PIPECAT RUNNER
# # ==========================================================
# # 👇 FIXED: Added caller_number so it saves correctly in DB
# async def run_bot(transport: BaseTransport, call_sid: str = "local_test", caller_number: str = "Unknown"):
#     await ensure_redis_client()
#     session_id = call_sid[:8]

#     google_creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
#     if not google_creds_path or not os.path.exists(google_creds_path):
#         logger.error(f"🚨 Missing Google Credentials! Path: {google_creds_path}")

#     stt = SarvamSTTService(
#         api_key=os.getenv("SARVAM_API_KEY"),
#         language="unknown",
#         model="saaras:v3",
#         mode="transcribe"
#     )
#     tts = GoogleTTSService(
#         credentials_path=google_creds_path,
#         voice="te-IN-Chirp3-HD-Despina",
#         language="te-IN"
#     )
#     llm = GoogleLLMService(
#         api_key=os.getenv("GEMINI_API_KEY"),
#         model="gemini-2.5-flash"
#     )

#     register_all_tools(llm)

#     context = LLMContext(
#         messages=[{"role": "system", "content": VOICE_SYSTEM_PROMPT}],
#         tools=get_tools_schema()
#     )
#     context_aggregator = LLMContextAggregatorPair(context)
#     billing_tracker = BillingTracker(context, session_id)

#     pipeline = Pipeline([
#         transport.input(),
#         stt,
#         STTTextCleanerProcessor(session_id),
#         context_aggregator.user(),
#         llm,
#         billing_tracker,
#         AutoLanguageProcessor(session_id),
#         tts,
#         PipecatBugFixProcessor(),
#         transport.output(),
#         context_aggregator.assistant()
#     ])

#     task = PipelineTask(
#         pipeline,
#         params=PipelineParams(
#             audio_in_sample_rate=8000,
#             audio_out_sample_rate=8000,
#             enable_metrics=True,
#             enable_usage_metrics=True
#         )
#     )

#     @transport.event_handler("on_client_connected")
#     async def on_client_connected(transport, client):
#         await task.queue_frames([
#             TTSSpeakFrame(
#                 "నమస్కారం! మిత్ర హాస్పిటల్స్‌కు స్వాగతం. నేను మీకు ఎలా సహాయపడగలను?",
#                 append_to_context=True
#             )
#         ])

#     @transport.event_handler("on_client_disconnected")
#     async def on_client_disconnected(transport, client):
#         await task.cancel()

#     runner = PipelineRunner(handle_sigint=False)
#     await runner.run(task)
#     billing_tracker.generate_receipt()

#     # 👇 NEW: Fire off the database save after the call ends
#     duration = time.time() - billing_tracker.start_time
#     # We await this now so the process won't die before saving is done
#     await save_call_log(call_sid, caller_number, duration, context.messages)


# # ==========================================================
# # 📞 TWILIO ROUTES
# # ==========================================================

# @router.post("/incoming")
# # 👇 FIXED: Grab 'From' phone number from Twilio webhook
# async def incoming_call(request: Request, From: str = Form("Unknown")):
#     """
#     Primary Twilio webhook — safely returns raw XML without relying on the Twilio SDK.
#     """
#     logger.info("📞 Incoming Twilio call webhook triggered!")
    
#     base_url = str(request.base_url).replace("http://", "wss://").replace("https://", "wss://").rstrip("/")
#     wss_url = f"{base_url}/media"
    
#     # Passing the caller ID back to the socket
#     xml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
# <Response>
#     <Connect>
#         <Stream url="{wss_url}">
#             <Parameter name="caller" value="{From}" />
#         </Stream>
#     </Connect>
# </Response>"""

#     logger.info(f"📤 Sending XML to Twilio:\n{xml_response}")
#     return Response(content=xml_response, media_type="text/xml")


# @router.post("/voice")
# async def voice_callback(request: Request):
#     """Fallback route just in case Twilio is configured to /voice"""
#     return await incoming_call(request)


# @router.websocket("/media")
# async def websocket_endpoint(websocket: WebSocket):
#     await websocket.accept()
#     logger.info("🔌 Twilio connected to /media WebSocket.")

#     try:
#         raw = await websocket.receive_text()
#         data = json.loads(raw)
        
#         if data.get("event") == "connected":
#             raw = await websocket.receive_text()
#             data = json.loads(raw)

#         if data.get("event") == "start":
#             stream_sid = data["start"]["streamSid"]
#             call_sid   = data.get("start", {}).get("callSid", "twilio_call")
            
#             # 👇 FIXED: Extract caller number from Twilio Stream parameters
#             caller_number = data.get("start", {}).get("customParameters", {}).get("caller", "Unknown")
            
#             logger.info(f"🎙️  Twilio Stream started | streamSid={stream_sid} | callSid={call_sid} | Caller={caller_number}")
            
#             serializer = TwilioFrameSerializer(
#                 stream_sid=stream_sid,
#                 params=TwilioFrameSerializer.InputParams(auto_hang_up=False)
#             )
            
#             transport = FastAPIWebsocketTransport(
#                 websocket=websocket,
#                 params=FastAPIWebsocketParams(
#                     audio_in_enabled=True,
#                     audio_out_enabled=True,
#                     add_wav_header=False,
#                     serializer=serializer
#                 )
#             )
#             await run_bot(transport, call_sid=call_sid, caller_number=caller_number)
#         else:
#             await websocket.close()
#     except Exception as e:
#         logger.error(f"❌ Twilio WebSocket error: {e}", exc_info=True)
        

# # ==========================================================
# # 📞 VOBIZ ROUTES
# # ==========================================================
# @router.post("/vobiz-events")
# async def vobiz_events(request: Request):
#     logger.info("📞 Incoming Vobiz call webhook triggered!")
    
#     body_bytes = await request.body()
#     body_str = body_bytes.decode('utf-8')
#     logger.info(f"📥 VOBIZ PAYLOAD: {body_str}")
    
#     if "ringing" in body_str.lower() or "initiated" in body_str.lower():
#         return Response(content="<Response></Response>", media_type="text/xml")
        
#     if "event=hangup" in body_str.lower():
#         logger.info("👋 Call has officially ended. Sending blank acknowledgement.")
#         return Response(content="<Response></Response>", media_type="text/xml")
    
#     wss_url = "wss://gymnastic-oversentimentally-marcelino.ngrok-free.dev/vobiz-media"
    
#     xml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
# <Response>
#     <Speak>Connecting to AI.</Speak>
#     <Stream bidirectional="true" keepCallAlive="true">{wss_url}</Stream>
# </Response>"""

#     return Response(content=xml_response, media_type="text/xml")

# @router.websocket("/vobiz-media")
# async def vobiz_websocket_endpoint(websocket: WebSocket):
#     await websocket.accept()
#     logger.info("🔌 Vobiz connected to /vobiz-media WebSocket.")
#     call_sid = "vobiz_call"

#     try:
#         stream_sid = "vobiz_stream"
#         caller_number = "Unknown"

#         while True:
#             raw = await websocket.receive_text()
#             data = json.loads(raw)
#             event_type = data.get("event")
            
#             if event_type == "connected":
#                 continue 
#             elif event_type == "start":
#                 start_obj = data.get("start", {})
#                 stream_sid = start_obj.get("streamId") or data.get("streamId") or "vobiz_stream"
#                 call_sid = start_obj.get("callId") or data.get("callId") or "vobiz_call"
#                 # 👇 FIXED: Extract caller number from Vobiz metadata
#                 caller_number = start_obj.get("metadata", {}).get("from", "Unknown")
#                 break
#             elif event_type == "media":
#                 break

#         logger.info(f"🎙️  Vobiz Stream started | streamSid={stream_sid} | callSid={call_sid} | Caller={caller_number}")

#         serializer = VobizFrameSerializer(stream_sid=stream_sid)
        
#         transport = FastAPIWebsocketTransport(
#             websocket=websocket,
#             params=FastAPIWebsocketParams(
#                 audio_in_enabled=True,
#                 audio_out_enabled=True,
#                 add_wav_header=False,
#                 serializer=serializer
#             )
#         )
        
#         await run_bot(transport, call_sid=call_sid, caller_number=caller_number)

#     except Exception as e:
#         logger.error(f"❌ Vobiz WebSocket error: {e}", exc_info=True)
#     finally:
#         try:
#             await websocket.close()
#         except:
#             pass
        
#         await force_vobiz_hangup(call_sid)


# # ==========================================================
# # 💳 RAZORPAY WEBHOOK
# # ==========================================================
# @router.post("/razorpay-webhook")
# async def razorpay_webhook(request: Request):
#     try:
#         payload = await request.json()
#         event = payload.get("event")

#         if event in ["payment_link.paid", "payment_link.cancelled", "payment_link.expired"]:
#             entity = payload.get("payload", {}).get("payment_link", {}).get("entity", {})
#             appointment_id = entity.get("notes", {}).get("appointment_id")

#             if appointment_id:
#                 from tools.pool import get_pool
#                 pool = get_pool()
#                 status = "paid" if event == "payment_link.paid" else "failed"

#                 async with pool.acquire() as conn:
#                     await conn.execute(
#                         "UPDATE payments SET status = $1 WHERE appointment_id = $2",
#                         status, appointment_id
#                     )
#                 logger.info(f"💳 Payment {event} → DB updated to '{status}' for {appointment_id}")

#                 if event == "payment_link.paid":
#                     await handle_successful_payment(appointment_id)

#         return {"status": "ok"}
#     except Exception as e:
#         logger.error(f"❌ Razorpay Webhook Error: {e}")
#         return {"status": "error"}
