import asyncio
import json
from contextlib import asynccontextmanager
import uuid
from typing import Optional
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from sse_starlette.sse import EventSourceResponse

from .utils.agent_bus import bus, session_var
from .agents.travel_manager_agent import TravelManagerAgent
from .agents.route_agent import RouteAgent
from .agents.traffic_agent import TrafficAgent
from .agents.weather_agent import WeatherAgent
from .agents.charging_agent import ChargingAgent
from .agents.food_agent import FoodAgent
from .agents.telemetry_agent import TelemetryAgent
from .agents.curation_agent import CurationAgent
from .agents.optimization_agent import OptimizationAgent
from .agents.memory_agent import MemoryAgent
from .agents.vehicle_brain_agent import VehicleBrainAgent
from .agents.mission_control import _mission_control_instance
from .database.db import init_db
from .routers import chat # 👈 Moved here

# Reset Bus to prevent duplicate subscribers during hot-reloads
bus.clear_all_listeners()

# Initialize Agents
manager = TravelManagerAgent()
route_agent = RouteAgent()
traffic_agent = TrafficAgent()
weather_agent = WeatherAgent()
charging_agent = ChargingAgent()
food_agent = FoodAgent()
telemetry_agent = TelemetryAgent()
curation_agent = CurationAgent()
optimization_agent = OptimizationAgent()
memory_agent = MemoryAgent()
vehicle_brain = VehicleBrainAgent()
mission_control = _mission_control_instance()  # Central Orchestrator

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize Database
    await init_db()
    yield
    # Shutdown: Add cleanup logic here if needed

app = FastAPI(title="AI EV TRAVEL ASSISTANT", lifespan=lifespan)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Models ---
class TripRequest(BaseModel):
    origin: str
    destination: str
    battery_level: int = 70
    preference: Optional[str] = "vegetarian"
    departure_time: Optional[str] = None
    session_id: Optional[str] = None

    @field_validator("battery_level", mode="before")
    @classmethod
    def parse_battery(cls, v):
        if isinstance(v, str):
            v = v.replace("%", "").replace("percentage", "").replace("pct", "").strip()
            try:
                return int(v)
            except ValueError:
                return 70
        return v

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    print(f"DEBUG: Validation Error at {request.url.path} -> {exc.errors()}")
    return JSONResponse(status_code=422, content={"detail": exc.errors()})

@app.post("/plan")
async def create_plan(request: TripRequest):
    req_payload = request.model_dump()
    session_id = req_payload.get("session_id")
    if not session_id:
        session_id = str(uuid.uuid4())
        req_payload["session_id"] = session_id
    session_var.set(session_id)

    loop = asyncio.get_running_loop()
    future = loop.create_future()

    async def on_plan_complete(plan):
        if plan.get("session_id") == session_id:
            if not future.done():
                future.set_result(plan)

    async def on_error(err):
        if err.get("session_id") == session_id:
            if not future.done():
                future.set_result(
                    {"error": f"Agent {err.get('agent')}: {err.get('message')}"}
                )

    bus.subscribe("plan_complete", on_plan_complete)
    bus.subscribe("error", on_error)

    await manager.start_planning(req_payload)

    try:
        result = await asyncio.wait_for(future, timeout=60.0)
        return result
    except asyncio.TimeoutError:
        return {"error": "Planning timed out"}

@app.get("/stream")
async def message_stream(request: Request, session_id: Optional[str] = None):
    async def event_generator():
        queue = asyncio.Queue()
        
        # 🎙 Forward all critical events to the UI
        async def on_event(data):
            if not session_id or not isinstance(data, dict) or data.get("session_id") == session_id or "session_id" not in data:
                await queue.put(data)
            
        events = [
            "alert_driver",      # Jarvis proactive chat
            "plan_complete",     # Final timeline (includes jarvis_briefing)
            "traffic_data",      # Progress indicator
            "weather_data",      # Progress indicator
            "charging_data",     # Progress indicator
            "connection_request", # Connection requests from external agents
            "evai_callback"      # Approval/rejection callbacks
        ]
        
        for e in events:
            bus.subscribe(e, on_event)
        
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=1.0)
                    yield {"data": json.dumps(data)}
                except asyncio.TimeoutError:
                    continue
        finally:
            for e in events:
                bus.unsubscribe(e, on_event)

    return EventSourceResponse(event_generator())

