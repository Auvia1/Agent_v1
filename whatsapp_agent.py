# import os
# import hmac
# import hashlib
# import json
# import asyncio
# from google import genai
# from google.genai import types
# from contextlib import asynccontextmanager
# from datetime import datetime
# import pytz
# import redis.asyncio as redis
# from dotenv import load_dotenv
# from loguru import logger

# import uvicorn
# from fastapi import FastAPI, Request
# from fastapi.responses import HTMLResponse

# # ✅ Clean Tool Imports (No raw DB queries in this file!)
# from tools.notify import send_confirmation, send_interactive_slots, handle_successful_payment
# from tools.booking import voice_book_appointment
# from tools.availability import check_availability
# from tools.followup import verify_followup
# from db.connection import get_db_pool
# from tools.pool import init_tool_db 

# load_dotenv(override=True)
# gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
# redis_client = None

# async def ensure_redis_client():
#     global redis_client
#     if redis_client: return
#     try:
#         redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
#         redis_client = redis.from_url(redis_url, decode_responses=True)
#         await redis_client.ping()
#         logger.info("✅ Redis client connected successfully.")
#     except Exception as e:
#         logger.warning(f"⚠️ Redis connection failed: {e}")
#         redis_client = None

# @asynccontextmanager
# async def app_lifespan(app):
#     pool = await get_db_pool()
#     init_tool_db(pool)
#     await ensure_redis_client()
#     yield
#     if redis_client: await redis_client.close()

# app = FastAPI(lifespan=app_lifespan)

# # ==========================================================
# # 💳 RAZORPAY WEBHOOK (Clean Integration)
# # ==========================================================
# @app.post("/razorpay-webhook")
# async def razorpay_webhook(request: Request):
#     logger.info("🔔 WEBHOOK TRIGGERED: Razorpay pinged the server!")
#     webhook_secret = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
#     webhook_signature = request.headers.get("X-Razorpay-Signature")
#     payload_body = await request.body()
    
#     if webhook_secret and webhook_signature:
#         expected_signature = hmac.new(key=webhook_secret.encode(), msg=payload_body, digestmod=hashlib.sha256).hexdigest()
#         if expected_signature != webhook_signature: return {"status": "error", "message": "Invalid Signature"}

#     try: 
#         payload = await request.json()
#     except: 
#         return {"status": "error", "message": "Invalid JSON"}
    
#     if payload.get("event") == "payment_link.paid":
#         payment_entity = payload.get("payload", {}).get("payment_link", {}).get("entity", {})
#         appointment_id = payment_entity.get("notes", {}).get("appointment_id")
        
#         if appointment_id:
#             logger.info(f"💰 WhatsApp Payment received for appointment: {appointment_id}")
#             # Delegate to our clean notify tool (updates DB and sends WhatsApp confirmation)
#             await handle_successful_payment(appointment_id)

#     return {"status": "success"}

# # ==========================================================
# # 🤖 WHATSAPP PROMPT
# # ==========================================================
# ist = pytz.timezone('Asia/Kolkata')
# current_time = datetime.now(ist).strftime('%A, %B %d, %Y at %I:%M %p IST')

# WHATSAPP_SYSTEM_PROMPT = f"""Role: Mithra Hospital AI Receptionist (WhatsApp).
# CURRENT LIVE TIME: {current_time}

# CRITICAL BEHAVIOR RULES:
# 1. ANTI-REPETITION: NEVER repeat a greeting (like "Welcome to Mithra Hospital") if you have already said it. 
# 2. SHORT & NATURAL: Keep responses concise and easy to read on a mobile screen. Use emojis naturally.
# 3. NO PREMATURE DETAILS: NEVER ask for the patient's name or phone number until AFTER they select a specific time slot.

# WORKFLOW (Execute strictly ONE step at a time):
# - STEP A (Symptoms): Ask what medical problem they are experiencing. STOP and wait.
# - STEP B (Follow-up Check): If they explicitly ask for a follow-up, use `verify_followup_wa` to check eligibility.
# - STEP C (Check Availability): DEDUCE THE SPECIALTY -> Call `check_availability_wa`. Leave `requested_date` BLANK for "next available".
# - STEP D (Offer Menu): 
#   * The tool will send an interactive menu directly to their WhatsApp. Say ONLY: "I have pulled up the schedule for you! Please tap the menu button above to select your preferred time ☝️"
# - STEP E (Get Details): Once they reply with a time, ask EXACTLY: "Great choice! Please provide the patient's name and 10-digit phone number." STOP AND WAIT.
# - STEP F (Book): Call `book_appointment_wa` AFTER the user texts their real name and phone number. 
#   * If the tool returns a warning (like duplicate appointment or expired follow-up), STOP and ask the user exactly what the tool tells you to ask.
# - STEP G (Wrap Up): Output EXACTLY the wrap-up message the tool provides.

