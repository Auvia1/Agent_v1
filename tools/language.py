#gemini language switcher for tts
# from pipecat.services.llm_service import FunctionCallParams
# from pipecat.frames.frames import TTSUpdateSettingsFrame
# from loguru import logger

# async def switch_language(params: FunctionCallParams, language: str):
#     """Switch the spoken language of the bot."""
#     lang_lower = language.lower()
    
#     # Map to Google Cloud Chirp 3 HD Voices
#     if lang_lower == "telugu":
#         lang_code = "te-IN"
#         voice_id = "te-IN-Chirp3-HD-Despina"
#     elif lang_lower == "hindi":
#         lang_code = "hi-IN"
#         voice_id = "hi-IN-Chirp3-HD-Despina"
#     else:
#         lang_code = "en-US"
#         voice_id = "en-US-Chirp3-HD-Despina"
        
#     logger.info(f"🗣️ Switching language to {language} | Voice: {voice_id}")
    
#     # FIXED: Pipecat expects 'voice' and 'language' keys
#     await params.llm.push_frame(
#         TTSUpdateSettingsFrame(settings={"language": lang_code, "voice": voice_id})
#     )
    
#     await params.result_callback({"status": f"Language switched to {language.capitalize()}."})

#tools/language.py
from pipecat.services.llm_service import FunctionCallParams
from pipecat.frames.frames import TTSUpdateSettingsFrame, EndTaskFrame
from pipecat.processors.frame_processor import FrameDirection
from loguru import logger

async def switch_language(params: FunctionCallParams, language: str):
    """Switch the spoken language of the bot."""
    lang_lower = language.lower()
    
    if lang_lower == "telugu":
        lang_code = "te-IN"
    elif lang_lower == "hindi":
        lang_code = "hi-IN"
    else:
        lang_code = "en-IN"
        
    logger.info(f"🗣️ Switching language to {language} | Voice: anushka")
    
    await params.llm.push_frame(
        TTSUpdateSettingsFrame(settings={"language": lang_code, "voice": "anushka"})
    )
    await params.result_callback({"status": f"Language switched to {language.capitalize()}."})

async def end_call(params: FunctionCallParams):
    """Ends the phone call gracefully after letting the final TTS audio play."""
    logger.info("👋 LLM requested to end the call. Queuing graceful shutdown...")
    
    # Push EndTaskFrame UPSTREAM. This allows all pending audio frames to play out first.
    await params.llm.push_frame(EndTaskFrame(), FrameDirection.UPSTREAM)
    await params.result_callback({"status": "Call ending initiated."})