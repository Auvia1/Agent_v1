-- ==========================================================
-- 🏥 Clinic SaaS – Production Database Schema v2 (Hardened)
-- Multi-tenant | Audit-ready | Payment-enabled | Role-based access
-- Real-world constraints applied (Emails, Phones, Dates, Amounts)
-- ==========================================================

-- 🔧 0. Extensions & Cleanup
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Drop existing tables in reverse dependency order to avoid conflicts
DROP TABLE IF EXISTS activity_log CASCADE;
DROP TABLE IF EXISTS audit_logs CASCADE;
DROP TABLE IF EXISTS doctor_time_off CASCADE;
DROP TABLE IF EXISTS doctor_schedule CASCADE;
DROP TABLE IF EXISTS payments CASCADE;
DROP TABLE IF EXISTS appointments CASCADE;
DROP TABLE IF EXISTS users CASCADE;
DROP TABLE IF EXISTS patients CASCADE;
DROP TABLE IF EXISTS doctors CASCADE;
DROP TABLE IF EXISTS clinics CASCADE;

-- Drop existing types
DROP TYPE IF EXISTS user_role CASCADE;
DROP TYPE IF EXISTS payment_status CASCADE;
DROP TYPE IF EXISTS appointment_status CASCADE;
DROP TYPE IF EXISTS gender_type CASCADE;
DROP TYPE IF EXISTS sub_status_type CASCADE;

---

-- 🏢 1. clinics
CREATE TYPE sub_status_type AS ENUM ('trial', 'active', 'past_due', 'canceled', 'suspended');

CREATE TABLE clinics (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name                 VARCHAR(255) NOT NULL CHECK (char_length(TRIM(name)) > 0),
  email                VARCHAR(255) CHECK (email ~* '^[A-Za-z0-9._+%-]+@[A-Za-z0-9.-]+\.[A-Za-z]+$'),
  phone                VARCHAR(20) CHECK (phone ~ '^[0-9]{10}$'), -- Strictly 10 digits
  address              TEXT,
  timezone             VARCHAR(50) NOT NULL DEFAULT 'Asia/Kolkata',
  subscription_plan    VARCHAR(50) DEFAULT 'trial' CHECK (char_length(TRIM(subscription_plan)) > 0),
  subscription_status  sub_status_type DEFAULT 'trial',
  deleted_at           TIMESTAMPTZ,
  created_at           TIMESTAMPTZ DEFAULT NOW(),
  updated_at           TIMESTAMPTZ DEFAULT NOW()
);

---