# Reschedule/Cancel Policy: Inform them confirmed appointments cannot be changed via AI. Call the clinic."""

# # ==========================================================
# # 💬 WHATSAPP TEXT CHAT WEBHOOKS
# # ==========================================================
# @app.get("/whatsapp-webhook")
# async def verify_whatsapp_webhook(request: Request):
#     mode = request.query_params.get("hub.mode")
#     token = request.query_params.get("hub.verify_token")
#     challenge = request.query_params.get("hub.challenge")
#     if mode == "subscribe" and token == os.getenv("WHATSAPP_VERIFY_TOKEN"): return int(challenge)
#     return HTMLResponse(content="Verification token mismatch", status_code=403)

# @app.post("/whatsapp-webhook")
# async def receive_whatsapp_message(request: Request):
#     try:
#         body = await request.json()
#         if body.get("object") == "whatsapp_business_account":
#             for entry in body.get("entry", []):
#                 for change in entry.get("changes", []):
#                     value = change.get("value", {})
#                     if "messages" in value:
#                         message = value["messages"][0]
#                         sender_phone = message.get("from")
                        
#                         incoming_text = ""
#                         msg_type = message.get("type")
#                         message_id = message.get("id")
                        
#                         if msg_type == "text": incoming_text = message["text"]["body"]
#                         elif msg_type == "interactive":
#                             interactive_data = message.get("interactive", {})
#                             if interactive_data.get("type") == "list_reply": incoming_text = interactive_data["list_reply"]["title"]
#                             elif interactive_data.get("type") == "button_reply": incoming_text = interactive_data["button_reply"]["title"]
                        
#                         if not incoming_text: continue
                            
#                         # Deduplication check
#                         if message_id and redis_client:
#                             is_duplicate = await redis_client.get(f"processed_msg:{message_id}")
#                             if is_duplicate: return {"status": "success"}
#                             await redis_client.setex(f"processed_msg:{message_id}", 60, "true")
                            
#                         logger.info(f"💬 Incoming WhatsApp from {sender_phone}: {incoming_text}")

#                         user_msg_lower = incoming_text.strip().lower()

#                         # 🔥 FIX: Reset conversation natively on "hi" or "reset"
#                         if user_msg_lower in ["hi", "hello", "hey", "start", "menu", "reset"]:
#                             if redis_client:
#                                 await redis_client.delete(f"wa_history:{sender_phone}")
#                                 await redis_client.delete(f"last_doc_id:{sender_phone}")
#                             logger.info(f"🧹 Auto-wiped memory for {sender_phone} due to new greeting.")
                            
#                             if user_msg_lower == "reset":
#                                 clean_phone = sender_phone.replace("+91", "").replace("+", "")
#                                 await send_confirmation(clean_phone, "🧹 AI Memory wiped! We are starting completely fresh. Say 'Hi'!")
#                                 return {"status": "success"}
                        
#                         # Mock Class to bridge Gemini Functions with our Pipecat Tools
#                         class WAParams:
#                             def __init__(self): self.result = None
#                             async def result_callback(self, result): self.result = result

#                         # --- WhatsApp Wrapped Tools ---
#                         async def check_availability_wa(problem_or_speciality: str, requested_date: str = None):
#                             """Check doctor availability by specialty. Map user symptoms to: 'General Physician', 'Dermatologist', 'Cardiologist', 'Pediatrician', 'Orthopedic', or 'Urologist'."""
#                             p = WAParams()
#                             await check_availability(p, problem_or_speciality, requested_date)
#                             result_data = p.result
                            
#                             # Intercept success to send interactive WhatsApp menu
#                             if result_data and result_data.get("status") == "success":
#                                 slots = result_data.get("all_available_slots", [])
#                                 doc_id = result_data.get("doctor_id")
#                                 doc_name = result_data.get("doctor_name")
#                                 target_date = result_data.get("target_date")
                                
