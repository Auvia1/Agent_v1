# call_agent.py
# call_agent.py
import os

# 👉 MAC M-SERIES FIXES: Prevent PyTorch/gRPC thread deadlocks
os.environ["GRPC_DNS_RESOLVER"] = "native"
os.environ["GRPC_POLL_STRATEGY"] = "poll"
os.environ["OMP_NUM_THREADS"] = "1" 

import torch
torch.set_num_threads(1) # 👈 THIS PREVENTS THE mutex.cc LOCK BLOCKING ERROR!

import json
import re 
import time 
from datetime import datetime
import pytz
import redis.asyncio as redis
from dotenv import load_dotenv
from loguru import logger
from fastapi import APIRouter, Request, Form, Response
from fastapi.responses import JSONResponse
import base64
import aiohttp
import asyncio
import google.generativeai as genai
from livekit import api
from pipecat.transports.livekit.transport import LiveKitTransport, LiveKitParams
from pipecat.serializers.base_serializer import FrameSerializer
from pipecat.frames.frames import Frame, AudioRawFrame, CancelFrame

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

# ✅ REVERTED TO SILERO (Now safe with the torch thread fix above)
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams

from pipecat.services.sarvam.stt import SarvamSTTService
from pipecat.services.google.tts import GoogleTTSService, TTSSettings
from pipecat.services.google.llm import GoogleLLMService

from tools.pipecat_tools import register_all_tools, get_tools_schema
from tools.notify import handle_successful_payment
from tools.pool import get_pool

load_dotenv(override=True)

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
summarizer_model = genai.GenerativeModel("gemini-2.5-flash")

router = APIRouter()
redis_conn_obj = None

async def ensure_redis_client():
    global redis_conn_obj
    if redis_conn_obj:
        return
    try:
        redis_endpoint = os.getenv("REDIS_URL", "redis://localhost:6379")
        redis_conn_obj = redis.from_url(redis_endpoint, decode_responses=True)
        await redis_conn_obj.ping()
        logger.info("✅ Redis client connected successfully.")
    except Exception as e:
        logger.warning(f"⚠️ Redis connection failed: {e}")
        redis_conn_obj = None

# ==========================================================
# 🧠 SYSTEM PROMPT
# ==========================================================
ist_zone = pytz.timezone('Asia/Kolkata')
live_time_str = datetime.now(ist_zone).strftime('%A, %B %d, %Y at %I:%M %p IST')

