#with sarvam tts language auto-detection and noise filtering for better accuracy and less hallucinations. Also added razorpay webhook handler for payment confirmation.
#call_agent.py
# import os
# import json
# import uuid
# import re  # <-- NEW: Added for language detection
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

# from pipecat.frames.frames import Frame, TextFrame, TranscriptionFrame, TTSSpeakFrame, TTSUpdateSettingsFrame
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
# # 🛠️ PROCESSORS (Cleaned up Noise Filter & Language Auto-Detect)
# # ==========================================================
# class STTTextCleanerProcessor(FrameProcessor):
#     async def process_frame(self, frame: Frame, direction: FrameDirection):
#         await super().process_frame(frame, direction)
#         if isinstance(frame, TranscriptionFrame):
#             text = frame.text.strip().lower()
            
#             # 🔥 Ignore tiny background noises to stop hallucinations
#             if len(text) <= 2:
#                 return  # Drop frame entirely

#             logger.info(f"🎤 USER SAID [Raw STT]: {text}")

#             corrections = {
#                 "పార్లమెంట్": "అపాయింట్మెంట్", "apartment": "appointment",
#                 "అపార్ట్మెంట్": "అపాయింట్మెంట్", "department": "appointment",
#                 "తెలుగు": "telugu", "हिंदी": "hindi"
#             }
#             for k, v in corrections.items():
#                 text = text.replace(k, v)
#             frame.text = text
            
#         await self.push_frame(frame, direction)

# # 🔥 NEW: Auto-detects language from LLM text and safely configures Sarvam TTS
# class AutoLanguageProcessor(FrameProcessor):
#     def __init__(self):
#         super().__init__()
#         self.current_language = "en-IN" # Default starting language

#     async def process_frame(self, frame: Frame, direction: FrameDirection):
#         # 🐛 THE FIX: This MUST be called first so Pipecat registers the call StartFrame!
#         await super().process_frame(frame, direction)
        
#         if isinstance(frame, TextFrame):
#             text = frame.text
#             new_lang = self.current_language
            
#             # Detect language based on script
#             if re.search(r'[\u0c00-\u0c7f]', text):
#                 new_lang = "te-IN"
#             elif re.search(r'[\u0900-\u097f]', text):
#                 new_lang = "hi-IN"
#             elif re.search(r'[a-zA-Z]', text):
#                 new_lang = "en-IN"
                
#             # If the language changed, tell Sarvam BEFORE sending the text
#             if new_lang != self.current_language:
#                 logger.info(f"🌐 Auto-switching TTS language to: {new_lang}")
#                 self.current_language = new_lang
#                 await self.push_frame(
#                     TTSUpdateSettingsFrame(settings={"language": new_lang, "voice": "anushka"}), 
#                     direction
#                 )
                
#         # Push the actual text/audio frame downstream!
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
# # 🎙️ PIPECAT RUNNER (Fully Isolated Per Call)
# # ==========================================================
# async def run_bot(transport: BaseTransport, call_sid: str = "local_test", is_twilio: bool = False):
#     await ensure_redis_client()

#     # ✅ 1. DYNAMIC TIME: Generate fresh time for every single call
#     ist = pytz.timezone('Asia/Kolkata')
#     current_time = datetime.now(ist).strftime('%A, %B %d, %Y at %I:%M %p IST')

#     # ✅ 2. FIXED PROMPT: Removed the hallucinatory instruction about bracketed tags
#     voice_system_prompt = f"""Role: Mithra Hospital AI Receptionist.
# CURRENT LIVE TIME: {current_time}

# You transition strictly through phases. NEVER backtrack.

