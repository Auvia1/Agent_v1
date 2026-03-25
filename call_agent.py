
#call_agent.py
# import os
# import json
# import uuid
# from datetime import datetime
# import pytz
# import redis.asyncio as redis
# from dotenv import load_dotenv
# from loguru import logger
# from fastapi import APIRouter, Request, Form, WebSocket
# from fastapi.responses import HTMLResponse

# try:
#     from twilio.twiml.voice_response import VoiceResponse, Connect
# except ImportError:
#     VoiceResponse = None
#     Connect = None
#     logger.warning("⚠️ Twilio not installed — phone call endpoints disabled.")

# from pipecat.frames.frames import Frame, TextFrame, TranscriptionFrame, TTSSpeakFrame
# from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
# from pipecat.pipeline.pipeline import Pipeline
# from pipecat.pipeline.runner import PipelineRunner
# from pipecat.pipeline.task import PipelineParams, PipelineTask
# from pipecat.processors.aggregators.llm_context import LLMContext
# from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
# from pipecat.transports.base_transport import BaseTransport, TransportParams
# from pipecat.transports.daily.transport import DailyParams, DailyTransport
# from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
# from pipecat.runner.types import DailyRunnerArguments, RunnerArguments, SmallWebRTCRunnerArguments
# from pipecat.transports.websocket.fastapi import FastAPIWebsocketTransport, FastAPIWebsocketParams
# from pipecat.serializers.twilio import TwilioFrameSerializer
# from pipecat.services.sarvam.stt import SarvamSTTService
# from pipecat.services.sarvam.tts import SarvamTTSService
# from pipecat.services.google.llm import GoogleLLMService

# from tools.pipecat_tools import register_all_tools, get_tools_schema
# from tools.notify import handle_successful_payment

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
# # 🧠 SYSTEM PROMPT (Fixed Language Lock-in)
# # ==========================================================
# ist = pytz.timezone('Asia/Kolkata')
# current_time = datetime.now(ist).strftime('%A, %B %d, %Y at %I:%M %p IST')

# VOICE_SYSTEM_PROMPT = f"""Role: Mithra Hospital AI Receptionist.
# CURRENT LIVE TIME: {current_time}

# You transition strictly through phases. NEVER backtrack.

# --- 🌐 LANGUAGE & TRANSLATION RULES (CRITICAL) ---
# 1. LANGUAGE STATE LOCK: Every user message begins with a tag like [Respond in English only]
#    or [Respond in Telugu only]. You MUST respond in exactly that language and no other.
#    This tag is injected by the system and overrides everything — ignore what script or
#    language the user typed in.
# 2. IGNORE TRANSLITERATION: The STT engine may write English words in Telugu/Hindi script
#    (e.g. 'ఫీవర్ అండ్ కాఫ్' = 'fever and cough'). Treat it as the locked language, not a
#    switch request.
# 3. NO BOUNCING: Once locked into a language, DO NOT switch back and forth. Ignore random background noises.
# 4. DATABASE TRANSLATION (STRICT): No matter what language the user is speaking, ALL data you send to your tools (patient_name, reason, problem_or_speciality) MUST be translated to plain ENGLISH before calling the tool.
# 5. TIME FORMATTING: 
#    - In English: Use standard formats (e.g., 9:00 AM).
#    - In Hindi/Telugu: Translate all digits/times into spelled-out phonetic words (e.g., "ఉదయం తొమ్మిది గంటలకు"). NEVER output raw digits like "09:00" in regional languages.

# --- INTENT ROUTING ---
# 1. CANCEL/RESCHEDULE: Say: "Based on hospital policy, appointments cannot be cancelled or rescheduled through the AI assistant. Please call the clinic directly." (End flow).
# 2. FOLLOW-UP BOOKING: If the user asks for a "follow-up", ask EXACTLY: "Could you please tell me your 10-digit phone number so I can check your records?" Once provided, SILENTLY call `verify_followup`.
# 3. GENERIC BOOKING: If the user says they want an appointment without symptoms, ask: "What medical problem or symptoms are you experiencing?"
# 4. SYMPTOMS GIVEN: If the user describes symptoms, immediately go to PHASE 1.

