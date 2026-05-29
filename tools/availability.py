
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
# tools/availability.py
import pytz
import datetime
import difflib
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
    today_date = now.date()

    target_date = None
    days_to_check = list(range(7))  # Default: Look ahead up to 7 days for the next open slot

    if requested_date:
        try: 
            target_date = date_parser.parse(requested_date).date()
            delta_days = (target_date - today_date).days
            
            if delta_days < 0:
                logger.warning(f"📅 Requested date {target_date} is in the past.")
                return await params.result_callback({
                    "status": "error", 
                    "message": "SYSTEM DIRECTIVE: Inform the user gracefully that appointments cannot be booked in the past. Ask them to provide a future date."
                })
                
            # If a specific date is requested, ONLY check that specific date
            days_to_check = [delta_days]
        except Exception as e:
            logger.error(f"Error parsing date: {e}")
            pass

    async with pool.acquire() as conn:
        settings = await conn.fetchrow("SELECT is_slots_needed FROM clinic_settings WHERE clinic_id = $1::uuid", clinic_id)
        is_slots_needed = settings['is_slots_needed'] if settings else False

        all_doctors_query = "SELECT id, name, speciality FROM doctors WHERE clinic_id = $1::uuid AND is_active = TRUE AND deleted_at IS NULL"
        all_doctors = await conn.fetch(all_doctors_query, clinic_id)

        matched_doctor = None
        highest_similarity = 0.0
        search_term = problem_or_speciality.lower().replace("dr.", "").replace("dr", "").strip()

        # 🧠 Enhanced Fuzzy Match (Checks both Speciality AND Name)
        for doc in all_doctors:
            db_doc_name = doc['name'].lower().replace("dr.", "").replace("dr", "").strip()
            db_spec = (doc['speciality'] or "").lower()
            
            # Check Name
            name_sim = difflib.SequenceMatcher(None, db_doc_name, search_term).ratio()
            if name_sim > 0.55 or search_term in db_doc_name or db_doc_name in search_term:
                if name_sim > highest_similarity:
                    highest_similarity = name_sim
                    matched_doctor = doc
                    
            # Check Speciality
            spec_sim = difflib.SequenceMatcher(None, db_spec, search_term).ratio()
            if spec_sim > 0.60 or search_term in db_spec or db_spec in search_term:
                if spec_sim > highest_similarity:
                    highest_similarity = spec_sim
                    matched_doctor = doc

        if not matched_doctor:
            logger.warning(f"No doctor found matching: {problem_or_speciality}")
            date_label = target_date.strftime('%A, %B %d') if target_date else "the upcoming week"
            return await params.result_callback({
                "status": "error",
                "message": f"SYSTEM DIRECTIVE: Inform the user that no doctor matching '{problem_or_speciality}' was found for {date_label}. Ask if they want to see a General Physician."
            })

        doc_id = str(matched_doctor['id'])
        doc_name = matched_doctor['name']
        doc_spec = matched_doctor['speciality']

        if is_slots_needed:
            sched_query = "SELECT day_of_week, start_time, end_time, slot_duration_minutes FROM doctor_schedule WHERE doctor_id = $1::uuid AND effective_from <= CURRENT_DATE AND (effective_to IS NULL OR effective_to >= CURRENT_DATE)"
        else:
            sched_query = "SELECT day_of_week, start_time, end_time, max_appointments_per_slot FROM slots_for_token_system WHERE doctor_id = $1::uuid AND status = 'open' AND effective_from <= CURRENT_DATE AND (effective_to IS NULL OR effective_to >= CURRENT_DATE)"

        records = await conn.fetch(sched_query, doc_id)
        doc_schedules = {}
        for r in records:
            dow = r['day_of_week']
            if dow not in doc_schedules:
                doc_schedules[dow] = []
            doc_schedules[dow].append((r['start_time'], r['end_time'], r[3]))

        available_slots = []
        valid_sessions = []
        checked_date_obj = None

        for offset in days_to_check:
            check_dt = now + datetime.timedelta(days=offset)
            check_date_obj = check_dt.date()

            pg_dow = (check_date_obj.weekday() + 1) % 7

            if pg_dow in doc_schedules:
                timeoff_query = "SELECT start_time AT TIME ZONE 'Asia/Kolkata' as off_start, end_time AT TIME ZONE 'Asia/Kolkata' as off_end FROM doctor_time_off WHERE doctor_id = $1::uuid AND DATE(start_time AT TIME ZONE 'Asia/Kolkata') = $2"
                time_offs = await conn.fetch(timeoff_query, doc_id, check_date_obj)

                for shift in doc_schedules[pg_dow]:
                    start_time = shift[0]
                    end_time = shift[1]

                    shift_start_dt = datetime.datetime.combine(check_date_obj, start_time)
                    shift_end_dt = datetime.datetime.combine(check_date_obj, end_time)

                    if any(off["off_start"] <= shift_start_dt and off["off_end"] >= shift_end_dt for off in time_offs):
                        continue

                    if check_date_obj == today_date and shift_end_dt.time() <= now.time():
                        continue

                    if is_slots_needed:
                        slot_duration = shift[2]
                        booked_query = "SELECT TO_CHAR(appointment_start AT TIME ZONE 'Asia/Kolkata', 'HH12:MI AM') as time_str FROM appointments WHERE doctor_id = $1::uuid AND deleted_at IS NULL AND DATE(appointment_start AT TIME ZONE 'Asia/Kolkata') = $2 AND status IN ('confirmed', 'pending')"
                        booked_records = await conn.fetch(booked_query, doc_id, check_date_obj)
                        booked_times = [r['time_str'] for r in booked_records]

                        current_dt = shift_start_dt
                        while current_dt < shift_end_dt:
                            time_formatted = current_dt.strftime("%I:%M %p")
                            if time_formatted not in booked_times and not (check_date_obj == today_date and current_dt.time() <= now.time()):
                                available_slots.append(time_formatted)
                            current_dt += datetime.timedelta(minutes=slot_duration)
                    else:
                        max_cap = shift[2]
                        token_count_query = "SELECT COUNT(id) FROM appointments WHERE doctor_id = $1::uuid AND DATE(appointment_start AT TIME ZONE 'Asia/Kolkata') = $2 AND (appointment_start AT TIME ZONE 'Asia/Kolkata')::time = $3 AND deleted_at IS NULL AND status IN ('confirmed', 'pending')"
                        current_tokens = await conn.fetchval(token_count_query, doc_id, check_date_obj, start_time)

                        if current_tokens < max_cap:
                            session_start = start_time.strftime("%I:%M %p")
                            session_end = end_time.strftime("%I:%M %p")
                            available_slots.append(session_start)
                            valid_sessions.append(f"{session_start} to {session_end}")

            if available_slots:
                checked_date_obj = check_date_obj
                break # 🟢 CRITICAL FIX: Stop looking! We found an open slot on this day!

        if available_slots:
            target_day_str = "TODAY" if (checked_date_obj == today_date) else checked_date_obj.strftime('%A, %B %d')
            
            if is_slots_needed:
                slots_str = ", ".join(available_slots[:3])
                system_directive = f"Inform the user: {doc_name}, our {doc_spec}, is available on {target_day_str}. The next available slots are {slots_str}. Ask if they want to book one of these."
            else:
                sessions_str = " and ".join(valid_sessions)
                system_directive = f"Inform the user: We use a Token System. {doc_name}, our {doc_spec}, is available on {target_day_str} for the {sessions_str} sessions. CRITICAL RULE: DO NOT ask the user what specific time they will come. Just ask if they want to book this session."

            return await params.result_callback({
                "status": "success",
                "doctor_id": doc_id,
                "doctor_name": doc_name,
                "target_date": checked_date_obj.isoformat(),
                "is_token_based": not is_slots_needed,
                "all_available_slots": available_slots,
                "system_directive": system_directive
            })

        # Fallback if the loop finishes and no slots are available
        date_label = target_date.strftime('%A, %B %d') if target_date else "the upcoming week"
        await params.result_callback({
            "status": "error", 
            "message": f"SYSTEM DIRECTIVE: Inform the user that {doc_name} is fully booked or unavailable for {date_label}. Ask if they would like to check a different date."
        })