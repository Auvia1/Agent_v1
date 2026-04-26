#tools/booking.py
import os
import datetime
import asyncio
import pytz
from loguru import logger
import redis.asyncio as redis
from pipecat.services.llm_service import FunctionCallParams
import difflib
 
# ✅ Only one pool import — from tools.pool, NOT db.connection
from tools.pool import get_pool
from db.queries import get_or_create_patient, book_new_appointment
from tools.payment import generate_payment_link
from tools.notify import send_confirmation
from tools.activity_logger import log_appointment_cancelled
 
 
async def cancel_unpaid_appointment(appointment_id: str):
    """Background task to cancel appointments and payments if not paid within 15 minutes."""
    logger.info(f"⏳ Timer started: Checking if {appointment_id} is paid in 15 minutes...")
    await asyncio.sleep(900) # 15 minutes

    pool = get_pool()  # ✅ reuse singleton, no new pool
    try:
        async with pool.acquire() as conn:
            # 1. Try to cancel the appointment IF it is still pending/unpaid
            # We use RETURNING id to know if it actually updated (meaning it wasn't paid)
            cancel_query = """
                UPDATE appointments
                SET status = 'cancelled', updated_at = NOW()
                WHERE id = $1::uuid AND status = 'pending' AND payment_status = 'unpaid'
                RETURNING id
            """
            cancelled_id = await conn.fetchval(cancel_query, appointment_id)

            # 2. If it was cancelled (meaning it wasn't paid in time), mark payment as 'failed'
            if cancelled_id:
                logger.warning(f"⏳ 15 mins passed. Cancelling unpaid appointment {appointment_id}")

                # Fetch appointment details for logging
                appt_info = await conn.fetchrow("""
                    SELECT a.clinic_id, p.name as patient_name, d.name as doctor_name
                    FROM appointments a
                    JOIN patients p ON a.patient_id = p.id
                    JOIN doctors d ON a.doctor_id = d.id
                    WHERE a.id = $1::uuid
                """, appointment_id)

                # Log the cancellation
                if appt_info:
                    await log_appointment_cancelled(
                        conn,
                        clinic_id=str(appt_info['clinic_id']),
                        appointment_id=appointment_id,
                        patient_name=appt_info['patient_name'],
                        doctor_name=appt_info['doctor_name'],
                        reason="Unpaid after 15 minutes"
                    )

                await conn.execute(
                    "UPDATE payments SET status = 'failed' WHERE appointment_id = $1::uuid",
                    appointment_id
                )
                logger.info(f"🛑 Payment ledger marked as 'failed' for {appointment_id}")
            else:
                logger.info(f"✅ 15 min check: Appointment {appointment_id} was already paid or handled.")

    except Exception as e:
        logger.error(f"❌ Error cancelling unpaid appointment: {e}")
 
 