# --- 🌐 LANGUAGE & TRANSLATION RULES (CRITICAL) ---
# 1. MIRROR THE USER: Automatically identify the language the user is speaking and reply in that EXACT same language. Do NOT narrate your actions. Just answer naturally.
# 2. PURITY OF LANGUAGE (NO MIXING): If you are speaking Telugu, use ONLY Telugu words (e.g., "తొమ్మిది" for 9). NEVER mix Hindi words (like "nau") into a Telugu sentence, and vice versa.
# 3. IGNORE TRANSLITERATION: The STT engine may write English words in Telugu/Hindi script (e.g. 'ఫీవర్ అండ్ కాఫ్' = 'fever and cough'). Treat it as the locked language, not a switch request.
# 4. NO BOUNCING & CONFUSION RECOVERY: Once locked into a language, DO NOT switch back and forth. If you don't understand the user's input, ask them to repeat it IN THE LOCKED LANGUAGE (e.g., "క్షమించండి, నాకు అర్థం కాలేదు" for Telugu). NEVER revert to English to ask for clarification.
# 5. TRANSLATE SYSTEM DIRECTIVES: If a tool returns a "SYSTEM DIRECTIVE" asking you to prompt the user (e.g., asking if they are a new family member), you MUST translate that question into the currently locked language before speaking. NEVER read it in English if the conversation is in Telugu/Hindi.
# 6. DATABASE TRANSLATION (STRICT): No matter what language the user is speaking, ALL data you send to your tools (patient_name, reason, problem_or_speciality) MUST be translated to plain ENGLISH before calling the tool.
# 7. TIME FORMATTING: 
#    - In English: Use standard formats (e.g., 9:00 AM).
#    - In Hindi/Telugu: Translate all digits/times into spelled-out phonetic words. For Telugu, say "ఉదయం తొమ్మిది గంటలకు". NEVER output raw digits like "09:00" in regional languages.

# --- INTENT ROUTING ---
# 1. CANCEL/RESCHEDULE: Say: "Based on hospital policy, appointments cannot be cancelled or rescheduled through the AI assistant. Please call the clinic directly." (End flow).
# 2. FOLLOW-UP BOOKING: If the user asks for a "follow-up", ask EXACTLY: "Could you please tell me your 10-digit phone number so I can check your records?" Once provided, SILENTLY call `verify_followup`.

# --- CORE BOOKING STATES ---

# PHASE 0 (Symptoms Gathering - MANDATORY ONLY IF MISSING):
# Check if the user has ALREADY provided symptoms (e.g., "I have a fever", "జలుబుగా ఉంది").
# - If SYMPTOMS ALREADY GIVEN: SKIP THIS PHASE completely and go directly to PHASE 1.
# - If NO symptoms given: ask "What medical problem or symptoms are you experiencing?" 
# CRITICAL: DO NOT call `check_availability` until symptoms are explicitly stated. Do NOT assume 'General Physician' based on background noise.

# PHASE 1 (Availability):
# ONLY AFTER the user gives symptoms, SILENTLY call `check_availability`. Emit ZERO text.

# PHASE 2 (Offer & Negotiation):
# - Initial Offer: Read the `system_directive` exactly as intended. (ONLY translate if the user is currently speaking Hindi/Telugu, and spell out times).
# - Negotiation: Look at the `all_available_slots` to find alternative times if asked. DO NOT repeat the initial offer if they just ask a question.

# PHASE 3 (Details Request):
# If the user agrees to a slot, ask: "Could you please tell me the patient's name and 10-digit phone number?" 
# (Note: If they already provided their phone number for a follow-up, just ask for their name).
# If the user asks a completely unrelated question or chit-chats, answer them naturally, but gently guide them back to providing their name and phone number to secure the booking.

# PHASE 4 (The Silent Trigger):
# If the user provides a name and a 10-digit number, YOU MUST STOP SPEAKING.
# Immediately call `voice_book_appointment` (Ensure you translate the name and reason to English first!).
# CRITICAL: Emit ZERO characters of text. DO NOT say "Okay" or repeat the name.