#                                 if slots and doc_id:
#                                     clean_phone = sender_phone.replace("+91", "").replace("+", "")
#                                     await send_interactive_slots(clean_phone, doc_name, target_date, slots)
#                                     if redis_client:
#                                         await redis_client.setex(f"last_doc_id:{sender_phone}", 86400, doc_id)
#                                     return {"status": "success", "message": "I have sent an interactive menu. Ask them to click it."}
#                             return result_data

#                         async def verify_followup_wa(phone: str):
#                             """Checks if a user is eligible for a free follow-up appointment."""
#                             p = WAParams()
#                             await verify_followup(p, phone)
#                             return p.result

#                         async def book_appointment_wa(patient_name: str, start_time_iso: str, phone: str, reason: str, force_book: bool = False, is_followup: str = "unknown"):
#                             """Book the appointment. Pass 'yes' to is_followup if user confirmed 7-day free follow-up."""
#                             p = WAParams()
#                             doctor_id = await redis_client.get(f"last_doc_id:{sender_phone}") if redis_client else None
                            
#                             if not doctor_id:
#                                 return {"status": "error", "message": "SYSTEM DIRECTIVE: Ask the user to select a doctor/time slot first."}
                            
#                             # Leverage the newly imported voice_book_appointment tool (handles all DB + intercept logic!)
#                             await voice_book_appointment(p, doctor_id, patient_name, start_time_iso, phone, reason, force_book, is_followup)
                            
#                             result_data = p.result
#                             if result_data and result_data.get("status") == "success":
#                                 if not result_data.get("is_followup"): 
#                                     return {"status": "success", "message": "Say EXACTLY: 'Your appointment has been tentatively booked! Please click the Razorpay link above to confirm your slot. Note: The payment link expires in 15 minutes.'"}
#                                 else:
#                                     return {"status": "success", "message": "Say EXACTLY: 'Your free 1-week follow-up has been successfully booked! No payment is required. See you then!'"}
#                             return result_data

#                         whatsapp_tools = [check_availability_wa, verify_followup_wa, book_appointment_wa]
                        
#                         history_key = f"wa_history:{sender_phone}"
#                         chat_history_str = await redis_client.get(history_key) if redis_client else None
#                         chat_history = []
                        
#                         if chat_history_str:
#                             for msg in json.loads(chat_history_str):
#                                 valid_parts = [types.Part.from_text(text=p) for p in msg.get("parts", []) if p]
#                                 if valid_parts: chat_history.append(types.Content(role=msg["role"], parts=valid_parts))

#                         chat_history.append(types.Content(role="user", parts=[types.Part.from_text(text=incoming_text)]))

#                         ai_reply = ""
#                         while True:
#                             response = await gemini_client.aio.models.generate_content(
#                                 model='gemini-2.5-flash',
#                                 contents=chat_history,
#                                 config=types.GenerateContentConfig(system_instruction=WHATSAPP_SYSTEM_PROMPT, tools=whatsapp_tools)
#                             )
                            
#                             if response.text:
#                                 ai_reply = response.text
#                                 break
                            
#                             if response.function_calls:
#                                 chat_history.append(response.candidates[0].content)
#                                 for function_call in response.function_calls:
#                                     func_name = function_call.name
#                                     func_args = function_call.args
#                                     logger.info(f"🛠️ LLM called: {func_name} | args: {func_args}")
                                    
#                                     tool_map = {f.__name__: f for f in whatsapp_tools}
#                                     if func_name in tool_map:
#                                         result = await tool_map[func_name](**func_args)
#                                         tool_result_part = types.Part.from_function_response(name=func_name, response={"result": result})
#                                         chat_history.append(types.Content(role="user", parts=[tool_result_part]))
                        
#                         storable_history = []
#                         for c in chat_history:
#                             text_parts = [p.text for p in c.parts if p.text]
#                             if text_parts: storable_history.append({"role": c.role, "parts": text_parts})
                                
#                         storable_history.append({"role": "model", "parts": [ai_reply]})
#                         if redis_client:
#                             await redis_client.setex(history_key, 86400, json.dumps(storable_history))

#                         clean_phone = sender_phone.replace("+91", "").replace("+", "")
#                         await send_confirmation(clean_phone, ai_reply)
                            
#         return {"status": "success"}
#     except Exception as e:
#         logger.error(f"❌ WhatsApp Webhook Error: {e}")
#         return {"status": "error"}

