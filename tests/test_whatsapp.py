#tests/test_whatsapp.py
import os
import requests
from dotenv import load_dotenv

load_dotenv(override=True)

ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
PHONE_ID = os.getenv("WHATSAPP_PHONE_ID")

# MUST include country code (e.g., 91 for India)
TEST_PHONE = "918309833107" 

def test_hello_world():
    print(f"Sending hello_world template to {TEST_PHONE}...")
    
    url = f"https://graph.facebook.com/v19.0/{PHONE_ID}/messages"
    
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    
    # The required payload structure for a template without variables
    payload = {
        "messaging_product": "whatsapp",
        "to": TEST_PHONE,
        "type": "template",
        "template": {
            "name": "hello_world",
            "language": {
                "code": "en_US"
            }
        }
    }
    
    response = requests.post(url, headers=headers, json=payload)
    
    print("\nStatus Code:", response.status_code)
    if response.status_code == 200:
        print("✅ SUCCESS! Check your WhatsApp.")
    else:
        print("❌ FAILED!")
        print("Error Details:", response.json())

if __name__ == "__main__":
    test_hello_world()