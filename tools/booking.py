#tools/booking.py
# import os
# import datetime
# import asyncio
# import pytz
# from loguru import logger
# import redis.asyncio as redis
# from pipecat.services.llm_service import FunctionCallParams

# from db.connection import get_pool
# from db.queries import get_or_create_patient, book_new_appointment
# from tools.pool import get_pool
# from tools.payment import generate_payment_link
# from tools.notify import send_confirmation

# async def cancel_unpaid_appointment(appointment_id: str):
#     """Background task to cancel appointments if not paid within 15 minutes."""
#     logger.info(f"⏳ Timer started: Checking if {appointment_id} is paid in 15 minutes...")
#     await asyncio.sleep(900)
#     pool = get_pool()
#     query = "UPDATE appointments SET status = 'cancelled', updated_at = NOW() WHERE id = $1::uuid AND status = 'pending' AND payment_status = 'unpaid'"
#     try:
#         async with pool.acquire() as conn:
#             await conn.execute(query, appointment_id)
#     except Exception as e:
#         logger.error(f"Error cancelling unpaid appointment: {e}")

# async def _execute_booking(params: FunctionCallParams, doctor_id: str, patient_name: str, start_time_iso: str, phone: str, reason: str, force_book: bool = False, is_followup: bool = False):
#     """Internal helper that performs the actual database insertion, Redis locking, and Meta notifications."""
#     clean_name = patient_name.strip()
#     clean_phone = "".join(filter(str.isdigit, str(phone)))
#     if clean_phone.startswith("91") and len(clean_phone) == 12:
#         clean_phone = clean_phone[2:]

#     logger.info(f"📅 Executing booking | Name: {clean_name} | Phone: {clean_phone}")

#     redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
#     redis_client = redis.from_url(redis_url, decode_responses=True)
#     lock_key = f"booking_lock:doctor_{doctor_id}:time_{start_time_iso}:phone_{clean_phone}"
    
#     try:
#         # Prevent double-booking race conditions
#         lock_acquired = await redis_client.set(lock_key, "locked", nx=True, ex=10)
#         if not lock_acquired:
#             await params.result_callback({"status": "error", "message": "SYSTEM DIRECTIVE: Tell the user: 'Another patient just grabbed that exact time slot! Shall I find the next available one?'"})
#             return

#         pool = get_pool()
#         async with pool.acquire() as conn:
#             clinic_id_query = "SELECT clinic_id FROM doctors WHERE id = $1::uuid"
#             clinic_id = await conn.fetchval(clinic_id_query, doctor_id)
#             patient_id = await get_or_create_patient(conn, str(clinic_id), clean_name, clean_phone)

#         start_dt = datetime.datetime.fromisoformat(start_time_iso)
#         end_dt = start_dt + datetime.timedelta(minutes=30)
        
#         appt_id = await book_new_appointment(
#             pool=pool, clinic_id=clinic_id, doctor_id=doctor_id, 
#             patient_name=clean_name, phone=clean_phone, start_time=start_dt,
#             end_time=end_dt, force_book=force_book, patient_id=patient_id,
#             reason=reason, is_followup=is_followup
#         )
        
#         if str(appt_id) == "ALREADY_BOOKED_BY_USER":
#             await params.result_callback({"status": "error", "message": "SYSTEM DIRECTIVE: Tell the user they already have an appointment booked at this time."})
#             return
#         elif str(appt_id) == "SLOT_TAKEN":
#             await params.result_callback({"status": "error", "message": "SYSTEM DIRECTIVE: Tell the user this slot was just taken and ask them to choose another time."})
#             return

#         # 🟢 Logic Branch A: Free Follow-up
#         if is_followup:
#             whatsapp_msg = f"🏥 *Mithra Hospitals*\n\nHi {clean_name}, your free 1-week follow-up is CONFIRMED for {start_dt.strftime('%I:%M %p')} on {start_dt.strftime('%B %d')}.\n\nNo payment is required. See you then!"
#             await send_confirmation(clean_phone, whatsapp_msg)
                
