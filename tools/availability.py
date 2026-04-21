
# import pytz
# import datetime
# from dateutil import parser as date_parser
# from loguru import logger
# from pipecat.services.llm_service import FunctionCallParams
# from tools.pool import get_pool
# from db.queries import get_clinic_id

# async def check_availability(params: FunctionCallParams, problem_or_speciality: str, requested_date: str = None):
#     logger.info(f"🔍 Tool Call: check_availability for '{problem_or_speciality}' | Date: {requested_date}")
    
#     pool = get_pool()
#     clinic_id = await get_clinic_id(pool)
    
#     doc_query = """
#         SELECT d.id, d.name, d.speciality, ds.day_of_week, ds.start_time, ds.end_time, ds.slot_duration_minutes, cs.is_slots_needed
#         FROM doctors d
#         JOIN doctor_schedule ds ON d.id = ds.doctor_id
#         JOIN clinic_settings cs ON cs.clinic_id = d.clinic_id
#         WHERE d.clinic_id = $1::uuid 
#           AND d.speciality ILIKE $2
#           AND d.is_active = TRUE
#           AND d.deleted_at IS NULL
#           AND ds.effective_from <= CURRENT_DATE
#           AND (ds.effective_to IS NULL OR ds.effective_to >= CURRENT_DATE)
#     """
#     async with pool.acquire() as conn:
#         records = await conn.fetch(doc_query, clinic_id, f"%{problem_or_speciality}%")

#     if not records:
#         await params.result_callback({"status": "error", "message": "No doctors found for this specialty."})
#         return

#     ist = pytz.timezone('Asia/Kolkata')
#     now = datetime.datetime.now(ist)

#     target_date = None
#     if requested_date:
#         try: target_date = date_parser.parse(requested_date).date()
#         except: pass

#     doctors_map = {}
#     for r in records:
#         doc_id = str(r['id'])
#         if doc_id not in doctors_map:
#             doctors_map[doc_id] = {"id": doc_id, "name": r['name'], "speciality": r['speciality'], "is_slots_needed": r['is_slots_needed'], "schedules": {}}
#         doctors_map[doc_id]["schedules"][r['day_of_week']] = (r['start_time'], r['end_time'], r['slot_duration_minutes'])

#     async with pool.acquire() as conn:
#         for doc_id, doc_data in doctors_map.items():
#             for days_checked in range(14):
#                 check_dt = now + datetime.timedelta(days=days_checked)
#                 check_date_obj = check_dt.date()
#                 if target_date and check_date_obj != target_date: continue

#                 pg_dow = (check_date_obj.weekday() + 1) % 7 
                
#                 if pg_dow in doc_data["schedules"]:
#                     start_time, end_time, slot_duration = doc_data["schedules"][pg_dow]
#                     is_slots_needed = doc_data["is_slots_needed"]
                    
#                     booked_query = "SELECT TO_CHAR(appointment_start AT TIME ZONE 'Asia/Kolkata', 'HH12:MI AM') as time_str FROM appointments WHERE doctor_id = $1::uuid AND deleted_at IS NULL AND DATE(appointment_start AT TIME ZONE 'Asia/Kolkata') = $2 AND status IN ('confirmed', 'pending')"
#                     booked_records = await conn.fetch(booked_query, doc_id, check_date_obj)
#                     booked_times = [r['time_str'] for r in booked_records]
                    
#                     timeoff_query = "SELECT start_time AT TIME ZONE 'Asia/Kolkata' as off_start, end_time AT TIME ZONE 'Asia/Kolkata' as off_end, reason FROM doctor_time_off WHERE doctor_id = $1::uuid AND DATE(start_time AT TIME ZONE 'Asia/Kolkata') = $2"
#                     time_offs = await conn.fetch(timeoff_query, doc_id, check_date_obj)
                    
#                     available_slots = []
#                     target_day_str = "TODAY" if (check_date_obj == now.date()) else check_dt.strftime('%A, %B %d')

#                     if is_slots_needed:
#                         # 🟢 SLOT BASED LOGIC (Chop times)
#                         current_dt = datetime.datetime.combine(check_date_obj, start_time)
#                         end_dt = datetime.datetime.combine(check_date_obj, end_time)
#                         while current_dt < end_dt:
#                             is_time_off = any(off["off_start"].time() <= current_dt.time() < off["off_end"].time() for off in time_offs)
#                             if not is_time_off:
#                                 time_formatted = current_dt.strftime("%I:%M %p")
#                                 if time_formatted not in booked_times and not (check_date_obj == now.date() and current_dt.time() <= now.time()):
#                                     available_slots.append(time_formatted)
#                             current_dt += datetime.timedelta(minutes=slot_duration)
                            
