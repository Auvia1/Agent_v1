# import os, json, uuid, hmac, hashlib, asyncio
# from datetime import datetime
# from contextlib import asynccontextmanager

# import pytz
# import redis.asyncio as redis
# from dotenv import load_dotenv
# from loguru import logger

# from fastapi import FastAPI, Request, Form, WebSocket
# from fastapi.responses import HTMLResponse, PlainTextResponse
# import uvicorn

# # ✅ PIPECAT & AI IMPORTS
# from pipecat.frames.frames import Frame, TextFrame, TranscriptionFrame, TTSSpeakFrame
# from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
# from pipecat.pipeline.pipeline import Pipeline
# from pipecat.pipeline.runner import PipelineRunner
# from pipecat.pipeline.task import PipelineParams, PipelineTask
# from pipecat.processors.aggregators.llm_context import LLMContext
# from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
# from pipecat.transports.base_transport import BaseTransport, TransportParams
# from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
# from pipecat.runner.types import RunnerArguments, SmallWebRTCRunnerArguments

# # Twilio Specific
# from pipecat.transports.websocket.fastapi import FastAPIWebsocketTransport, FastAPIWebsocketParams
# from pipecat.serializers.twilio import TwilioFrameSerializer

# from pipecat.services.sarvam.stt import SarvamSTTService
# from pipecat.services.sarvam.tts import SarvamTTSService
# from pipecat.services.google.llm import GoogleLLMService

# # ✅ INTERNAL TOOLS
# from db.connection import get_db_pool
# from tools.pool import init_tool_db
# from tools.pipecat_tools import register_all_tools, get_tools_schema
# from tools.notify import handle_successful_payment, send_confirmation, send_interactive_slots
# from tools.booking import voice_book_appointment
# from tools.availability import check_availability
# from tools.followup import verify_followup

# load_dotenv(override=True)

# # ====== GLOBAL CLIENTS ======
# redis_client = None
# gemini_client = None # For WhatsApp Async calls if needed

# async def ensure_redis_client():
#     global redis_client
#     if redis_client: return
#     redis_client = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"), decode_responses=True)
#     await redis_client.ping()

# @asynccontextmanager
# async def lifespan(app: FastAPI):
#     pool = await get_db_pool()
#     init_tool_db(pool)
#     await ensure_redis_client()
#     yield
#     if redis_client: await redis_client.close()

# app = FastAPI(lifespan=lifespan)

# # ==========================================================
# # 🧠 SHARED SYSTEM PROMPT
# # ==========================================================
# ist = pytz.timezone('Asia/Kolkata')
# current_time_str = datetime.now(ist).strftime('%A, %B %d, %Y at %I:%M %p IST')

# SYSTEM_PROMPT = f"""Role: Mithra Hospital AI Receptionist.
# CURRENT LIVE TIME: {current_time_str}

# --- RULES ---
# 1. DEFAULT: English. Switch to Hindi/Telugu ONLY if user speaks them.
# 2. TTS: Phonetic words for numbers/dates ONLY in Hindi/Telugu (e.g. "తొమ్మిదిన్నరకు").
# 3. FLOW: 
#    - Symptoms -> check_availability
#    - Confirm Slot -> voice_book_appointment
#    - Only book once name AND 10-digit number are provided.
# """

# # ==========================================================
# # 🎙️ VOICE BOT RUNNER (Handles both WebRTC and Twilio)
# # ==========================================================
# async def run_bot(transport: BaseTransport, call_sid: str = "local", is_twilio: bool = False):
#     in_rate = 8000 if is_twilio else 16000
#     out_rate = 8000 if is_twilio else 24000

#     stt = SarvamSTTService(api_key=os.getenv("SARVAM_API_KEY"), language="unknown", model="saaras:v3")
#     tts = SarvamTTSService(api_key=os.getenv("SARVAM_API_KEY"), speech_sample_rate=out_rate)
#     llm = GoogleLLMService(api_key=os.getenv("GEMINI_API_KEY"), model="gemini-2.0-flash")

#     register_all_tools(llm)

#     context = LLMContext(messages=[{"role": "system", "content": SYSTEM_PROMPT}], tools=get_tools_schema())
#     agg = LLMContextAggregatorPair(context)

#     pipeline = Pipeline([transport.input(), stt, agg.user(), llm, tts, transport.output(), agg.assistant()])
#     task = PipelineTask(pipeline, params=PipelineParams(audio_in_sample_rate=in_rate, audio_out_sample_rate=out_rate))

#     @transport.event_handler("on_client_connected")
#     async def connected(t, c):
#         await task.queue_frames([TTSSpeakFrame("Welcome to Mithra Hospitals. How can I help you?")])

#     runner = PipelineRunner(handle_sigint=False)
#     await runner.run(task)

# # ==========================================================
# # 📞 TWILIO VOICE ROUTES
# # ==========================================================
# @app.post("/voice")
# async def voice(request: Request):
#     base_url = str(request.base_url).replace("http://", "wss://").replace("https://", "wss://")
#     twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
#     <Response><Connect><Stream url="{base_url}media" /></Connect><Hangup /></Response>"""
#     return HTMLResponse(twiml, media_type="application/xml")

# @app.websocket("/media")
# async def media(ws: WebSocket):
#     await ws.accept()
#     logger.info("☎️ Twilio connected to WebSocket")
    
#     msg = await ws.receive_text()
#     data = json.loads(msg)
#     if data.get('event') == 'connected':
#         msg = await ws.receive_text()
#         data = json.loads(msg)