#             logger.info(f"✅ Free Follow-up booked: {appt_id}")
#             await params.result_callback({"status": "success", "appointment_id": str(appt_id), "is_followup": True})
#             return

#         # 🟢 Logic Branch B: Standard Paid Appointment
#         consultation_fee = 500
#         payment_link = await generate_payment_link(consultation_fee, clean_phone, str(appt_id), clean_name)

#         if payment_link:
#             whatsapp_msg = f"🏥 *Mithra Hospitals*\n\nHi {clean_name}, your appointment is tentatively booked for {start_dt.strftime('%I:%M %p')} on {start_dt.strftime('%B %d')}.\n\nPlease pay ₹{consultation_fee} using this link to confirm your slot (Valid for 15 mins): {payment_link}"
#             await send_confirmation(clean_phone, whatsapp_msg)

#         logger.info(f"✅ Standard Appointment booked: {appt_id}")
        
#         # Fire the 15-minute cancellation timer in the background
#         asyncio.create_task(cancel_unpaid_appointment(str(appt_id)))
        
#         await params.result_callback({"status": "success", "appointment_id": str(appt_id), "is_followup": False})
        
#     except Exception as e:
#         logger.error(f"❌ Exception during booking: {e}")
#         await params.result_callback({"status": "error", "message": "SYSTEM DIRECTIVE: Tell the user a system error occurred and to please call the clinic directly."})
#     finally:
#         await redis_client.close()

# # ==========================================================
# # 🧠 MAIN LLM TOOL ENTRYPOINT (Smart Intercept)
# # ==========================================================
# async def voice_book_appointment(params: FunctionCallParams, doctor_id: str, patient_name: str, start_time_iso: str, phone: str, reason: str, force_book: bool = False, is_followup: str = "unknown"):
#     """This function acts as the gatekeeper, intercepting the LLM's booking attempt to verify safety checks."""
#     clean_phone = "".join(filter(str.isdigit, str(phone)))
#     if clean_phone.startswith("91") and len(clean_phone) == 12: 
#         clean_phone = clean_phone[2:]
    
#     # 1. Phone length check FIRST to avoid unnecessary DB calls
#     if len(clean_phone) != 10:
#         logger.warning(f"⚠️ Blocked booking: Invalid phone length {len(clean_phone)} ({clean_phone})")
#         await params.result_callback({
#             "status": "error", 
#             "message": f"SYSTEM DIRECTIVE: Tell the user EXACTLY: 'You provided a {len(clean_phone)}-digit number. I need exactly 10 digits. Could you please repeat your 10-digit phone number?'"
#         })
#         return

#     start_time_iso = start_time_iso.replace("Z", "+05:30") if "Z" in start_time_iso else start_time_iso + "+05:30" if "+" not in start_time_iso else start_time_iso
    
#     try:
#         pool = get_pool()
#         async with pool.acquire() as conn:
            
#             # 2. Intercept: Check for existing upcoming appointments
#             if not force_book:
#                 upcoming_query = """
#                     SELECT a.appointment_start, d.name as doctor_name FROM appointments a
#                     JOIN patients p ON a.patient_id = p.id JOIN doctors d ON a.doctor_id = d.id
#                     WHERE p.phone = $1 AND a.status IN ('confirmed', 'pending') AND a.deleted_at IS NULL AND a.appointment_start >= NOW()
#                     ORDER BY a.appointment_start ASC LIMIT 1
#                 """
#                 upcoming_appt = await conn.fetchrow(upcoming_query, clean_phone)
#                 if upcoming_appt:
#                     appt_time = upcoming_appt['appointment_start'].astimezone(pytz.timezone('Asia/Kolkata')).strftime('%b %d at %I:%M %p')
#                     doc_name = upcoming_appt['doctor_name']
#                     logger.warning(f"🛑 SMART INTERCEPT: DB found existing upcoming appointment for this phone on {appt_time}!")
#                     await params.result_callback({"status": "warning", "message": f"SYSTEM DIRECTIVE: Tell the user: 'I see you already have an upcoming appointment on {appt_time} with {doc_name}. Do you want to proceed with booking an additional new appointment?'"})
#                     return

