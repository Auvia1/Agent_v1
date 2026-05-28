# web_ui_tester.py
import os
import json
import time
import base64
import asyncio
from loguru import logger
from fastapi import APIRouter, WebSocket
from fastapi.responses import HTMLResponse

from pipecat.serializers.base_serializer import FrameSerializer
from pipecat.frames.frames import (
    Frame, AudioRawFrame, CancelFrame, TextFrame, TranscriptionFrame, 
    FunctionCallInProgressFrame, FunctionCallResultFrame
)
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.transports.websocket.fastapi import FastAPIWebsocketTransport, FastAPIWebsocketParams

from pipecat.services.sarvam.stt import SarvamSTTService
from pipecat.services.sarvam import SarvamTTSService # 👈 Added Sarvam TTS
from pipecat.services.google.llm import GoogleLLMService

from tools.pipecat_tools import register_all_tools, get_tools_schema

# Re-use the existing logic from your call agent
from call_agent import (
    ensure_redis_client,
    VOICE_SYSTEM_PROMPT,
    STTTextCleanerProcessor,
    AutoLanguageProcessor,
    BillingTracker,
    PipecatBugFixProcessor,
    save_call_log
)

router = APIRouter()

# ==========================================================
# 🔍 ENHANCED UI TRACKER (Tracks Text + Function Calls)
# ==========================================================
class EnhancedUITracker(FrameProcessor):
    def __init__(self, frontend_ws: WebSocket, role: str):
        super().__init__()
        self.frontend_ws = frontend_ws
        self.role = role

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        
        if not self.frontend_ws:
            return

        try:
            # 1. Track User Speech (STT)
            if self.role == "user" and direction == FrameDirection.DOWNSTREAM and isinstance(frame, TranscriptionFrame):
                await self.frontend_ws.send_text(json.dumps({"event": "text", "text": frame.text, "sender": "user"}))
            
            # 2. Track AI Output and Function/Tool Executions
            elif self.role == "ai":
                if direction == FrameDirection.DOWNSTREAM and isinstance(frame, TextFrame):
                    await self.frontend_ws.send_text(json.dumps({"event": "text", "text": frame.text, "sender": "ai"}))
                
                # When LLM decides to call a tool
                elif isinstance(frame, FunctionCallInProgressFrame):
                    await self.frontend_ws.send_text(json.dumps({
                        "event": "function_call",
                        "name": frame.function_name,
                        "args": frame.arguments
                    }))
                    
                # When the Tool returns the database success/failure result
                elif isinstance(frame, FunctionCallResultFrame):
                    result_data = getattr(frame, "result", getattr(frame, "tool_return", {}))
                    await self.frontend_ws.send_text(json.dumps({
                        "event": "function_result",
                        "name": frame.function_name,
                        "result": result_data
                    }))
        except Exception:
            pass
            
        await self.push_frame(frame, direction)

# ==========================================================
# 🧪 WEB TEST PIPELINE RUNNER
# ==========================================================
async def run_local_test_bot(websocket: WebSocket, session_call_uuid: str):
    await ensure_redis_client()
    short_session_id = session_call_uuid[:8]

    # Local UI Transport
    active_transport = FastAPIWebsocketTransport(
        websocket=websocket, 
        params=FastAPIWebsocketParams(
            audio_in_enabled=True, 
            audio_out_enabled=True, 
            add_wav_header=False, 
            vad_enabled=True,
            serializer=WebTestSerializer()
        )
    )

    stt_service = SarvamSTTService(api_key=os.getenv("SARVAM_API_KEY"), language="unknown", model="saaras:v3", mode="transcribe")
    
    # 👈 Swapped to Sarvam TTS
    tts_service = SarvamTTSService(
        api_key=os.getenv("SARVAM_API_KEY"),
        settings=SarvamTTSService.Settings(
            model="bulbul:v3",
            voice="priya",
            language="te-IN"
        )
    )
    
    llm_service = GoogleLLMService(api_key=os.getenv("GEMINI_API_KEY"), model="gemini-2.5-flash")

    register_all_tools(llm_service)

    sys_context = LLMContext(messages=[{"role": "system", "content": VOICE_SYSTEM_PROMPT}], tools=get_tools_schema())
    context_aggregator = LLMContextAggregatorPair(sys_context)
    bill_tracker = BillingTracker(sys_context, short_session_id)

    pipeline = Pipeline([
        active_transport.input(),
        stt_service,
        STTTextCleanerProcessor(short_session_id),
        EnhancedUITracker(websocket, "user"), # <--- Catches User STT
        context_aggregator.user(),
        llm_service,
        EnhancedUITracker(websocket, "ai"),   # <--- Catches LLM Text & Functions
        bill_tracker,
        AutoLanguageProcessor(short_session_id),
        tts_service,
        PipecatBugFixProcessor(),
        active_transport.output(),
        context_aggregator.assistant()
    ])

    task_pipeline = PipelineTask(pipeline, params=PipelineParams(audio_in_sample_rate=8000, audio_out_sample_rate=8000))

    bot_runner = PipelineRunner(handle_sigint=False)
    await bot_runner.run(task_pipeline)

    bill_tracker.generate_receipt()
    final_call_duration = time.time() - bill_tracker.timer_start
    await save_call_log(session_call_uuid, "9999999999", final_call_duration, sys_context.messages)