# PHASE 5 (Confirmation):
# ONLY AFTER the tool returns "success", say exactly the confirmation message based on the tool result:
# - Paid Appointment: "A tentative appointment is booked and a payment link is sent to your WhatsApp. Please do the payment in 15 minutes to confirm the booking. Thank you."
# - Free Follow-up: "Your free follow-up appointment is confirmed. A WhatsApp message has been sent to you. Thank you."
# (CRITICAL: ONLY translate this if the user is speaking Hindi/Telugu).
# CRITICAL RULE: DO NOT append any questions like "Shall I book this?" at the end. Just say the confirmation and STOP.
# Immediately after saying this, call the `end_call` tool.
# """

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
#         messages=[{"role": "system", "content": voice_system_prompt}],
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
#         AutoLanguageProcessor(), # 👈 NEW: Auto-language detection!
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



# #call_agent.py
# import os
# import json
# import uuid
# import re 
# import time  # <-- Billing duration
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

# from pipecat.frames.frames import Frame, TextFrame, TranscriptionFrame, TTSSpeakFrame, TTSUpdateSettingsFrame, FunctionCallInProgressFrame
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
# from pipecat.services.google.tts import GoogleTTSService
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
# # 🧠 SYSTEM PROMPT (Untouched, exactly as previous working version)
# # ==========================================================
# ist = pytz.timezone('Asia/Kolkata')
# current_time = datetime.now(ist).strftime('%A, %B %d, %Y at %I:%M %p IST')

# VOICE_SYSTEM_PROMPT = f"""Role: Mithra Hospital AI Receptionist.
# CURRENT LIVE TIME: {current_time}

# You transition strictly through phases. NEVER backtrack.

# --- 🌐 LANGUAGE & TRANSLATION RULES (CRITICAL) ---
# 1. MIRROR THE USER: Automatically identify the language the user is speaking and reply in that EXACT same language. Do NOT narrate your actions. Just answer naturally.
# 2. PURITY OF LANGUAGE (NO MIXING): If you are speaking Telugu, use ONLY Telugu words (e.g., "తొమ్మిది" for 9). NEVER mix Hindi words (like "nau") into a Telugu sentence, and vice versa.
# 3. IGNORE TRANSLITERATION: The STT engine may write English words in Telugu/Hindi script (e.g. 'ఫీవర్ అండ్ కాఫ్' = 'fever and cough'). Treat it as the locked language, not a switch request.
# 4. NO BOUNCING & CONFUSION RECOVERY: Once locked into a language, DO NOT switch back and forth. If you don't understand the user's input, ask them to repeat it IN THE LOCKED LANGUAGE (e.g., "క్షమించండి, నాకు అర్థం కాలేదు" for Telugu). NEVER revert to English to ask for clarification.
# 5. TRANSLATE SYSTEM DIRECTIVES: If a tool returns a "SYSTEM DIRECTIVE" asking you to prompt the user (e.g., asking if they are a new family member), you MUST translate that question into the currently locked language before speaking. NEVER read it in English if the conversation is in Telugu/Hindi.
# 6. DATABASE TRANSLATION (STRICT): No matter what language the user is speaking, ALL data you send to your tools (patient_name, reason, problem_or_speciality) MUST be translated to plain ENGLISH before calling the tool.
# 7. TIME FORMATTING: 
#    - In English: Use standard formats (e.g., 9:00 AM).
#    - In Hindi/Telugu: Translate all digits/times into spelled-out phonetic words. For Telugu, say "ఉదయం తొమ్మిది గంటలకు". NEVER output raw digits like "09:00" in regional languages.
# 8. RESPECTFUL VOCABULARY: NEVER use the Telugu word "రోగి" (rogi) to refer to a patient. It sounds clinical and disrespectful. Always use the English word "patient" (e.g., "దయచేసి పేషెంట్ పేరు చెప్పగలరా?").
# 9. PACE & SPEED: Keep all your sentences extremely short, crisp, and punchy. Avoid long explanations.

# --- INTENT ROUTING ---
# 1. CANCEL/RESCHEDULE: Say: "Based on hospital policy, appointments cannot be cancelled or rescheduled through the AI assistant. Please call the clinic directly." (End flow).
# 2. FOLLOW-UP BOOKING: If the user asks for a "follow-up", ask EXACTLY: "Could you please tell me your 10-digit phone number so I can check your records?" Once provided, SILENTLY call `verify_followup`.

