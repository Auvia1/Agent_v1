#tools/notify.py
# import os
# import pytz
# import httpx
# from loguru import logger
# from tools.pool import get_pool 

# def _format_whatsapp_number(phone_number: str) -> str:
#     """Cleans the phone number and safely ensures it has a country code."""
#     digits_only = "".join(filter(str.isdigit, str(phone_number)))
    
#     # If strictly 10 digits, assume India and prepend 91
#     if len(digits_only) == 10:
#         return f"91{digits_only}"
        
#     # If it's already 11+ digits (like your US number 16315551181 or 91XXXXXXXXXX), leave it alone
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
#         async with httpx.AsyncClient() as client:
#             response = await client.post(url, headers=headers, json=payload)
            
#             # 👇 DEEP LOGGING: Reveal exactly what Meta is secretly doing
#             try:
#                 response_data = response.json()
#                 logger.info(f"🔍 META RAW RESPONSE: {response_data}")
#             except Exception:
#                 logger.info(f"🔍 META RAW TEXT RESPONSE: {response.text}")

#             if response.status_code in [200, 201]:
#                 logger.info(f"✅ Meta WhatsApp message officially accepted for {formatted_number}!")
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
#     rows = [{"id": f"SLOT_{slot}", "title": slot} for slot in display_slots]

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
#         async with httpx.AsyncClient() as client:
#             response = await client.post(url, json=payload, headers=headers)
            
#             # 👇 DEEP LOGGING
#             try:
#                 logger.info(f"🔍 META RAW RESPONSE (Slots): {response.json()}")
#             except:
#                 pass

#             if response.status_code in [200, 201]:
#                 logger.info(f"✅ Interactive slot list accepted for {formatted_number}!")
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
#     """Updates the DB to 'paid', generates the Token Number, and triggers the final WhatsApp receipt."""
#     try:
#         pool = get_pool()  
#         async with pool.acquire() as conn:
#             # 1. Fetch appointment details and lock the row to prevent race conditions during token generation
#             record_query = """
#                 SELECT a.doctor_id, a.appointment_start, cs.is_slots_needed, a.token_number
#                 FROM appointments a JOIN clinic_settings cs ON cs.clinic_id = a.clinic_id
#                 WHERE a.id = $1::uuid FOR UPDATE OF a
#             """
#             appt_record = await conn.fetchrow(record_query, appointment_id)
#             if not appt_record: return

#             new_token = appt_record['token_number']

#             # 2. Generate token IF it's a token clinic and one hasn't been assigned yet
#             if not appt_record['is_slots_needed'] and new_token is None:
#                 token_query = """
#                     SELECT COALESCE(MAX(token_number), 0) + 1 
#                     FROM appointments 
#                     WHERE doctor_id = $1::uuid 
#                       AND DATE(appointment_start AT TIME ZONE 'Asia/Kolkata') = DATE($2 AT TIME ZONE 'Asia/Kolkata')
#                       AND (appointment_start AT TIME ZONE 'Asia/Kolkata')::time = ($2 AT TIME ZONE 'Asia/Kolkata')::time
#                       AND deleted_at IS NULL AND token_number IS NOT NULL
#                 """
#                 new_token = await conn.fetchval(token_query, appt_record['doctor_id'], appt_record['appointment_start'])

#             # 3. Mark Paid and Save Token
#             await conn.execute(
#                 "UPDATE appointments SET status = 'confirmed', payment_status = 'paid', updated_at = NOW(), token_number = $2 WHERE id = $1::uuid",
#                 appointment_id, new_token
#             )

#             # 4. Fetch the final data for the WhatsApp Receipt
#             query = """
#                 SELECT p.name as patient_name, p.phone, d.name as doctor_name, a.reason, a.appointment_start, a.token_number
#                 FROM appointments a JOIN patients p ON a.patient_id = p.id JOIN doctors d ON a.doctor_id = d.id
#                 WHERE a.id = $1::uuid
#             """
#             record = await conn.fetchrow(query, appointment_id)

#             if record:
#                 ist = pytz.timezone('Asia/Kolkata')
#                 appt_time = record['appointment_start'].astimezone(ist).strftime('%B %d, %Y at %I:%M %p')

#                 token_text = f"🔢 *Token Number:* {record['token_number']}\n" if record['token_number'] else ""
                
