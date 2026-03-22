# #tools/reschedule.py
# import pytz
# from datetime import datetime, timedelta
# from loguru import logger
# from pipecat.services.llm_service import FunctionCallParams
# from db.queries import lookup_active_appointment
# from tools.pool import get_pool

# async def lookup_appointment(params: FunctionCallParams, phone: str):
#     """Look up an existing appointment using the patient's phone number."""
#     # Clean the phone number
#     clean_phone = "".join(filter(str.isdigit, str(phone)))
#     if clean_phone.startswith("91") and len(clean_phone) == 12:
#         clean_phone = clean_phone[2:]
        
#     logger.info(f"🔍 Tool Call: lookup_appointment | Phone: {clean_phone}")
    
#     pool = get_pool()
#     records = await lookup_active_appointment(pool, clean_phone)
    
#     if not records:
#         logger.warning(f"⚠️ No active upcoming appointments found for {clean_phone}.")
#         await params.result_callback({"status": "not_found", "message": "No active upcoming appointments found for this number."})
#         return
        
#     # Timezone converter
#     ist_tz = pytz.timezone('Asia/Kolkata')
    
#     appt_list = []
#     msg_parts = []
    
#     # 🟢 Loop through ALL upcoming appointments and format them
#     for idx, record in enumerate(records, 1):
#         start_time_ist = record['appointment_start'].astimezone(ist_tz)
#         formatted_time = start_time_ist.strftime('%B %d, %Y at %I:%M %p')
        
#         appt_list.append({
#             "appointment_id": str(record['id']),
#             "patient": record['patient_name'],
#             "doctor": record['doctor_name'],
#             "time": formatted_time
#         })
#         msg_parts.append(f"{idx}. {record['patient_name']} with {record['doctor_name']} on {formatted_time}")
        
#     joined_msgs = "\n".join(msg_parts)
    
#     # 🟢 Instruct the AI dynamically based on how many it found
#     if len(records) == 1:
#         msg = f"Found 1 upcoming appointment:\n{joined_msgs}\n\nAsk the user what new date and time they would prefer."
#     else:
#         msg = f"Found multiple upcoming appointments:\n{joined_msgs}\n\nCRITICAL: Ask the user exactly which one of these they would like to reschedule before asking for a new time."

#     logger.info(f"✅ Lookup Success: Found {len(records)} appointments.")
        
#     await params.result_callback({
#         "status": "success", 
#         "appointments": appt_list,
#         "message": msg
#     })


# async def reschedule_appointment(params: FunctionCallParams, appointment_id: str, new_start_time_iso: str):
#     """Reschedule an existing appointment to a new time."""
#     logger.info(f"🔄 Tool Call: reschedule_appointment | Appt ID: {appointment_id} | New Time: {new_start_time_iso}")
    
#     # TIMEZONE FIX: Stop the UTC Ghost Booking by forcing IST!
#     if "Z" in new_start_time_iso:
#         new_start_time_iso = new_start_time_iso.replace("Z", "+05:30")
#     elif "+" not in new_start_time_iso:
#         new_start_time_iso += "+05:30"
        
#     pool = get_pool()
#     start_time = datetime.fromisoformat(new_start_time_iso)
#     end_time = start_time + timedelta(minutes=30)
    
#     try:
#         async with pool.acquire() as conn:
#             await conn.execute(
#                 "UPDATE appointments SET appointment_start=$1, appointment_end=$2, updated_at=NOW() WHERE id=$3::uuid", 
#                 start_time, end_time, appointment_id
#             )
#         logger.info(f"✅ Successfully rescheduled appointment {appointment_id} to {start_time.strftime('%I:%M %p')}")
#         await params.result_callback({"status": "success", "message": "Appointment successfully rescheduled. Tell the user it has been confirmed!"})
#     except Exception as e:
#         logger.error(f"❌ Failed to reschedule appointment: {e}")
#         await params.result_callback({"status": "error", "message": f"Database error: {str(e)}"})
