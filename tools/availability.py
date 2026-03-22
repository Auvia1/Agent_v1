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
    
    doc_query = """
        SELECT d.id, d.name, d.speciality, ds.day_of_week, ds.start_time, ds.end_time, ds.slot_duration_minutes
        FROM doctors d
        JOIN doctor_schedule ds ON d.id = ds.doctor_id
        WHERE d.clinic_id = $1::uuid 
          AND d.speciality ILIKE $2
          AND d.is_active = TRUE
          AND d.deleted_at IS NULL
          AND ds.effective_from <= CURRENT_DATE
          AND (ds.effective_to IS NULL OR ds.effective_to >= CURRENT_DATE)
    """
    async with pool.acquire() as conn:
        records = await conn.fetch(doc_query, clinic_id, f"%{problem_or_speciality}%")

    if not records:
        await params.result_callback({"status": "error", "message": "No doctors found for this specialty. Ask the user if they want a General Physician instead."})
        return

    ist = pytz.timezone('Asia/Kolkata')
    now = datetime.datetime.now(ist)

    target_date = None
    if requested_date:
        try:
            parsed = date_parser.parse(requested_date)
            target_date = parsed.date()
        except:
            pass

    doctors_map = {}
    for r in records:
        doc_id = str(r['id'])
        if doc_id not in doctors_map:
            doctors_map[doc_id] = {"id": doc_id, "name": r['name'], "speciality": r['speciality'], "schedules": {}}
        doctors_map[doc_id]["schedules"][r['day_of_week']] = (r['start_time'], r['end_time'], r['slot_duration_minutes'])

    async with pool.acquire() as conn:
        for doc_id, doc_data in doctors_map.items():
            for days_checked in range(14):
                check_dt = now + datetime.timedelta(days=days_checked)
                check_date_obj = check_dt.date()
                
                if target_date and check_date_obj != target_date:
                    continue

                pg_dow = (check_date_obj.weekday() + 1) % 7 
                
                if pg_dow in doc_data["schedules"]:
                    start_time, end_time, slot_duration = doc_data["schedules"][pg_dow]
                    
                    booked_query = "SELECT TO_CHAR(appointment_start AT TIME ZONE 'Asia/Kolkata', 'HH12:MI AM') as time_str FROM appointments WHERE doctor_id = $1::uuid AND deleted_at IS NULL AND DATE(appointment_start AT TIME ZONE 'Asia/Kolkata') = $2 AND status IN ('confirmed', 'pending')"
                    booked_records = await conn.fetch(booked_query, doc_id, check_date_obj)
                    booked_times = [r['time_str'] for r in booked_records]
                    
                    timeoff_query = "SELECT start_time AT TIME ZONE 'Asia/Kolkata' as off_start, end_time AT TIME ZONE 'Asia/Kolkata' as off_end, reason FROM doctor_time_off WHERE doctor_id = $1::uuid AND DATE(start_time AT TIME ZONE 'Asia/Kolkata') = $2"
                    timeoff_records = await conn.fetch(timeoff_query, doc_id, check_date_obj)
                    time_offs = [{"start": r["off_start"].time(), "end": r["off_end"].time(), "reason": r["reason"]} for r in timeoff_records]

                    available_slots = []
                    formatted_time_offs = []
                    
                    for off in time_offs:
                        formatted_time_offs.append(f"{off['start'].strftime('%I:%M %p')} to {off['end'].strftime('%I:%M %p')} ({off['reason']})")

                    current_dt = datetime.datetime.combine(check_date_obj, start_time)
                    end_dt = datetime.datetime.combine(check_date_obj, end_time)

                    while current_dt < end_dt:
                        t = current_dt.time()
                        is_time_off = any(off["start"] <= t < off["end"] for off in time_offs)
                        
                        if not is_time_off:
                            time_formatted = current_dt.strftime("%I:%M %p")
                            if time_formatted not in booked_times:
                                if not (check_date_obj == now.date() and current_dt.time() <= now.time()):
                                    available_slots.append(time_formatted)
                        
                        current_dt += datetime.timedelta(minutes=slot_duration)
                    
                    if available_slots:
                        target_day_str = "TODAY" if (check_date_obj == now.date()) else check_dt.strftime('%A, %B %d')
                        
                        await params.result_callback({
                            "status": "success",
                            "doctor_id": doc_id,
                            "doctor_name": doc_data["name"],
                            "speciality": doc_data["speciality"],
                            "working_hours": f"{start_time.strftime('%I:%M %p')} to {end_time.strftime('%I:%M %p')}",
                            "target_date": target_day_str,
                            "time_offs": formatted_time_offs,
                            "all_available_slots": available_slots,
                            "system_directive": f"Inform the user: {doc_data['name']}, our {doc_data['speciality']}, works from {start_time.strftime('%I:%M %p')} to {end_time.strftime('%I:%M %p')}. The next available slot on {target_day_str} is at {available_slots[0]}. Ask if they want to book this."
                        })
                        return 
                    elif target_date:
                         await params.result_callback({
                             "status": "error",
                             "message": f"The doctor is completely booked or unavailable on {target_date.strftime('%B %d')}. Ask the user if they want the next available date instead."
                         })
                         return
                    
    await params.result_callback({"status": "error", "message": "No slots available for the next 14 days."})