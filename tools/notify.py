#tools/notify.py
# import os
# import pytz
# from loguru import logger

# def _format_whatsapp_number(phone_number: str) -> str:
#     digits_only = "".join(filter(str.isdigit, str(phone_number)))
#     if len(digits_only) == 10:
#         return f"91{digits_only}"
#     if digits_only.startswith("91") and len(digits_only) == 12:
#         return digits_only
#     return digits_only

# async def send_confirmation(phone_number: str, message: str):
#     """Sends a WhatsApp message using Meta's Official Cloud API."""
#     meta_access_token = os.getenv("META_ACCESS_TOKEN") or os.getenv("WHATSAPP_ACCESS_TOKEN")
#     meta_phone_number_id = os.getenv("META_PHONE_NUMBER_ID") or os.getenv("WHATSAPP_PHONE_ID")

#     if not meta_access_token or not meta_phone_number_id:
#         logger.error("⚠️ Meta WhatsApp credentials missing in .env")
#         return False

#     formatted_number = _format_whatsapp_number(phone_number)

#     url = f"https://graph.facebook.com/v22.0/{meta_phone_number_id}/messages"
#     headers = {
#         "Authorization": f"Bearer {meta_access_token}",
#         "Content-Type": "application/json",
#     }
#     payload = {
#         "messaging_product": "whatsapp",
#         "recipient_type": "individual",
#         "to": formatted_number,
#         "type": "text",
#         "text": {
#             "preview_url": False,
#             "body": message,
#         },
#     }

#     try:
#         import httpx
#         async with httpx.AsyncClient() as client:
#             response = await client.post(url, headers=headers, json=payload)

#             if response.status_code in [200, 201]:
#                 logger.info(f"✅ Meta WhatsApp message sent to {formatted_number}!")
#                 return True

#             logger.error(f"❌ Meta WhatsApp Error {response.status_code}: {response.text}")
#             return False

#     except Exception as e:
#         logger.error(f"❌ Meta WhatsApp request failed: {e}")
#         return False

# async def send_interactive_slots(phone_number: str, doc_name: str, date_str: str, slots: list):
#     """Sends a WhatsApp Interactive List message with available time slots."""
#     meta_access_token = os.getenv("WHATSAPP_ACCESS_TOKEN") or os.getenv("META_ACCESS_TOKEN")
#     meta_phone_number_id = os.getenv("WHATSAPP_PHONE_ID") or os.getenv("META_PHONE_NUMBER_ID")

#     if not meta_access_token or not meta_phone_number_id:
#         logger.error("⚠️ Meta WhatsApp credentials missing in .env")
#         return False

#     formatted_number = _format_whatsapp_number(phone_number)
#     url = f"https://graph.facebook.com/v22.0/{meta_phone_number_id}/messages"
#     headers = {
#         "Authorization": f"Bearer {meta_access_token}",
#         "Content-Type": "application/json",
#     }

#     display_slots = slots[:10]
#     rows = []
#     for slot in display_slots:
#         rows.append({
#             "id": f"SLOT_{slot}",
#             "title": slot,
#         })

#     payload = {
#         "messaging_product": "whatsapp",
#         "to": formatted_number,
#         "type": "interactive",
#         "interactive": {
#             "type": "list",
#             "body": {
#                 "text": f"👨‍⚕️ *{doc_name}* is available on {date_str}.\n\nPlease select a time slot below:",
#             },
#             "action": {
#                 "button": "View Available Slots",
#                 "sections": [
#                     {
#                         "title": "Available Times",
#                         "rows": rows,
#                     }
#                 ],
#             },
#         },
#     }

#     try:
#         import httpx
#         async with httpx.AsyncClient() as client:
#             response = await client.post(url, json=payload, headers=headers)
#             if response.status_code in [200, 201]:
#                 logger.info(f"✅ Interactive slot list sent to {formatted_number}!")
#                 return True

#             logger.error(f"❌ Failed to send interactive slots: {response.text}")
#             return False
#     except Exception as e:
#         logger.error(f"❌ Interactive slot request failed: {e}")
#         return False