VOICE_SYSTEM_PROMPT = f"""Role: Your name is Vayu, the AI Receptionist for Mithra Hospitals.
CURRENT LIVE TIME: {live_time_str}

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
- TOKEN SYSTEM RULE: You MUST say EXACTLY: "We use a token system. Dr. Nikhil is available for these sessions. After the payment has been made, you will be allocated a token number that will give you entry. Please choose your preferred session."
- Ask the user *which specific session* they prefer. DO NOT just ask a yes/no question. Once they choose a specific session, immediately move to PHASE 3.

PHASE 3 (Details Request - ANTI-HALLUCINATION STRICT):
- If the user agrees to a slot, ask: "Could you please tell me the patient's name and 10-digit phone number?"
- ZERO-LEAKAGE RULE: You are STRICTLY FORBIDDEN from using any name, phone number, or data from previous calls or "default" values. 
- If you do not have the name and phone number from the CURRENT conversation, you MUST NOT call `voice_book_appointment`. You must ask the user for this information again.

PHASE 3.5 (Confirmation - CRITICAL):
Once the user provides their actual name and phone number, DO NOT call the booking tool immediately.
If the user provides fewer than 10 digits for the phone number, DO NOT confirm it. Instead, ask: "I only got a few digits, could you please repeat the full 10-digit number?"
If the number is complete (10 digits), you MUST repeat the phone number back to them digit-by-digit to confirm. 
Say: "Your number is [Digit Digit Digit...]. Is that correct?"

PHASE 4 (The Silent Trigger):
ONLY AFTER the user explicitly says "Yes", "Correct", "Avunu", etc., YOU MUST STOP SPEAKING.
Immediately call `voice_book_appointment` using ONLY the exact name and phone number the user provided. 
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
    def __init__(self, session_identifier):
        super().__init__()
        self.session_identifier = session_identifier

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame):
            stt_raw_text = frame.text.strip().lower()
            if len(stt_raw_text) <= 2:
                return
            logger.info(f"[{self.session_identifier}] 🎤 USER SAID [Raw STT]: {stt_raw_text}")
            lexicon_fixes = {
                "పార్లమెంట్": "అపాయింట్మెంట్",
                "apartment": "appointment",
                "అపార్ట్మెంట్": "అపాయింట్మెంట్",
                "department": "appointment",
                "తెలుగు": "telugu",
                "हिंदी": "hindi"
            }
            for wrong_val, right_val in lexicon_fixes.items():
                stt_raw_text = stt_raw_text.replace(wrong_val, right_val)
            frame.text = stt_raw_text
        await self.push_frame(frame, direction)

class AutoLanguageProcessor(FrameProcessor):
    def __init__(self, session_identifier):
        super().__init__()
        self.session_identifier = session_identifier
        self.active_locale = "te-IN"

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TextFrame):
            ai_spoken_text = frame.text.lower().strip()

            has_telugu_chars = bool(re.search(r'[\u0c00-\u0c7f]', ai_spoken_text))
            has_hindi_chars = bool(re.search(r'[\u0900-\u097f]', ai_spoken_text))

            target_locale = "en-US"
            if has_telugu_chars:
                target_locale = "te-IN"
            elif has_hindi_chars:
                target_locale = "hi-IN"

            female_voices = {
                "te-IN": "te-IN-Chirp3-HD-Laomedeia",
                "hi-IN": "hi-IN-Chirp3-HD-Laomedeia",
                "en-US": "en-US-Chirp3-HD-Laomedeia"
            }

            target_voice_id = female_voices.get(target_locale, "en-US-Chirp3-HD-Laomedeia")

            if target_locale != self.active_locale:
                logger.info(f"[{self.session_identifier}] 🌐 Switching to: {target_locale} | Voice: {target_voice_id}")
                self.active_locale = target_locale
            logger.info(
                f"TTS UPDATE -> locale={target_locale}, voice={target_voice_id}, rate={2.0 if target_locale == 'te-IN' else 1.0}"
            )
            await self.push_frame(
                TTSUpdateSettingsFrame(
                    delta=TTSSettings(
                        language=target_locale,
                        voice=target_voice_id,
                        speaking_rate=2.0 if target_locale == "te-IN" else 1.0
                    )
                ),
                direction
            )

        if isinstance(frame, FunctionCallInProgressFrame):
            time_filler_text = ""
            if frame.function_name == "voice_book_appointment":
                time_filler_text = "ఒక్క నిమిషం" if self.active_locale == "te-IN" else "एक मिनट" if self.active_locale == "hi-IN" else "One moment"
            elif frame.function_name in ["check_availability", "query_clinic_faq"]:
                time_filler_text = "చూస్తున్నాను" if self.active_locale == "te-IN" else "चेक कर रही हूँ" if self.active_locale == "hi-IN" else "Checking"
            
            if time_filler_text:
                logger.info(f"[{self.session_identifier}] ⏳ Filler: {time_filler_text}")
                await self.push_frame(TTSSpeakFrame(text=time_filler_text), direction)

        await self.push_frame(frame, direction)

class BillingTracker(FrameProcessor):
    def __init__(self, bot_context, session_identifier):
        super().__init__()
        self.tts_char_count = 0
        self.llm_out_tokens = 0
        self.timer_start = time.time()
        self.bot_context = bot_context
        self.session_identifier = session_identifier

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TextFrame) and direction == FrameDirection.DOWNSTREAM:
            self.tts_char_count += len(frame.text)
            self.llm_out_tokens += len(frame.text) / 4.0
        await self.push_frame(frame, direction)

    def generate_receipt(self):
        run_duration_sec = time.time() - self.timer_start
        run_duration_min = run_duration_sec / 60.0
        dialogue_log_text = json.dumps(self.bot_context.messages)
        total_input_tokens = len(dialogue_log_text) / 4.0
        
        stt_usd_val = run_duration_min * 0.006
        tts_usd_val = self.tts_char_count * 0.00003
        llm_in_usd_val = total_input_tokens * 0.0000003
        llm_out_usd_val = self.llm_out_tokens * 0.0000025
        grand_total_usd = stt_usd_val + tts_usd_val + llm_in_usd_val + llm_out_usd_val
        
        rate_usd_per_min = grand_total_usd / run_duration_min if run_duration_min > 0 else 0
        inr_multiplier = 93.29
        grand_total_inr = grand_total_usd * inr_multiplier
        inr_rate_per_min = rate_usd_per_min * inr_multiplier

        logger.info("\n" + "=" * 55)
        logger.info(f"[{self.session_identifier}] 💰 SESSION BILLING RECEIPT 💰")
        logger.info("=" * 55)
        logger.info(f"⏱️  Duration:     {run_duration_min:.2f} mins ({run_duration_sec:.0f}s)")
        logger.info(f"🎙️  STT Cost:     ₹{stt_usd_val * inr_multiplier:.4f} (${stt_usd_val:.4f})")
        logger.info(f"🧠 LLM In:       ₹{llm_in_usd_val * inr_multiplier:.4f} (~{total_input_tokens:.0f} tokens)")
        logger.info(f"🧠 LLM Out:      ₹{llm_out_usd_val * inr_multiplier:.4f} (~{self.llm_out_tokens:.0f} tokens)")
        logger.info(f"🗣️  TTS Cost:     ₹{tts_usd_val * inr_multiplier:.4f} ({self.tts_char_count} chars)")
        logger.info("-" * 55)
        logger.info(f"💵 TOTAL:        ₹{grand_total_inr:.4f} (${grand_total_usd:.4f})")
        logger.info(f"📊 PER MIN:      ₹{inr_rate_per_min:.4f} (${rate_usd_per_min:.4f})")
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

# ==========================================================
# 💾 DB SAVING HELPER
# ==========================================================
async def save_call_log(session_call_uuid: str, inbound_caller_id: str, call_runtime_sec: float, msg_history: list):
    logger.info(f"💾 Starting DB save for call {session_call_uuid}...")
    try:
        chat_lines = []
        for hist_item in msg_history:
            speaker_role = "AI" if hist_item.get('role') == 'model' else "Patient"
            content_str = ""
            if 'content' in hist_item and isinstance(hist_item['content'], str):
                content_str = hist_item['content']
            elif 'parts' in hist_item:
                for sub_part in hist_item['parts']:
                    if isinstance(sub_part, dict) and 'text' in sub_part:
                        content_str += sub_part['text']
                    elif isinstance(sub_part, str):
                        content_str += sub_part
            if content_str.strip():
                chat_lines.append(f"{speaker_role}: {content_str.strip()}")
        
        full_call_transcript = "\n".join(chat_lines)
        
        if not chat_lines:
            llm_generated_synopsis = "Call connected but no speech detected."
        else:
            summary_prompt = f"""
            Analyze the following medical receptionist transcript and provide a summary strictly following this template:
            "Patient {{Name}} booked an appointment on {{Date}} at {{Time}} with {{Doctor Name}} ({{Specialization}}) due to {{Reason}}"

            Rules:
            - If no appointment was booked, write: "Patient called to inquire but no appointment was booked."
            - Translate any Telugu details into English for the summary.
            - Keep it to one single sentence.

            Transcript:
            {full_call_transcript}
            """
            response = await asyncio.to_thread(summarizer_model.generate_content, summary_prompt)
            llm_generated_synopsis = response.text.strip() if response else "No summary generated."

        db_conn_pool = get_pool()
        if not db_conn_pool: return
        async with db_conn_pool.acquire() as conn:
            target_clinic_id = await conn.fetchval("SELECT id FROM clinics LIMIT 1")
            if not target_clinic_id: return
            await conn.execute("""
                INSERT INTO calls (clinic_id, type, caller, agent_type, duration, ai_summary)
                VALUES ($1, 'incoming', $2, 'ai', $3, $4)
            """, target_clinic_id, inbound_caller_id, int(call_runtime_sec), llm_generated_synopsis)
        logger.info(f"✅ Call Log Saved | Summary: {llm_generated_synopsis}")
    except Exception as e:
        logger.error(f"❌ Failed to save call log: {e}", exc_info=True)

# ==========================================================
# 🎙️ PIPECAT RUNNER (LIVEKIT SIP)
# ==========================================================
async def run_bot(room_name: str, session_call_uuid: str = "livekit_call", inbound_caller_id: str = "Unknown"):
    await ensure_redis_client()
    short_session_id = session_call_uuid[:8]
    gcp_credentials_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

    # --- 1. Generate LiveKit Access Token ---
    livekit_url = os.getenv("LIVEKIT_URL")
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")

    token = api.AccessToken(api_key, api_secret) \
        .with_identity(f"mithra-ai-{short_session_id}") \
        .with_name("Mithra AI") \
        .with_grants(api.VideoGrants(room_join=True, room=room_name)) \
        .to_jwt()

    # ✅ SILERO VAD (Safe now because of torch.set_num_threads(1))
    custom_vad = SileroVADAnalyzer(
        params=VADParams(
            stop_secs=1.5,
            start_secs=0.2,
            confidence=0.7
        )
    )

    # --- 2. Initialize LiveKit Transport ---
    active_transport = LiveKitTransport(
        room_name=room_name,
        url=livekit_url,
        token=token,
        params=LiveKitParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_analyzer=custom_vad 
        )
    )

    # --- 3. Initialize Services ---
    stt_service = SarvamSTTService(api_key=os.getenv("SARVAM_API_KEY"), language="unknown", model="saaras:v3", mode="transcribe")
    tts_service = GoogleTTSService(
        credentials_path=gcp_credentials_file,
        voice="te-IN-Chirp3-HD-Laomedeia",
        language="te-IN"
    )

    await tts_service._update_settings(
        TTSSettings(
            voice="te-IN-Chirp3-HD-Laomedeia",
            language="te-IN",
            speaking_rate=2.0
        )
    )
    llm_service = GoogleLLMService(api_key=os.getenv("GEMINI_API_KEY"), model="gemini-2.5-flash")

    register_all_tools(llm_service)

    sys_context = LLMContext(messages=[{"role": "system", "content": VOICE_SYSTEM_PROMPT}], tools=get_tools_schema())

    context_aggregator = LLMContextAggregatorPair(sys_context)

    bill_tracker = BillingTracker(sys_context, short_session_id)

    # --- 4. Pipeline Setup ---
    pipeline = Pipeline([
        active_transport.input(),
        stt_service,
        STTTextCleanerProcessor(short_session_id),
        context_aggregator.user(),
        llm_service,
        bill_tracker,
        AutoLanguageProcessor(short_session_id),
        tts_service,
        PipecatBugFixProcessor(),
        active_transport.output(),
        context_aggregator.assistant()
    ])

    task_pipeline = PipelineTask(pipeline, params=PipelineParams(audio_in_sample_rate=16000, audio_out_sample_rate=16000))

    async def trigger_greeting():
        await asyncio.sleep(0.2)
        logger.info(f"[{short_session_id}] 🗣️ Triggering initial Vayu greeting...")
        vayu_greeting = "నమస్కారం! నేను వాయు, మిత్ర హాస్పిటల్స్ నుండి మాట్లాడుతున్నాను. నేను మీకు ఎలా సహాయపడగలను? మీ ఆరోగ్య సమస్య లేదా లక్షణాలు ఏమిటి?"
        await task_pipeline.queue_frames([TTSSpeakFrame(vayu_greeting, append_to_context=True)])

    asyncio.create_task(trigger_greeting())

    @active_transport.event_handler("on_participant_disconnected")
    async def on_participant_disconnected(transport, participant):
        logger.info(f"[{short_session_id}] 🔌 Call disconnected.")
        await task_pipeline.cancel()

    bot_runner = PipelineRunner(handle_sigint=False)
    await bot_runner.run(task_pipeline)
    await active_transport.close()

    # 1. IMMEDIATE HANGUP (Safe Cleanup Pattern)
    logger.info(f"🧹 Deleting LiveKit room '{room_name}' to force SIP hangup...")
    api_client = api.LiveKitAPI(livekit_url, api_key, api_secret)
    try:
        await api_client.room.delete_room(api.DeleteRoomRequest(room=room_name))
        logger.info(f"✅ Room deleted. Phone call successfully hung up.")
    except Exception as e:
        logger.warning(f"⚠️ Room cleanup ignored (likely already closed): {e}")
    finally:
        await api_client.aclose()

    # 2. BACKGROUND TASKS
    logger.info("⚙️ Running post-call background tasks (Billing & DB)...")
    bill_tracker.generate_receipt()
    final_call_duration = time.time() - bill_tracker.timer_start
    await save_call_log(session_call_uuid, inbound_caller_id, final_call_duration, sys_context.messages)

# ==========================================================
# 📞 LIVEKIT WEBHOOK ROUTE (UPDATED)
# ==========================================================
@router.post("/livekit-webhook")
async def livekit_webhook(request: Request):
    try:
        payload = await request.json()
        event_type = payload.get("event")
        room_name = payload.get("room", {}).get("name")

        # 🟢 1. Handle New Calls
        if event_type == "room_started":
            room_sid = payload.get("room", {}).get("sid", "livekit_call")
            logger.info(f"🟢 LiveKit Room Started: {room_name}. Spinning up AI Agent...")
            asyncio.create_task(run_bot(
                room_name=room_name,
                session_call_uuid=room_sid,
                inbound_caller_id="Exotel_SIP_Caller"
            ))

        # 🔴 2. The Zombie Killer: Force close room when human leaves
        elif event_type == "participant_left":
            participant_identity = payload.get("participant", {}).get("identity", "")
            if "mithra-ai" not in participant_identity:
                logger.info(f"🚨 Human disconnected! Force deleting zombie room: {room_name}")
                
                livekit_url = os.getenv("LIVEKIT_URL")
                api_key = os.getenv("LIVEKIT_API_KEY")
                api_secret = os.getenv("LIVEKIT_API_SECRET")
                
                api_client = api.LiveKitAPI(livekit_url, api_key, api_secret)
                try:
                    await api_client.room.delete_room(api.DeleteRoomRequest(room=room_name))
                    logger.info(f"✅ Zombie room {room_name} successfully deleted.")
                except Exception as e:
                    logger.warning(f"⚠️ Room cleanup ignored (likely already closed): {e}")
                finally:
                    await api_client.aclose()

        return {"status": "success"}
    except Exception as e:
        logger.error(f"❌ LiveKit Webhook Error: {e}")
        return {"status": "error"}

# ==========================================================
# 💳 RAZORPAY WEBHOOK
# ==========================================================
@router.post("/razorpay-webhook")
async def razorpay_webhook(request: Request):
    try:
        payload_data = await request.json()
        rzp_event = payload_data.get("event")
        if rzp_event in ["payment_link.paid", "payment_link.cancelled", "payment_link.expired"]:
            entity_data = payload_data.get("payload", {}).get("payment_link", {}).get("entity", {})
            target_appointment_id = entity_data.get("notes", {}).get("appointment_id")
            if target_appointment_id:
                from tools.pool import get_pool
                db_conn_pool = get_pool()
                pay_status = "paid" if rzp_event == "payment_link.paid" else "failed"
                async with db_conn_pool.acquire() as conn:
                    await conn.execute("UPDATE payments SET status = $1 WHERE appointment_id = $2", pay_status, target_appointment_id)
                if rzp_event == "payment_link.paid": await handle_successful_payment(target_appointment_id)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"❌ Razorpay Webhook Error: {e}")
        return {"status": "error"}
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
# from fastapi import APIRouter, Request, Form, Response
# from fastapi.responses import JSONResponse
# import base64
# import aiohttp
# import asyncio
# import google.generativeai as genai
# from livekit import api
# from pipecat.transports.livekit.transport import LiveKitTransport, LiveKitParams
# from pipecat.serializers.base_serializer import FrameSerializer
# from pipecat.frames.frames import Frame, AudioRawFrame, CancelFrame

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

# from pipecat.services.sarvam.stt import SarvamSTTService
# from pipecat.services.google.tts import GoogleTTSService
# from pipecat.services.google.llm import GoogleLLMService
# from pipecat.services.google.tts import GoogleTTSService, TTSSettings
# from tools.pipecat_tools import register_all_tools, get_tools_schema
# from tools.notify import handle_successful_payment
# from tools.pool import get_pool

# load_dotenv(override=True)

# genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
# summarizer_model = genai.GenerativeModel("gemini-2.5-flash")

# router = APIRouter()
# redis_conn_obj = None

# async def ensure_redis_client():
#     global redis_conn_obj
#     if redis_conn_obj:
#         return
#     try:
#         redis_endpoint = os.getenv("REDIS_URL", "redis://localhost:6379")
#         redis_conn_obj = redis.from_url(redis_endpoint, decode_responses=True)
#         await redis_conn_obj.ping()
#         logger.info("✅ Redis client connected successfully.")
#     except Exception as e:
#         logger.warning(f"⚠️ Redis connection failed: {e}")
#         redis_conn_obj = None

# # ==========================================================
# # 🧠 SYSTEM PROMPT
# # ==========================================================
# ist_zone = pytz.timezone('Asia/Kolkata')
# live_time_str = datetime.now(ist_zone).strftime('%A, %B %d, %Y at %I:%M %p IST')

# VOICE_SYSTEM_PROMPT = f"""Role: Your name is Vayu, the AI Receptionist for Mithra Hospitals.
# CURRENT LIVE TIME: {live_time_str}

