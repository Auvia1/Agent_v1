# ====== COMMON IMPORTS ======
import os, json, uuid, hmac, hashlib
from datetime import datetime
from contextlib import asynccontextmanager

import pytz
import redis.asyncio as redis
from dotenv import load_dotenv
from loguru import logger

from fastapi import FastAPI, Request, Form, WebSocket
from fastapi.responses import HTMLResponse, PlainTextResponse
import uvicorn

# ====== LOAD ENV ======
load_dotenv(override=True)

# ====== REDIS ======
redis_client = None

async def ensure_redis_client():
    global redis_client
    if redis_client: return
    redis_client = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"), decode_responses=True)
    await redis_client.ping()

# ====== DB ======
from db.connection import get_db_pool
from tools.pool import init_tool_db

@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await get_db_pool()
    init_tool_db(pool)
    await ensure_redis_client()
    yield
    if redis_client: await redis_client.close()

app = FastAPI(lifespan=lifespan)

# ==========================================================
# 💳 RAZORPAY WEBHOOK (COMMON)
# ==========================================================
from tools.notify import handle_successful_payment

@app.post("/razorpay-webhook")
async def razorpay_webhook(request: Request):
    body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature")
    secret = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")

    if secret and signature:
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if expected != signature:
            return {"status": "invalid signature"}

    data = await request.json()

    if data.get("event") == "payment_link.paid":
        appointment_id = data["payload"]["payment_link"]["entity"]["notes"].get("appointment_id")
        if appointment_id:
            await handle_successful_payment(appointment_id)

    return {"status": "ok"}

# ==========================================================
# 📞 TWILIO VOICE (FROM call_agent)
# ==========================================================
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.transports.websocket.fastapi import FastAPIWebsocketTransport, FastAPIWebsocketParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.services.sarvam.stt import SarvamSTTService
from pipecat.services.sarvam.tts import SarvamTTSService
from pipecat.services.google.llm import GoogleLLMService

# (reuse your existing run_bot function logic)
async def run_bot(transport, call_sid="test", is_twilio=False):
    stt = SarvamSTTService(api_key=os.getenv("SARVAM_API_KEY"))
    tts = SarvamTTSService(api_key=os.getenv("SARVAM_API_KEY"))
    llm = GoogleLLMService(api_key=os.getenv("GEMINI_API_KEY"))

    context = LLMContext(messages=[{"role": "system", "content": "You are a hospital assistant."}])
    agg = LLMContextAggregatorPair(context)

    pipeline = Pipeline([transport.input(), stt, agg.user(), llm, tts, transport.output(), agg.assistant()])
    task = PipelineTask(pipeline)

    runner = PipelineRunner()
    await runner.run(task)

@app.post("/voice")
async def voice():
    return HTMLResponse("""
    <Response>
        <Connect>
            <Stream url="wss://YOUR_DOMAIN/media" />
        </Connect>
    </Response>
    """, media_type="application/xml")

@app.websocket("/media")
async def media(ws: WebSocket):
    await ws.accept()
    serializer = TwilioFrameSerializer(stream_sid="test")
    transport = FastAPIWebsocketTransport(
        websocket=ws,
        params=FastAPIWebsocketParams(serializer=serializer)
    )
    await run_bot(transport, is_twilio=True)

# ==========================================================
# 💬 WHATSAPP WEBHOOK (FIXED)
# ==========================================================
from tools.notify import send_confirmation

@app.get("/whatsapp-webhook")
async def verify_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == os.getenv("WHATSAPP_VERIFY_TOKEN"):
        return PlainTextResponse(content=challenge)   # ✅ FIXED

    return PlainTextResponse("error", status_code=403)

@app.post("/whatsapp-webhook")
async def whatsapp_webhook(request: Request):
    data = await request.json()

    try:
        msg = data["entry"][0]["changes"][0]["value"]["messages"][0]
        phone = msg["from"]
        text = msg.get("text", {}).get("body", "")

        logger.info(f"📩 WhatsApp: {phone} -> {text}")

        # simple reply (replace with your Gemini logic)
        await send_confirmation(phone, f"You said: {text}")

    except Exception as e:
        logger.error(f"WA Error: {e}")

    return {"status": "ok"}

# ==========================================================
# 🚀 RUN SERVER
# ==========================================================
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)