#             # 3. Intercept: Follow-up check (if the LLM didn't explicitly specify 'yes' or 'no')
#             followup_query = """
#                 SELECT a.appointment_start, d.name as doctor_name, p.name as patient_name 
#                 FROM appointments a JOIN patients p ON a.patient_id = p.id JOIN doctors d ON a.doctor_id = d.id
#                 WHERE p.phone = $1 AND a.status = 'confirmed' AND a.deleted_at IS NULL AND a.appointment_start >= NOW() - INTERVAL '7 days' AND a.appointment_start < NOW()
#                 ORDER BY a.appointment_start DESC LIMIT 1
#             """
#             has_recent = await conn.fetchrow(followup_query, clean_phone)

#             is_followup_bool = False
#             if is_followup == "unknown":
#                 if has_recent:
#                     recent_date = has_recent['appointment_start'].astimezone(pytz.timezone('Asia/Kolkata')).strftime('%B %d')
#                     recent_patient = has_recent['patient_name']
#                     recent_doc = has_recent['doctor_name']
#                     logger.warning(f"🛑 SMART INTERCEPT: Prompting for free follow-up confirmation.")
#                     await params.result_callback({"status": "warning", "message": f"SYSTEM DIRECTIVE: Tell the user: 'I see {recent_patient} had a confirmed appointment with {recent_doc} on {recent_date}. Is this a free 1-week follow-up for that visit, or a completely new medical problem?'"})
#                     return
#             elif is_followup == "yes":
#                 if has_recent: 
#                     is_followup_bool = True
#                 else:
#                     await params.result_callback({"status": "warning", "message": "SYSTEM DIRECTIVE: Tell the user: 'Your free 1-week follow-up period has expired, or no previous record was found. I will need to book this as a new paid consultation. Shall I proceed?'"})
#                     return

#     except Exception as e:
#         logger.warning(f"⚠️ DB Intercept Error: {e}")

#     # 4. Passed all checks -> Proceed to actual booking
#     await _execute_booking(params, doctor_id, patient_name, start_time_iso, phone, reason, force_book, is_followup_bool)



import os
import datetime
import asyncio
import pytz
from loguru import logger
import redis.asyncio as redis
from pipecat.services.llm_service import FunctionCallParams
 
# ✅ Only one pool import — from tools.pool, NOT db.connection
from tools.pool import get_pool
from db.queries import get_or_create_patient, book_new_appointment
from tools.payment import generate_payment_link
from tools.notify import send_confirmation
 
 
async def cancel_unpaid_appointment(appointment_id: str):
    """Background task to cancel appointments if not paid within 15 minutes."""
    logger.info(f"⏳ Timer started: Checking if {appointment_id} is paid in 15 minutes...")
    await asyncio.sleep(900)
    pool = get_pool()  # ✅ reuse singleton, no new pool
    query = "UPDATE appointments SET status = 'cancelled', updated_at = NOW() WHERE id = $1::uuid AND status = 'pending' AND payment_status = 'unpaid'"
    try:
        async with pool.acquire() as conn:
            await conn.execute(query, appointment_id)
    except Exception as e:
        logger.error(f"Error cancelling unpaid appointment: {e}")
 
 