# You transition strictly through phases. NEVER backtrack.

# --- 🌐 LANGUAGE & TRANSLATION RULES (CRITICAL) ---
# 1. STARTING LANGUAGE: You start the conversation in Telugu.
# 2. CASUAL MIXING: If the user casually uses English or Hindi words (e.g., "book cheyandi", "10 o clock", "haan"), DO NOT switch languages. Continue replying in your current language.
# 3. EXPLICIT LANGUAGE SWITCHING (CRITICAL): If the user EXPLICITLY commands you to change the language (e.g., "Can you talk in English?", "Speak in Hindi"):
#    - You MUST immediately switch your text output to the requested language.
#    - You MUST acknowledge the switch (e.g., "Sure, I can speak in English.")
#    - You MUST repeat the exact question you were just asking. DO NOT hallucinate symptoms or skip steps. DO NOT call `check_availability` unless the user actually stated their medical problem.
# 4. DATABASE TRANSLATION (CRITICAL): ALL data sent to your internal tools (like patient_name, reason) MUST be translated to plain ENGLISH.
# 5. TIME FORMATTING: Translate all digits into spelled-out phonetic words in your active language.
# 6. PHONE NUMBER SPELLING (CRITICAL): When repeating a phone number to confirm, spell out EACH digit individually phonetically.
# 7. RESPECTFUL VOCABULARY: Always use the English word "patient" (even when speaking Telugu or Hindi).
# 8. PACE & SPEED: Keep all your sentences extremely short, crisp, and punchy. Avoid long explanations.

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
# - TOKEN SYSTEM RULE: You MUST say EXACTLY: "We use a token system. Dr. Nikhil is available for these sessions. After the payment has been made, you will be allocated a token number that will give you entry. Please choose your preferred session."
# - Ask the user *which specific session* they prefer. DO NOT just ask a yes/no question. Once they choose a specific session, immediately move to PHASE 3.