#     if data.get('event') == 'start':
#         stream_sid = data['start']['streamSid']
#         # Disable auto_hang_up to avoid API credential errors
#         serializer = TwilioFrameSerializer(stream_sid, params=TwilioFrameSerializer.InputParams(auto_hang_up=False))
#         transport = FastAPIWebsocketTransport(websocket=ws, params=FastAPIWebsocketParams(serializer=serializer))
#         await run_bot(transport, call_sid=data['start']['callSid'], is_twilio=True)

# # ==========================================================
# # 💬 WHATSAPP WEBHOOK
# # ==========================================================
# @app.get("/whatsapp-webhook")
# async def verify_wa(request: Request):
#     if request.query_params.get("hub.mode") == "subscribe" and \
#        request.query_params.get("hub.verify_token") == os.getenv("WHATSAPP_VERIFY_TOKEN"):
#         return PlainTextResponse(request.query_params.get("hub.challenge"))
#     return PlainTextResponse("Forbidden", status_code=403)

# @app.post("/whatsapp-webhook")
# async def whatsapp_msg(request: Request):
#     data = await request.json()
#     try:
#         val = data["entry"][0]["changes"][0]["value"]
#         if "messages" in val:
#             msg = val["messages"][0]
#             phone = msg["from"]
#             text = msg.get("text", {}).get("body", "")
#             logger.info(f"📩 WA: {phone} -> {text}")
#             # Insert your logic to call Gemini here
#             await send_confirmation(phone, f"Mithra AI: Received your message '{text}'")
#     except Exception as e:
#         logger.error(f"WA Error: {e}")
#     return {"status": "ok"}

# # ==========================================================
# # 💳 RAZORPAY WEBHOOK
# # ==========================================================
# @app.post("/razorpay-webhook")
# async def razorpay_webhook(request: Request):
#     data = await request.json()
#     if data.get("event") == "payment_link.paid":
#         appt_id = data["payload"]["payment_link"]["entity"]["notes"].get("appointment_id")
#         if appt_id:
#             await handle_successful_payment(appt_id)
#     return {"status": "ok"}

# # ==========================================================
# # 🚀 ENTRY POINT
# # ==========================================================
# if __name__ == "__main__":
#     import subprocess
#     import time

#     # 1. Start Redis
#     try:
#         subprocess.Popen(["redis-server"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
#         logger.info("✅ Redis Server started in background.")
#     except Exception as e:
#         logger.error(f"❌ Could not start Redis: {e}")

#     # 2. Start Ngrok
#     try:
#         subprocess.Popen(["ngrok", "http", "8000"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
#         logger.info("✅ Ngrok Tunnel started on port 8000.")
#     except Exception as e:
#         logger.error(f"❌ Could not start Ngrok: {e}")

#     # 3. Give them a second to breathe
#     time.sleep(2)

#     # 4. Run the FastAPI App
#     logger.info("🚀 Launching Mithra Call Server...")
#     uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
import os
import hmac
import hashlib
import json
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from loguru import logger
from dotenv import load_dotenv

from db.connection import get_db_pool
from tools.pool import init_tool_db
from tools.notify import handle_successful_payment

import whatsapp_agent
import call_agent

load_dotenv(override=True)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("⚙️ Initializing DB and Redis...")
    pool = await get_db_pool()
    init_tool_db(pool)
    await whatsapp_agent.ensure_redis_client()
    await call_agent.ensure_redis_client()
    logger.info("✅ All services initialized.")
    yield
    if whatsapp_agent.redis_client: await whatsapp_agent.redis_client.close()
    if call_agent.redis_client: await call_agent.redis_client.close()
    logger.info("🛑 Server shutting down, connections closed.")

app = FastAPI(lifespan=lifespan)

app.include_router(whatsapp_agent.router)
app.include_router(call_agent.router)

@app.post("/razorpay-webhook")
async def razorpay_webhook(request: Request):
    logger.info("🔔 Razorpay webhook received.")
    webhook_secret = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
    webhook_signature = request.headers.get("X-Razorpay-Signature")
    payload_body = await request.body()

    if webhook_secret and webhook_signature:
        expected_signature = hmac.new(
            key=webhook_secret.encode(),
            msg=payload_body,
            digestmod=hashlib.sha256
        ).hexdigest()
        if expected_signature != webhook_signature:
            logger.warning("❌ Razorpay signature mismatch.")
            return {"status": "error", "message": "Invalid Signature"}

    try:
        payload = json.loads(payload_body)
    except Exception:
        return {"status": "error", "message": "Invalid JSON"}

    if payload.get("event") == "payment_link.paid":
        payment_entity = payload.get("payload", {}).get("payment_link", {}).get("entity", {})
        appointment_id = payment_entity.get("notes", {}).get("appointment_id")
        if appointment_id:
            logger.info(f"💰 Payment confirmed for appointment: {appointment_id}")
            await handle_successful_payment(appointment_id)

    return {"status": "success"}

@app.get("/health")
async def health_check():
    return {"status": "ok"}

# ⚠️ LOCAL DEV ONLY — this block never runs on Render
if __name__ == "__main__":
    import subprocess, time
    try:
        subprocess.Popen(["redis-server"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        logger.info("✅ Redis started locally.")
    except Exception as e:
        logger.warning(f"⚠️ Redis start failed: {e}")
    try:
        subprocess.Popen(["ngrok", "http", "8000"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        logger.info("✅ Ngrok tunnel started.")
    except Exception as e:
        logger.warning(f"⚠️ Ngrok start failed: {e}")
    time.sleep(2)
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)