# --- CORE BOOKING STATES ---

# PHASE 0 (Symptoms Gathering - MANDATORY ONLY IF MISSING):
# Check if the user has ALREADY provided symptoms (e.g., "I have a fever", "జలుబుగా ఉంది").
# - If SYMPTOMS ALREADY GIVEN: SKIP THIS PHASE completely and go directly to PHASE 1.
# - If NO symptoms given: ask "What medical problem or symptoms are you experiencing?" 
# CRITICAL: DO NOT call `check_availability` until symptoms are explicitly stated. Do NOT assume 'General Physician' based on background noise.

# PHASE 1 (Availability):
# ONLY AFTER the user gives symptoms, SILENTLY call `check_availability`. Emit ZERO text.

# PHASE 2 (Offer & Negotiation):
# - Initial Offer: Read the `system_directive` exactly as intended. (ONLY translate if the user is currently speaking Hindi/Telugu, and spell out times).
# - Negotiation: Look at the `all_available_slots` to find alternative times if asked. DO NOT repeat the initial offer if they just ask a question.

# PHASE 3 (Details Request):
# If the user agrees to a slot, ask: "Could you please tell me the patient's name and 10-digit phone number?" 
# (Note: If they already provided their phone number for a follow-up, just ask for their name).
# If the user asks a completely unrelated question or chit-chats, answer them naturally, but gently guide them back to providing their name and phone number to secure the booking.

# PHASE 4 (The Silent Trigger):
# If the user provides a name and a 10-digit number, YOU MUST STOP SPEAKING.
# Immediately call `voice_book_appointment` (Ensure you translate the name and reason to English first!).
# CRITICAL: Emit ZERO characters of text. DO NOT say "Okay" or repeat the name.

# PHASE 5 (Confirmation):
# ONLY AFTER the tool returns "success", inform the patient.
# CRITICAL RULES FOR CONFIRMATION:
# - Speak ONLY in the locked language. NEVER speak English first and then translate. 
# - For a paid appointment, say EXACTLY the native translation of this phrase: "A tentative appointment has been booked. Please click the payment link on WhatsApp and do the payment under 15 minutes. Thank you."
# - For a free follow-up, say EXACTLY the native translation of: "Your free follow-up appointment is confirmed. A WhatsApp message has been sent to you. Thank you."
# - DO NOT append any questions like "Shall I book this?"
# - Immediately after saying this, call the `end_call` tool.
# """

# # ==========================================================
# # 🛠️ PROCESSORS (Shortened Filler Words for Speed)
# # ==========================================================
# class STTTextCleanerProcessor(FrameProcessor):
#     async def process_frame(self, frame: Frame, direction: FrameDirection):
#         await super().process_frame(frame, direction)
#         if isinstance(frame, TranscriptionFrame):
#             text = frame.text.strip().lower()
            
#             if len(text) <= 2:
#                 return

#             logger.info(f"🎤 USER SAID [Raw STT]: {text}")

#             corrections = {
#                 "పార్లమెంట్": "అపాయింట్మెంట్", "apartment": "appointment",
#                 "అపార్ట్మెంట్": "అపాయింట్మెంట్", "department": "appointment",
#                 "తెలుగు": "telugu", "हिंदी": "hindi"
#             }
#             for k, v in corrections.items():
#                 text = text.replace(k, v)
#             frame.text = text
            
#         await self.push_frame(frame, direction)

# class AutoLanguageProcessor(FrameProcessor):
#     def __init__(self):
#         super().__init__()
#         self.current_language = "en-US"

#     async def process_frame(self, frame: Frame, direction: FrameDirection):
#         await super().process_frame(frame, direction)
        
#         if isinstance(frame, TextFrame):
#             text = frame.text
#             new_lang = self.current_language
#             new_voice = "en-US-Chirp3-HD-Despina"
            
