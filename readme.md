# 🏥 Mithra Hospital AI Project: Operational Guide

This project is a multi-channel AI reception system featuring a **Real-time Voice Agent** (WebRTC & Twilio) and a **WhatsApp Chatbot**, both integrated with **Razorpay** for automated appointment billing.

---

## 📂 Prerequisites

Before running any flow, ensure your environment is ready:

* **Redis:** Must be running (`redis-server`).
* **Database:** Postgres must be active with the correct schema.
* **Ngrok:** Must be tunneling to port 8000 (`ngrok http 8000`).
* **Environment:** `.env` file must be fully populated with API keys (Gemini, Sarvam, Meta, Razorpay, Twilio).

---

## 🚀 Execution Flows

### 1. Twilio (Live Phone Calls)
This mode boots the FastAPI server to listen for Twilio's webhook and handle bidirectional audio streams over the phone network.

* **Terminal A:** `redis-server`
* **Terminal B:** `ngrok http 8000`
* **Terminal C:** `python3 call_agent.py --twilio`

**Execution Steps:**
1.  Set your Twilio Console **"Voice Configuration"** URL to: `https://your-ngrok-url.ngrok-free.dev/voice`
2.  Trigger the call via the Twilio CLI, Curl, or by calling your Twilio number.
3.  The agent will answer and speak at **8000Hz** (Telephony standard).

---

### 2. Pipecat UI (Browser Voice)
Use this for high-fidelity testing directly in your web browser. It uses port **7860** for the UI and can run simultaneously with the WhatsApp bot.

* **Terminal A:** `redis-server`
* **Terminal B:** `python3 call_agent.py` (No flags)

**Execution Steps:**
1.  Open your browser to `http://localhost:7860/client`.
2.  Click **"Connect"** and start talking.
3.  The agent will speak at **24000Hz** (High Definition).

---

### 3. WhatsApp Bot & Payment Webhooks
This boots the WhatsApp webhook listener and the Razorpay payment confirmation logic.

* **Terminal A:** `redis-server`
* **Terminal B:** `ngrok http 8000`
* **Terminal C:** `python3 whatsapp_agent.py`

**Execution Steps:**
* **WhatsApp:** Send "Hi" or "Reset" to your Meta Test Number. Meta will ping `/whatsapp-webhook`.
* **Payments:** When a payment is completed, Razorpay pings `/razorpay-webhook`.
* **Note:** This terminal **must** be running to handle database updates and confirmation receipts for **both** the voice and chat bots.

---

## 💡 Troubleshooting

| Problem | Cause | Solution |
| :--- | :--- | :--- |
| **502 Bad Gateway** | Ngrok is running but the Python script is not. | Start `call_agent.py --twilio` or `whatsapp_agent.py`. |
| **Port 8000 in use** | Two scripts are trying to use port 8000. | Stop one script before starting the other. |
| **No WhatsApp receipt** | `whatsapp_agent.py` is not running. | The Webhook server must be active to "hear" the payment from Razorpay. |
| **App Error (Twilio)** | URL is wrong in Twilio console. | Ensure the URL ends in `/voice`, not just the ngrok root. |

---

## ✅ Summary Checklist for a Full Test
1.  Start **Redis** and **Ngrok**.
2.  Run `whatsapp_agent.py` — *Handles chat + payment receipts.*
3.  Run `call_agent.py` — *Handles browser voice UI.*
4.  **Update URLs:** Ensure your Meta Dashboard and Razorpay Webhook settings match your current Ngrok link.
5.  **Test:** Book via Voice UI -> Pay via WhatsApp link -> Receive final confirmation receipt.