# PHASE 3 (Details Request - ANTI-HALLUCINATION STRICT):
# - If the user agrees to a slot, ask: "Could you please tell me the patient's name and 10-digit phone number?"
# - ZERO-LEAKAGE RULE: You are STRICTLY FORBIDDEN from using any name, phone number, or data from previous calls or "default" values. 
# - If you do not have the name and phone number from the CURRENT conversation, you MUST NOT call `voice_book_appointment`. You must ask the user for this information again.

# PHASE 3.5 (Confirmation - CRITICAL):
# Once the user provides their actual name and phone number, DO NOT call the booking tool immediately.
# You MUST repeat the phone number back to them digit-by-digit to confirm. 
# Say: "Your number is [Digit Digit Digit...]. Is that correct?"

# PHASE 4 (The Silent Trigger):
# ONLY AFTER the user explicitly says "Yes", "Correct", "Avunu", etc., YOU MUST STOP SPEAKING.
# Immediately call `voice_book_appointment` using ONLY the exact name and phone number the user provided. 
# CRITICAL: Emit ZERO characters of text. DO NOT say "Okay" or repeat the name again.

# PHASE 5 (Confirmation & Persistence):
# ONLY AFTER the tool returns "success", inform the patient.
# - For a paid appointment, say EXACTLY the native translation of: "A tentative appointment has been booked. Please click the payment link on WhatsApp and do the payment under 15 minutes."
# - CRITICAL: After the confirmation, DO NOT end the call. Ask: "Is there anything else I can help you with today?"
# - CLOSING THE CALL: If the user says they are done, have no more questions, or say goodbye, you MUST first say a polite thank you and goodbye in your active language, and THEN call `end_call`.
# """