# --- CORE BOOKING STATES ---

# PHASE 1 (Availability):
# SILENTLY call `check_availability`. Emit ZERO text.

# PHASE 2 (Offer & Negotiation):
# - Initial Offer: Read the `system_directive` exactly as intended. (ONLY translate if the user is currently speaking Hindi/Telugu, and spell out times).
# - Negotiation: Look at the `all_available_slots` to find alternative times if asked. DO NOT repeat the initial offer if they just ask a question.

# PHASE 3 (Details Request):
# If the user agrees to a slot, ask EXACTLY: "Could you please tell me the patient's name and 10-digit phone number?" 
# (Note: If they already provided their phone number for a follow-up, just ask for their name).
# CRITICAL: Ask this ONLY ONCE. NEVER mention the doctor or time again.

# PHASE 4 (The Silent Trigger):
# If the user provides a name and a 10-digit number, YOU MUST STOP SPEAKING.
# Immediately call `voice_book_appointment` (Ensure you translate the name and reason to English first!).
# CRITICAL: Emit ZERO characters of text. DO NOT say "Okay" or repeat the name.

# PHASE 5 (Confirmation):
# ONLY AFTER the tool returns "success", say exactly the confirmation message based on the tool result:
# - Paid Appointment: "A tentative appointment is booked and a payment link is sent to your WhatsApp. Please do the payment in 15 minutes to confirm the booking. Thank you."
# - Free Follow-up: "Your free follow-up appointment is confirmed. A WhatsApp message has been sent to you. Thank you."
# CRITICAL RULE: DO NOT append any questions like "Shall I book this?" at the end. Just say the confirmation and STOP.
# Immediately after saying this, call the `end_call` tool.
# """

# # ==========================================================
# # 🛠️ PROCESSORS (Added Noise Filter)
# # ==========================================================
# class STTTextCleanerProcessor(FrameProcessor):
#     async def process_frame(self, frame: Frame, direction: FrameDirection):
#         await super().process_frame(frame, direction)
#         if isinstance(frame, TranscriptionFrame):
#             text = frame.text.strip().lower()
            
#             # 🔥 FIX: Ignore tiny background noises to stop language hallucinations
#             if len(text) <= 2:
#                 return # Drops the frame entirely so the LLM doesn't hear it
                
#             if text:
#                 logger.info(f"🎤 USER SAID [Raw STT]: {text}")
#             corrections = {
#                 "పార్లమెంట్": "అపాయింట్మెంట్", "apartment": "appointment",
#                 "అపార్ట్మెంట్": "అపాయింట్మెంట్", "department": "appointment",
#                 "తెలుగు": "telugu", "हिंदी": "hindi"
#             }
#             for k, v in corrections.items():
#                 text = text.replace(k, v)
#             frame.text = text
            
#         await self.push_frame(frame, direction)

# class BillingTracker(FrameProcessor):
#     def __init__(self):
#         super().__init__()
#         self.tts_chars = 0
#         self.llm_output_tokens = 0

#     async def process_frame(self, frame: Frame, direction: FrameDirection):
#         await super().process_frame(frame, direction)
#         if isinstance(frame, TextFrame):
#             self.tts_chars += len(frame.text)
#             self.llm_output_tokens += (len(frame.text) / 4.0)
#         await self.push_frame(frame, direction)

# # ==========================================================
# # 🎙️ PIPECAT RUNNER
# # ==========================================================
# async def run_bot(transport: BaseTransport, call_sid: str = "local_test", is_twilio: bool = False):
#     await ensure_redis_client()

#     in_rate = 8000 if is_twilio else 16000
#     out_rate = 8000 if is_twilio else 24000