#             if re.search(r'[\u0c00-\u0c7f]', text):
#                 new_lang = "te-IN"
#                 new_voice = "te-IN-Chirp3-HD-Despina"
#             elif re.search(r'[\u0900-\u097f]', text):
#                 new_lang = "hi-IN"
#                 new_voice = "hi-IN-Chirp3-HD-Despina"
#             elif re.search(r'[a-zA-Z]', text):
#                 new_lang = "en-US"
#                 new_voice = "en-US-Chirp3-HD-Despina"
                
#             if new_lang != self.current_language:
#                 logger.info(f"🌐 Auto-switching TTS language to: {new_lang} | Voice: {new_voice}")
#                 self.current_language = new_lang
#                 await self.push_frame(
#                     TTSUpdateSettingsFrame(settings={"language": new_lang, "voice": new_voice, "speaking_rate": 1.15}), 
#                     direction
#                 )
                
#         # 👇 SHORTENED: Very quick filler words so they don't block the queue
#         if isinstance(frame, FunctionCallInProgressFrame):
#             filler = ""
#             if frame.function_name == "voice_book_appointment":
#                 if self.current_language == "te-IN": filler = "ఒక్క నిమిషం..."
#                 elif self.current_language == "hi-IN": filler = "एक मिनट..."
#                 else: filler = "One moment..."
#             elif frame.function_name == "check_availability":
#                 if self.current_language == "te-IN": filler = "చూస్తున్నాను..."
#                 elif self.current_language == "hi-IN": filler = "चेक कर रही हूँ..."
#                 else: filler = "Checking..."
            
#             if filler:
#                 logger.info(f"⏳ Playing short filler audio: {filler}")
#                 await self.push_frame(TTSSpeakFrame(text=filler), direction)
                
#         await self.push_frame(frame, direction)

# class BillingTracker(FrameProcessor):
#     def __init__(self, context):
#         super().__init__()
#         self.tts_chars = 0
#         self.llm_output_tokens_est = 0
#         self.start_time = time.time()
#         self.context = context 

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
        
#         logger.info("\n" + "="*55)
#         logger.info("💰 SESSION BILLING RECEIPT (INR) 💰")
#         logger.info("="*55)
#         logger.info(f"⏱️ Duration:        {duration_minutes:.2f} mins ({duration_seconds:.0f}s)")
#         logger.info(f"🎙️ STT Cost:        ₹{stt_cost_usd * exchange_rate:.4f} (${stt_cost_usd:.4f})")
#         logger.info(f"🧠 LLM In Cost:     ₹{llm_input_cost_usd * exchange_rate:.4f} (${llm_input_cost_usd:.4f}) | ~{total_input_tokens:.0f} tokens")
#         logger.info(f"🧠 LLM Out Cost:    ₹{llm_output_cost_usd * exchange_rate:.4f} (${llm_output_cost_usd:.4f}) | ~{self.llm_output_tokens_est:.0f} tokens")
#         logger.info(f"🗣️ TTS Cost:        ₹{tts_cost_usd * exchange_rate:.4f} (${tts_cost_usd:.4f}) | {self.tts_chars} chars")
#         logger.info("-" * 55)
#         logger.info(f"💵 TOTAL COST:      ₹{total_cost_inr:.4f} (${total_cost_usd:.4f})")
#         logger.info(f"📊 COST PER MIN:    ₹{cost_per_minute_inr:.4f} (${cost_per_minute_usd:.4f})")
#         logger.info("="*55 + "\n")

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
    
#     google_creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
#     if not google_creds_path or not os.path.exists(google_creds_path):
#         logger.error(f"🚨 Missing Google Credentials File! Path checked: {google_creds_path}")

#     tts = GoogleTTSService(
#         credentials_path=google_creds_path,
#         voice="en-US-Chirp3-HD-Despina",
#         language="en-US"
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
#     billing_tracker = BillingTracker(context)

