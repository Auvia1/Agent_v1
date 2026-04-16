#tools/followup.py
import pytz  # ✅ fixed — was missing, caused NameError at runtime
from datetime import datetime
from loguru import logger
from pipecat.services.llm_service import FunctionCallParams
from tools.pool import get_pool


async def verify_followup(params: FunctionCallParams, phone: str):
    """Checks the database to see if the user is eligible for a free follow-up."""
    clean_phone = "".join(filter(str.isdigit, str(phone)))
    if clean_phone.startswith("91") and len(clean_phone) == 12:
        clean_phone = clean_phone[2:]

    if len(clean_phone) != 10:
        await params.result_callback({
            "status": "error",
            "message": f"SYSTEM DIRECTIVE: Tell the user EXACTLY 'You provided a {len(clean_phone)}-digit number. I need exactly 10 digits to check your records. Please repeat your 10-digit phone number.'"
        })
        return

    try:
        pool = get_pool()  # ✅ reuse singleton
        query = """
            SELECT a.appointment_start, d.name as doctor_name, d.speciality
            FROM appointments a
            JOIN patients p ON a.patient_id = p.id
            JOIN doctors d ON a.doctor_id = d.id
            WHERE p.phone = $1 AND a.status = 'confirmed' AND a.deleted_at IS NULL
              AND a.appointment_start >= NOW() - INTERVAL '30 days'
              AND a.appointment_start < NOW()
            ORDER BY a.appointment_start DESC LIMIT 1
        """
        async with pool.acquire() as conn:
            recent_appt = await conn.fetchrow(query, clean_phone)

        if not recent_appt:
            await params.result_callback({
                "status": "not_eligible",
                "message": "SYSTEM DIRECTIVE: Tell the user: 'I could not find any recent appointments for this number. We will need to book a new paid consultation. What medical problem or symptoms are you experiencing?'"
            })
            return

        appt_date = recent_appt['appointment_start']
        ist = pytz.timezone('Asia/Kolkata')
        now = datetime.now(ist)
        days_since = (now - appt_date.astimezone(ist)).days

        if days_since <= 7:
            doc_name = recent_appt['doctor_name']
            spec = recent_appt['speciality']
            await params.result_callback({
                "status": "eligible",
                "message": f"SYSTEM DIRECTIVE: Tell the user: 'You are eligible for a free 1-week follow-up with {doc_name}. What day and time would you prefer?' CRITICAL INTERNAL NOTE: When the user replies with a time, use '{spec}' as the speciality to check availability. When booking the appointment later, you MUST pass is_followup='yes'."
            })
        else:
            await params.result_callback({
                "status": "expired",
                "message": "SYSTEM DIRECTIVE: Tell the user: 'Your 7-day free follow-up period has expired. We will need to book a new paid consultation. What medical problem or symptoms are you experiencing?'"
            })

    except Exception as e:
        logger.error(f"Error in verify_followup: {e}")
        await params.result_callback({
            "status": "error",
            "message": "SYSTEM DIRECTIVE: Tell the user a system error occurred while checking records. Please ask them to call the clinic directly."
        })