#     stt = SarvamSTTService(
#         api_key=os.getenv("SARVAM_API_KEY"),
#         language="unknown",
#         model="saaras:v3",
#         mode="transcribe"
#     )
#     tts = SarvamTTSService(
#         api_key=os.getenv("SARVAM_API_KEY"),
#         target_language_code="en-IN",
#         model="bulbul:v2",
#         speaker="anushka",
#         speech_sample_rate=out_rate
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
#     billing_tracker = BillingTracker()

#     pipeline = Pipeline([
#         transport.input(),
#         stt,
#         STTTextCleanerProcessor(),
#         context_aggregator.user(),
#         llm,
#         billing_tracker,
#         tts,
#         transport.output(),
#         context_aggregator.assistant()
#     ])

#     task = PipelineTask(
#         pipeline,
#         params=PipelineParams(
#             audio_in_sample_rate=in_rate,
#             audio_out_sample_rate=out_rate,
#             enable_metrics=True,
#             enable_usage_metrics=True
#         )
#     )

#     @transport.event_handler("on_client_connected")
#     async def on_client_connected(transport, client):
#         if redis_client:
#             await redis_client.setex(f"active_call:{call_sid}", 3600, "in_progress")
#         await task.queue_frames([
#             TTSSpeakFrame(
#                 "Hello! Welcome to Mithra Hospitals. How can I help you today?",
#                 append_to_context=True
#             )
#         ])

#     @transport.event_handler("on_client_disconnected")
#     async def on_client_disconnected(transport, client):
#         if redis_client:
#             await redis_client.delete(f"active_call:{call_sid}")
#             await redis_client.setex(
#                 f"history:{call_sid}",
#                 86400,
#                 json.dumps([m for m in context.messages if m.get("role") != "system"])
#             )
#         await task.cancel()

#     runner = PipelineRunner(handle_sigint=False)
#     await runner.run(task)

# # ==========================================================
# # 🔌 PIPECAT WEB RUNNER ENTRY POINT
# # ==========================================================
# async def bot(runner_args: RunnerArguments):
#     transport = None
#     if isinstance(runner_args, SmallWebRTCRunnerArguments):
#         transport = SmallWebRTCTransport(
#             webrtc_connection=runner_args.webrtc_connection,
#             params=TransportParams(
#                 audio_in_enabled=True,
#                 audio_out_enabled=True,
#                 audio_out_sample_rate=24000
#             )
#         )
#     elif isinstance(runner_args, DailyRunnerArguments):
#         transport = DailyTransport(
#             runner_args.room_url,
#             runner_args.token,
#             "Pipecat Bot",
#             params=DailyParams(
#                 audio_in_enabled=True,
#                 audio_out_enabled=True,
#                 audio_out_sample_rate=24000
#             )
#         )
#     if transport:
#         await run_bot(transport, call_sid=f"local_webrtc_{uuid.uuid4().hex[:8]}", is_twilio=False)

# # ==========================================================
# # 💳 RAZORPAY WEBHOOK
# # ==========================================================
# @router.post("/razorpay-webhook")
# async def razorpay_webhook(request: Request):
#     try:
#         payload = await request.json()
#         if payload.get("event") == "payment_link.paid":
#             entity = payload.get("payload", {}).get("payment_link", {}).get("entity", {})
#             appointment_id = entity.get("notes", {}).get("appointment_id")
#             if appointment_id:
#                 logger.info(f"💰 Payment received for appointment: {appointment_id}")
#                 await handle_successful_payment(appointment_id)
#         return {"status": "ok"}
#     except Exception as e:
#         logger.error(f"❌ Razorpay Webhook Error: {e}")
#         return {"status": "error"}

