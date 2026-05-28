# # main.py
# import os
# import hmac
# import hashlib
# import json
# from contextlib import asynccontextmanager

# import uvicorn
# from fastapi import FastAPI, Request
# from loguru import logger
# from dotenv import load_dotenv

# from db.connection import get_db_pool
# from tools.pool import init_tool_db
# from tools.notify import handle_successful_payment

# import whatsapp_agent
# import call_agent
# import web_ui_tester # <--- ADDED: Imports your new UI testing file

# load_dotenv(override=True)

# @asynccontextmanager
# async def lifespan(app: FastAPI):
#     logger.info("⚙️ Initializing DB and Redis...")
#     pool = await get_db_pool()
#     init_tool_db(pool)
    
#     await whatsapp_agent.ensure_redis_client()
#     await call_agent.ensure_redis_client()
    
#     logger.info("✅ All services initialized. Server is AWAKE!")
#     yield
    
#     if whatsapp_agent.redis_client: await whatsapp_agent.redis_client.close()
#     if call_agent.redis_client: await call_agent.redis_client.close()
#     logger.info("🛑 Server shutting down.")

# # 1. Create the Engine
# app = FastAPI(lifespan=lifespan)

# # 2. Attach the Train Cars (Your Agents & UI)
# app.include_router(whatsapp_agent.router)
# app.include_router(call_agent.router)
# app.include_router(web_ui_tester.router) # <--- ADDED: Attaches the /test-ui route to the server

# # 3. Razorpay Webhook
# @app.post("/razorpay-webhook")
# async def razorpay_webhook(request: Request):
#     webhook_secret = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
#     webhook_signature = request.headers.get("X-Razorpay-Signature")
#     payload_body = await request.body()

#     if webhook_secret and webhook_signature:
#         expected_signature = hmac.new(key=webhook_secret.encode(), msg=payload_body, digestmod=hashlib.sha256).hexdigest()
#         if expected_signature != webhook_signature:
#             return {"status": "error", "message": "Invalid Signature"}

#     try:
#         payload = json.loads(payload_body)
#         if payload.get("event") == "payment_link.paid":
#             appointment_id = payload.get("payload", {}).get("payment_link", {}).get("entity", {}).get("notes", {}).get("appointment_id")
#             if appointment_id:
#                 await handle_successful_payment(appointment_id)
#         return {"status": "success"}
#     except Exception:
#         return {"status": "error", "message": "Invalid JSON"}

# @app.get("/health")
# async def health_check():
#     return {"status": "ok"}

# # 4. Start the Server
# if __name__ == "__main__":
#     logger.info("🚀 Starting Uvicorn server...")
#     uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
import os
import hmac
import hashlib
import json
import base64  # <--- ADDED: Required for decoding the Base64 Coolify variable
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from loguru import logger
from dotenv import load_dotenv

load_dotenv(override=True)

# ==========================================
# 🛑 GOOGLE CREDENTIALS BASE64 DECODER
# ==========================================
# This reads the scrambled Coolify text, turns it back into JSON, 
# and saves it as a file right before the server starts.
if "GOOGLE_JSON_BASE64" in os.environ:
    decoded_json = base64.b64decode(os.environ["GOOGLE_JSON_BASE64"]).decode('utf-8')
    with open("google-credentials.json", "w") as f:
        f.write(decoded_json)
    
    # Tell Google where to find the file
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "google-credentials.json"
# ==========================================

from db.connection import get_db_pool
from tools.pool import init_tool_db
from tools.notify import handle_successful_payment

import whatsapp_agent
import call_agent
import web_ui_tester # <--- ADDED: Imports your new UI testing file


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("⚙️ Initializing DB and Redis...")
    pool = await get_db_pool()
    init_tool_db(pool)
    
    await whatsapp_agent.ensure_redis_client()
    await call_agent.ensure_redis_client()
    
    logger.info("✅ All services initialized. Server is AWAKE!")
    yield
    
    if whatsapp_agent.redis_client: await whatsapp_agent.redis_client.close()
    if call_agent.redis_client: await call_agent.redis_client.close()
    logger.info("🛑 Server shutting down.")

# 1. Create the Engine
app = FastAPI(lifespan=lifespan)

# 2. Attach the Train Cars (Your Agents & UI)
app.include_router(whatsapp_agent.router)
app.include_router(call_agent.router)
app.include_router(web_ui_tester.router) # <--- ADDED: Attaches the /test-ui route to the server

# 3. Razorpay Webhook
@app.post("/razorpay-webhook")
async def razorpay_webhook(request: Request):
    webhook_secret = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
    webhook_signature = request.headers.get("X-Razorpay-Signature")
    payload_body = await request.body()

    if webhook_secret and webhook_signature:
        expected_signature = hmac.new(key=webhook_secret.encode(), msg=payload_body, digestmod=hashlib.sha256).hexdigest()
        if expected_signature != webhook_signature:
            return {"status": "error", "message": "Invalid Signature"}

    try:
        payload = json.loads(payload_body)
        if payload.get("event") == "payment_link.paid":
            appointment_id = payload.get("payload", {}).get("payment_link", {}).get("entity", {}).get("notes", {}).get("appointment_id")
            if appointment_id:
                await handle_successful_payment(appointment_id)
        return {"status": "success"}
    except Exception:
        return {"status": "error", "message": "Invalid JSON"}

@app.get("/health")
async def health_check():
    return {"status": "ok"}

# 4. Start the Server
if __name__ == "__main__":
    logger.info("🚀 Starting Uvicorn server...")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)