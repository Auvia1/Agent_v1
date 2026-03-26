#db/queries.py
from loguru import logger

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
            result = await conn.execute(query)
            logger.info(f"🧹 Startup Sweep: Cleared expired pending appointments.")
    except Exception as e:
        logger.error(f"❌ Failed to cleanup expired appointments: {e}")

async def get_or_create_patient(conn, clinic_id: str, patient_name: str, phone: str, is_same_patient: str = "unknown", existing_patient_id: str = None) -> str:
    clean_name = patient_name.strip()
    clean_phone = phone.strip()
    
    # 🟢 Scenario A: User confirmed they are the same person (Update Name)
    if is_same_patient == "yes" and existing_patient_id:
        update_query = "UPDATE patients SET name = $1, updated_at = NOW() WHERE id = $2::uuid RETURNING id"
        await conn.fetchval(update_query, clean_name, existing_patient_id)
        logger.info(f"🔄 Updated existing patient {existing_patient_id} to name: {clean_name}")
        return str(existing_patient_id)
        
    # 🟢 Scenario B: New User OR Family Member (Create New Profile)
    # Note: Requires dropping the UNIQUE constraint on (clinic_id, phone) in Supabase
    insert_query = "INSERT INTO patients (clinic_id, name, phone) VALUES ($1::uuid, $2, $3) RETURNING id"
    try:
        new_id = await conn.fetchval(insert_query, clinic_id, clean_name, clean_phone)
        logger.info(f"🆕 Created NEW patient profile: {clean_name} ({clean_phone})")
        return str(new_id)
    except Exception as e:
        logger.error(f"❌ Failed to get or create patient: {e}")
        raise

async def get_clinic_id(pool):
    return await pool.fetchval("SELECT id FROM clinics WHERE name = 'Mithra Hospital' AND deleted_at IS NULL LIMIT 1")

async def book_new_appointment(pool, clinic_id, doctor_id, patient_name, phone, start_time, end_time, force_book=False, patient_id=None, reason=None, is_followup=False, is_same_patient="unknown", existing_patient_id=None):
    """Inserts the actual appointment record into PostgreSQL."""
    async with pool.acquire() as conn:
        # Run a micro-cleanup just for safety right before booking
        cleanup_query = "UPDATE appointments SET status = 'cancelled', updated_at = NOW() WHERE status = 'pending' AND payment_status = 'unpaid' AND created_at < NOW() - INTERVAL '15 minutes'"
        await conn.execute(cleanup_query)

        resolved_patient_id = patient_id or await get_or_create_patient(conn, clinic_id, patient_name, phone, is_same_patient, existing_patient_id)

        # Check for slot collisions
        check_query = """
            SELECT patient_id FROM appointments
            WHERE doctor_id = $1::uuid AND appointment_start = $2
            AND deleted_at IS NULL
            AND (status = 'confirmed' OR (status = 'pending' AND created_at >= NOW() - INTERVAL '15 minutes'))
        """
        existing_appt_patient = await conn.fetchval(check_query, doctor_id, start_time)

        if existing_appt_patient:
            if str(existing_appt_patient) == str(resolved_patient_id):
                return "ALREADY_BOOKED_BY_USER"
            else:
                return "SLOT_TAKEN"

        status = 'confirmed' if is_followup else 'pending'
        payment_status = 'paid' if is_followup else 'unpaid'
        payment_amount = 0.00 if is_followup else 500.00

        insert_query = """
            INSERT INTO appointments (clinic_id, patient_id, doctor_id, appointment_start, appointment_end, status, reason, payment_status, payment_amount)
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6::appointment_status, $7, $8, $9)
            ON CONFLICT (doctor_id, appointment_start) 
            DO UPDATE SET 
                patient_id = EXCLUDED.patient_id,
                appointment_end = EXCLUDED.appointment_end,
                status = EXCLUDED.status,
                reason = EXCLUDED.reason,
                payment_status = EXCLUDED.payment_status,
                payment_amount = EXCLUDED.payment_amount,
                updated_at = NOW(),
                created_at = NOW()
            RETURNING id
        """
        appt_id = await conn.fetchval(insert_query, clinic_id, resolved_patient_id, doctor_id, start_time, end_time, status, reason, payment_status, payment_amount)
        return appt_id