# # ==========================================================
# # 🛠️ PROCESSORS & SERIALIZERS
# # ==========================================================
# class STTTextCleanerProcessor(FrameProcessor):
#     def __init__(self, session_identifier):
#         super().__init__()
#         self.session_identifier = session_identifier

#     async def process_frame(self, frame: Frame, direction: FrameDirection):
#         await super().process_frame(frame, direction)
#         if isinstance(frame, TranscriptionFrame):
#             stt_raw_text = frame.text.strip().lower()
#             if len(stt_raw_text) <= 2:
#                 return
#             logger.info(f"[{self.session_identifier}] 🎤 USER SAID [Raw STT]: {stt_raw_text}")
#             lexicon_fixes = {
#                 "పార్లమెంట్": "అపాయింట్మెంట్",
#                 "apartment": "appointment",
#                 "అపార్ట్మెంట్": "అపాయింట్మెంట్",
#                 "department": "appointment",
#                 "తెలుగు": "telugu",
#                 "हिंदी": "hindi"
#             }
#             for wrong_val, right_val in lexicon_fixes.items():
#                 stt_raw_text = stt_raw_text.replace(wrong_val, right_val)
#             frame.text = stt_raw_text
#         await self.push_frame(frame, direction)

# class AutoLanguageProcessor(FrameProcessor):
#     def __init__(self, session_identifier):
#         super().__init__()
#         self.session_identifier = session_identifier
#         self.active_locale = "te-IN"

