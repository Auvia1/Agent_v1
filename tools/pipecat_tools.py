#tools/pipecat_tools.py
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.services.llm_service import FunctionCallParams
from pipecat.frames.frames import EndTaskFrame, TTSUpdateSettingsFrame
from pipecat.processors.frame_processor import FrameDirection
from loguru import logger

from tools.pool import init_tool_db, get_pool  # re-export so agent.py import path stays the same

# Import the actual tool functions
from tools.availability import check_availability
from tools.booking import voice_book_appointment
from tools.followup import verify_followup
from tools.faq import query_clinic_faq  # ✅ NEW: Imported FAQ tool

# ==========================================================
# 🛑 END CALL & LANGUAGE TOOLS 
# ==========================================================
async def switch_language(params: FunctionCallParams, language: str):
    """Switch the spoken language of the bot."""
    lang_lower = language.lower()
    
    # 👇 FIXED: Correctly mapping to Google Chirp 3 HD Female Voices
    if lang_lower == "telugu":
        lang_code = "te-IN"
        voice_id = "te-IN-Chirp3-HD-Despina"
    elif lang_lower == "hindi":
        lang_code = "hi-IN"
        voice_id = "hi-IN-Chirp3-HD-Despina"
    else:
        lang_code = "en-US"
        voice_id = "en-US-Chirp3-HD-Despina"
        
    logger.info(f"🗣️ Switching language to {language} | Voice: {voice_id}")
    
    await params.llm.push_frame(
        TTSUpdateSettingsFrame(settings={"language": lang_code, "voice": voice_id, "speaking_rate": 1.15})
    )
    await params.result_callback({"status": f"Language switched to {language.capitalize()}."})

async def end_call(params: FunctionCallParams):
    """Ends the phone call gracefully after letting the final TTS audio play."""
    logger.info("👋 LLM requested to end the call. Queuing graceful shutdown...")
    await params.llm.push_frame(EndTaskFrame(), FrameDirection.UPSTREAM)
    await params.result_callback({"status": "Call ending initiated."})


# ==========================================================
# 📋 SCHEMAS
# ==========================================================

# ✅ NEW: FAQ Schema
query_clinic_faq_schema = FunctionSchema(
    name="query_clinic_faq",
    description="Search the clinic's official documents for information about policies, cancellations, doctors, surgeries, and general hospital info. Call this if the user asks any general question.",
    properties={
        "user_question": {
            "type": "string",
            "description": "The specific question the user asked (e.g., 'What are your surgery charges?' or 'Can I cancel my booking?')."
        }
    },
    required=["user_question"]
)

check_availability_schema = FunctionSchema(
    name="check_availability",
    description="Checks doctor availability by specialty. CRITICAL: You must execute this tool SILENTLY. DO NOT output any text when calling this.",
    properties={
        "problem_or_speciality": {
            "type": "string",
            "description": "CRITICAL: Map user symptoms to: 'General Physician', 'Dermatologist', 'Cardiologist', 'Pediatrician', 'Orthopedic', or 'Urologist'."
        },
        "requested_date": {
            "type": "string",
            "description": "Optional (YYYY-MM-DD). ONLY provide if the user specifies a day."
        }
    },
    required=["problem_or_speciality"]
)

verify_followup_schema = FunctionSchema(
    name="verify_followup",
    description="Checks if a user is eligible for a free follow-up appointment. Call this SILENTLY if the user asks for a follow-up and provides their phone number.",
    properties={
        "phone": {"type": "string", "description": "The 10-digit phone number."}
    },
    required=["phone"]
)

voice_book_appointment_schema = FunctionSchema(
    name="voice_book_appointment",
    description="Books the appointment in the database. CRITICAL INSTRUCTION: When you call this tool, your response MUST consist ONLY of the function call.",
    properties={
        "doctor_id": {"type": "string", "description": "The exact 36-character UUID of the doctor."},
        "patient_name": {"type": "string", "description": "The name of the patient. CRITICAL: MUST be translated to plain English characters (e.g., 'Ravi', NOT 'రవి')."},
        "start_time_iso": {
            "type": "string", 
            "description": "The start time in ISO 8601 format (e.g., '2026-04-18T17:00:00'). CRITICAL: You must use the strict YYYY-MM-DDTHH:MM:SS format. NEVER use literal words like 'TODAY' or 'TOMORROW' in this field."
        },
        "phone": {"type": "string", "description": "The 10-digit phone number."},
        "reason": {"type": "string", "description": "The medical problem or symptoms. CRITICAL: MUST be translated to plain English (e.g., 'fever and cold', NOT 'జ్వరం, జలుబు')."},
        "force_book": {"type": "boolean", "description": "Set to true ONLY if the user explicitly confirmed they want to overwrite/add to their existing appointment."},
        "is_followup": {"type": "string", "description": "'unknown' by default. Pass 'yes' if user confirms it is a 7-day free follow-up, 'no' if new."},
        "is_same_patient": {"type": "string", "description": "'unknown' by default. Pass 'yes' if the user confirms they are the same person updating their name, or 'no' if they are a new family member."}
    },
    required=["doctor_id", "patient_name", "start_time_iso", "phone", "reason"],
)

switch_language_schema = FunctionSchema(
    name="switch_language",
    # 👇 FIXED: Strict tool instructions to prevent hallucinatory language switching
    description="Changes the spoken language of the AI. CRITICAL: ONLY call this if the user EXPLICITLY asks to change the language (e.g., 'speak in Hindi', 'telugu lo matladu'). DO NOT call this just because the user says a few words in another language.",
    properties={
        "language": {"type": "string", "description": "The language to switch to. Must be 'english', 'hindi', or 'telugu'."}
    },
    required=["language"]
)

# ==========================================================
# 🛠️ EXPORTED HELPERS
# ==========================================================
def register_all_tools(llm):
    llm.register_direct_function(check_availability, cancel_on_interruption=False)
    llm.register_direct_function(verify_followup, cancel_on_interruption=False)
    llm.register_direct_function(voice_book_appointment, cancel_on_interruption=False)
    llm.register_direct_function(query_clinic_faq, cancel_on_interruption=False) # ✅ Registered
    llm.register_direct_function(switch_language, cancel_on_interruption=False)
    llm.register_direct_function(end_call, cancel_on_interruption=False)

def get_tools_schema():
    return ToolsSchema(standard_tools=[
        check_availability_schema,
        verify_followup_schema,
        voice_book_appointment_schema,
        query_clinic_faq_schema, # ✅ Added to Schema
        switch_language_schema,
        end_call
    ])