# # ==========================================================
# # 📞 TWILIO ROUTES
# # ==========================================================
# @router.post("/incoming")
# async def incoming_call(request: Request, CallSid: str = Form(None)):
#     logger.info(f"📞 NEW CALL INITIATED! ID: {CallSid}")
#     if not VoiceResponse or not Connect:
#         return HTMLResponse(content="Twilio not installed", status_code=500)
#     response = VoiceResponse()
#     connect = Connect()
#     wss_url = str(request.base_url).replace("http", "ws") + "media"
#     if CallSid:
#         connect.stream(url=wss_url).parameter(name="CallSid", value=CallSid)
#     response.append(connect)
#     return HTMLResponse(content=str(response), media_type="application/xml")

# @router.post("/voice")
# async def voice_callback(request: Request):
#     base_url = str(request.base_url).replace("http://", "wss://").replace("https://", "wss://")
#     twiml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
#     <Response>
#         <Connect>
#             <Stream url="{base_url}media" />
#         </Connect>
#         <Hangup />
#     </Response>"""
#     return HTMLResponse(content=twiml_response, media_type="application/xml")

# @router.websocket("/media")
# async def websocket_endpoint(websocket: WebSocket):
#     await websocket.accept()
#     logger.info("🔌 Twilio connected to /media WebSocket!")
#     try:
#         message = await websocket.receive_text()
#         data = json.loads(message)
#         if data.get('event') == 'connected':
#             message = await websocket.receive_text()
#             data = json.loads(message)
#         if data.get('event') == 'start':
#             stream_sid = data['start']['streamSid']
#             call_sid = data['start']['callSid']
#             logger.info(f"🎙️ Twilio Stream Started: {stream_sid}")
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
#             await run_bot(transport, call_sid=call_sid, is_twilio=True)
#     except Exception as e:
#         logger.error(f"❌ WebSocket error: {e}")

#call_agent.py
import os
import json
import uuid
from datetime import datetime
import pytz
import redis.asyncio as redis
from dotenv import load_dotenv
from loguru import logger
from fastapi import APIRouter, Request, Form, WebSocket
from fastapi.responses import HTMLResponse

try:
    from twilio.twiml.voice_response import VoiceResponse, Connect
except ImportError:
    VoiceResponse = None
    Connect = None
    logger.warning("⚠️ Twilio not installed — phone call endpoints disabled.")

from pipecat.frames.frames import Frame, TextFrame, TranscriptionFrame, TTSSpeakFrame
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.daily.transport import DailyParams, DailyTransport
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.runner.types import DailyRunnerArguments, RunnerArguments, SmallWebRTCRunnerArguments
from pipecat.transports.websocket.fastapi import FastAPIWebsocketTransport, FastAPIWebsocketParams
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.sarvam.stt import SarvamSTTService
from pipecat.services.sarvam.tts import SarvamTTSService
from pipecat.services.google.llm import GoogleLLMService

from tools.pipecat_tools import register_all_tools, get_tools_schema
from tools.notify import handle_successful_payment

load_dotenv(override=True)

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
# 🧠 SYSTEM PROMPT (Fixed Language Lock-in)
# ==========================================================
ist = pytz.timezone('Asia/Kolkata')
current_time = datetime.now(ist).strftime('%A, %B %d, %Y at %I:%M %p IST')