#     async def process_frame(self, frame: Frame, direction: FrameDirection):
#         await super().process_frame(frame, direction)

#         if isinstance(frame, TextFrame):
#             ai_spoken_text = frame.text.lower().strip()

#             # Detect language
#             has_telugu_chars = bool(re.search(r'[\u0c00-\u0c7f]', ai_spoken_text))
#             has_hindi_chars = bool(re.search(r'[\u0900-\u097f]', ai_spoken_text))

#             # Default English
#             target_locale = "en-US"

#             if has_telugu_chars:
#                 target_locale = "te-IN"
#             elif has_hindi_chars:
#                 target_locale = "hi-IN"

#             # ✅ FORCE FEMALE VOICES
#             female_voices = {
#                 "te-IN": "te-IN-Chirp3-HD-Despina",
#                 "hi-IN": "hi-IN-Chirp3-HD-Despina",
#                 "en-US": "en-US-Chirp3-HD-Despina"
#             }

#             target_voice_id = female_voices.get(
#                 target_locale,
#                 "en-US-Chirp3-HD-Despina"
#             )

#             # Switch voice only when locale changes
#             if target_locale != self.active_locale:
#                 logger.info(
#                     f"[{self.session_identifier}] 🌐 Switching to: "
#                     f"{target_locale} | Voice: {target_voice_id}"
#                 )

#                 self.active_locale = target_locale

#                 # ✅ FIXED SETTINGS UPDATE
#                 await self.push_frame(
#                     TTSUpdateSettingsFrame(
#                         delta=TTSSettings(
#                             language=target_locale,
#                             voice=target_voice_id,
#                             speaking_rate=2 if target_locale == "te-IN" else 1.0
#                         )
#                     ),
#                     direction
#                 )

#         if isinstance(frame, FunctionCallInProgressFrame):
#             time_filler_text = ""
#             if frame.function_name == "voice_book_appointment":
#                 time_filler_text = "ఒక్క నిమిషం" if self.active_locale == "te-IN" else "एक मिनट" if self.active_locale == "hi-IN" else "One moment"
#             elif frame.function_name in ["check_availability", "query_clinic_faq"]:
#                 time_filler_text = "చూస్తున్నాను" if self.active_locale == "te-IN" else "चेक कर रही हूँ" if self.active_locale == "hi-IN" else "Checking"
            
#             if time_filler_text:
#                 logger.info(f"[{self.session_identifier}] ⏳ Filler: {time_filler_text}")
#                 await self.push_frame(TTSSpeakFrame(text=time_filler_text), direction)

#         await self.push_frame(frame, direction)

# class BillingTracker(FrameProcessor):
#     def __init__(self, bot_context, session_identifier):
#         super().__init__()
#         self.tts_char_count = 0
#         self.llm_out_tokens = 0
#         self.timer_start = time.time()
#         self.bot_context = bot_context
#         self.session_identifier = session_identifier

#     async def process_frame(self, frame: Frame, direction: FrameDirection):
#         await super().process_frame(frame, direction)
#         if isinstance(frame, TextFrame) and direction == FrameDirection.DOWNSTREAM:
#             self.tts_char_count += len(frame.text)
#             self.llm_out_tokens += len(frame.text) / 4.0
#         await self.push_frame(frame, direction)

#     def generate_receipt(self):
#         run_duration_sec = time.time() - self.timer_start
#         run_duration_min = run_duration_sec / 60.0
#         dialogue_log_text = json.dumps(self.bot_context.messages)
#         total_input_tokens = len(dialogue_log_text) / 4.0
        
#         stt_usd_val = run_duration_min * 0.006
#         tts_usd_val = self.tts_char_count * 0.00003
#         llm_in_usd_val = total_input_tokens * 0.0000003
#         llm_out_usd_val = self.llm_out_tokens * 0.0000025
#         grand_total_usd = stt_usd_val + tts_usd_val + llm_in_usd_val + llm_out_usd_val
        
#         rate_usd_per_min = grand_total_usd / run_duration_min if run_duration_min > 0 else 0
#         inr_multiplier = 93.29
#         grand_total_inr = grand_total_usd * inr_multiplier
#         inr_rate_per_min = rate_usd_per_min * inr_multiplier

#         logger.info("\n" + "=" * 55)
#         logger.info(f"[{self.session_identifier}] 💰 SESSION BILLING RECEIPT 💰")
#         logger.info("=" * 55)
#         logger.info(f"⏱️  Duration:     {run_duration_min:.2f} mins ({run_duration_sec:.0f}s)")
#         logger.info(f"🎙️  STT Cost:     ₹{stt_usd_val * inr_multiplier:.4f} (${stt_usd_val:.4f})")
#         logger.info(f"🧠 LLM In:       ₹{llm_in_usd_val * inr_multiplier:.4f} (~{total_input_tokens:.0f} tokens)")
#         logger.info(f"🧠 LLM Out:      ₹{llm_out_usd_val * inr_multiplier:.4f} (~{self.llm_out_tokens:.0f} tokens)")
#         logger.info(f"🗣️  TTS Cost:     ₹{tts_usd_val * inr_multiplier:.4f} ({self.tts_char_count} chars)")
#         logger.info("-" * 55)
#         logger.info(f"💵 TOTAL:        ₹{grand_total_inr:.4f} (${grand_total_usd:.4f})")
#         logger.info(f"📊 PER MIN:      ₹{inr_rate_per_min:.4f} (${rate_usd_per_min:.4f})")
#         logger.info("=" * 55 + "\n")

