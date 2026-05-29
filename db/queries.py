# #db/queries.py
# from loguru import logger

# async def cleanup_expired_pending_appointments(pool):
#     """Sweeps the DB for pending, unpaid appointments older than 15 minutes and cancels them."""
#     query = """
#         UPDATE appointments 
#         SET status = 'cancelled', updated_at = NOW() 
#         WHERE status = 'pending' 
#           AND payment_status = 'unpaid'
#           AND created_at <= NOW() - INTERVAL '15 minutes'
#     """
#     try:
#         async with pool.acquire() as conn:
#             result = await conn.execute(query)
#             logger.info(f"🧹 Startup Sweep: Cleared expired pending appointments.")
#     except Exception as e:
#         logger.error(f"❌ Failed to cleanup expired appointments: {e}")

# async def get_or_create_patient(conn, clinic_id: str, patient_name: str, phone: str, is_same_patient: str = "unknown", existing_patient_id: str = None) -> str:
#     clean_name = patient_name.strip()
#     clean_phone = phone.strip()
    
#     # 1. If we already know the existing ID (from LLM family intercept), use it
#     if existing_patient_id:
#         update_query = "UPDATE patients SET name = $1, updated_at = NOW() WHERE id = $2::uuid RETURNING id"
#         await conn.fetchval(update_query, clean_name, existing_patient_id)
#         logger.info(f"🔄 Updated existing patient {existing_patient_id} to name: {clean_name}")
#         return str(existing_patient_id)

#     # 2. Check if a patient with this phone already exists in the DB
#     check_query = "SELECT id FROM patients WHERE phone = $1 AND clinic_id = $2::uuid ORDER BY created_at DESC LIMIT 1"
#     existing_id = await conn.fetchval(check_query, clean_phone, clinic_id)

#     # 3. Handle based on what we found
#     if existing_id and is_same_patient != "no":
#         # If we found a profile, and the user didn't explicitly say "I am a new family member",
#         # we will safely reuse the existing profile and update the name.
#         update_query = "UPDATE patients SET name = $1, updated_at = NOW() WHERE id = $2::uuid RETURNING id"
#         await conn.fetchval(update_query, clean_name, existing_id)
#         logger.info(f"🔄 Reused existing patient {existing_id} (Name updated to {clean_name})")
#         return str(existing_id)
        
#     # 4. Create new profile (Works perfectly now that the UNIQUE constraint is dropped)
#     insert_query = "INSERT INTO patients (clinic_id, name, phone) VALUES ($1::uuid, $2, $3) RETURNING id"
#     try:
#         new_id = await conn.fetchval(insert_query, clinic_id, clean_name, clean_phone)
#         logger.info(f"🆕 Created NEW patient profile: {clean_name} ({clean_phone})")
#         return str(new_id)
#     except Exception as e:
#         # Ultimate fallback just in case the SQL command wasn't run
#         if "unique constraint" in str(e).lower() and existing_id:
#             logger.warning(f"⚠️ Unique constraint hit for {clean_phone}. Safely falling back to existing ID.")
#             return str(existing_id)
#         logger.error(f"❌ Failed to get or create patient: {e}")
#         raise

# async def get_clinic_id(pool):
#     # Hardcoded to "nikhil's clinic" for demo/testing token logic
#     return "ecef6c1d-83cc-4dcb-8e17-e6a8845965ee"

# async def book_new_appointment(pool, clinic_id, doctor_id, patient_name, phone, start_time, end_time, force_book=False, patient_id=None, reason=None, is_followup=False, is_same_patient="unknown", existing_patient_id=None):
#     """Inserts the appointment. Defers token generation for paid appointments until payment success."""
#     async with pool.acquire() as conn:
#         cleanup_query = "UPDATE appointments SET status = 'cancelled', updated_at = NOW() WHERE status = 'pending' AND payment_status = 'unpaid' AND created_at < NOW() - INTERVAL '15 minutes'"
#         await conn.execute(cleanup_query)

#         resolved_patient_id = patient_id or await get_or_create_patient(conn, clinic_id, patient_name, phone, is_same_patient, existing_patient_id)