async def _execute_booking(params: FunctionCallParams, doctor_id: str, patient_name: str, start_time_iso: str, phone: str, reason: str, force_book: bool = False, is_followup: bool = False, is_same_patient: str = "unknown", existing_patient_id: str = None):
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
            await params.result_callback({"status": "error", "message": "SYSTEM DIRECTIVE: Tell the user: 'Another patient just grabbed that exact time slot/session! Shall I find the next available one?'"})
            return
 
        pool = get_pool()  # ✅ reuse singleton
        async with pool.acquire() as conn:
            clinic_id_query = "SELECT clinic_id FROM doctors WHERE id = $1::uuid"
            clinic_id = await conn.fetchval(clinic_id_query, doctor_id)
            
            # 🛑 ADD THIS CHECK: Don't proceed if clinic_id is None
            if not clinic_id:
                logger.error("🚨 Execution aborted: clinic_id is None (Doctor not found).")
                await params.result_callback({"status": "error", "message": "SYSTEM DIRECTIVE: The doctor ID was invalid. Please apologize to the user and ask them to select the time again."})
                return
            
            # ✅ Pass the family logic down to the query
            patient_id = await get_or_create_patient(conn, str(clinic_id), clean_name, clean_phone, is_same_patient, existing_patient_id)
 
        start_dt = datetime.datetime.fromisoformat(start_time_iso)
        end_dt = start_dt + datetime.timedelta(minutes=30)
 
        # 👇 UPDATED: Unpack appt_id, token_number, AND is_slots_needed
        appt_id, token_number, is_slots_needed = await book_new_appointment(
            pool=pool, clinic_id=clinic_id, doctor_id=doctor_id,
            patient_name=clean_name, phone=clean_phone, start_time=start_dt,
            end_time=end_dt, force_book=force_book, patient_id=patient_id,
            reason=reason, is_followup=is_followup
        )
 
        if str(appt_id) == "ALREADY_BOOKED_BY_USER":
            await params.result_callback({"status": "error", "message": "SYSTEM DIRECTIVE: Tell the user they already have an appointment booked for this session/time."})
            return
        elif str(appt_id) == "SLOT_TAKEN":
            await params.result_callback({"status": "error", "message": "SYSTEM DIRECTIVE: Tell the user this slot was just taken and ask them to choose another time."})
            return

        time_display = f"Session starting at {start_dt.strftime('%I:%M %p')}" if not is_slots_needed else f"{start_dt.strftime('%I:%M %p')}"
 
        # 🟢 Logic Branch A: Free Follow-up (Token generated instantly)
        if is_followup:
            token_display = f"\n🔢 *Token Number:* {token_number}" if token_number else ""
            whatsapp_msg = f"🏥 *Mithra Hospitals*\n\nHi {clean_name}, your free 1-week follow-up is CONFIRMED for {time_display} on {start_dt.strftime('%B %d')}.{token_display}\n\nNo payment is required. See you then!"
            await send_confirmation(clean_phone, whatsapp_msg)
            
            await params.result_callback({
                "status": "success", 
                "appointment_id": str(appt_id), 
                "is_followup": True,
                "message": "SYSTEM DIRECTIVE: The booking is complete and the message was sent. Tell the user the booking is confirmed, and then explicitly ask: 'Is there anything else I can help you with today?' Do NOT end the call."
            })
            return
 
        # 🟢 Logic Branch B: Standard Paid Appointment (Token generated AFTER payment)
        consultation_fee = 500
        payment_link = await generate_payment_link(consultation_fee, clean_phone, str(appt_id), clean_name)
 
        if payment_link:
            # 👇 Inform the user about the deferred token
            token_notice = "\n*(Your Token Number will be generated instantly after payment)*" if not is_slots_needed else ""
            whatsapp_msg = f"🏥 *Mithra Hospitals*\n\nHi {clean_name}, your appointment is tentatively booked for {time_display} on {start_dt.strftime('%B %d')}.\n\nPlease pay ₹{consultation_fee} using this link to confirm your slot (Valid for 15 mins): {payment_link}{token_notice}"
            await send_confirmation(clean_phone, whatsapp_msg)
 
        logger.info(f"✅ Standard Appointment held pending payment: {appt_id}")
 
        asyncio.create_task(cancel_unpaid_appointment(str(appt_id)))
 
        await params.result_callback({
            "status": "success", 
            "appointment_id": str(appt_id), 
            "is_followup": False,
            "message": "SYSTEM DIRECTIVE: The booking is tentatively held. Tell the user the payment link was sent via WhatsApp, and then explicitly ask: 'Is there anything else I can help you with today?' Do NOT end the call."
        })
 
    except Exception as e:
        logger.error(f"❌ Exception during booking: {e}")
        await params.result_callback({"status": "error", "message": "SYSTEM DIRECTIVE: Tell the user a system error occurred and to please call the clinic directly."})
    finally:
        await redis_client.close()
 