VOICE_SYSTEM_PROMPT = f"""Role: Mithra Hospital AI Receptionist.
CURRENT LIVE TIME: {current_time}

You transition strictly through phases. NEVER backtrack.

--- 🌐 LANGUAGE & TRANSLATION RULES (CRITICAL) ---
1. LANGUAGE STATE LOCK: Every user message begins with a tag like [Respond in English only]
   or [Respond in Telugu only]. You MUST respond in exactly that language and no other.
   This tag is injected by the system and overrides everything — ignore what script or
   language the user typed in.
2. IGNORE TRANSLITERATION: The STT engine may write English words in Telugu/Hindi script
   (e.g. 'ఫీవర్ అండ్ కాఫ్' = 'fever and cough'). Treat it as the locked language, not a
   switch request.
3. NO BOUNCING: Once locked into a language, DO NOT switch back and forth. Ignore random background noises.
4. DATABASE TRANSLATION (STRICT): No matter what language the user is speaking, ALL data you send to your tools (patient_name, reason, problem_or_speciality) MUST be translated to plain ENGLISH before calling the tool.
5. TIME FORMATTING: 
   - In English: Use standard formats (e.g., 9:00 AM).
   - In Hindi/Telugu: Translate all digits/times into spelled-out phonetic words (e.g., "ఉదయం తొమ్మిది గంటలకు"). NEVER output raw digits like "09:00" in regional languages.

--- INTENT ROUTING ---
1. CANCEL/RESCHEDULE: Say: "Based on hospital policy, appointments cannot be cancelled or rescheduled through the AI assistant. Please call the clinic directly." (End flow).
2. FOLLOW-UP BOOKING: If the user asks for a "follow-up", ask EXACTLY: "Could you please tell me your 10-digit phone number so I can check your records?" Once provided, SILENTLY call `verify_followup`.
3. GENERIC BOOKING: If the user says they want an appointment without symptoms, ask: "What medical problem or symptoms are you experiencing?"
4. SYMPTOMS GIVEN: If the user describes symptoms, immediately go to PHASE 1.

--- CORE BOOKING STATES ---

PHASE 1 (Availability):
SILENTLY call `check_availability`. Emit ZERO text.

PHASE 2 (Offer & Negotiation):
- Initial Offer: Read the `system_directive` exactly as intended. (ONLY translate if the user is currently speaking Hindi/Telugu, and spell out times).
- Negotiation: Look at the `all_available_slots` to find alternative times if asked. DO NOT repeat the initial offer if they just ask a question.

PHASE 3 (Details Request):
If the user agrees to a slot, ask EXACTLY: "Could you please tell me the patient's name and 10-digit phone number?" 
(Note: If they already provided their phone number for a follow-up, just ask for their name).
CRITICAL: Ask this ONLY ONCE. NEVER mention the doctor or time again.

PHASE 4 (The Silent Trigger):
If the user provides a name and a 10-digit number, YOU MUST STOP SPEAKING.
Immediately call `voice_book_appointment` (Ensure you translate the name and reason to English first!).
CRITICAL: Emit ZERO characters of text. DO NOT say "Okay" or repeat the name.

PHASE 5 (Confirmation):
ONLY AFTER the tool returns "success", say exactly the confirmation message based on the tool result:
- Paid Appointment: "A tentative appointment is booked and a payment link is sent to your WhatsApp. Please do the payment in 15 minutes to confirm the booking. Thank you."
- Free Follow-up: "Your free follow-up appointment is confirmed. A WhatsApp message has been sent to you. Thank you."
CRITICAL RULE: DO NOT append any questions like "Shall I book this?" at the end. Just say the confirmation and STOP.
Immediately after saying this, call the `end_call` tool.
"""