# # ==========================================================
# # 💳 PAYMENT CONFIRMATION HANDLER
# # ==========================================================
# async def handle_successful_payment(appointment_id: str):
#     """Updates the DB to 'paid' and triggers the final WhatsApp receipt."""
#     from db.connection import db_pool
#     try:
#         pool = get_pool()
#         async with pool.acquire() as conn:
#             # 1. Mark appointment as paid and confirmed
#             await conn.execute(
#                 "UPDATE appointments SET status = 'confirmed', payment_status = 'paid', updated_at = NOW() WHERE id = $1::uuid",
#                 appointment_id
#             )
            
#             # 2. Fetch the data for the WhatsApp confirmation
#             query = """
#                 SELECT p.name as patient_name, p.phone, d.name as doctor_name, a.reason, a.appointment_start
#                 FROM appointments a
#                 JOIN patients p ON a.patient_id = p.id
#                 JOIN doctors d ON a.doctor_id = d.id
#                 WHERE a.id = $1::uuid
#             """
#             record = await conn.fetchrow(query, appointment_id)
            
#             if record:
#                 ist = pytz.timezone('Asia/Kolkata')
#                 appt_time = record['appointment_start'].astimezone(ist).strftime('%B %d, %Y at %I:%M %p')
                
#                 # 3. Format the exact message
#                 whatsapp_msg = (
#                     "✅ *Booking Confirmed!*\n\n"
#                     f"👤 *Name:* {record['patient_name']}\n"
#                     f"📱 *Phone:* {record['phone']}\n"
#                     f"👨‍⚕️ *Doctor:* {record['doctor_name']}\n"
#                     f"🩺 *Reason:* {record['reason']}\n"
#                     f"📅 *Time:* {appt_time}\n\n"
#                     "Thank you for choosing Mithra Hospitals!"
#                 )
                
#                 # 4. Send it via Meta
#                 await send_confirmation(record['phone'], whatsapp_msg)
#                 logger.info(f"✅ Final WhatsApp confirmation sent to {record['phone']}")
                
#     except Exception as e:
#         logger.error(f"❌ Database error processing successful payment: {e}")
import os
import pytz
from loguru import logger
from tools.pool import get_pool  # ✅ fixed — was broken `from db.connection import`


def _format_whatsapp_number(phone_number: str) -> str:
    digits_only = "".join(filter(str.isdigit, str(phone_number)))
    if len(digits_only) == 10:
        return f"91{digits_only}"
    if digits_only.startswith("91") and len(digits_only) == 12:
        return digits_only
    return digits_only


async def send_confirmation(phone_number: str, message: str):
    """Sends a WhatsApp message using Meta's Official Cloud API."""
    meta_access_token = os.getenv("META_ACCESS_TOKEN") or os.getenv("WHATSAPP_ACCESS_TOKEN")
    meta_phone_number_id = os.getenv("META_PHONE_NUMBER_ID") or os.getenv("WHATSAPP_PHONE_ID")

    if not meta_access_token or not meta_phone_number_id:
        logger.error("⚠️ Meta WhatsApp credentials missing in .env")
        return False

    formatted_number = _format_whatsapp_number(phone_number)
    url = f"https://graph.facebook.com/v22.0/{meta_phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {meta_access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": formatted_number,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": message,
        },
    }

    try:
        import httpx
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload)
            if response.status_code in [200, 201]:
                logger.info(f"✅ Meta WhatsApp message sent to {formatted_number}!")
                return True
            logger.error(f"❌ Meta WhatsApp Error {response.status_code}: {response.text}")
            return False
    except Exception as e:
        logger.error(f"❌ Meta WhatsApp request failed: {e}")
        return False


