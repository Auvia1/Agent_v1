"""
Quick test: sends a WhatsApp message via Meta Cloud API.
Run from project root:  python3 tests/test_whatsapp.py
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv(override=True)

from tools.notify import send_confirmation

TEST_PHONE = "8309833107"   # change if needed

async def main():
    print(f"Sending test WhatsApp to {TEST_PHONE} ...")
    
    # Dummy data simulating what the database would return
    patient_name = "Hari Ram"
    phone = TEST_PHONE
    doctor_name = "Dr. Rohan Sharma"
    reason = "Fever and cough"
    appt_time = "March 23, 2026 at 01:00 PM"
    
    # The exact template from tools/notify.py -> handle_successful_payment
    whatsapp_msg = (
        "✅ *Booking Confirmed!*\n\n"
        f"👤 *Name:* {patient_name}\n"
        f"📱 *Phone:* {phone}\n"
        f"👨‍⚕️ *Doctor:* {doctor_name}\n"
        f"🩺 *Reason:* {reason}\n"
        f"📅 *Time:* {appt_time}\n\n"
        "Thank you for choosing Mithra Hospitals!"
    )

    result = await send_confirmation(TEST_PHONE, whatsapp_msg)
    
    print("Result:", "SUCCESS" if result else "FAILED")

if __name__ == "__main__":
    asyncio.run(main())