# if __name__ == "__main__":
#     logger.info("🚀 Starting Standalone WhatsApp Webhook Server on Port 8000...")
#     uvicorn.run("whatsapp_agent:app", host="0.0.0.0", port=8000, reload=True)
import os
import json
from datetime import datetime
import pytz
import redis.asyncio as redis
from dotenv import load_dotenv
from loguru import logger
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from google import genai
from google.genai import types

from tools.notify import send_confirmation, send_interactive_slots
from tools.booking import voice_book_appointment
from tools.availability import check_availability
from tools.followup import verify_followup

load_dotenv(override=True)

# Define this file as a Router instead of a standalone App
router = APIRouter()

gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
redis_client = None

async def ensure_redis_client():
    global redis_client
    if redis_client: return
    try:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        redis_client = redis.from_url(redis_url, decode_responses=True)
        await redis_client.ping()
    except Exception as e:
        redis_client = None

# ==========================================================
# 🤖 WHATSAPP PROMPT & LOGIC
# ==========================================================
ist = pytz.timezone('Asia/Kolkata')
current_time = datetime.now(ist).strftime('%A, %B %d, %Y at %I:%M %p IST')

WHATSAPP_SYSTEM_PROMPT = f"""Role: Mithra Hospital AI Receptionist (WhatsApp).
CURRENT LIVE TIME: {current_time}

CRITICAL BEHAVIOR RULES:
1. ANTI-REPETITION: NEVER repeat a greeting. 
2. SHORT & NATURAL: Keep responses concise. Use emojis naturally.
3. NO PREMATURE DETAILS: NEVER ask for the patient's name or phone number until AFTER they select a specific time slot.

WORKFLOW (Execute strictly ONE step at a time):
- STEP A (Symptoms): Ask what medical problem they are experiencing. STOP and wait.
- STEP B (Follow-up Check): If they ask for a follow-up, use `verify_followup_wa` to check eligibility.
- STEP C (Check Availability): Call `check_availability_wa`. Leave `requested_date` BLANK for "next available".
- STEP D (Offer Menu): The tool will send an interactive menu directly to their WhatsApp. Say ONLY: "I have pulled up the schedule for you! Please tap the menu button above to select your preferred time ☝️"
- STEP E (Get Details): Once they reply with a time, ask EXACTLY: "Great choice! Please provide the patient's name and 10-digit phone number." STOP AND WAIT.
- STEP F (Book): Call `book_appointment_wa` AFTER the user texts their real name and phone number. 
- STEP G (Wrap Up): Output EXACTLY the wrap-up message the tool provides.
"""

@router.get("/whatsapp-webhook")
async def verify_whatsapp_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if mode == "subscribe" and token == os.getenv("WHATSAPP_VERIFY_TOKEN"): 
        return PlainTextResponse(challenge)
    return HTMLResponse(content="Verification token mismatch", status_code=403)