# ==========================================================
# 🧪 LOCAL TESTING UI & ROUTING
# ==========================================================
class WebTestSerializer(FrameSerializer):
    async def serialize(self, frame: Frame) -> str | None:
        if isinstance(frame, AudioRawFrame):
            return json.dumps({"event": "media", "payload": base64.b64encode(frame.audio).decode("utf-8")})
        elif isinstance(frame, CancelFrame):
            return json.dumps({"event": "stop"})
        return None

    async def deserialize(self, data: str | bytes) -> Frame | None:
        try:
            parsed_msg = json.loads(data if isinstance(data, str) else data.decode("utf-8"))
            if parsed_msg.get("event") == "media":
                pcm_bytes_raw = base64.b64decode(parsed_msg.get("payload"))
                frame = AudioRawFrame(audio=pcm_bytes_raw, sample_rate=8000, num_channels=1)
                frame.id = "inbound-audio-id"
                frame.pts, frame.transport_destination, frame.broadcast_sibling_id = None, None, None
                return frame
            elif parsed_msg.get("event") == "stop":
                return CancelFrame()
        except Exception: pass
        return None

@router.get("/test-ui")
async def get_test_ui():
    html_page = """
    <!DOCTYPE html>
    <html>
    <head><title>Mithra AI Developer Console</title><style>
        body { font-family: Arial; max-width: 800px; margin: 40px auto; padding: 20px; background: #1e1e1e; color: #f4f4f4;} 
        #chat { background: #2d2d2d; height: 500px; overflow-y: auto; padding: 15px; border-radius: 8px; border: 1px solid #444; display: flex; flex-direction: column;} 
        .msg { margin-bottom: 12px; padding: 12px 18px; border-radius: 10px; max-width: 85%; line-height: 1.5; font-size: 15px;} 
        .msg.user { background: #007bff; color: white; align-self: flex-end; border-bottom-right-radius: 3px; } 
        .msg.ai { background: #444; color: white; align-self: flex-start; border-bottom-left-radius: 3px; }
        .msg.sys { background: #332b00; color: #f3da35; font-family: monospace; font-size: 13px; align-self: center; width: 95%; border-left: 4px solid #f3da35; white-space: pre-wrap; word-wrap: break-word;}
        .controls { display: flex; gap: 10px; margin-top: 15px; }
        button { padding: 15px; border-radius: 5px; border: none; cursor: pointer; color: white; font-weight: bold; font-size: 15px; transition: 0.2s;}
        #startBtn { background: #28a745; width: 100%; font-size: 16px; margin-bottom: 10px; }
        #muteBtn { background: #dc3545; flex: 1; }
        #endBtn { background: #6c757d; flex: 1; }
        button:disabled { opacity: 0.6; cursor: not-allowed; }
    </style></head>
    <body>
        <h2>🏥 Mithra AI - Live Tracing Console</h2>
        <button id="startBtn">Start Conversation</button>
        <div id="chat"></div>
        <div class="controls">
            <button id="muteBtn" disabled>Mute Microphone</button>
            <button id="endBtn" disabled>End Call</button>
        </div>
        <script>
            let ws; let audioCtx; let nextTime = 0; let isMuted = false; 
            let source; let processor; let micStream;
            
            const chat = document.getElementById('chat'); 
            const startBtn = document.getElementById('startBtn');
            const muteBtn = document.getElementById('muteBtn');
            const endBtn = document.getElementById('endBtn');
            let lastSender = null; let lastBubble = null;

            function log(text, sender) { 
                if (sender === 'ai' && lastSender === 'ai' && lastBubble) {
                    lastBubble.innerText += text; 
                } else {
                    const div = document.createElement('div'); 
                    div.className = 'msg ' + sender; 
                    if(sender === 'user') div.innerText = '🗣️ You: ' + text;
                    else if(sender === 'ai') div.innerText = '🤖 AI: ' + text;
                    else div.innerText = text;
                    chat.appendChild(div); 
                    lastBubble = div;
                }
                lastSender = sender;
                chat.scrollTop = chat.scrollHeight; 
            }

            startBtn.onclick = async () => {
                startBtn.disabled = true; startBtn.innerText = "Connecting...";
                audioCtx = new (window.AudioContext || window.webkitAudioContext)({sampleRate: 8000});
                try { micStream = await navigator.mediaDevices.getUserMedia({ audio: true }); } 
                catch (err) { alert("Microphone access required!"); startBtn.disabled = false; startBtn.innerText = "Start Conversation"; return; }
                
                source = audioCtx.createMediaStreamSource(micStream);
                processor = audioCtx.createScriptProcessor(1024, 1, 1);
                ws = new WebSocket((location.protocol==='https:'?'wss:':'ws:')+'//'+location.host+'/test-media');
                
                processor.onaudioprocess = (e) => {
                    if (isMuted || ws.readyState !== WebSocket.OPEN) return;
                    const input = e.inputBuffer.getChannelData(0);
                    const pcm16 = new Int16Array(input.length);
                    for (let i=0; i<input.length; i++) { 
                        let s = Math.max(-1, Math.min(1, input[i]*2.5)); 
                        pcm16[i] = s<0 ? s*0x8000 : s*0x7FFF; 
                    }
                    ws.send(JSON.stringify({event: "media", payload: btoa(String.fromCharCode(...new Uint8Array(pcm16.buffer)))}));
                };
                
                const zeroGain = audioCtx.createGain(); zeroGain.gain.value = 0;
                source.connect(processor); processor.connect(zeroGain); zeroGain.connect(audioCtx.destination);

                ws.onopen = () => { 
                    startBtn.style.display = 'none'; muteBtn.disabled = false; endBtn.disabled = false;
                };
                
                ws.onmessage = (e) => {
                    const d = JSON.parse(e.data);
                    if (d.event === 'media') {
                        const b = atob(d.payload); const bytes = new Int16Array(b.length/2);
                        for (let i=0; i<b.length; i+=2) bytes[i/2] = b.charCodeAt(i) | (b.charCodeAt(i+1)<<8);
                        const f = new Float32Array(bytes.length); for (let i=0; i<bytes.length; i++) f[i] = bytes[i]/32768.0;
                        const buf = audioCtx.createBuffer(1, f.length, 8000); buf.getChannelData(0).set(f);
                        const s = audioCtx.createBufferSource(); s.buffer = buf; s.connect(audioCtx.destination);
                        if (nextTime < audioCtx.currentTime) nextTime = audioCtx.currentTime;
                        s.start(nextTime); nextTime += buf.duration;
                    } 
                    else if (d.event === 'text') log(d.text, d.sender);
                    else if (d.event === 'function_call') log('🛠️ CALLING DATABASE: ' + d.name + '\\nARGS: ' + JSON.stringify(d.args, null, 2), 'sys');
                    else if (d.event === 'function_result') log('✅ DB RESULT (' + d.name + '):\\n' + JSON.stringify(d.result, null, 2), 'sys');
                };
            };

            muteBtn.onclick = () => {
                isMuted = !isMuted;
                muteBtn.innerText = isMuted ? "Unmute Microphone" : "Mute Microphone";
                muteBtn.style.background = isMuted ? "#6c757d" : "#dc3545";
            };

            endBtn.onclick = () => {
                if(ws) ws.close(); if(processor) processor.disconnect(); if(source) source.disconnect();
                if(micStream) micStream.getTracks().forEach(t => t.stop());
                log("Call Ended.", "ai");
                muteBtn.disabled = true; endBtn.disabled = true;
                startBtn.style.display = 'block'; startBtn.innerText = "Start New Call"; startBtn.disabled = false;
            };
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_page)

@router.websocket("/test-media")
async def test_websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    session_call_uuid = f"web_test_{int(time.time())}"
    try:
        await run_local_test_bot(websocket, session_call_uuid)
    except Exception as e:
        logger.error(f"❌ Web Test WebSocket error: {e}")