async def _execute_booking(params: FunctionCallParams, doctor_id: str, patient_name: str, start_time_iso: str, phone: str, reason: str, force_book: bool = False, is_followup: bool = False):
    """Internal helper that performs the actual database insertion, Redis locking, and Meta notifications."""
    clean_name = patient_name.strip()
    clean_phone = "".join(filter(str.isdigit, str(phone)))
    if clean_phone.startswith("91") and len(clean_phone) == 12:
        clean_phone = clean_phone[2:]
 
    logger.info(f"📅 Executing booking | Name: {clean_name} | Phone: {clean_phone}")
 
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    redis_client = redis.from_url(redis_url, decode_responses=True)
    lock_key = f"booking_lock:doctor_{doctor_id}:time_{start_time_iso}:phone_{clean_phone}"
 
    try:
        # Prevent double-booking race conditions
        lock_acquired = await redis_client.set(lock_key, "locked", nx=True, ex=10)
        if not lock_acquired:
            await params.result_callback({"status": "error", "message": "SYSTEM DIRECTIVE: Tell the user: 'Another patient just grabbed that exact time slot! Shall I find the next available one?'"})
            return
 
        pool = get_pool()  # ✅ reuse singleton
        async with pool.acquire() as conn:
            clinic_id_query = "SELECT clinic_id FROM doctors WHERE id = $1::uuid"
            clinic_id = await conn.fetchval(clinic_id_query, doctor_id)
            patient_id = await get_or_create_patient(conn, str(clinic_id), clean_name, clean_phone)
 
        start_dt = datetime.datetime.fromisoformat(start_time_iso)
        end_dt = start_dt + datetime.timedelta(minutes=30)
 
        appt_id = await book_new_appointment(
            pool=pool, clinic_id=clinic_id, doctor_id=doctor_id,
            patient_name=clean_name, phone=clean_phone, start_time=start_dt,
            end_time=end_dt, force_book=force_book, patient_id=patient_id,
            reason=reason, is_followup=is_followup
        )
 
        if str(appt_id) == "ALREADY_BOOKED_BY_USER":
            await params.result_callback({"status": "error", "message": "SYSTEM DIRECTIVE: Tell the user they already have an appointment booked at this time."})
            return
        elif str(appt_id) == "SLOT_TAKEN":
            await params.result_callback({"status": "error", "message": "SYSTEM DIRECTIVE: Tell the user this slot was just taken and ask them to choose another time."})
            return
 
        # 🟢 Logic Branch A: Free Follow-up
        if is_followup:
            whatsapp_msg = f"🏥 *Mithra Hospitals*\n\nHi {clean_name}, your free 1-week follow-up is CONFIRMED for {start_dt.strftime('%I:%M %p')} on {start_dt.strftime('%B %d')}.\n\nNo payment is required. See you then!"
            await send_confirmation(clean_phone, whatsapp_msg)
            logger.info(f"✅ Free Follow-up booked: {appt_id}")
            await params.result_callback({"status": "success", "appointment_id": str(appt_id), "is_followup": True})
            return
 
        # 🟢 Logic Branch B: Standard Paid Appointment
        consultation_fee = 500
        payment_link = await generate_payment_link(consultation_fee, clean_phone, str(appt_id), clean_name)
 
        if payment_link:
            whatsapp_msg = f"🏥 *Mithra Hospitals*\n\nHi {clean_name}, your appointment is tentatively booked for {start_dt.strftime('%I:%M %p')} on {start_dt.strftime('%B %d')}.\n\nPlease pay ₹{consultation_fee} using this link to confirm your slot (Valid for 15 mins): {payment_link}"
            await send_confirmation(clean_phone, whatsapp_msg)
 
        logger.info(f"✅ Standard Appointment booked: {appt_id}")
 
        # Fire the 15-minute cancellation timer in the background
        asyncio.create_task(cancel_unpaid_appointment(str(appt_id)))
 
        await params.result_callback({"status": "success", "appointment_id": str(appt_id), "is_followup": False})
 
    except Exception as e:
        logger.error(f"❌ Exception during booking: {e}")
        await params.result_callback({"status": "error", "message": "SYSTEM DIRECTIVE: Tell the user a system error occurred and to please call the clinic directly."})
    finally:
        await redis_client.close()
 
 