# ==========================================================
# 🛠️ PROCESSORS
# ==========================================================
class STTTextCleanerProcessor(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.curr_lang = "English"  # 🌐 Default language state

    # ------------------------------------------------------------------
    # Detect language purely from Unicode script ranges (no extra libs)
    # ------------------------------------------------------------------
    def _detect_script(self, text: str):
        telugu = sum(1 for c in text if '\u0C00' <= c <= '\u0C7F')
        hindi  = sum(1 for c in text if '\u0900' <= c <= '\u097F')
        total  = max(len(text.strip()), 1)
        if telugu / total > 0.15:
            return "Telugu"
        if hindi / total > 0.15:
            return "Hindi"
        return None  # Looks like English / unknown — keep current

    # ------------------------------------------------------------------
    # Detect EXPLICIT "please talk in X" requests
    # Must match BEFORE auto-detect so an explicit request always wins
    # ------------------------------------------------------------------
    def _check_explicit_lang_request(self, text: str):
        t = text.lower()
        if any(k in t for k in ["in telugu", "speak telugu", "talk telugu",
                                  "telugu lo", "telugu లో", "switch to telugu"]):
            return "Telugu"
        if any(k in t for k in ["in hindi", "speak hindi", "talk hindi",
                                  "hindi mein", "hindi me", "switch to hindi"]):
            return "Hindi"
        if any(k in t for k in ["in english", "speak english", "talk english",
                                  "switch to english"]):
            return "English"
        return None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame):
            original_text = frame.text.strip()       # Keep original for script detection
            text = original_text.lower()

            # 🔥 Ignore tiny background noises to stop language hallucinations
            if len(text) <= 2:
                return  # Drop frame entirely

            logger.info(f"🎤 USER SAID [Raw STT]: {text}")

            # ── STT correction map ─────────────────────────────────────
            corrections = {
                "పార్లమెంట్": "అపాయింట్మెంట్", "apartment": "appointment",
                "అపార్ట్మెంట్": "అపాయింట్మెంట్", "department": "appointment",
                "తెలుగు": "telugu", "हिंदी": "hindi"
            }
            for k, v in corrections.items():
                text = text.replace(k, v)

            # ── Language state update ──────────────────────────────────
            # 1️⃣  Explicit intent ("can you talk in Telugu?") always wins
            explicit = self._check_explicit_lang_request(text)
            if explicit:
                if explicit != self.curr_lang:
                    logger.info(f"🌐 Explicit language switch → {explicit}")
                self.curr_lang = explicit
            else:
                # 2️⃣  Auto-detect from Unicode script of the raw STT output
                detected = self._detect_script(original_text)
                if detected and detected != self.curr_lang:
                    logger.info(f"🌐 Script auto-detected language → {detected}")
                    self.curr_lang = detected

            # ── Inject language lock tag so the LLM obeys it ──────────
            frame.text = f"[Respond in {self.curr_lang} only] {text}"
            logger.info(f"📝 Frame tagged: [Respond in {self.curr_lang} only]")

        await self.push_frame(frame, direction)


class BillingTracker(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.tts_chars = 0
        self.llm_output_tokens = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TextFrame):
            self.tts_chars += len(frame.text)
            self.llm_output_tokens += (len(frame.text) / 4.0)
        await self.push_frame(frame, direction)

# ==========================================================
# 🎙️ PIPECAT RUNNER
# ==========================================================
async def run_bot(transport: BaseTransport, call_sid: str = "local_test", is_twilio: bool = False):
    await ensure_redis_client()

    in_rate = 8000 if is_twilio else 16000
    out_rate = 8000 if is_twilio else 24000

    stt = SarvamSTTService(
        api_key=os.getenv("SARVAM_API_KEY"),
        language="unknown",
        model="saaras:v3",
        mode="transcribe"
    )
    tts = SarvamTTSService(
        api_key=os.getenv("SARVAM_API_KEY"),
        target_language_code="en-IN",
        model="bulbul:v2",
        speaker="anushka",
        speech_sample_rate=out_rate
    )
    llm = GoogleLLMService(
        api_key=os.getenv("GEMINI_API_KEY"),
        model="gemini-2.5-flash"
    )

    register_all_tools(llm)

    context = LLMContext(
        messages=[{"role": "system", "content": VOICE_SYSTEM_PROMPT}],
        tools=get_tools_schema()
    )
    context_aggregator = LLMContextAggregatorPair(context)
    billing_tracker = BillingTracker()

    pipeline = Pipeline([
        transport.input(),
        stt,
        STTTextCleanerProcessor(),
        context_aggregator.user(),
        llm,
        billing_tracker,
        tts,
        transport.output(),
        context_aggregator.assistant()
    ])

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=in_rate,
            audio_out_sample_rate=out_rate,
            enable_metrics=True,
            enable_usage_metrics=True
        )
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        if redis_client:
            await redis_client.setex(f"active_call:{call_sid}", 3600, "in_progress")
        await task.queue_frames([
            TTSSpeakFrame(
                "Hello! Welcome to Mithra Hospitals. How can I help you today?",
                append_to_context=True
            )
        ])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        if redis_client:
            await redis_client.delete(f"active_call:{call_sid}")
            await redis_client.setex(
                f"history:{call_sid}",
                86400,
                json.dumps([m for m in context.messages if m.get("role") != "system"])
            )
        await task.cancel()

    runner = PipelineRunner(handle_sigint=False)
    await runner.run(task)