#                         system_directive = f"Inform the user: {doc_data['name']} works from {start_time.strftime('%I:%M %p')} to {end_time.strftime('%I:%M %p')}. The next available slot on {target_day_str} is at {available_slots[0] if available_slots else 'N/A'}. Ask if they want to book this."

#                     else:
#                         # 🔵 TOKEN BASED LOGIC (Give the whole session)
#                         # We just pass the start time of the session as the "slot" so the LLM has a time to return.
#                         session_start = start_time.strftime("%I:%M %p")
#                         session_end = end_time.strftime("%I:%M %p")
#                         available_slots.append(session_start)
                        
#                         system_directive = f"Inform the user: We use a Token System. {doc_data['name']} is available for a session from {session_start} to {session_end} on {target_day_str}. You will be assigned a token number for this session. Ask if they want to book a token."

#                     if available_slots:
#                         await params.result_callback({
#                             "status": "success",
#                             "doctor_id": doc_id,
#                             "doctor_name": doc_data["name"],
#                             "target_date": target_day_str,
#                             "is_token_based": not is_slots_needed,
#                             "all_available_slots": available_slots,
#                             "system_directive": system_directive
#                         })
#                         return 
                    
#     await params.result_callback({"status": "error", "message": "No slots/sessions available for the next 14 days."})

#tools/availability.py
import pytz
import datetime
from dateutil import parser as date_parser
from loguru import logger
from pipecat.services.llm_service import FunctionCallParams
from tools.pool import get_pool
from db.queries import get_clinic_id