# class PipecatBugFixProcessor(FrameProcessor):
#     async def process_frame(self, frame: Frame, direction: FrameDirection):
#         await super().process_frame(frame, direction)
#         if isinstance(frame, AudioRawFrame):
#             if not hasattr(frame, 'pts'): frame.pts = None
#             if not hasattr(frame, 'transport_destination'): frame.transport_destination = None
#             if not hasattr(frame, 'id'): frame.id = "fixed-audio-frame-id"
#             if not hasattr(frame, 'broadcast_sibling_id'): frame.broadcast_sibling_id = None
#         await self.push_frame(frame, direction)

# # ==========================================================
# # 💾 DB SAVING HELPER
# # ==========================================================
# async def save_call_log(session_call_uuid: str, inbound_caller_id: str, call_runtime_sec: float, msg_history: list):
#     logger.info(f"💾 Starting DB save for call {session_call_uuid}...")
#     try:
#         chat_lines = []
#         for hist_item in msg_history:
#             speaker_role = "AI" if hist_item.get('role') == 'model' else "Patient"
#             content_str = ""
#             if 'content' in hist_item and isinstance(hist_item['content'], str):
#                 content_str = hist_item['content']
#             elif 'parts' in hist_item:
#                 for sub_part in hist_item['parts']:
#                     if isinstance(sub_part, dict) and 'text' in sub_part:
#                         content_str += sub_part['text']
#                     elif isinstance(sub_part, str):
#                         content_str += sub_part
#             if content_str.strip():
#                 chat_lines.append(f"{speaker_role}: {content_str.strip()}")
        
#         full_call_transcript = "\n".join(chat_lines)
        
#         if not chat_lines:
#             llm_generated_synopsis = "Call connected but no speech detected."
#         else:
#             summary_prompt = f"""
#             Analyze the following medical receptionist transcript and provide a summary strictly following this template:
#             "Patient {{Name}} booked an appointment on {{Date}} at {{Time}} with {{Doctor Name}} ({{Specialization}}) due to {{Reason}}"

#             Rules:
#             - If no appointment was booked, write: "Patient called to inquire but no appointment was booked."
#             - Translate any Telugu details into English for the summary.
#             - Keep it to one single sentence.

#             Transcript:
#             {full_call_transcript}
#             """
#             response = await asyncio.to_thread(summarizer_model.generate_content, summary_prompt)
#             llm_generated_synopsis = response.text.strip() if response else "No summary generated."

#         db_conn_pool = get_pool()
#         if not db_conn_pool: return
#         async with db_conn_pool.acquire() as conn:
#             target_clinic_id = await conn.fetchval("SELECT id FROM clinics LIMIT 1")
#             if not target_clinic_id: return
#             await conn.execute("""
#                 INSERT INTO calls (clinic_id, type, caller, agent_type, duration, ai_summary)
#                 VALUES ($1, 'incoming', $2, 'ai', $3, $4)
#             """, target_clinic_id, inbound_caller_id, int(call_runtime_sec), llm_generated_synopsis)
#         logger.info(f"✅ Call Log Saved | Summary: {llm_generated_synopsis}")
#     except Exception as e:
#         logger.error(f"❌ Failed to save call log: {e}", exc_info=True)

# # ==========================================================
# # 🎙️ PIPECAT RUNNER (LIVEKIT SIP)
# # ==========================================================
# async def run_bot(room_name: str, session_call_uuid: str = "livekit_call", inbound_caller_id: str = "Unknown"):
#     await ensure_redis_client()
#     short_session_id = session_call_uuid[:8]
#     gcp_credentials_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

#     # --- 1. Generate LiveKit Access Token ---
#     livekit_url = os.getenv("LIVEKIT_URL")
#     api_key = os.getenv("LIVEKIT_API_KEY")
#     api_secret = os.getenv("LIVEKIT_API_SECRET")

#     token = api.AccessToken(api_key, api_secret) \
#         .with_identity(f"mithra-ai-{short_session_id}") \
#         .with_name("Mithra AI") \
#         .with_grants(api.VideoGrants(room_join=True, room=room_name)) \
#         .to_jwt()

#     # --- 2. Initialize LiveKit Transport ---
#     active_transport = LiveKitTransport(
#         room_name=room_name,
#         url=livekit_url,
#         token=token,
#         params=LiveKitParams(
#             audio_in_enabled=True, 
#             audio_out_enabled=True,
#             vad_enabled=True
#         )
#     )

#     # --- 3. Initialize Services ---
#     stt_service = SarvamSTTService(api_key=os.getenv("SARVAM_API_KEY"), language="unknown", model="saaras:v3", mode="transcribe")
#     tts_service = GoogleTTSService(
#         credentials_path=gcp_credentials_file,
#         voice="te-IN-Chirp3-HD-Despina",
#         language="te-IN"
#     )

#     # ✅ FORCE SETTINGS IMMEDIATELY AFTER INIT
#     await tts_service._update_settings(
#         TTSSettings(
#             voice="te-IN-Chirp3-HD-Despina",
#             language="te-IN"
#         )
#     )
#     llm_service = GoogleLLMService(api_key=os.getenv("GEMINI_API_KEY"), model="gemini-2.5-flash")

#     register_all_tools(llm_service)