# ==========================================================
# 🔌 PIPECAT WEB RUNNER ENTRY POINT
# ==========================================================
async def bot(runner_args: RunnerArguments):
    transport = None
    if isinstance(runner_args, SmallWebRTCRunnerArguments):
        transport = SmallWebRTCTransport(
            webrtc_connection=runner_args.webrtc_connection,
            params=TransportParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                audio_out_sample_rate=24000
            )
        )
    elif isinstance(runner_args, DailyRunnerArguments):
        transport = DailyTransport(
            runner_args.room_url,
            runner_args.token,
            "Pipecat Bot",
            params=DailyParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                audio_out_sample_rate=24000
            )
        )
    if transport:
        await run_bot(transport, call_sid=f"local_webrtc_{uuid.uuid4().hex[:8]}", is_twilio=False)

# ==========================================================
# 💳 RAZORPAY WEBHOOK
# ==========================================================
@router.post("/razorpay-webhook")
async def razorpay_webhook(request: Request):
    try:
        payload = await request.json()
        if payload.get("event") == "payment_link.paid":
            entity = payload.get("payload", {}).get("payment_link", {}).get("entity", {})
            appointment_id = entity.get("notes", {}).get("appointment_id")
            if appointment_id:
                logger.info(f"💰 Payment received for appointment: {appointment_id}")
                await handle_successful_payment(appointment_id)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"❌ Razorpay Webhook Error: {e}")
        return {"status": "error"}

# ==========================================================
# 📞 TWILIO ROUTES
# ==========================================================
@router.post("/incoming")
async def incoming_call(request: Request, CallSid: str = Form(None)):
    logger.info(f"📞 NEW CALL INITIATED! ID: {CallSid}")
    if not VoiceResponse or not Connect:
        return HTMLResponse(content="Twilio not installed", status_code=500)
    response = VoiceResponse()
    connect = Connect()
    wss_url = str(request.base_url).replace("http", "ws") + "media"
    if CallSid:
        connect.stream(url=wss_url).parameter(name="CallSid", value=CallSid)
    response.append(connect)
    return HTMLResponse(content=str(response), media_type="application/xml")

@router.post("/voice")
async def voice_callback(request: Request):
    base_url = str(request.base_url).replace("http://", "wss://").replace("https://", "wss://")
    twiml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
    <Response>
        <Connect>
            <Stream url="{base_url}media" />
        </Connect>
        <Hangup />
    </Response>"""
    return HTMLResponse(content=twiml_response, media_type="application/xml")

@router.websocket("/media")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("🔌 Twilio connected to /media WebSocket!")
    try:
        message = await websocket.receive_text()
        data = json.loads(message)
        if data.get('event') == 'connected':
            message = await websocket.receive_text()
            data = json.loads(message)
        if data.get('event') == 'start':
            stream_sid = data['start']['streamSid']
            call_sid = data['start']['callSid']
            logger.info(f"🎙️ Twilio Stream Started: {stream_sid}")
            serializer = TwilioFrameSerializer(
                stream_sid=stream_sid,
                params=TwilioFrameSerializer.InputParams(auto_hang_up=False)
            )
            transport = FastAPIWebsocketTransport(
                websocket=websocket,
                params=FastAPIWebsocketParams(
                    audio_in_enabled=True,
                    audio_out_enabled=True,
                    add_wav_header=False,
                    serializer=serializer
                )
            )
            await run_bot(transport, call_sid=call_sid, is_twilio=True)
    except Exception as e:
        logger.error(f"❌ WebSocket error: {e}")