-- ==========================================================
-- 🌱 Database Seed Script for AuviaLabs
-- Target: Mithra Hospital & Doctors
-- ==========================================================

DO $$
DECLARE
    v_clinic_id UUID;
    v_doc_record RECORD;
    v_day INT;
    v_date DATE;
BEGIN
    -- 🏥 1. Insert Clinic: Mithra Hospital
    INSERT INTO clinics (name, phone, email, subscription_status, subscription_plan)
    VALUES ('Mithra Hospital', '9876543210', 'admin@mithrahospital.in', 'active', 'pro')
    RETURNING id INTO v_clinic_id;

    -- 👩‍⚕️ 2. Insert 5 Doctors (Linked to Mithra Hospital)
    -- Doctor 1: General Physician
    INSERT INTO doctors (clinic_id, name, speciality, consultation_duration_minutes) 
    VALUES (v_clinic_id, 'Dr. Rohan Sharma', 'General Physician', 30);

    -- Doctor 2 to 5: Other Specialities
    INSERT INTO doctors (clinic_id, name, speciality, consultation_duration_minutes) 
    VALUES (v_clinic_id, 'Dr. Neha Gupta', 'Cardiologist', 30);

    INSERT INTO doctors (clinic_id, name, speciality, consultation_duration_minutes) 
    VALUES (v_clinic_id, 'Dr. Amit Patel', 'Pediatrician', 20);

    INSERT INTO doctors (clinic_id, name, speciality, consultation_duration_minutes) 
    VALUES (v_clinic_id, 'Dr. Priya Singh', 'Dermatologist', 30);

    INSERT INTO doctors (clinic_id, name, speciality, consultation_duration_minutes) 
    VALUES (v_clinic_id, 'Dr. Vikram Rao', 'Orthopedics', 40);


    -- 🗓️ 3. Apply Schedules & Lunch Breaks for ALL doctors
    FOR v_doc_record IN SELECT id FROM doctors WHERE clinic_id = v_clinic_id LOOP

        -- A. Schedule: Monday (1) to Friday (5), 9 AM to 5 PM
        FOR v_day IN 1..5 LOOP
            INSERT INTO doctor_schedule (clinic_id, doctor_id, day_of_week, start_time, end_time, slot_duration_minutes)
            VALUES (v_clinic_id, v_doc_record.id, v_day, '09:00:00', '17:00:00', 30);
        END LOOP;

        -- B. Time Off (Lunch): 12 PM - 1 PM for the next 14 days
        -- We loop through the next 14 days from today
        FOR i IN 0..13 LOOP
            v_date := CURRENT_DATE + i;
            
            -- Only insert the lunch break if the day is a weekday (Monday=1, Friday=5)
            IF EXTRACT(ISODOW FROM v_date) BETWEEN 1 AND 5 THEN
                INSERT INTO doctor_time_off (clinic_id, doctor_id, start_time, end_time, reason)
                VALUES (
                    v_clinic_id,
                    v_doc_record.id,
                    (v_date::text || ' 12:00:00+05:30')::TIMESTAMPTZ, -- Assuming IST Timezone
                    (v_date::text || ' 13:00:00+05:30')::TIMESTAMPTZ,
                    'Daily Lunch Break'
                );
            END IF;
        END LOOP;

    END LOOP;

    -- 👤 4. Insert a couple of dummy patients just to have data
    INSERT INTO patients (clinic_id, name, phone, email, date_of_birth, gender) VALUES
    (v_clinic_id, 'Rahul Verma', '9998887771', 'rahul@example.com', '1990-05-15', 'Male'),
    (v_clinic_id, 'Sneha Iyer', '9998887772', 'sneha@example.com', '1995-08-22', 'Female');

    RAISE NOTICE 'Seed completed successfully for Mithra Hospital!';
END $$;