@app.get("/tts")
async def get_tts(text: str):
    import aiohttp
    import os
    import edge_tts
    from fastapi.responses import StreamingResponse
    
    # ElevenLabs TTS Configuration
    api_key = os.getenv("ELEVENLABS_API_KEY")
    voice_id = "P7vsEyTOpZ6YUTulin8m" # Tara - Conversational and Expressive
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
    
    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": api_key
    }
    
    payload = {
        "text": text,
        "model_id": "eleven_flash_v2_5", # Ultra-fast model for instant audio
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75
        }
    }
    
    async def get_audio_bytes():
        # Try ElevenLabs first if API key is configured
        if api_key:
            try:
                async with aiohttp.ClientSession() as session:
                    # Use non-stream endpoint for faster full-file download since we buffer it anyway
                    sync_url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
                    async with session.post(sync_url, json=payload, headers=headers) as resp:
                        if resp.status == 200:
                            return await resp.read()
                        else:
                            print(f"ElevenLabs API Error: {await resp.text()}")
                            print("Falling back to edge-tts...")
            except Exception as e:
                print(f"ElevenLabs Connection Error: {e}")
                print("Falling back to edge-tts...")
            
        # Fallback to Edge-TTS if ElevenLabs fails
        communicate = edge_tts.Communicate(text, "en-US-AriaNeural", rate="+10%")
        audio_data = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data += chunk["data"]
        return audio_data
                    
    audio_bytes = await get_audio_bytes()
    from fastapi.responses import Response
    return Response(content=audio_bytes, media_type="audio/mpeg")

@app.post("/api/connection-requests")
@app.post("/api/agents/connection-requests")
@app.post("/api/agents/connection-request")
@app.post("/api/connection-request")
@app.post("/connection-request")
async def handle_connection_request(request: Request):
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}
        
    client_ip = request.client.host
    callback_url = payload.get("callback_url") or payload.get("payload", {}).get("callback_url", "")
    
    # Fix the callback URL if the external agent mistakenly sends localhost
    if callback_url and ("localhost" in callback_url or "127.0.0.1" in callback_url):
        callback_url = callback_url.replace("localhost", client_ip).replace("127.0.0.1", client_ip)
    
    agent_name = payload.get("agent") or payload.get("system", "echo")
    
    # Format for the frontend
    event_data = {
        "type": "connection_request",
        "agent": agent_name,
        "message": payload.get("message", f"An external agent '{agent_name}' is trying to comment"),
        "payload": payload,
        "resolved_callback_url": callback_url
    }
    
    await bus.publish("connection_request", event_data)
    
    # We return accepted immediately or pending based on original behavior.
    # The external agent waits for the callback for the actual user decision.
    return {"status": "pending"}

@app.post("/api/agents/evai-callback")
async def handle_evai_callback(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    
    status = payload.get("status", "unknown")
    callback_url = payload.get("callback_url", "")
    print(f"EVAI Callback received: {status}. Forwarding to: {callback_url}")
    
    # Publish to AgentBus so the frontend knows (if it's listening)
    await bus.publish("evai_callback", {"status": status})
    
    try:
        if not callback_url or callback_url == "http://172.20.10.7:8000/api/agents/evai-callback":
            # Prevent infinite loops if the external agent sends our own URL back
            print("Invalid or loopback callback URL detected. Skipping forward.")
            return {"status": "success", "decision": status, "note": "Forwarding skipped (loopback URL)"}
            
        import httpx
        async with httpx.AsyncClient() as client:
            print(f"Executing async POST to: {callback_url}")
            response = await client.post(callback_url, json={"status": status}, timeout=5.0)
            print(f"External agent response: {response.text}")
            
            return {"status": "success", "decision": status, "response": response.text}
    except Exception as e:
        print(f"Failed to execute external callback: {e}")
        return {"status": "error", "message": str(e)}
    
    return {"status": "forwarded", "decision": status}

app.include_router(chat.router)
app.include_router(chat.router, prefix="/api/v1")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("ev_travel_agent.main:app", host="0.0.0.0", port=8000, reload=True)