#         settings = await conn.fetchrow("SELECT is_slots_needed FROM clinic_settings WHERE clinic_id = $1::uuid", clinic_id)
#         is_slots_needed = settings['is_slots_needed'] if settings else False

#         token_number = None
#         status = 'confirmed' if is_followup else 'pending'
#         payment_status = 'paid' if is_followup else 'unpaid'
#         payment_amount = 0.00 if is_followup else 500.00

#         if is_slots_needed:
#             # 🟢 SLOT-BASED LOGIC
#             check_query = "SELECT patient_id FROM appointments WHERE doctor_id = $1::uuid AND appointment_start = $2 AND deleted_at IS NULL AND (status = 'confirmed' OR (status = 'pending' AND created_at >= NOW() - INTERVAL '15 minutes'))"
#             existing_appt_patient = await conn.fetchval(check_query, doctor_id, start_time)
#             if existing_appt_patient:
#                 return ("ALREADY_BOOKED_BY_USER", None, is_slots_needed) if str(existing_appt_patient) == str(resolved_patient_id) else ("SLOT_TAKEN", None, is_slots_needed)
#         else:
#             # 🔵 TOKEN-BASED LOGIC
#             check_user_query = "SELECT id FROM appointments WHERE doctor_id = $1::uuid AND patient_id = $2::uuid AND appointment_start = $3 AND deleted_at IS NULL AND status IN ('confirmed', 'pending')"
#             if await conn.fetchval(check_user_query, doctor_id, resolved_patient_id, start_time):
#                 return "ALREADY_BOOKED_BY_USER", None, is_slots_needed
                
#             # Check max capacity for this specific shift
#             capacity_query = "SELECT max_appointments_per_slot FROM slots_for_token_system WHERE doctor_id = $1::uuid AND start_time = ($2 AT TIME ZONE 'Asia/Kolkata')::time AND status = 'open' AND deleted_at IS NULL"
#             max_capacity = await conn.fetchval(capacity_query, doctor_id, start_time)
#             if not max_capacity: return "SLOT_TAKEN", None, is_slots_needed

#             # 👇 FIXED: Count current bookings for this exact Date AND Time Shift
#             count_query = """
#                 SELECT COUNT(id) FROM appointments 
#                 WHERE doctor_id = $1::uuid 
#                   AND DATE(appointment_start AT TIME ZONE 'Asia/Kolkata') = DATE($2 AT TIME ZONE 'Asia/Kolkata') 
#                   AND (appointment_start AT TIME ZONE 'Asia/Kolkata')::time = ($2 AT TIME ZONE 'Asia/Kolkata')::time 
#                   AND deleted_at IS NULL AND status IN ('confirmed', 'pending')
#             """
#             if await conn.fetchval(count_query, doctor_id, start_time) >= max_capacity:
#                 return "SLOT_TAKEN", None, is_slots_needed
            
#             # 👇 FIXED: Isolate token counter to this exact Date AND Time Shift
#             if status == 'confirmed':
#                 token_query = """
#                     SELECT COALESCE(MAX(token_number), 0) + 1 
#                     FROM appointments 
#                     WHERE doctor_id = $1::uuid 
#                       AND DATE(appointment_start AT TIME ZONE 'Asia/Kolkata') = DATE($2 AT TIME ZONE 'Asia/Kolkata') 
#                       AND (appointment_start AT TIME ZONE 'Asia/Kolkata')::time = ($2 AT TIME ZONE 'Asia/Kolkata')::time 
#                       AND deleted_at IS NULL AND token_number IS NOT NULL
#                 """
#                 token_number = await conn.fetchval(token_query, doctor_id, start_time)

#         insert_query = """
#             INSERT INTO appointments (clinic_id, patient_id, doctor_id, appointment_start, appointment_end, status, reason, payment_status, payment_amount, token_number)
#             VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6::appointment_status, $7, $8, $9, $10) RETURNING id
#         """
#         appt_id = await conn.fetchval(insert_query, clinic_id, resolved_patient_id, doctor_id, start_time, end_time, status, reason, payment_status, payment_amount, token_number)
        