-- 👩‍⚕️ 2. doctors
CREATE TABLE doctors (
  id                            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  clinic_id                     UUID NOT NULL REFERENCES clinics(id) ON DELETE CASCADE,
  name                          VARCHAR(255) NOT NULL CHECK (char_length(TRIM(name)) > 0),
  speciality                    VARCHAR(255),
  consultation_duration_minutes INT DEFAULT 30 CHECK (consultation_duration_minutes > 0),
  buffer_time_minutes           INT DEFAULT 0 CHECK (buffer_time_minutes >= 0),
  max_appointments_per_day      INT CHECK (max_appointments_per_day > 0),
  is_active                     BOOLEAN DEFAULT TRUE,
  deleted_at                    TIMESTAMPTZ,
  created_at                    TIMESTAMPTZ DEFAULT NOW(),
  updated_at                    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_doctors_clinic_id ON doctors(clinic_id);

---

-- 👤 3. patients
CREATE TYPE gender_type AS ENUM ('Male', 'Female', 'Other', 'Prefer not to say');

CREATE TABLE patients (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  clinic_id     UUID NOT NULL REFERENCES clinics(id) ON DELETE CASCADE,
  name          VARCHAR(255) NOT NULL CHECK (char_length(TRIM(name)) > 0),
  phone         VARCHAR(20) NOT NULL CHECK (phone ~ '^[0-9]{10}$'), -- Strictly 10 digits
  email         VARCHAR(255) CHECK (email ~* '^[A-Za-z0-9._+%-]+@[A-Za-z0-9.-]+\.[A-Za-z]+$'),
  date_of_birth DATE CHECK (date_of_birth <= CURRENT_DATE), -- Cannot be born in the future
  gender        gender_type,
  deleted_at    TIMESTAMPTZ,
  created_at    TIMESTAMPTZ DEFAULT NOW(),
  updated_at    TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(clinic_id, phone)
);

CREATE INDEX idx_patients_clinic_id ON patients(clinic_id);

---

-- 🔐 4. users (RBAC)
CREATE TYPE user_role AS ENUM (
  'super_admin',
  'clinic_admin',
  'receptionist',
  'doctor'
);

CREATE TABLE users (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  clinic_id     UUID REFERENCES clinics(id) ON DELETE CASCADE, -- Nullable for super_admins
  name          VARCHAR(255) CHECK (char_length(TRIM(name)) > 0),
  email         VARCHAR(255) UNIQUE NOT NULL CHECK (email ~* '^[A-Za-z0-9._+%-]+@[A-Za-z0-9.-]+\.[A-Za-z]+$'),
  password_hash TEXT NOT NULL CHECK (char_length(password_hash) > 0),
  role          user_role NOT NULL,
  is_active     BOOLEAN DEFAULT TRUE,
  deleted_at    TIMESTAMPTZ,
  created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_users_clinic_id ON users(clinic_id);

---

-- 📅 5. appointments
CREATE TYPE appointment_status AS ENUM (
  'pending',
  'confirmed',
  'cancelled',
  'completed',
  'no_show',
  'rescheduled'
);

CREATE TABLE appointments (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  clinic_id          UUID NOT NULL REFERENCES clinics(id) ON DELETE CASCADE,
  patient_id         UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
  doctor_id          UUID NOT NULL REFERENCES doctors(id) ON DELETE CASCADE,

  appointment_start  TIMESTAMPTZ NOT NULL,
  appointment_end    TIMESTAMPTZ NOT NULL,

  reason             TEXT,
  notes              TEXT,

  status             appointment_status DEFAULT 'pending',
  source             VARCHAR(50) DEFAULT 'ai_agent',

  payment_status     VARCHAR(50) DEFAULT 'unpaid',
  payment_amount     NUMERIC(10,2) CHECK (payment_amount >= 0),

  created_by         UUID REFERENCES users(id) ON DELETE SET NULL,
  cancelled_by       UUID REFERENCES users(id) ON DELETE SET NULL,

  version            INT DEFAULT 1 CHECK (version >= 1),
  deleted_at         TIMESTAMPTZ,

  created_at         TIMESTAMPTZ DEFAULT NOW(),
  updated_at         TIMESTAMPTZ DEFAULT NOW(),

  UNIQUE(doctor_id, appointment_start),
  
  -- Time validity check
  CONSTRAINT chk_appointment_times CHECK (appointment_end > appointment_start)
);

CREATE INDEX idx_appointments_clinic_id   ON appointments(clinic_id);
CREATE INDEX idx_appointments_doctor_id   ON appointments(doctor_id);
CREATE INDEX idx_appointments_patient_id  ON appointments(patient_id);
CREATE INDEX idx_appointments_start       ON appointments(appointment_start);

ALTER TABLE appointments ENABLE ROW LEVEL SECURITY;

---

-- 💰 6. payments
CREATE TYPE payment_status AS ENUM (
  'pending',
  'paid',
  'failed',
  'refunded'
);

CREATE TABLE payments (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  clinic_id           UUID NOT NULL REFERENCES clinics(id) ON DELETE CASCADE,
  appointment_id      UUID NOT NULL REFERENCES appointments(id) ON DELETE CASCADE,
  amount              NUMERIC(10,2) NOT NULL CHECK (amount >= 0),
  currency            VARCHAR(10) DEFAULT 'INR' CHECK (char_length(TRIM(currency)) > 0),
  status              payment_status DEFAULT 'pending',
  provider            VARCHAR(50), 
  provider_payment_id VARCHAR(255),
  created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_payments_clinic_id       ON payments(clinic_id);
CREATE INDEX idx_payments_appointment_id  ON payments(appointment_id);

---

-- 🗓️ 7. doctor_schedule
CREATE TABLE doctor_schedule (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  clinic_id             UUID NOT NULL REFERENCES clinics(id) ON DELETE CASCADE,
  doctor_id             UUID NOT NULL REFERENCES doctors(id) ON DELETE CASCADE,
  day_of_week           INT CHECK (day_of_week BETWEEN 0 AND 6),  -- 0=Sunday, 6=Saturday
  start_time            TIME NOT NULL,
  end_time              TIME NOT NULL,
  slot_duration_minutes INT NOT NULL CHECK (slot_duration_minutes > 0),
  effective_from        DATE DEFAULT CURRENT_DATE,
  effective_to          DATE,
  
  -- Logical checks
  CONSTRAINT chk_schedule_times CHECK (end_time > start_time),
  CONSTRAINT chk_effective_dates CHECK (effective_to IS NULL OR effective_to >= effective_from)
);

CREATE INDEX idx_doctor_schedule_doctor_id ON doctor_schedule(doctor_id);
CREATE INDEX idx_doctor_schedule_clinic_id ON doctor_schedule(clinic_id);

---

-- 🏖️ 8. doctor_time_off
CREATE TABLE doctor_time_off (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  clinic_id  UUID NOT NULL REFERENCES clinics(id) ON DELETE CASCADE,
  doctor_id  UUID NOT NULL REFERENCES doctors(id) ON DELETE CASCADE,
  start_time TIMESTAMPTZ NOT NULL,
  end_time   TIMESTAMPTZ NOT NULL,
  reason     TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  
  -- Prevent negative time off
  CONSTRAINT chk_time_off_duration CHECK (end_time > start_time)
);

CREATE INDEX idx_time_off_doctor_id ON doctor_time_off(doctor_id);

---

-- 📊 9. audit_logs
CREATE TABLE audit_logs (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  clinic_id   UUID REFERENCES clinics(id) ON DELETE CASCADE,
  user_id     UUID,
  action      VARCHAR(100) NOT NULL CHECK (char_length(TRIM(action)) > 0),
  entity_type VARCHAR(100) NOT NULL,
  entity_id   UUID NOT NULL,
  meta        JSONB,
  created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_audit_logs_clinic_id   ON audit_logs(clinic_id);
CREATE INDEX idx_audit_logs_entity      ON audit_logs(entity_type, entity_id);
CREATE INDEX idx_audit_logs_created_at  ON audit_logs(created_at DESC);

---

-- ⚡ 10. activity_log
CREATE TABLE activity_log (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  clinic_id   UUID NOT NULL REFERENCES clinics(id) ON DELETE CASCADE,
  event_type  VARCHAR(50) NOT NULL CHECK (char_length(TRIM(event_type)) > 0),
  title       TEXT NOT NULL CHECK (char_length(TRIM(title)) > 0),
  entity_type VARCHAR(50),
  entity_id   UUID,
  user_id     UUID,
  meta        JSONB,
  created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_activity_log_clinic_id  ON activity_log(clinic_id);
CREATE INDEX idx_activity_log_created_at ON activity_log(created_at DESC);
CREATE INDEX idx_activity_log_entity     ON activity_log(entity_type, entity_id);

---

-- ☎️ 11. calls (for tracking incoming calls)
CREATE TABLE IF NOT EXISTS calls (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  clinic_id     UUID NOT NULL REFERENCES clinics(id) ON DELETE CASCADE,
  type          VARCHAR(50) NOT NULL DEFAULT 'incoming',
  caller        VARCHAR(20),
  agent_type    VARCHAR(50) DEFAULT 'ai',
  duration      INTEGER DEFAULT 0,
  ai_summary    TEXT,
  created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_calls_clinic_id   ON calls(clinic_id);
CREATE INDEX idx_calls_created_at  ON calls(created_at DESC);


//new statement
ALTER TABLE appointments DISABLE ROW LEVEL SECURITY;

//new statement
-- Remove the strict unique constraint so family members can share a phone number
ALTER TABLE patients DROP CONSTRAINT IF EXISTS patients_clinic_id_phone_key;