#     pipeline = Pipeline([
#         transport.input(),
#         stt,
#         STTTextCleanerProcessor(),
#         context_aggregator.user(),
#         llm,
#         billing_tracker,
#         AutoLanguageProcessor(), 
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
    
#     billing_tracker.generate_receipt()

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
import re 
import time  # <-- Billing duration
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

from pipecat.frames.frames import Frame, TextFrame, TranscriptionFrame, TTSSpeakFrame, TTSUpdateSettingsFrame, FunctionCallInProgressFrame
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
from pipecat.services.google.tts import GoogleTTSService
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
# 🧠 SYSTEM PROMPT (Original prompt + Negotiation & Lang Lock fixes)
# ==========================================================
ist = pytz.timezone('Asia/Kolkata')
current_time = datetime.now(ist).strftime('%A, %B %d, %Y at %I:%M %p IST')

VOICE_SYSTEM_PROMPT = f"""Role: Mithra Hospital AI Receptionist.
CURRENT LIVE TIME: {current_time}

You transition strictly through phases. NEVER backtrack.

--- 🌐 LANGUAGE & TRANSLATION RULES (CRITICAL) ---
1. MIRROR THE USER: Automatically identify the language the user is speaking and reply in that EXACT same language. Do NOT narrate your actions. Just answer naturally.
2. PURITY OF LANGUAGE (NO MIXING): If you are speaking Telugu, use ONLY Telugu words (e.g., "తొమ్మిది" for 9). NEVER mix Hindi words (like "nau") into a Telugu sentence, and vice versa.
3. IGNORE TRANSLITERATION: The STT engine may write English words in Telugu/Hindi script (e.g. 'ఫీవర్ అండ్ కాఫ్' = 'fever and cough'). Treat it as the locked language, not a switch request.
4. NO BOUNCING & CONFUSION RECOVERY: Once locked into a language, DO NOT switch back and forth. If you don't understand the user's input, ask them to repeat it IN THE LOCKED LANGUAGE (e.g., "క్షమించండి, నాకు అర్థం కాలేదు" for Telugu). NEVER revert to English to ask for clarification.
5. TRANSLATE SYSTEM DIRECTIVES: If a tool returns a "SYSTEM DIRECTIVE" asking you to prompt the user (e.g., asking if they are a new family member), you MUST translate that question into the currently locked language before speaking. NEVER read it in English if the conversation is in Telugu/Hindi.
6. DATABASE TRANSLATION (STRICT): No matter what language the user is speaking, ALL data you send to your tools (patient_name, reason, problem_or_speciality) MUST be translated to plain ENGLISH before calling the tool.
7. TIME FORMATTING: 
   - In English: Use standard formats (e.g., 9:00 AM).
   - In Hindi/Telugu: Translate all digits/times into spelled-out phonetic words. For Telugu, say "ఉదయం తొమ్మిది గంటలకు". NEVER output raw digits like "09:00" in regional languages.
8. RESPECTFUL VOCABULARY: NEVER use the Telugu word "రోగి" (rogi) to refer to a patient. It sounds clinical and disrespectful. Always use the English word "patient" (e.g., "దయచేసి పేషెంట్ పేరు చెప్పగలరా?").
9. PACE & SPEED: Keep all your sentences extremely short, crisp, and punchy. Avoid long explanations.
10. STAY IN NATIVE LANGUAGE: Do NOT switch languages just because of name pronunciations. ONLY switch if the user explicitly asks to speak in Hindi or English.

--- INTENT ROUTING ---
1. CANCEL/RESCHEDULE: Say: "Based on hospital policy, appointments cannot be cancelled or rescheduled through the AI assistant. Please call the clinic directly." (End flow).
2. FOLLOW-UP BOOKING: If the user asks for a "follow-up", ask EXACTLY: "Could you please tell me your 10-digit phone number so I can check your records?" Once provided, SILENTLY call `verify_followup`.

--- CORE BOOKING STATES ---

PHASE 0 (Symptoms Gathering - MANDATORY ONLY IF MISSING):
Check if the user has ALREADY provided symptoms (e.g., "I have a fever", "జలుబుగా ఉంది").
- If SYMPTOMS ALREADY GIVEN: SKIP THIS PHASE completely and go directly to PHASE 1.
- If NO symptoms given: ask "What medical problem or symptoms are you experiencing?" 
CRITICAL: DO NOT call `check_availability` until symptoms are explicitly stated. Do NOT assume 'General Physician' based on background noise.

PHASE 1 (Availability):
ONLY AFTER the user gives symptoms, SILENTLY call `check_availability`. Emit ZERO text.

PHASE 2 (Offer & Negotiation):
- Initial Offer: Read the `system_directive` exactly as intended. (ONLY translate if the user is currently speaking Hindi/Telugu, and spell out times).
- Negotiation: Look at the `all_available_slots` to find alternative times if asked. Check if the requested time is available, confirm it, and ONLY THEN proceed to Phase 3. DO NOT repeat the initial offer if they just ask a question.

PHASE 3 (Details Request):
If the user agrees to a slot, ask: "Could you please tell me the patient's name and 10-digit phone number?" 
(Note: If they already provided their phone number for a follow-up, just ask for their name).
If the user asks a completely unrelated question or chit-chats, answer them naturally, but gently guide them back to providing their name and phone number to secure the booking.

PHASE 4 (The Silent Trigger):
If the user provides a name and a 10-digit number, YOU MUST STOP SPEAKING.
Immediately call `voice_book_appointment` (Ensure you translate the name and reason to English first!).
CRITICAL: Emit ZERO characters of text. DO NOT say "Okay" or repeat the name.

PHASE 5 (Confirmation):
ONLY AFTER the tool returns "success", inform the patient.
CRITICAL RULES FOR CONFIRMATION:
- Speak ONLY in the locked language. NEVER speak English first and then translate. 
- For a paid appointment, say EXACTLY the native translation of this phrase: "A tentative appointment has been booked. Please click the payment link on WhatsApp and do the payment under 15 minutes. Thank you."
- For a free follow-up, say EXACTLY the native translation of: "Your free follow-up appointment is confirmed. A WhatsApp message has been sent to you. Thank you."
- DO NOT append any questions like "Shall I book this?"
- Immediately after saying this, call the `end_call` tool.
"""

