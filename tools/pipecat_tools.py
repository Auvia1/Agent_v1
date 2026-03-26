#tools/pipecat_tools.py
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from tools.pool import init_tool_db, get_pool  # re-export so agent.py import path stays the same

# Import the actual tool functions
from tools.availability import check_availability
from tools.booking import voice_book_appointment
from tools.followup import verify_followup
from tools.language import switch_language, end_call

# 1. Availability Schema
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

# 2. Follow-Up Schema
verify_followup_schema = FunctionSchema(
    name="verify_followup",
    description="Checks if a user is eligible for a free follow-up appointment. Call this SILENTLY if the user asks for a follow-up and provides their phone number.",
    properties={
        "phone": {"type": "string", "description": "The 10-digit phone number."}
    },
    required=["phone"]
)

# 3. Booking Schema
voice_book_appointment_schema = FunctionSchema(
    name="voice_book_appointment",
    description="Books the appointment in the database. CRITICAL INSTRUCTION: When you call this tool, your response MUST consist ONLY of the function call. You are FORBIDDEN from generating any text, confirmation, or questions alongside this tool call.",
    properties={
        "doctor_id": {
            "type": "string",
            "description": "The exact 36-character UUID of the doctor."
        },
        "patient_name": {
            "type": "string",
            "description": "The name of the patient."
        },
        "start_time_iso": {
            "type": "string",
            "description": "The start time in ISO 8601 format."
        },
        "phone": {
            "type": "string",
            "description": "The 10-digit phone number."
        },
        "reason": {
            "type": "string",
            "description": "The medical problem or symptoms."
        },
        "force_book": {
            "type": "boolean",
            "description": "Set to true ONLY if the user explicitly confirmed they want to overwrite/add to their existing appointment."
        },
        "is_followup": {
            "type": "string", 
            "description": "'unknown' by default. Pass 'yes' if user confirms it is a 7-day free follow-up, 'no' if new."
        },
        "is_same_patient": {
            "type": "string",
            "description": "'unknown' by default. Pass 'yes' if the user confirms they are the same person updating their name, or 'no' if they are a new family member."
        }
    },
    required=["doctor_id", "patient_name", "start_time_iso", "phone", "reason"],
)

# 4. Language Switch Schema
switch_language_schema = FunctionSchema(
    name="switch_language",
    description="Changes the spoken language of the AI to match the user. Call this IMMEDIATELY if the user starts speaking in Hindi or Telugu, or explicitly asks to change the language.",
    properties={
        "language": {
            "type": "string",
            "description": "The language to switch to. Must be 'english', 'hindi', or 'telugu'."
        }
    },
    required=["language"]
)

# --- EXPORTED HELPERS ---

def register_all_tools(llm):
    """Registers all tools with the LLM service with appropriate timeouts."""
    llm.register_direct_function(check_availability, cancel_on_interruption=False, timeout_secs=20.0)
    llm.register_direct_function(verify_followup, cancel_on_interruption=False, timeout_secs=15.0)
    llm.register_direct_function(voice_book_appointment, cancel_on_interruption=False, timeout_secs=30.0)
    llm.register_direct_function(switch_language, cancel_on_interruption=False)
    llm.register_direct_function(end_call, cancel_on_interruption=False)

def get_tools_schema():
    """Returns the bundled ToolsSchema for the LLM context."""
    return ToolsSchema(standard_tools=[
        check_availability_schema,
        verify_followup_schema,
        voice_book_appointment_schema,
        switch_language_schema,
        end_call
    ])