#     sys_context = LLMContext(messages=[{"role": "system", "content": VOICE_SYSTEM_PROMPT}], tools=get_tools_schema())
#     context_aggregator = LLMContextAggregatorPair(sys_context)
#     bill_tracker = BillingTracker(sys_context, short_session_id)

#     # --- 4. Pipeline Setup ---
#     pipeline = Pipeline([
#         active_transport.input(),
#         stt_service,
#         STTTextCleanerProcessor(short_session_id),
#         context_aggregator.user(),
#         llm_service,
#         bill_tracker,
#         AutoLanguageProcessor(short_session_id),
#         tts_service,
#         PipecatBugFixProcessor(),
#         active_transport.output(),
#         context_aggregator.assistant()
#     ])

#     task_pipeline = PipelineTask(pipeline, params=PipelineParams(audio_in_sample_rate=8000, audio_out_sample_rate=8000))

#     async def trigger_greeting():
#         # Wait 1.5 seconds to ensure the SIP audio channel is fully negotiated before speaking
#         await asyncio.sleep(1.5)
#         logger.info(f"[{short_session_id}] 🗣️ Triggering initial Vayu greeting...")
#         vayu_greeting = "నమస్కారం! నేను వాయు, మిత్ర హాస్పిటల్స్ నుండి మాట్లాడుతున్నాను. నేను మీకు ఎలా సహాయపడగలను? మీ ఆరోగ్య సమస్య లేదా లక్షణాలు ఏమిటి?"
#         await task_pipeline.queue_frames([TTSSpeakFrame(vayu_greeting, append_to_context=True)])

#     # Trigger the greeting timer the moment the bot boots up
#     asyncio.create_task(trigger_greeting())
    
#     @active_transport.event_handler("on_participant_disconnected")
#     async def on_participant_disconnected(transport, participant):
#         logger.info(f"[{short_session_id}] 🔌 Call disconnected.")
#         await task_pipeline.cancel()

#     bot_runner = PipelineRunner(handle_sigint=False)
#     await bot_runner.run(task_pipeline) # This finishes the moment the bot says goodbye

#     # 1. IMMEDIATE HANGUP
#     logger.info(f"🧹 Deleting LiveKit room '{room_name}' to force SIP hangup...")
#     try:
#         livekit_url = os.getenv("LIVEKIT_URL")
#         api_key = os.getenv("LIVEKIT_API_KEY")
#         api_secret = os.getenv("LIVEKIT_API_SECRET")
        
#         lkapi = api.LiveKitAPI(livekit_url, api_key, api_secret)
#         await lkapi.room.delete_room(api.DeleteRoomRequest(room=room_name))
#         await lkapi.aclose()
#         logger.info(f"✅ Room deleted. Phone call successfully hung up.")
#     except Exception as e:
#         logger.error(f"❌ Failed to delete room and hang up call: {e}")

#     # 2. BACKGROUND TASKS
#     logger.info("⚙️ Running post-call background tasks (Billing & DB)...")
#     bill_tracker.generate_receipt()
#     final_call_duration = time.time() - bill_tracker.timer_start
#     await save_call_log(session_call_uuid, inbound_caller_id, final_call_duration, sys_context.messages)

# # ==========================================================
# # 📞 LIVEKIT WEBHOOK ROUTE
# # ==========================================================
# @router.post("/livekit-webhook")
# async def livekit_webhook(request: Request):
#     try:
#         payload = await request.json()
#         event_type = payload.get("event")

#         # When a new SIP call creates a room, spin up an AI agent to join it
#         if event_type == "room_started":
#             room_name = payload.get("room", {}).get("name")
#             room_sid = payload.get("room", {}).get("sid", "livekit_call")
            
#             logger.info(f"🟢 LiveKit Room Started: {room_name}. Spinning up AI Agent...")
            
#             # Start the bot as a background task so the webhook returns 200 OK immediately
#             asyncio.create_task(run_bot(
#                 room_name=room_name, 
#                 session_call_uuid=room_sid, 
#                 inbound_caller_id="Exotel_SIP_Caller"
#             ))

#         return {"status": "success"}
#     except Exception as e:
#         logger.error(f"❌ LiveKit Webhook Error: {e}")
#         return {"status": "error"}

# # ==========================================================
# # 💳 RAZORPAY WEBHOOK
# # ==========================================================
# @router.post("/razorpay-webhook")
# async def razorpay_webhook(request: Request):
#     try:
#         payload_data = await request.json()
#         rzp_event = payload_data.get("event")
#         if rzp_event in ["payment_link.paid", "payment_link.cancelled", "payment_link.expired"]:
#             entity_data = payload_data.get("payload", {}).get("payment_link", {}).get("entity", {})
#             target_appointment_id = entity_data.get("notes", {}).get("appointment_id")
#             if target_appointment_id:
#                 from tools.pool import get_pool
#                 db_conn_pool = get_pool()
#                 pay_status = "paid" if rzp_event == "payment_link.paid" else "failed"
#                 async with db_conn_pool.acquire() as conn:
#                     await conn.execute("UPDATE payments SET status = $1 WHERE appointment_id = $2", pay_status, target_appointment_id)
#                 if rzp_event == "payment_link.paid": await handle_successful_payment(target_appointment_id)
#         return {"status": "ok"}
#     except Exception as e:
#         logger.error(f"❌ Razorpay Webhook Error: {e}")
#         return {"status": "error"}