#                 whatsapp_msg = (
#                     "✅ *Booking Confirmed & Paid!*\n\n"
#                     f"👤 *Name:* {record['patient_name']}\n"
#                     f"📱 *Phone:* {record['phone']}\n"
#                     f"👨‍⚕️ *Doctor:* {record['doctor_name']}\n"
#                     f"{token_text}"
#                     f"📅 *Time:* {appt_time}\n\n"
#                     "Thank you for choosing us! Please present this message at the front desk."
#                 )

#                 await send_confirmation(record['phone'], whatsapp_msg)
#                 logger.info(f"✅ Final WhatsApp confirmation sent to {record['phone']} | Token: {record['token_number']}")

#     except Exception as e:
#         logger.error(f"❌ Database error processing successful payment: {e}")

# tools/notify.py
import os
import pytz
import httpx
from loguru import logger
from tools.pool import get_pool 

def _format_whatsapp_number(phone_number: str) -> str:
    """Cleans the phone number and safely ensures it has a country code."""
    digits_only = "".join(filter(str.isdigit, str(phone_number)))
    
    # If strictly 10 digits, assume India and prepend 91
    if len(digits_only) == 10:
        return f"91{digits_only}"
        
    # If it's already 11+ digits (like your US number 16315551181 or 91XXXXXXXXXX), leave it alone
    return digits_only

async def send_confirmation(phone_number: str, message: str):
    """Sends a standard WhatsApp text message (ONLY works if 24-hour user-initiated window is open)."""
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
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload)
            
            try:
                response_data = response.json()
                logger.info(f"🔍 META RAW TEXT RESPONSE: {response_data}")
            except Exception:
                pass

            if response.status_code in [200, 201]:
                logger.info(f"✅ Meta WhatsApp text message accepted for {formatted_number}!")
                return True
                
            logger.error(f"❌ Meta WhatsApp Text Error {response.status_code}: {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"❌ Meta WhatsApp request failed: {e}")
        return False

async def send_whatsapp_template(phone_number: str, template_name: str, language_code: str, body_variables: list, button_variable: str = None):
    """Sends an approved Meta WhatsApp Template (Bypasses the 24-hour window restriction)."""
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

    components = []
    
    # 1. Body Variables
    if body_variables:
        body_params = [{"type": "text", "text": str(var)} for var in body_variables]
        components.append({"type": "body", "parameters": body_params})

    # 2. Button Variable (If required by template)
    if button_variable:
        components.append({
            "type": "button",
            "sub_type": "url",
            "index": "0",
            "parameters": [{"type": "text", "text": str(button_variable)}]
        })

    payload = {
        "messaging_product": "whatsapp",
        "to": formatted_number,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language_code},
            "components": components
        }
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload)
            
            try:
                response_data = response.json()
                logger.info(f"🔍 META RAW TEMPLATE RESPONSE: {response_data}")
            except Exception:
                pass

            if response.status_code in [200, 201]:
                logger.info(f"✅ Meta WhatsApp Template '{template_name}' successfully sent to {formatted_number}!")
                return True
                
            logger.error(f"❌ Meta Template Error {response.status_code}: {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"❌ WhatsApp Template Request Failed: {e}")
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
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, headers=headers)
            if response.status_code in [200, 201]:
                logger.info(f"✅ Interactive slot list accepted for {formatted_number}!")
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
    """Updates the DB to 'paid', generates the Token Number, and triggers the final WhatsApp receipt using the Meta Template."""
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
                token_val = str(record['token_number']) if record['token_number'] else "N/A"

                # Trigger the approved template 'the_final_paid_receipt_telugu'
                # Variables: {{1}}: Clinic, {{2}}: Name, {{3}}: Phone, {{4}}: Doctor, {{5}}: Token, {{6}}: Time
                template_vars = [
                    "Mithra Hospitals",            # {{1}}
                    str(record['patient_name']),   # {{2}}
                    str(record['phone']),          # {{3}}
                    str(record['doctor_name']),    # {{4}}
                    token_val,                     # {{5}}
                    str(appt_time)                 # {{6}}
                ]

                await send_whatsapp_template(
                    phone_number=record['phone'],
                    template_name="the_final_paid_receipt_telugu", # 👈 MATCHES META
                    language_code="en",                            # 👈 META EXPECTS ENGLISH
                    body_variables=template_vars
                )
                
                logger.info(f"✅ Final WhatsApp Template receipt triggered for {record['phone']} | Token: {record['token_number']}")

    except Exception as e:
        logger.error(f"❌ Database error processing successful payment: {e}")