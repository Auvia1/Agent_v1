#main.py
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