#tools/payment.py
import os
import time
import requests
from loguru import logger
from tools.pool import get_pool  # 👈 Added database pool import

def _build_reference_id(appointment_id: str) -> str:
    """Build a unique Razorpay reference_id that stays within the 40-char limit."""
    compact_id = str(appointment_id).replace("-", "").strip()
    timestamp = str(int(time.time()))
    return f"{compact_id[:28]}_{timestamp}"


async def generate_payment_link(amount_in_rupees: int, phone_number: str, appointment_id: str, patient_name: str) -> str:
    """Generates a Razorpay Payment Link and logs it as 'pending' in the database."""
    razorpay_key_id = os.getenv("RAZORPAY_KEY_ID")
    razorpay_key_secret = os.getenv("RAZORPAY_KEY_SECRET")

    if not razorpay_key_id or not razorpay_key_secret:
        logger.error("⚠️ Razorpay credentials missing in .env.")
        return ""

    url = "https://api.razorpay.com/v1/payment_links"
    amount_in_paise = int(amount_in_rupees * 100)
    reference_id = _build_reference_id(appointment_id)

    if len(reference_id) > 40:
        logger.error(f"❌ Razorpay reference_id too long: {reference_id}")
        return ""

    payload = {
        "amount": amount_in_paise,
        "currency": "INR",
        "accept_partial": False,
        "reference_id": reference_id,
        "description": "Mithra Hospitals - Appointment Booking",
        "notes": {
            "appointment_id": str(appointment_id),
        },
        "customer": {
            "name": patient_name,
            "contact": f"+91{phone_number}",
        },
        "notify": {
            "sms": False,
            "email": False,
        },
        "reminder_enable": False,
    }

    try:
        response = requests.post(url, json=payload, auth=(razorpay_key_id, razorpay_key_secret))
        res_data = response.json()

        if response.status_code == 200:
            short_url = res_data.get("short_url")
            provider_payment_id = res_data.get("id") # Razorpay's plink_ id
            logger.info(f"✅ Razorpay link generated: {short_url}")
            
            # --- 📝 NEW: INSERT PENDING PAYMENT INTO DB ---
            try:
                pool = get_pool()
                async with pool.acquire() as conn:
                    # We need the clinic_id for the foreign key, so we fetch it from the appointment
                    clinic_record = await conn.fetchrow(
                        "SELECT clinic_id FROM appointments WHERE id = $1", 
                        appointment_id
                    )
                    
                    if clinic_record:
                        await conn.execute(
                            """
                            INSERT INTO payments (clinic_id, appointment_id, amount, currency, status, provider, provider_payment_id)
                            VALUES ($1, $2, $3, $4, 'pending', 'razorpay', $5)
                            """,
                            clinic_record["clinic_id"],
                            appointment_id,
                            amount_in_rupees,
                            "INR",
                            provider_payment_id
                        )
                        logger.info(f"📝 Inserted 'pending' payment record for {appointment_id}")
                    else:
                        logger.error(f"❌ Could not find clinic_id for appointment {appointment_id}")
                        
            except Exception as db_err:
                logger.error(f"❌ Database insert error for payment: {db_err}")
            # ---------------------------------------------

            return short_url

        logger.error(f"❌ Razorpay Error: {res_data}")
        return ""

    except Exception as e:
        logger.error(f"❌ Razorpay Exception: {e}")
        return ""