#         return appt_id, token_number, is_slots_needed

# db/queries.py
from loguru import logger
import datetime

async def cleanup_expired_pending_appointments(pool):
    """Sweeps the DB for pending, unpaid appointments older than 15 minutes and cancels them."""
    query = """
        UPDATE appointments 
        SET status = 'cancelled', updated_at = NOW() 
        WHERE status = 'pending' 
          AND payment_status = 'unpaid'
          AND created_at <= NOW() - INTERVAL '15 minutes'
    """
    try:
        async with pool.acquire() as conn:
            await conn.execute(query)
            logger.info(f"🧹 Startup Sweep: Cleared expired pending appointments.")
    except Exception as e:
        logger.error(f"❌ Failed to cleanup expired appointments: {e}")

async def get_or_create_patient(conn, clinic_id: str, patient_name: str, phone: str, is_same_patient: str = "unknown", existing_patient_id: str = None) -> str:
    clean_name = patient_name.strip()
    clean_phone = phone.strip()
    
    # 1. If we already know the existing ID (from LLM family intercept), use it
    if existing_patient_id:
        update_query = "UPDATE patients SET name = $1, updated_at = NOW() WHERE id = $2::uuid RETURNING id"
        await conn.fetchval(update_query, clean_name, existing_patient_id)
        logger.info(f"🔄 Updated existing patient {existing_patient_id} to name: {clean_name}")
        return str(existing_patient_id)

    # 2. Check if a patient with this phone already exists in the DB
    check_query = "SELECT id FROM patients WHERE phone = $1 AND clinic_id = $2::uuid ORDER BY created_at DESC LIMIT 1"
    existing_id = await conn.fetchval(check_query, clean_phone, clinic_id)

    # 3. Handle based on what we found
    if existing_id and is_same_patient != "no":
        # If we found a profile, and the user didn't explicitly say "I am a new family member",
        # we will safely reuse the existing profile and update the name.
        update_query = "UPDATE patients SET name = $1, updated_at = NOW() WHERE id = $2::uuid RETURNING id"
        await conn.fetchval(update_query, clean_name, existing_id)
        logger.info(f"🔄 Reused existing patient {existing_id} (Name updated to {clean_name})")
        return str(existing_id)
        
    # 4. Create new profile
    insert_query = "INSERT INTO patients (clinic_id, name, phone) VALUES ($1::uuid, $2, $3) RETURNING id"
    try:
        new_id = await conn.fetchval(insert_query, clinic_id, clean_name, clean_phone)
        logger.info(f"🆕 Created NEW patient profile: {clean_name} ({clean_phone})")
        return str(new_id)
    except Exception as e:
        if "unique constraint" in str(e).lower() and existing_id:
            logger.warning(f"⚠️ Unique constraint hit for {clean_phone}. Safely falling back to existing ID.")
            return str(existing_id)
        logger.error(f"❌ Failed to get or create patient: {e}")
        raise

async def get_clinic_id(pool):
    # Hardcoded for demo/testing
    return "2cdd3c18-9537-4efe-8ba6-4e215aa4a2c8"