async def send_interactive_slots(phone_number: str, doc_name: str, date_str: str, slots: list):
    """Sends a WhatsApp Interactive List message with available time slots."""
    meta_access_token = os.getenv("WHATSAPP_ACCESS_TOKEN") or os.getenv("META_ACCESS_TOKEN")
    meta_phone_number_id = os.getenv("WHATSAPP_PHONE_ID") or os.getenv("META_PHONE_NUMBER_ID")

    if not meta_access_token or not meta_phone_number_id:
        logger.error("⚠️ Meta WhatsApp credentials missing in .env")
        return False

    formatted_number = _format_whatsapp_number(phone_number)
    url = f"https://graph.facebook.com/v22.0/{meta_phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {meta_access_token}",
        "Content-Type": "application/json",
    }

    display_slots = slots[:10]
    rows = [{"id": f"SLOT_{slot}", "title": slot} for slot in display_slots]

    payload = {
        "messaging_product": "whatsapp",
        "to": formatted_number,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {
                "text": f"👨‍⚕️ *{doc_name}* is available on {date_str}.\n\nPlease select a time slot below:",
            },
            "action": {
                "button": "View Available Slots",
                "sections": [
                    {
                        "title": "Available Times",
                        "rows": rows,
                    }
                ],
            },
        },
    }

    try:
        import httpx
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, headers=headers)
            if response.status_code in [200, 201]:
                logger.info(f"✅ Interactive slot list sent to {formatted_number}!")
                return True
            logger.error(f"❌ Failed to send interactive slots: {response.text}")
            return False
    except Exception as e:
        logger.error(f"❌ Interactive slot request failed: {e}")
        return False


# ==========================================================
# 💳 PAYMENT CONFIRMATION HANDLER
# ==========================================================
async def handle_successful_payment(appointment_id: str):
    """Updates the DB to 'paid', generates the Token Number, and triggers the final WhatsApp receipt."""
    try:
        pool = get_pool()  
        async with pool.acquire() as conn:
            # 1. Fetch appointment details and lock the row to prevent race conditions during token generation
            record_query = """
                SELECT a.doctor_id, a.appointment_start, cs.is_slots_needed, a.token_number
                FROM appointments a JOIN clinic_settings cs ON cs.clinic_id = a.clinic_id
                WHERE a.id = $1::uuid FOR UPDATE OF a
            """
            appt_record = await conn.fetchrow(record_query, appointment_id)
            if not appt_record: return

            new_token = appt_record['token_number']

            # 2. Generate token IF it's a token clinic and one hasn't been assigned yet
            if not appt_record['is_slots_needed'] and new_token is None:
                # 👇 FIXED: Token counter strictly bound to Date AND Shift Time
                token_query = """
                    SELECT COALESCE(MAX(token_number), 0) + 1 
                    FROM appointments 
                    WHERE doctor_id = $1::uuid 
                      AND DATE(appointment_start AT TIME ZONE 'Asia/Kolkata') = DATE($2 AT TIME ZONE 'Asia/Kolkata')
                      AND (appointment_start AT TIME ZONE 'Asia/Kolkata')::time = ($2 AT TIME ZONE 'Asia/Kolkata')::time
                      AND deleted_at IS NULL AND token_number IS NOT NULL
                """
                new_token = await conn.fetchval(token_query, appt_record['doctor_id'], appt_record['appointment_start'])

            # 3. Mark Paid and Save Token
            await conn.execute(
                "UPDATE appointments SET status = 'confirmed', payment_status = 'paid', updated_at = NOW(), token_number = $2 WHERE id = $1::uuid",
                appointment_id, new_token
            )

            # 4. Fetch the final data for the WhatsApp Receipt
            query = """
                SELECT p.name as patient_name, p.phone, d.name as doctor_name, a.reason, a.appointment_start, a.token_number
                FROM appointments a JOIN patients p ON a.patient_id = p.id JOIN doctors d ON a.doctor_id = d.id
                WHERE a.id = $1::uuid
            """
            record = await conn.fetchrow(query, appointment_id)

            if record:
                ist = pytz.timezone('Asia/Kolkata')
                appt_time = record['appointment_start'].astimezone(ist).strftime('%B %d, %Y at %I:%M %p')

                token_text = f"🔢 *Token Number:* {record['token_number']}\n" if record['token_number'] else ""
                
                whatsapp_msg = (
                    "✅ *Booking Confirmed & Paid!*\n\n"
                    f"👤 *Name:* {record['patient_name']}\n"
                    f"📱 *Phone:* {record['phone']}\n"
                    f"👨‍⚕️ *Doctor:* {record['doctor_name']}\n"
                    f"{token_text}"
                    f"📅 *Time:* {appt_time}\n\n"
                    "Thank you for choosing us! Please present this message at the front desk."
                )

                await send_confirmation(record['phone'], whatsapp_msg)
                logger.info(f"✅ Final WhatsApp confirmation sent to {record['phone']} | Token: {record['token_number']}")

    except Exception as e:
        logger.error(f"❌ Database error processing successful payment: {e}")