async def check_availability(params: FunctionCallParams, problem_or_speciality: str, requested_date: str = None):
    logger.info(f"🔍 Tool Call: check_availability for '{problem_or_speciality}' | Date: {requested_date}")
    
    pool = get_pool()
    clinic_id = await get_clinic_id(pool)
    
    ist = pytz.timezone('Asia/Kolkata')
    now = datetime.datetime.now(ist)

    target_date = None
    if requested_date:
        try: target_date = date_parser.parse(requested_date).date()
        except: pass

    async with pool.acquire() as conn:
        # 1. Determine Clinic Mode
        settings = await conn.fetchrow("SELECT is_slots_needed FROM clinic_settings WHERE clinic_id = $1::uuid", clinic_id)
        is_slots_needed = settings['is_slots_needed'] if settings else False

        # 2. Fetch doctors and their schedules
        if is_slots_needed:
            doc_query = """
                SELECT d.id, d.name, d.speciality, ds.day_of_week, ds.start_time, ds.end_time, ds.slot_duration_minutes
                FROM doctors d
                JOIN doctor_schedule ds ON d.id = ds.doctor_id
                WHERE d.clinic_id = $1::uuid AND d.speciality ILIKE $2 AND d.is_active = TRUE AND d.deleted_at IS NULL
                  AND ds.effective_from <= CURRENT_DATE AND (ds.effective_to IS NULL OR ds.effective_to >= CURRENT_DATE)
            """
        else:
            doc_query = """
                SELECT d.id, d.name, d.speciality, ts.day_of_week, ts.start_time, ts.end_time, ts.max_appointments_per_slot
                FROM doctors d
                JOIN slots_for_token_system ts ON d.id = ts.doctor_id
                WHERE d.clinic_id = $1::uuid AND d.speciality ILIKE $2 AND d.is_active = TRUE AND d.deleted_at IS NULL
                  AND ts.status = 'open' AND ts.effective_from <= CURRENT_DATE AND (ts.effective_to IS NULL OR ts.effective_to >= CURRENT_DATE)
            """
            
        records = await conn.fetch(doc_query, clinic_id, f"%{problem_or_speciality}%")

        if not records:
            await params.result_callback({"status": "error", "message": "SYSTEM DIRECTIVE: Tell the user no doctors are found. (Dev Note: Check if the doctor has valid rows in slots_for_token_system for the current day of the week). "})
            return

        doctors_map = {}
        for r in records:
            doc_id = str(r['id'])
            if doc_id not in doctors_map:
                doctors_map[doc_id] = {"id": doc_id, "name": r['name'], "speciality": r['speciality'], "schedules": {}}
            
            dow = r['day_of_week']
            if dow not in doctors_map[doc_id]["schedules"]:
                doctors_map[doc_id]["schedules"][dow] = []
            
            if is_slots_needed:
                doctors_map[doc_id]["schedules"][dow].append((r['start_time'], r['end_time'], r['slot_duration_minutes']))
            else:
                doctors_map[doc_id]["schedules"][dow].append((r['start_time'], r['end_time'], r['max_appointments_per_slot']))

        # 3. Check days for availability
        for doc_id, doc_data in doctors_map.items():
            for days_checked in range(2): # Check today and tomorrow
                check_dt = now + datetime.timedelta(days=days_checked)
                check_date_obj = check_dt.date()
                if target_date and check_date_obj != target_date: continue

                pg_dow = (check_date_obj.weekday() + 1) % 7 
                
                if pg_dow in doc_data["schedules"]:
                    timeoff_query = "SELECT start_time AT TIME ZONE 'Asia/Kolkata' as off_start, end_time AT TIME ZONE 'Asia/Kolkata' as off_end FROM doctor_time_off WHERE doctor_id = $1::uuid AND DATE(start_time AT TIME ZONE 'Asia/Kolkata') = $2"
                    time_offs = await conn.fetch(timeoff_query, doc_id, check_date_obj)
                    
                    target_day_str = "TODAY" if (check_date_obj == now.date()) else check_dt.strftime('%A, %B %d')

                    available_slots = []
                    valid_sessions = []

                    # 👇 NEW: Loop through ALL shifts for the day before returning
                    for shift in doc_data["schedules"][pg_dow]:
                        start_time = shift[0]
                        end_time = shift[1]
                        
                        shift_start_dt = datetime.datetime.combine(check_date_obj, start_time)
                        shift_end_dt = datetime.datetime.combine(check_date_obj, end_time)

                        is_shift_blocked = any(off["off_start"] <= shift_start_dt and off["off_end"] >= shift_end_dt for off in time_offs)
                        if is_shift_blocked:
                            continue 

                        if check_date_obj == now.date() and shift_end_dt.time() <= now.time():
                            continue

                        if is_slots_needed:
                            slot_duration = shift[2]
                            booked_query = "SELECT TO_CHAR(appointment_start AT TIME ZONE 'Asia/Kolkata', 'HH12:MI AM') as time_str FROM appointments WHERE doctor_id = $1::uuid AND deleted_at IS NULL AND DATE(appointment_start AT TIME ZONE 'Asia/Kolkata') = $2 AND status IN ('confirmed', 'pending')"
                            booked_records = await conn.fetch(booked_query, doc_id, check_date_obj)
                            booked_times = [r['time_str'] for r in booked_records]

                            current_dt = shift_start_dt
                            while current_dt < shift_end_dt:
                                time_formatted = current_dt.strftime("%I:%M %p")
                                if time_formatted not in booked_times and not (check_date_obj == now.date() and current_dt.time() <= now.time()):
                                    available_slots.append(time_formatted)
                                current_dt += datetime.timedelta(minutes=slot_duration)
                        else:
                            max_cap = shift[2]
                            token_count_query = "SELECT COUNT(id) FROM appointments WHERE doctor_id = $1::uuid AND DATE(appointment_start AT TIME ZONE 'Asia/Kolkata') = $2 AND appointment_start::time = $3 AND deleted_at IS NULL AND status IN ('confirmed', 'pending')"
                            current_tokens = await conn.fetchval(token_count_query, doc_id, check_date_obj, start_time)
                            
                            if current_tokens < max_cap:
                                session_start = start_time.strftime("%I:%M %p")
                                session_end = end_time.strftime("%I:%M %p")
                                available_slots.append(session_start) 
                                valid_sessions.append(f"{session_start} to {session_end}")
                    
                    # 👇 Now that we collected all shifts for this day, construct the message
                    if available_slots:
                        doc_name = doc_data['name']
                        doc_spec = doc_data['speciality'] # 👈 Grab the specialization
                        
                        if is_slots_needed:
                            slots_str = ", ".join(available_slots[:3]) 
                            system_directive = f"Inform the user: {doc_name}, our {doc_spec}, is available on {target_day_str}. The next available slots are {slots_str}. Ask if they want to book one of these."
                        else:
                            sessions_str = " and ".join(valid_sessions) 
                            if check_date_obj != now.date() and not target_date:
                                system_directive = f"Inform the user: The doctor is not available for the rest of today. However, for tomorrow ({target_day_str}), {doc_name} ({doc_spec}) has tokens available for the {sessions_str} sessions. CRITICAL RULE: DO NOT ask the user what specific time they will come. Just ask which session they prefer."
                            else:
                                system_directive = f"Inform the user: We use a Token System. {doc_name}, our {doc_spec}, is available on {target_day_str} for the {sessions_str} sessions. CRITICAL RULE: DO NOT ask the user what specific time they will come. Just ask if they want to book this session."
                        await params.result_callback({
                            "status": "success",
                            "doctor_id": doc_id,
                            "doctor_name": doc_name,
                            # 👇 FIX: Give the LLM the strict machine-readable date (e.g., "2026-04-18")
                            "target_date": check_date_obj.isoformat(), 
                            "is_token_based": not is_slots_needed,
                            "all_available_slots": available_slots,
                            "system_directive": system_directive
                        })
                        return
                    
    await params.result_callback({"status": "error", "message": "SYSTEM DIRECTIVE: Inform the user that the doctor is fully booked or unavailable for today and tomorrow. Ask them to try calling back later."})