async def book_new_appointment(pool, clinic_id, doctor_id, patient_name, phone, start_time, end_time, force_book=False, patient_id=None, reason=None, is_followup=False, is_same_patient="unknown", existing_patient_id=None):
    """Inserts the appointment, enforcing capacities dynamically based on DB schema."""
    async with pool.acquire() as conn:
        cleanup_query = "UPDATE appointments SET status = 'cancelled', updated_at = NOW() WHERE status = 'pending' AND payment_status = 'unpaid' AND created_at < NOW() - INTERVAL '15 minutes'"
        await conn.execute(cleanup_query)

        resolved_patient_id = patient_id or await get_or_create_patient(conn, clinic_id, patient_name, phone, is_same_patient, existing_patient_id)

        settings = await conn.fetchrow("SELECT is_slots_needed FROM clinic_settings WHERE clinic_id = $1::uuid", clinic_id)
        is_slots_needed = settings['is_slots_needed'] if settings else False

        token_number = None
        status = 'confirmed' if is_followup else 'pending'
        payment_status = 'paid' if is_followup else 'unpaid'
        payment_amount = 0.00 if is_followup else 500.00

        if is_slots_needed:
            # 🟢 SLOT-BASED LOGIC (Strict 1-on-1 Overlap Protection)
            if not force_book:
                check_query = """
                    SELECT patient_id FROM appointments 
                    WHERE doctor_id = $1::uuid AND deleted_at IS NULL AND status IN ('confirmed', 'pending')
                    AND (appointment_start < $3 AND appointment_end > $2)
                """
                existing_appt_patient = await conn.fetchval(check_query, doctor_id, start_time, end_time)
                if existing_appt_patient:
                    return ("ALREADY_BOOKED_BY_USER", None, is_slots_needed) if str(existing_appt_patient) == str(resolved_patient_id) else ("SLOT_TAKEN", None, is_slots_needed)
        else:
            # 🔵 TOKEN-BASED LOGIC (Session Capacity Protection)
            if not force_book:
                check_user_query = "SELECT id FROM appointments WHERE doctor_id = $1::uuid AND patient_id = $2::uuid AND appointment_start = $3 AND deleted_at IS NULL AND status IN ('confirmed', 'pending')"
                if await conn.fetchval(check_user_query, doctor_id, resolved_patient_id, start_time):
                    return "ALREADY_BOOKED_BY_USER", None, is_slots_needed
                    
                # Fetch dynamically assigned max capacity from DB
                capacity_query = """
                    SELECT max_appointments_per_slot 
                    FROM slots_for_token_system 
                    WHERE doctor_id = $1::uuid 
                      AND start_time = ($2 AT TIME ZONE 'Asia/Kolkata')::time 
                      AND status = 'open' AND deleted_at IS NULL
                """
                max_capacity = await conn.fetchval(capacity_query, doctor_id, start_time)
                if not max_capacity: 
                    logger.warning(f"No open token session found for {start_time}")
                    return "SLOT_TAKEN", None, is_slots_needed

                # Count bookings for this exact Date AND Time Shift
                count_query = """
                    SELECT COUNT(id) FROM appointments 
                    WHERE doctor_id = $1::uuid 
                      AND DATE(appointment_start AT TIME ZONE 'Asia/Kolkata') = DATE($2 AT TIME ZONE 'Asia/Kolkata') 
                      AND (appointment_start AT TIME ZONE 'Asia/Kolkata')::time = ($2 AT TIME ZONE 'Asia/Kolkata')::time 
                      AND deleted_at IS NULL AND status IN ('confirmed', 'pending')
                """
                current_bookings = await conn.fetchval(count_query, doctor_id, start_time)
                
                if current_bookings >= max_capacity:
                    logger.warning(f"Token session full: {current_bookings}/{max_capacity}")
                    return "SLOT_TAKEN", None, is_slots_needed
                
                # Assign sequential token number for this shift
                token_query = """
                    SELECT COALESCE(MAX(token_number), 0) + 1 
                    FROM appointments 
                    WHERE doctor_id = $1::uuid 
                      AND DATE(appointment_start AT TIME ZONE 'Asia/Kolkata') = DATE($2 AT TIME ZONE 'Asia/Kolkata') 
                      AND (appointment_start AT TIME ZONE 'Asia/Kolkata')::time = ($2 AT TIME ZONE 'Asia/Kolkata')::time 
                      AND deleted_at IS NULL AND token_number IS NOT NULL
                """
                token_number = await conn.fetchval(token_query, doctor_id, start_time)

        # 🚀 Final Database Insertion
        insert_query = """
            INSERT INTO appointments (clinic_id, patient_id, doctor_id, appointment_start, appointment_end, status, reason, payment_status, payment_amount, token_number)
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6::appointment_status, $7, $8, $9, $10) RETURNING id
        """
        appt_id = await conn.fetchval(insert_query, clinic_id, resolved_patient_id, doctor_id, start_time, end_time, status, reason, payment_status, payment_amount, token_number)
        
        logger.info(f"✅ Created new appointment {appt_id} for {patient_name}. Token: {token_number}")
        return appt_id, token_number, is_slots_needed