# ==========================================================
# 🛠️ PROCESSORS
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
                "పార్లమెంట్": "అపాయింట్మెంట్", "apartment": "appointment",
                "అపార్ట్మెంట్": "అపాయింట్మెంట్", "department": "appointment",
                "తెలుగు": "telugu", "हिंदी": "hindi"
            }
            for k, v in corrections.items():
                text = text.replace(k, v)
            frame.text = text
            
        await self.push_frame(frame, direction)

class AutoLanguageProcessor(FrameProcessor):
    def __init__(self, session_id):
        super().__init__()
        self.session_id = session_id
        self.current_language = "te-IN" # Default to Telugu

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        
        if isinstance(frame, TextFrame):
            text = frame.text
            new_lang = self.current_language
            new_voice = f"{self.current_language}-Chirp3-HD-Despina" # Always default to current language + female voice
            
            # 👇 FIXED: Strict language locking. Only switch if confident it's a new primary language.
            if re.search(r'[\u0c00-\u0c7f]', text): # Telugu
                new_lang = "te-IN"
                new_voice = "te-IN-Chirp3-HD-Despina"
            elif re.search(r'[\u0900-\u097f]', text): # Hindi
                new_lang = "hi-IN"
                new_voice = "hi-IN-Chirp3-HD-Despina"
            elif re.search(r'^[a-zA-Z0-9\s\.,!\?\'\"]+$', text) and len(text) > 10: 
                # Only switch to English if it's completely Latin text, to avoid STT hallucination switching
                new_lang = "en-US"
                new_voice = "en-US-Chirp3-HD-Despina"
                
            if new_lang != self.current_language:
                logger.info(f"[{self.session_id}] 🌐 Auto-switching TTS language to: {new_lang} | Voice: {new_voice}")
                self.current_language = new_lang
                await self.push_frame(
                    TTSUpdateSettingsFrame(settings={"language": new_lang, "voice": new_voice, "speaking_rate": 1.15}), 
                    direction
                )
                
        if isinstance(frame, FunctionCallInProgressFrame):
            filler = ""
            if frame.function_name == "voice_book_appointment":
                if self.current_language == "te-IN": filler = "ఒక్క నిమిషం..."
                elif self.current_language == "hi-IN": filler = "एक मिनट..."
                else: filler = "One moment..."
            elif frame.function_name == "check_availability":
                if self.current_language == "te-IN": filler = "చూస్తున్నాను..."
                elif self.current_language == "hi-IN": filler = "चेक कर रही हूँ..."
                else: filler = "Checking..."
            
            if filler:
                logger.info(f"[{self.session_id}] ⏳ Playing short filler audio: {filler}")
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
        cost_per_minute_usd = total_cost_usd / duration_minutes if duration_minutes > 0 else 0
        
        exchange_rate = 83.5
        total_cost_inr = total_cost_usd * exchange_rate
        cost_per_minute_inr = cost_per_minute_usd * exchange_rate
        
        logger.info("\n" + "="*55)
        logger.info(f"[{self.session_id}] 💰 SESSION BILLING RECEIPT (INR) 💰")
        logger.info("="*55)
        logger.info(f"⏱️ Duration:        {duration_minutes:.2f} mins ({duration_seconds:.0f}s)")
        logger.info(f"🎙️ STT Cost:        ₹{stt_cost_usd * exchange_rate:.4f} (${stt_cost_usd:.4f})")
        logger.info(f"🧠 LLM In Cost:     ₹{llm_input_cost_usd * exchange_rate:.4f} (${llm_input_cost_usd:.4f}) | ~{total_input_tokens:.0f} tokens")
        logger.info(f"🧠 LLM Out Cost:    ₹{llm_output_cost_usd * exchange_rate:.4f} (${llm_output_cost_usd:.4f}) | ~{self.llm_output_tokens_est:.0f} tokens")
        logger.info(f"🗣️ TTS Cost:        ₹{tts_cost_usd * exchange_rate:.4f} (${tts_cost_usd:.4f}) | {self.tts_chars} chars")
        logger.info("-" * 55)
        logger.info(f"💵 TOTAL COST:      ₹{total_cost_inr:.4f} (${total_cost_usd:.4f})")
        logger.info(f"📊 COST PER MIN:    ₹{cost_per_minute_inr:.4f} (${cost_per_minute_usd:.4f})")
        logger.info("="*55 + "\n")

# ==========================================================
# 🎙️ PIPECAT RUNNER
# ==========================================================
async def run_bot(transport: BaseTransport, call_sid: str = "local_test", is_twilio: bool = False):
    await ensure_redis_client()
    session_id = call_sid[:8]

    in_rate = 8000 if is_twilio else 16000
    out_rate = 8000 if is_twilio else 24000

    stt = SarvamSTTService(
        api_key=os.getenv("SARVAM_API_KEY"),
        language="unknown",
        model="saaras:v3",
        mode="transcribe"
    )
    
    google_creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not google_creds_path or not os.path.exists(google_creds_path):
        logger.error(f"🚨 Missing Google Credentials File! Path checked: {google_creds_path}")

    # 👇 FIXED: Starts with Female Telugu voice immediately
    tts = GoogleTTSService(
        credentials_path=google_creds_path,
        voice="te-IN-Chirp3-HD-Despina", 
        language="te-IN"
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
            # 👇 FIXED: Initial greeting is now natively in Telugu
            TTSSpeakFrame(
                "నమస్కారం! మిత్ర హాస్పిటల్స్‌కు స్వాగతం. నేను మీకు ఎలా సహాయపడగలను?",
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
    
    billing_tracker.generate_receipt()

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