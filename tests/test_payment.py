"""
Quick test: generates a Razorpay payment link for a dummy appointment.
Run from project root:  python3 tests/test_payment.py
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv(override=True)

from tools.payment import generate_payment_link

TEST_PHONE      = "8309833107"   # change if needed
TEST_APPT_ID    = "test-appt-001"
TEST_NAME       = "Test User"
TEST_AMOUNT_INR = 500

async def main():
    print(f"Generating ₹{TEST_AMOUNT_INR} Razorpay link for {TEST_NAME} ({TEST_PHONE}) ...")
    link = await generate_payment_link(TEST_AMOUNT_INR, TEST_PHONE, TEST_APPT_ID, TEST_NAME)
    if link:
        print(f"✅ Payment link generated:\n   {link}")
    else:
        print("❌ Failed to generate link — check logs above.")

asyncio.run(main())
