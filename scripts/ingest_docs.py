#tools/ingest_docs.py
import os
import asyncio
import asyncpg
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv() 

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

clinic_data = [
    # 🕒 Timings & Availability
    "Mithra Hospital is open 24/7 for emergency services including trauma and ICU care.",
    "OPD consultation timings are from 9 AM to 8 PM Monday to Saturday, and 10 AM to 2 PM on Sundays.",
    "The pharmacy inside the hospital operates 24 hours for inpatients and emergency prescriptions.",
    
    # 📅 Appointments & Booking
    "Appointments can be booked online via the hospital website or by calling the front desk.",
    "Walk-in patients are accepted but may experience longer waiting times.",
    "Each doctor consultation lasts approximately 15 to 20 minutes.",
    "Patients are advised to arrive at least 15 minutes before their appointment time.",
    
    # 🔄 Cancellation / Rescheduling
    "Appointments once booked and paid cannot be cancelled or refunded.",
    "Appointments can be rescheduled up to 2 hours before the scheduled time.",
    "Missed appointments will not be eligible for refund or rescheduling.",
    
    # 👨‍⚕️ Doctors & Specializations
    "Dr. Sharma is an orthopedic surgeon specializing in knee replacement and sports injuries.",
    "Dr. Priya Mehta is a cardiologist specializing in heart disease and hypertension management.",
    "Dr. Rakesh Verma is a general physician available for fever, infections, and routine checkups.",
    "Dr. Anjali Rao is a pediatrician available for child healthcare and vaccinations.",
    
    # 🏥 Departments
    "The hospital has departments including cardiology, orthopedics, pediatrics, neurology, and general medicine.",
    "Emergency department handles trauma, accidents, and critical care cases 24/7.",
    "Radiology department provides X-ray, MRI, CT scan, and ultrasound services.",
    "The pathology lab provides blood tests, urine tests, and diagnostic reports.",
    
    # 💰 Payments & Insurance
    "We accept cash, UPI, credit cards, and debit cards for all payments.",
    "The hospital accepts insurance providers including Star Health, HDFC Ergo, and ICICI Lombard.",
    "Cashless insurance facility is available for selected treatments.",
    "Patients must carry valid ID and insurance documents during admission.",
    
    # 🛏️ Admission & Facilities
    "The hospital provides private rooms, semi-private rooms, and general wards.",
    "ICU and ventilator support are available for critical patients.",
    "Ambulance services are available 24/7 for emergency transport.",
    "Free Wi-Fi is available for patients and visitors inside the hospital.",
    
    # 🧾 Reports & Records
    "Lab reports can be collected from the hospital or downloaded online.",
    "Digital prescriptions are sent via SMS or email after consultation.",
    "Patients can request medical records from the records department.",
    
    # 💉 Vaccination & Preventive Care
    "Vaccination services are available for children and adults.",
    "COVID-19 vaccination and booster doses are available at the hospital.",
    
    # 🚗 Parking & Location
    "The hospital has dedicated parking for patients and visitors.",
    "Wheelchair assistance is available at the entrance.",
    
    # 📞 Support & Helpdesk
    "The front desk is available 24/7 for queries and assistance.",
    "Patients can call the helpline number for appointment booking and emergency support.",
    
    # ⚠️ Policies
    "Visitors are allowed during visiting hours from 5 PM to 7 PM.",
    "Only one attendant is allowed per patient in general wards.",
    "Silence must be maintained in ICU and critical care areas.",
    
    # 🧠 AI-specific helpful chunks (VERY IMPORTANT)
    "If a patient asks about doctor availability, the system should check doctor schedules before confirming.",
    "If a patient asks for emergency help, immediately direct them to emergency services or ambulance support.",
    "If a patient asks about symptoms, suggest consulting a relevant specialist instead of giving medical advice.",
    "If a patient asks about pricing, inform them that final cost depends on consultation and treatment."
]

async def ingest():
    db_url = os.getenv("DATABASE_URL")
    
    # 👇 FIXED: Added statement_cache_size=0 to prevent PgBouncer conflicts
    conn = await asyncpg.connect(db_url, statement_cache_size=0)
    
    print("🚀 Embedding and saving documents...")
    for text in clinic_data:
        response = genai.embed_content(
            model="models/gemini-embedding-001",
            content=text,
            task_type="retrieval_document",
            output_dimensionality=768
        )
        
        await conn.execute(
            "INSERT INTO clinic_knowledge (chunk_text, embedding) VALUES ($1, $2)",
            text, str(response['embedding'])
        )
        print(f"✅ Saved: {text[:30]}...")
    
    print("\n✨ Database is now populated!")
    await conn.close()

if __name__ == "__main__":
    asyncio.run(ingest())