# ==========================================================
# 🧠 MAIN LLM TOOL ENTRYPOINT (Smart Intercept)
# ==========================================================
async def voice_book_appointment(params: FunctionCallParams, doctor_id: str, patient_name: str, start_time_iso: str, phone: str, reason: str, force_book: bool = False, is_followup: str = "unknown", is_same_patient: str = "unknown"):
    """Gatekeeper — runs safety checks before actual booking."""
    
    is_followup_bool = False
    existing_patient_id = None

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
            
            clinic_id_query = "SELECT clinic_id FROM doctors WHERE id = $1::uuid"
            clinic_id = await conn.fetchval(clinic_id_query, doctor_id)
            
            # 🛑 NEW INTERCEPT 0: Guard against hallucinated/invalid doctor IDs
            if not clinic_id:
                logger.error(f"🚨 Invalid Doctor ID passed by LLM: {doctor_id}")
                await params.result_callback({
                    "status": "error",
                    "message": "SYSTEM DIRECTIVE: Tell the user a system error occurred while finding the doctor. Ask them to please say their preferred time again to retry."
                })
                return # <-- CRITICAL: Stop execution here
            
            # 🔥 NEW INTERCEPT 1: Fetch ALL family members with this phone number!
            patient_check_query = "SELECT id, name FROM patients WHERE phone = $1 AND clinic_id = $2::uuid"
            existing_patients = await conn.fetch(patient_check_query, clean_phone, clinic_id)
            
            if existing_patients:
                best_match_id = None
                best_match_name = None
                highest_score = 0.0
                
                # 1. Apply 70% Fuzzy Match Logic
                for p in existing_patients:
                    db_name = p['name'].lower()
                    input_name = patient_name.lower()
                    
                    # Calculate similarity ratio (0.0 to 1.0)
                    similarity = difflib.SequenceMatcher(None, db_name, input_name).ratio()
                    
                    # Substring fallback (handles "Hari Ram" vs "Hari Ram Varma")
                    is_substring = db_name in input_name or input_name in db_name
                    
                    # 2. Threshold Check (70% or exact substring)
                    if similarity >= 0.70 or is_substring:
                        if similarity > highest_score:
                            highest_score = similarity
                            best_match_id = str(p['id'])
                            best_match_name = p['name']
                
                # 3. Route the Conversation
                if is_same_patient == "unknown":
                    if best_match_id:
                        # We found a strong match (e.g., Hari Ram). Ignore Hemanth. Ask to update.
                        logger.warning(f"🛑 SMART INTERCEPT: High match. DB: {best_match_name}, Input: {patient_name}")
                        await params.result_callback({
                            "status": "warning", 
                            "message": f"SYSTEM DIRECTIVE: Tell the user: 'I see a patient profile for {best_match_name} under this number. Should I update this profile to {patient_name}, or is this a completely new patient?'"
                        })
                        return
                    else:
                        # No matches > 70% (e.g., caller said a totally new name).
                        # Let it pass through silently to create a new profile.
                        pass
                
                elif is_same_patient == "yes":
                    # User confirmed it's them. Pass the ID down so db/queries.py updates the name.
                    existing_patient_id = best_match_id if best_match_id else str(existing_patients[0]['id'])
                
                elif is_same_patient == "no":
                    # User said they are a new patient.
                    existing_patient_id = None
 
            # 2. Intercept 2: Check for existing upcoming appointments
            if not force_book:
                # ✅ FIX: Added p.name as patient_name to the SELECT query
                upcoming_query = """
                    SELECT a.appointment_start, d.name as doctor_name, p.name as patient_name 
                    FROM appointments a
                    JOIN patients p ON a.patient_id = p.id JOIN doctors d ON a.doctor_id = d.id
                    WHERE p.phone = $1 AND a.status IN ('confirmed', 'pending') AND a.deleted_at IS NULL AND a.appointment_start >= NOW()
                    ORDER BY a.appointment_start ASC LIMIT 1
                """
                upcoming_appt = await conn.fetchrow(upcoming_query, clean_phone)
                if upcoming_appt:
                    appt_time = upcoming_appt['appointment_start'].astimezone(pytz.timezone('Asia/Kolkata')).strftime('%b %d at %I:%M %p')
                    doc_name = upcoming_appt['doctor_name']
                    pat_name = upcoming_appt['patient_name'] # ✅ Extracted the name!
                    
                    logger.warning(f"🛑 SMART INTERCEPT: Existing upcoming appointment on {appt_time} for {pat_name}!")
                    
                    # ✅ FIX: Updated the System Directive to include the exact phrasing you want
                    await params.result_callback({
                        "status": "warning", 
                        "message": f"SYSTEM DIRECTIVE: Tell the user: 'I see you already have an appointment booked under the name {pat_name} with {doc_name} on {appt_time}. Do you want to proceed with booking a new one?'"
                    })
                    return
 
            # 3. Intercept 3: Follow-up check
            followup_query = """
                SELECT a.appointment_start, d.name as doctor_name, p.name as patient_name
                FROM appointments a JOIN patients p ON a.patient_id = p.id JOIN doctors d ON a.doctor_id = d.id
                WHERE p.phone = $1 AND a.status = 'confirmed' AND a.deleted_at IS NULL AND a.appointment_start >= NOW() - INTERVAL '7 days' AND a.appointment_start < NOW()
                ORDER BY a.appointment_start DESC LIMIT 1
            """
            has_recent = await conn.fetchrow(followup_query, clean_phone)
 
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
        await params.result_callback({
            "status": "error",
            "message": "SYSTEM DIRECTIVE: Tell the user a system error occurred and to please call the clinic directly."
        })
        return # <-- This ensures we don't proceed to book on error
 
    # 4. Passed all checks → Proceed to actual booking
    await _execute_booking(params, doctor_id, patient_name, start_time_iso, phone, reason, force_book, is_followup_bool, is_same_patient, existing_patient_id)