# ==========================================================
# 🧠 MAIN LLM TOOL ENTRYPOINT (Smart Intercept)
# ==========================================================
async def voice_book_appointment(params: FunctionCallParams, doctor_id: str, patient_name: str, start_time_iso: str, phone: str, reason: str, force_book: bool = False, is_followup: str = "unknown"):
    """Gatekeeper — runs safety checks before actual booking."""
    clean_phone = "".join(filter(str.isdigit, str(phone)))
    if clean_phone.startswith("91") and len(clean_phone) == 12:
        clean_phone = clean_phone[2:]
 
    # 1. Phone length check FIRST to avoid unnecessary DB calls
    if len(clean_phone) != 10:
        logger.warning(f"⚠️ Blocked booking: Invalid phone length {len(clean_phone)} ({clean_phone})")
        await params.result_callback({
            "status": "error",
            "message": f"SYSTEM DIRECTIVE: Tell the user EXACTLY: 'You provided a {len(clean_phone)}-digit number. I need exactly 10 digits. Could you please repeat your 10-digit phone number?'"
        })
        return
 
    start_time_iso = start_time_iso.replace("Z", "+05:30") if "Z" in start_time_iso else start_time_iso + "+05:30" if "+" not in start_time_iso else start_time_iso
 
    try:
        pool = get_pool()  # ✅ reuse singleton
        async with pool.acquire() as conn:
 
            # 2. Intercept: Check for existing upcoming appointments
            if not force_book:
                upcoming_query = """
                    SELECT a.appointment_start, d.name as doctor_name FROM appointments a
                    JOIN patients p ON a.patient_id = p.id JOIN doctors d ON a.doctor_id = d.id
                    WHERE p.phone = $1 AND a.status IN ('confirmed', 'pending') AND a.deleted_at IS NULL AND a.appointment_start >= NOW()
                    ORDER BY a.appointment_start ASC LIMIT 1
                """
                upcoming_appt = await conn.fetchrow(upcoming_query, clean_phone)
                if upcoming_appt:
                    appt_time = upcoming_appt['appointment_start'].astimezone(pytz.timezone('Asia/Kolkata')).strftime('%b %d at %I:%M %p')
                    doc_name = upcoming_appt['doctor_name']
                    logger.warning(f"🛑 SMART INTERCEPT: Existing upcoming appointment on {appt_time}!")
                    await params.result_callback({"status": "warning", "message": f"SYSTEM DIRECTIVE: Tell the user: 'I see you already have an upcoming appointment on {appt_time} with {doc_name}. Do you want to proceed with booking an additional new appointment?'"})
                    return
 
            # 3. Intercept: Follow-up check
            followup_query = """
                SELECT a.appointment_start, d.name as doctor_name, p.name as patient_name
                FROM appointments a JOIN patients p ON a.patient_id = p.id JOIN doctors d ON a.doctor_id = d.id
                WHERE p.phone = $1 AND a.status = 'confirmed' AND a.deleted_at IS NULL AND a.appointment_start >= NOW() - INTERVAL '7 days' AND a.appointment_start < NOW()
                ORDER BY a.appointment_start DESC LIMIT 1
            """
            has_recent = await conn.fetchrow(followup_query, clean_phone)
 
            is_followup_bool = False
            if is_followup == "unknown":
                if has_recent:
                    recent_date = has_recent['appointment_start'].astimezone(pytz.timezone('Asia/Kolkata')).strftime('%B %d')
                    recent_patient = has_recent['patient_name']
                    recent_doc = has_recent['doctor_name']
                    logger.warning(f"🛑 SMART INTERCEPT: Prompting for free follow-up confirmation.")
                    await params.result_callback({"status": "warning", "message": f"SYSTEM DIRECTIVE: Tell the user: 'I see {recent_patient} had a confirmed appointment with {recent_doc} on {recent_date}. Is this a free 1-week follow-up for that visit, or a completely new medical problem?'"})
                    return
            elif is_followup == "yes":
                if has_recent:
                    is_followup_bool = True
                else:
                    await params.result_callback({"status": "warning", "message": "SYSTEM DIRECTIVE: Tell the user: 'Your free 1-week follow-up period has expired, or no previous record was found. I will need to book this as a new paid consultation. Shall I proceed?'"})
                    return
 
    except Exception as e:
        logger.warning(f"⚠️ DB Intercept Error: {e}")
 
    # 4. Passed all checks → Proceed to actual booking
    await _execute_booking(params, doctor_id, patient_name, start_time_iso, phone, reason, force_book, is_followup_bool)