@router.post("/whatsapp-webhook")
async def receive_whatsapp_message(request: Request):
    try:
        body = await request.json()
        if body.get("object") == "whatsapp_business_account":
            for entry in body.get("entry", []):
                for change in entry.get("changes", []):
                    value = change.get("value", {})
                    if "messages" in value:
                        message = value["messages"][0]
                        sender_phone = message.get("from")
                        
                        incoming_text = ""
                        msg_type = message.get("type")
                        message_id = message.get("id")
                        
                        if msg_type == "text": incoming_text = message["text"]["body"]
                        elif msg_type == "interactive":
                            interactive_data = message.get("interactive", {})
                            if interactive_data.get("type") == "list_reply": incoming_text = interactive_data["list_reply"]["title"]
                            elif interactive_data.get("type") == "button_reply": incoming_text = interactive_data["button_reply"]["title"]
                        
                        if not incoming_text: continue
                            
                        if message_id and redis_client:
                            is_duplicate = await redis_client.get(f"processed_msg:{message_id}")
                            if is_duplicate: return {"status": "success"}
                            await redis_client.setex(f"processed_msg:{message_id}", 60, "true")
                            
                        logger.info(f"💬 Incoming WhatsApp from {sender_phone}: {incoming_text}")
                        user_msg_lower = incoming_text.strip().lower()

                        if user_msg_lower in ["hi", "hello", "hey", "start", "menu", "reset"]:
                            if redis_client:
                                await redis_client.delete(f"wa_history:{sender_phone}")
                                await redis_client.delete(f"last_doc_id:{sender_phone}")
                            if user_msg_lower == "reset":
                                clean_phone = sender_phone.replace("+91", "").replace("+", "")
                                await send_confirmation(clean_phone, "🧹 AI Memory wiped! We are starting completely fresh. Say 'Hi'!")
                                return {"status": "success"}
                        
                        class WAParams:
                            def __init__(self): self.result = None
                            async def result_callback(self, result): self.result = result

                        async def check_availability_wa(problem_or_speciality: str, requested_date: str = None):
                            p = WAParams()
                            await check_availability(p, problem_or_speciality, requested_date)
                            result_data = p.result
                            if result_data and result_data.get("status") == "success":
                                slots = result_data.get("all_available_slots", [])
                                doc_id = result_data.get("doctor_id")
                                doc_name = result_data.get("doctor_name")
                                target_date = result_data.get("target_date")
                                if slots and doc_id:
                                    clean_phone = sender_phone.replace("+91", "").replace("+", "")
                                    await send_interactive_slots(clean_phone, doc_name, target_date, slots)
                                    if redis_client: await redis_client.setex(f"last_doc_id:{sender_phone}", 86400, doc_id)
                                    return {"status": "success", "message": "I have sent an interactive menu. Ask them to click it."}
                            return result_data

                        async def verify_followup_wa(phone: str):
                            p = WAParams()
                            await verify_followup(p, phone)
                            return p.result

                        async def book_appointment_wa(patient_name: str, start_time_iso: str, phone: str, reason: str, force_book: bool = False, is_followup: str = "unknown"):
                            p = WAParams()
                            doctor_id = await redis_client.get(f"last_doc_id:{sender_phone}") if redis_client else None
                            if not doctor_id: return {"status": "error", "message": "SYSTEM DIRECTIVE: Ask the user to select a doctor/time slot first."}
                            
                            await voice_book_appointment(p, doctor_id, patient_name, start_time_iso, phone, reason, force_book, is_followup)
                            result_data = p.result
                            if result_data and result_data.get("status") == "success":
                                if not result_data.get("is_followup"): 
                                    return {"status": "success", "message": "Say EXACTLY: 'Your appointment has been tentatively booked! Please click the Razorpay link above to confirm your slot. Note: The payment link expires in 15 minutes.'"}
                                else:
                                    return {"status": "success", "message": "Say EXACTLY: 'Your free 1-week follow-up has been successfully booked! No payment is required. See you then!'"}
                            return result_data

                        whatsapp_tools = [check_availability_wa, verify_followup_wa, book_appointment_wa]
                        
                        history_key = f"wa_history:{sender_phone}"
                        chat_history_str = await redis_client.get(history_key) if redis_client else None
                        chat_history = []
                        
                        if chat_history_str:
                            for msg in json.loads(chat_history_str):
                                valid_parts = [types.Part.from_text(text=p) for p in msg.get("parts", []) if p]
                                if valid_parts: chat_history.append(types.Content(role=msg["role"], parts=valid_parts))

                        chat_history.append(types.Content(role="user", parts=[types.Part.from_text(text=incoming_text)]))

                        ai_reply = ""
                        while True:
                            response = await gemini_client.aio.models.generate_content(
                                model='gemini-2.5-flash',
                                contents=chat_history,
                                config=types.GenerateContentConfig(system_instruction=WHATSAPP_SYSTEM_PROMPT, tools=whatsapp_tools)
                            )
                            if response.text:
                                ai_reply = response.text
                                break
                            
                            if response.function_calls:
                                chat_history.append(response.candidates[0].content)
                                for function_call in response.function_calls:
                                    func_name = function_call.name
                                    func_args = function_call.args
                                    
                                    tool_map = {f.__name__: f for f in whatsapp_tools}
                                    if func_name in tool_map:
                                        result = await tool_map[func_name](**func_args)
                                        tool_result_part = types.Part.from_function_response(name=func_name, response={"result": result})
                                        chat_history.append(types.Content(role="user", parts=[tool_result_part]))
                        
                        storable_history = []
                        for c in chat_history:
                            text_parts = [p.text for p in c.parts if p.text]
                            if text_parts: storable_history.append({"role": c.role, "parts": text_parts})
                                
                        storable_history.append({"role": "model", "parts": [ai_reply]})
                        if redis_client: await redis_client.setex(history_key, 86400, json.dumps(storable_history))

                        clean_phone = sender_phone.replace("+91", "").replace("+", "")
                        await send_confirmation(clean_phone, ai_reply)
                            
        return {"status": "success"}
    except Exception as e:
        logger.error(f"❌ WhatsApp Webhook Error: {e}")
        return {"status": "error"}