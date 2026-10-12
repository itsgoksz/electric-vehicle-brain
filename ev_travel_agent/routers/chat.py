from fastapi import APIRouter, Request
from pydantic import BaseModel
from typing import List, Optional, Literal, Any
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from datetime import datetime
import json
import re
from enum import Enum
from pydantic import ValidationError
from pydantic import BaseModel, ConfigDict
import uuid
from ..utils.agent_bus import session_var

router = APIRouter()


# ── Tool Layer ─────────────────────────────────────────────────────────────
def get_vehicle_state():
    return {"battery": 67, "range_km": 312, "charging": False}


# ── Intent Router ──────────────────────────────────────────────────────────
class Intent(str, Enum):
    PROVIDE_INFO = "provide_info"
    ASK_STATUS = "ask_status"
    MODIFY_TRIP = "modify_trip"
    SMALL_TALK = "small_talk"
    UNKNOWN = "unknown"


def classify_intent(text: str) -> str:
    t = text.lower()
    if any(x in t for x in ["%", "battery", "charge", "percent"]):
        return Intent.PROVIDE_INFO
    if any(x in t for x in ["from", "to", "going to", "headed to"]):
        return Intent.PROVIDE_INFO
    if any(x in t for x in ["when", "time", "leave", "am", "pm", "tomorrow", "today"]):
        return Intent.PROVIDE_INFO
    if any(x in t for x in ["status", "ready", "summary", "where are we"]):
        return Intent.ASK_STATUS
    if any(x in t for x in ["change", "update", "instead", "cancel"]):
        return Intent.MODIFY_TRIP
    if len(t.split()) < 3 and any(x in t for x in ["hi", "hello", "hey", "sup"]):
        return Intent.SMALL_TALK
    return Intent.UNKNOWN


# ── Deterministic Extractors ───────────────────────────────────────────────
def extract_battery(text: str):
    m = re.search(r"(\d{1,3})\s*%", text)
    if not m:
        m = re.search(r"(\d{1,3})\s*percent", text, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _strip_time_from_location(loc: str) -> str:
    """Remove trailing time/date phrases from a location string.
    E.g. 'Chennai at 5pm today' → 'Chennai',  'Ooty tomorrow morning' → 'Ooty'
    """
    if not loc:
        return loc
    # Strip trailing patterns: "at 5pm", "at 8am tomorrow", "tonight", "today", "tomorrow", "by 7pm", etc.
    loc = re.sub(
        r"\s+(?:at|by|around|before|after)\s+\d{1,2}\s*(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?).*$",
        "",
        loc,
        flags=re.IGNORECASE,
    ).strip()
    # Strip standalone time/date words at the end (including "tomorrow morning", "today evening", etc.)
    loc = re.sub(
        r"\s+(?:today|tonight|tomorrow|this\s+(?:morning|afternoon|evening|night)|in\s+the\s+(?:morning|afternoon|evening))(?:\s+(?:morning|afternoon|evening|night))?$",
        "",
        loc,
        flags=re.IGNORECASE,
    ).strip()
    # Strip standalone time at the end: "5pm", "8am"
    loc = re.sub(
        r"\s+\d{1,2}\s*(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)$", "", loc, flags=re.IGNORECASE
    ).strip()
    return loc


def extract_origin_destination(text: str):
    # Match "from X to Y"
    m_both = re.search(r"from (.+?) to (.+)", text, re.IGNORECASE)
    if m_both:
        return _strip_time_from_location(
            m_both.group(1).strip()
        ), _strip_time_from_location(m_both.group(2).strip())

    # Match "to X from Y" (reversed order, e.g. "traveling to madurai from JP nagar")
    m_both_rev = re.search(
        r"(?:to|traveling to|going to|headed to) (.+?) from (.+)", text, re.IGNORECASE
    )
    if m_both_rev:
        dest = _strip_time_from_location(m_both_rev.group(1).strip())
        orig = _strip_time_from_location(m_both_rev.group(2).strip())
        if len(dest) > 2 and len(orig) > 2:
            return orig, dest

    # Match "to X" or "going to X" or "traveling to X"
    m_dest = re.search(
        r"(?:to|headed to|going to|traveling to) (.+)", text, re.IGNORECASE
    )
    if m_dest:
        dest = _strip_time_from_location(m_dest.group(1).strip().rstrip(".,!?"))
        if len(dest) > 2 and dest.lower() not in ["my", "the", "a", "it"]:
            return None, dest

    # Match "from X" or "starting from X"
    m_orig = re.search(r"(?:from|starting from|leaving from) (.+)", text, re.IGNORECASE)
    if m_orig:
        orig = _strip_time_from_location(m_orig.group(1).strip().rstrip(".,!?"))
        if len(orig) > 2 and orig.lower() not in ["my", "the", "a"]:
            return orig, None

    return None, None


# ── State Machine ──────────────────────────────────────────────────────────
class TripState(str, Enum):
    COLLECT_ORIGIN = "collect_origin"
    COLLECT_DESTINATION = "collect_destination"
    COLLECT_BATTERY = "collect_battery"
    COLLECT_TIME = "collect_time"
    COLLECT_PREFERENCE = "collect_preference"
    READY = "ready"
    TRIP_PLANNED = "trip_planned"


def get_state(ctx: "ChatContext") -> TripState:
    if ctx.active_trip or ctx.current_trip_plan:
        return TripState.TRIP_PLANNED
    
    if not ctx.origin:
        return TripState.COLLECT_ORIGIN
    if not ctx.destination:
        return TripState.COLLECT_DESTINATION
    if ctx.battery_level is None:
        return TripState.COLLECT_BATTERY

    dep_val = ctx.departure_time or ""
    dep_ok = False
    if dep_val and any(
        x in dep_val.lower() for x in ["now", "immediately", "asap", "right now"]
    ):
        dep_ok = True
    elif dep_val:
        # Improved regex to catch "8am tomorrow", "8:00", etc.
        dep_ok = bool(
            re.search(
                r"\d{1,2}(?::\d{2})?\s*(a\.?m\.?|p\.?m\.?)|\d{1,2}:\d{2}", dep_val, re.IGNORECASE
            )
        )

    if not dep_val or not dep_ok:
        return TripState.COLLECT_TIME
    if not ctx.preference:
        return TripState.COLLECT_PREFERENCE

    # If a mission has already been triggered, we are in follow-up mode
    if ctx.last_mission_checksum:
        return TripState.TRIP_PLANNED

    return TripState.READY


# ── Global LLM Instance ────────────────────────────────────────────────────
llm = ChatOllama(
    model="gemma3:4b",
    temperature=0,
    format="json",
    num_ctx=8192,
    num_predict=256,
    options={
        "top_p": 0.9,
        "repeat_penalty": 1.1,
    },
)


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    role: str
    content: Any  # Allow dict for structured plans in history
    type: Optional[str] = None # Optional message type


class ChatContext(BaseModel):
    model_config = ConfigDict(extra="ignore")
    origin: Optional[str] = None
    destination: Optional[str] = None
    battery_level: Optional[Any] = None  # Use Any to prevent 422 on string inputs
    preference: Optional[str] = None
    departure_time: Optional[str] = None
    last_mission_checksum: Optional[str] = None
    # NEW: Architectural fields for trip session mode
    active_trip: bool = False
    trip_needs_replan: bool = False
    current_trip_plan: Optional[dict] = None
    session_id: Optional[str] = None


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    message: str
    history: List[ChatMessage] = []
    context: ChatContext


class ChatResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reply: str
    origin: Optional[str] = None
    destination: Optional[str] = None
    battery_level: Optional[int] = None
    preference: Optional[str] = None
    departure_time: Optional[str] = None
    is_ready: bool = False
    last_mission_checksum: Optional[str] = None
    # NEW: Echo back session state
    active_trip: bool = False
    trip_needs_replan: bool = False
    current_trip_plan: Optional[dict] = None
    session_id: Optional[str] = None
    # Map URLs for Echo/external agents
    map_url: Optional[str] = None
    directions_url: Optional[str] = None


def _clean(val):
    """Return None if val is a junk/empty string."""
    if val is None:
        return None
    s = str(val).strip().lower()
    if s in (
        "",
        "none",
        "null",
        "unknown",
        "not specified",
        "missing",
        "n/a",
        "location access denied",
    ):
        return None

    # Check if the string explicitly mentions access denied
    if "access denied" in s:
        return None

    return str(val).strip()


def _has_clock_time(val: str) -> bool:
    """Return True only if the string contains an explicit HH:MM or am/pm pattern."""
    if not val:
        return False
    # These are "now"-equivalent strings that are fine without a clock time
    if val.lower() in ["now", "immediately", "asap", "right now"]:
        return True
    return bool(re.search(r"\d{1,2}[:\s]*(a\.?m\.?|p\.?m\.?)|\d{2}:\d{2}", val, re.IGNORECASE))


@router.post("/chat")
async def chat_endpoint(request_data: dict, http_request: Request):
    is_echo = "api/v1" in http_request.url.path
    # ── Nuclear Option: Manual Validation ──────────────────────────────────
    import logging
    chat_logger = logging.getLogger("chat_debug")
    if not chat_logger.handlers:
        handler = logging.FileHandler("chat_debug.log")
        handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
        chat_logger.addHandler(handler)
        chat_logger.setLevel(logging.INFO)

    try:
        # Sanitize battery_level if it's a string like "58%"
        ctx_data = request_data.get("context", {})
        if isinstance(ctx_data, dict) and "battery_level" in ctx_data:
            raw_b = ctx_data["battery_level"]
            if isinstance(raw_b, str):
                try:
                    ctx_data["battery_level"] = int(float(raw_b.split()[0].replace("%", "")))
                except:
                    ctx_data["battery_level"] = None
        
        request = ChatRequest.model_validate(request_data)
        
        # ── Memory + Planning Separation ───────────────────────────────────
        # Reconstruct trip plan from history if context is missing it
        if not request.context.current_trip_plan:
            for msg in reversed(request.history):
                if getattr(msg, "type", None) == "plan" and isinstance(msg.content, dict):
                    request.context.current_trip_plan = msg.content
                    request.context.active_trip = True
                    break

        chat_logger.info(f"VALID REQUEST: {request.message}")
    except Exception as e:
        chat_logger.error(f"VALIDATION FAILED: {e}")
        chat_logger.error(f"RAW DATA: {json.dumps(request_data)}")
        
        # Emergency recovery
        fallback_ctx = request_data.get("context", {}) if isinstance(request_data.get("context"), dict) else {}
        return ChatResponse(
            reply="I'm having a little trouble with the trip data, but I've recovered. What can I do for you?",
            origin=str(fallback_ctx.get("origin", "")) if fallback_ctx.get("origin") else None,
            destination=str(fallback_ctx.get("destination", "")) if fallback_ctx.get("destination") else None,
            is_ready=False,
            active_trip=bool(fallback_ctx.get("active_trip", False)),
            session_id=fallback_ctx.get("session_id")
        ).model_dump()

    now = datetime.now()
    current_time_str = now.strftime("%I:%M %p")
    ctx = request.context
    
    if not ctx.session_id:
        ctx.session_id = str(uuid.uuid4())
    session_var.set(ctx.session_id)

    # ── 0. Mission Control Intercept ─────────────────────────────────────
    from ..agents.mission_control import MissionControl, _mission_control_instance
    mc = _mission_control_instance()
    mc_context = {
        "origin": ctx.origin,
        "destination": ctx.destination,
        "battery_level": ctx.battery_level,
        "preference": ctx.preference,
        "departure_time": ctx.departure_time,
    }
    mc_result = await mc.handle(request.message, mc_context)
    if mc_result and mc_result.get("handled"):
        return ChatResponse(
            reply=str(mc_result.get("reply", "I've processed that for you.")),
            origin=ctx.origin,
            destination=ctx.destination,
            battery_level=ctx.battery_level,
            preference=ctx.preference,
            departure_time=ctx.departure_time,
            is_ready=ctx.last_mission_checksum is not None,
            active_trip=ctx.active_trip,
            current_trip_plan=ctx.current_trip_plan,
            last_mission_checksum=ctx.last_mission_checksum,
            session_id=ctx.session_id
        ).model_dump()

    # ── 1. Fast Intent Routing & Tool Layer ──────────────────────────────────
    intent = classify_intent(request.message)
    if intent == Intent.ASK_STATUS:
        vehicle = get_vehicle_state()
        reply = f"Current trip: {ctx.origin or 'Unknown'} \u2192 {ctx.destination or 'Unknown'}. Battery at {vehicle['battery']}%. Estimated range {vehicle['range_km']} km."
        return ChatResponse(
            reply=reply, 
            origin=ctx.origin,
            destination=ctx.destination,
            active_trip=ctx.active_trip,
            current_trip_plan=ctx.current_trip_plan,
            last_mission_checksum=ctx.last_mission_checksum,
            session_id=ctx.session_id
        ).model_dump()

    # ── 2. Deterministic Extraction (Schema-First & State-Aware) ─────────────
    current_state = get_state(ctx)

    battery_from_user = extract_battery(request.message)
    if battery_from_user is not None:
        ctx.battery_level = battery_from_user

    orig, dest = extract_origin_destination(request.message)

    # If the user provides a completely new origin and destination, they are starting a new trip.
    # Reset the old preferences and trip state so we don't assume preferences from a previous session.
    if orig and dest and ctx.origin and ctx.destination:
        if orig.lower() != str(ctx.origin).lower() or dest.lower() != str(ctx.destination).lower():
            ctx.preference = None
            ctx.active_trip = False
            ctx.current_trip_plan = None
            ctx.last_mission_checksum = None
            ctx.trip_needs_replan = False

    # Words/patterns that should NEVER be treated as a location
    _non_location_words = {
        "yes",
        "no",
        "ok",
        "okay",
        "sure",
        "yeah",
        "yep",
        "nope",
        "hi",
        "hello",
        "hey",
        "thanks",
        "thank you",
        "perfect",
        "great",
        "good",
        "fine",
        "alright",
        "confirm",
        "confirmed",
        "correct",
        "right",
        "proceed",
        "go",
        "done",
    }

    # Fallback: If no 'from/to' keywords, but we are in a collection state,
    # treat the whole message as the location if it's reasonably short.
    _msg_clean = request.message.strip().rstrip(".,!?")
    _msg_lower = _msg_clean.lower()
    _is_non_location = (
        _msg_lower in _non_location_words
        or extract_battery(_msg_clean) is not None  # "74 percent" is not a location
        or bool(re.match(r"^\d+\s*%?$", _msg_clean))  # "74" or "74%" is not a location
    )

    if (
        not orig
        and not dest
        and not _is_non_location
        and len(request.message.split()) <= 10
    ):
        if current_state == TripState.COLLECT_ORIGIN:
            orig = _msg_clean
            # Clear generic conversational words
            for skip in [
                "i am starting from",
                "starting from",
                "my origin is",
                "i am at",
            ]:
                if orig.lower().startswith(skip):
                    orig = orig[len(skip) :].strip()
        elif current_state == TripState.COLLECT_DESTINATION:
            dest = _msg_clean
            for skip in ["going to", "traveling to", "headed to"]:
                if dest.lower().startswith(skip):
                    dest = dest[len(skip) :].strip()

    if orig:
        ctx.origin = _strip_time_from_location(orig)
    if dest:
        ctx.destination = _strip_time_from_location(dest)

    # ── 3. Temporal Alignment (Meal Logic) ───────────────────────────────────

    # Pre-scan the user message for an explicit time mention to supplement context
    _tm = re.search(
        r"\b(\d{1,2})\s*:\s*(\d{2})\s*(a\.?m\.?|p\.?m\.?)\b|\b(\d{1,2})\s*(a\.?m\.?|p\.?m\.?)\b|\b(\d{2}):(\d{2})\b",
        request.message,
        re.IGNORECASE,
    )
    _is_departure_mention = any(
        x in _msg_lower
        for x in [
            "leave",
            "depart",
            "start",
            "heading",
            "going",
            "traveling",
            "trip at",
            "go to",
            "travel to",
            "heading to",
            "now",
            "immediately",
            "right now",
        ]
    )
    _is_coffee_mention = any(
        x in _msg_lower for x in ["coffee", "tea", "snack", "break", "eat", "food"]
    )
    _user_time = None

    # Deterministic "now" handling
    if any(x in _msg_lower for x in ["right now", "immediately", "asap", "now"]):
        if not _is_coffee_mention:
            ctx.departure_time = "now"
            _user_time = "now"

    if _tm and not _user_time:
        _matched = _tm.group(0).strip()
        try:
            from datetime import datetime as _dt

            for _fmt in ["%I:%M %p", "%I%p", "%I %p", "%H:%M", "%I:%M%p"]:
                try:
                    _clean_matched = _matched.upper().replace(" ", "").replace(".", "")
                    _t = _dt.strptime(_clean_matched, _fmt.replace(" ", ""))
                    _user_time = _t.strftime("%I:%M %p")
                    break
                except ValueError:
                    continue

            # Update context immediately if this looks like a departure time
            # Aggressive: if we don't have a time, and this isn't food, assume it's the start time
            if (
                not ctx.departure_time or _is_departure_mention
            ) and not _is_coffee_mention:
                ctx.departure_time = _user_time
                if (
                    "tomorrow" in _msg_lower
                    and "tomorrow" not in ctx.departure_time.lower()
                ):
                    ctx.departure_time = f"tomorrow at {ctx.departure_time}"
                elif (
                    "today" in _msg_lower and "today" not in ctx.departure_time.lower()
                ):
                    ctx.departure_time = f"{ctx.departure_time} today"

        except Exception:
            pass

    # 2d. Dietary Preference
    _food_keywords = [
        "coffee", "tea", "food", "dinner", "lunch", "breakfast", "snack",
        "veg", "non veg", "non-veg", "biryani", "biriyani", "burger", "pizza",
        "south indian", "north indian", "fast food", "kfc", "mcdonald", "subway", "domino"
    ]
    _food_modification_keywords = ["instead", "change", "replace", "different", "another", "lets do", "choose", "prefer", "want"]
    
    _msg_lower = request.message.lower()
    is_food_msg = any(x in _msg_lower for x in _food_keywords)
    is_mod_msg = any(x in _msg_lower for x in _food_modification_keywords)
    
    if (is_food_msg or is_mod_msg) and current_state in [TripState.COLLECT_PREFERENCE, TripState.TRIP_PLANNED]:
        # If it's a short, specific message, treat it as the preference
        if len(request.message.split()) <= 15:
            new_pref = request.message.strip().rstrip(".,!?")
            # If the preference changed, OR if it's a food mention while a trip is already active
            if new_pref != ctx.preference or ctx.active_trip:
                ctx.preference = new_pref
                # Use the new Modification Engine logic
                if ctx.active_trip or current_state == TripState.TRIP_PLANNED:
                    ctx.trip_needs_replan = True
                    is_ready = True  # Force re-trigger of planner

    # Determine departure hour: prefer context + newly extracted time
    _dep_hour = now.hour + now.minute / 60.0  # default = now

    if _user_time and ctx.departure_time and not _has_clock_time(ctx.departure_time):
        _dep_src = f"{ctx.departure_time} at {_user_time}"
    else:
        _dep_src = _user_time or ctx.departure_time or ""

    if _dep_src and _dep_src.lower() not in ["now", "immediately", "asap", "right now"]:
        _clock_m = re.search(
            r"\b(\d{1,2})\s*:\s*(\d{2})\s*(a\.?m\.?|p\.?m\.?)\b|\b(\d{1,2})\s*(a\.?m\.?|p\.?m\.?)\b|\b(\d{2}):(\d{2})\b",
            _dep_src,
            re.IGNORECASE,
        )
        if _clock_m:
            _clock_str = _clock_m.group(0).strip()
            for _fmt in ["%I:%M %p", "%I%p", "%I %p", "%H:%M", "%I:%M%p"]:
                try:
                    _clean_clock_str = _clock_str.upper().replace(" ", "").replace(".", "")
                    _pt = datetime.strptime(_clean_clock_str, _fmt.replace(" ", ""))
                    _dep_hour = _pt.hour + _pt.minute / 60.0
                    break
                except ValueError:
                    continue
    # Meal windows (must match optimization_agent.py)
    _meal_windows = [
        ("Breakfast", 7.5, 10.5),  # 7:30 AM to 10:30 AM
        ("Lunch", 11.5, 15.0),  # 11:30 AM to 3:00 PM
        ("Tea/Coffee/Snacks", 15.5, 19.0),  # 3:30 PM to 7:00 PM
        ("Dinner", 19.5, 22.5),  # 7:30 PM to 10:30 PM
    ]
    
    needed_meal = "meals or snacks"
    
    # If we are about to ask for food preferences, calculate exactly which meals fall in the trip!
    if get_state(ctx) == TripState.COLLECT_PREFERENCE and ctx.origin and ctx.destination:
        try:
            import asyncio
            from ..services.maps_service import MapsService
            ms = MapsService()
            res = await asyncio.to_thread(ms.get_directions, ctx.origin, ctx.destination)
            if "duration_minutes" in res:
                dur_hours = res["duration_minutes"] / 60.0
                needed_meals_list = [
                    name
                    for name, start, end in _meal_windows
                    if end > _dep_hour and start < (_dep_hour + dur_hours)
                ]
                if needed_meals_list:
                    if len(needed_meals_list) == 1:
                        needed_meal = needed_meals_list[0]
                    else:
                        needed_meal = f"{', '.join(needed_meals_list[:-1])} and {needed_meals_list[-1]}"
        except Exception:
            pass

    # Departure time check
    dep_val = ctx.departure_time or ""
    dep_ok = _has_clock_time(dep_val)
    if not dep_val:
        dep_status = "❌ MISSING"
    elif not dep_ok:
        dep_status = (
            f"⚠️ PARTIAL (Found '{dep_val}', but need a specific clock time like 7 AM)"
        )
    else:
        dep_status = f"✅ {dep_val}"

    status_lines = [
        f"  origin        : {'✅ ' + ctx.origin if ctx.origin else '❌ MISSING'}",
        f"  destination   : {'✅ ' + ctx.destination if ctx.destination else '❌ MISSING'}",
        f"  battery_level : {'✅ ' + str(ctx.battery_level) + '%' if ctx.battery_level is not None else '❌ MISSING'}",
        f"  departure_time: {dep_status}",
        f"  preference    : {'✅ ' + ctx.preference if ctx.preference else '❌ MISSING'}",
    ]
    # Build status block
    status_block = "\n".join(status_lines)

    # Build a human-friendly list of meals relevant after the departure hour
    meals_planned = ", ".join(
        [name for name, start, end in _meal_windows if start > _dep_hour]
    )
    if not meals_planned:
        meals_planned = "None (Late departure)"

    # ── State Machine Guidance ────────────────────────────────────────────────
    current_state = get_state(ctx)

    state_instructions = {
        TripState.COLLECT_ORIGIN: "Ask for the starting location.",
        TripState.COLLECT_DESTINATION: "Ask for the destination.",
        TripState.COLLECT_BATTERY: "Ask the driver for their current car battery percentage. Vary your phrasing to sound natural.",
        TripState.COLLECT_TIME: "Ask for the departure time.",
        TripState.COLLECT_PREFERENCE: f"Ask what they would like for {needed_meal} during the trip. YOU MUST EXPLICITLY MENTION the specific names of these meals in your question (e.g., 'Lunch and Dinner'). Do not generalize them to just 'meals'.",
        TripState.READY: "Confirm all details and announce that trip planning is starting.",
        TripState.TRIP_PLANNED: "Your trip is ready! Answer questions about the route, chargers, or food. Be helpful and proactive.",
    }

    system_prompt = f"""You are Jarvis, a friendly and efficient EV travel co-pilot.
    
Current time: {current_time_str}

STATUS:
{status_block}

MISSION STATUS: {"✅ PLANNED" if current_state == TripState.TRIP_PLANNED else "⏳ IN PROGRESS"}

RULES:
- Fields marked ✅ are DONE. NEVER ask about them again.
- CURRENT GOAL: {state_instructions.get(current_state, "Help the user.")}
- When ALL fields are ✅ for the FIRST time, just confirm the details briefly. The system will handle the transition to planning.
- If the trip is already PLANNED, be a helpful concierge. Answer questions about the stops, chargers, and distances from the plan.
- Keep replies to 1-2 sentences. Be warm but concise.
- Never say "Let's confirm" or repeat back all fields.

Return ONLY this JSON:
{{
  "reply": "your short reply here",
  "origin": null or "place name",
  "destination": null or "place name",
  "battery_level": null or number,
  "departure_time": null or "time string",
  "preference": null or "EXACT words of their preference. MUST be null if the user has not explicitly stated a food preference yet.",
  "is_ready": true or false
}}"""

    MAX_TURNS = 6  # keep last 6 messages (3 exchanges)
    history = request.history[-MAX_TURNS:]

    messages = [SystemMessage(content=system_prompt)]
    for msg in history:
        if msg.role == "user":
            messages.append(HumanMessage(content=str(msg.content)))
        else:
            # Strip JSON from assistant history to prevent gemma3:4b from getting confused
            if isinstance(msg.content, str):
                clean_content = re.sub(r"\{.*\}", "", msg.content, flags=re.DOTALL).strip()
            else:
                clean_content = "Summary: Trip Plan Generated."
                
            if not clean_content:
                # Fallback if the whole message was JSON
                clean_content = "I've noted that. What's next?"
            messages.append(AIMessage(content=clean_content))
    messages.append(HumanMessage(content=request.message))

    raw = await llm.ainvoke(messages)
    raw_text = raw.content if hasattr(raw, "content") else str(raw)
    raw_text = raw_text.strip()

    # ── Parse JSON safely ────────────────────────────────────────────────────
    parsed: dict = {}
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        # Try to extract JSON block
        m = re.search(r"\{.*\}", raw_text, re.DOTALL)
        if m:
            try:
                parsed = json.loads(m.group())
            except Exception:
                parsed = {}

    print(f"DEBUG LLM RAW: {raw_text}")
    print(f"DEBUG PARSED: {parsed}")

    reply = parsed.get("reply", "Sorry, I had a hiccup. Could you repeat that?")

    # ── Extract fields (prefer LLM output, fall back to context) ─────────────
    def merge(new_val, old_val):
        cleaned = _clean(new_val)
        return cleaned if cleaned is not None else old_val

    origin = merge(parsed.get("origin"), ctx.origin)
    destination = merge(parsed.get("destination"), ctx.destination)
    preference = merge(parsed.get("preference"), ctx.preference)

    # Sanitize: strip time info that the LLM may echo back (e.g. "Chennai at 5pm today" → "Chennai")
    if origin:
        origin = _strip_time_from_location(origin)
    if destination:
        destination = _strip_time_from_location(destination)

    # Battery: coerce strings like "67 percent" → 67
    raw_battery = parsed.get("battery_level")
    battery_level = ctx.battery_level
    if raw_battery is not None:
        try:
            battery_level = int(str(raw_battery).split()[0].replace("%", ""))
        except (ValueError, IndexError):
            pass

    # Smarter merging for departure_time (merging Date + Time)
    raw_dep = _clean(parsed.get("departure_time"))
    if raw_dep and any(
        x in raw_dep.lower() for x in ["now", "immediately", "asap", "right now"]
    ):
        raw_dep = current_time_str

    departure_time = raw_dep if raw_dep else ctx.departure_time
    # If old was just a date (tomorrow) and new is a clock time (7am), merge them
    if ctx.departure_time and raw_dep:
        old_val = ctx.departure_time.lower()
        new_val = raw_dep.lower()
        date_words = [
            "tomorrow",
            "today",
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        ]
        if any(x in old_val for x in date_words) and not _has_clock_time(old_val):
            if _has_clock_time(new_val) and not any(x in new_val for x in date_words):
                departure_time = f"{ctx.departure_time} at {raw_dep}"

    # ── Hard validation: override LLM's is_ready ─────────────────────────────
    # If the schema is full, we ARE ready. No more loops.
    missing = []
    if not origin:
        missing.append("origin")
    if not destination:
        missing.append("destination")
    if battery_level is None:
        missing.append("battery level")
    departure_time_ok = _has_clock_time(departure_time or "")
    if not departure_time or not departure_time_ok:
        missing.append("departure time")
    if not preference:
        missing.append(f"dietary preference for {needed_meal}")

    # ── 4. Final Validation & Planning Trigger ─────────────────────────────
    is_data_complete = not missing
    
    checksum_payload = {
        "origin": origin, "destination": destination, "battery": battery_level,
        "preference": preference, "departure_time": departure_time,
    }
    checksum = json.dumps(checksum_payload, sort_keys=True)
    has_new_checksum = bool(ctx.last_mission_checksum and checksum != ctx.last_mission_checksum)
    
    # Trigger if data is complete for the first time OR if active trip needs update
    should_trigger_plan = is_data_complete and (not ctx.last_mission_checksum or ctx.trip_needs_replan or has_new_checksum)
    
    # ── Reply Sanitization: strip redundant questions about captured fields ───
    _captured_field_questions = {
        "origin": ["starting", "start from", "origin", "where are you", "starting location", "starting point"],
        "destination": ["headed", "going to", "destination", "where to"],
        "battery level": ["battery", "charge level", "charge", "percent", "juice"],
        "departure time": ["what time", "when do you", "when are you", "departure"],
    }
    for field, keywords in _captured_field_questions.items():
        if field not in missing:
            for kw in keywords:
                if kw in reply.lower() and "?" in reply:
                    sentences = re.split(r"(?<=[.!?])\s+", reply)
                    reply = " ".join(s for s in sentences if kw not in s.lower() or "?" not in s)
                    break

    CRITICAL_PROMPTS = {
        "origin": "Where are we starting from?",
        "destination": "Where are we headed?",
        "battery level": "What's your current charge level?",
        "departure time": "What time are you planning to leave?" if not departure_time else "Could you give a specific time? (e.g. 7 AM, 2 PM)",
    }

    is_ready = is_data_complete
    if not is_data_complete:
        first_missing = missing[0]
        if first_missing in CRITICAL_PROMPTS:
            llm_asked_q = "?" in reply
            keywords_for_missing = _captured_field_questions.get(first_missing, [])
            is_relevant = llm_asked_q and any(k in reply.lower() for k in keywords_for_missing)
            if not is_relevant:
                reply = re.sub(r"[^.!?]*\?\s*$", "", reply).strip()
                if reply:
                    reply = reply.rstrip(". ") + ". " + CRITICAL_PROMPTS[first_missing]
                else:
                    reply = CRITICAL_PROMPTS[first_missing]
        else:
            # Preference gate: only ask if we are NOT triggering a plan
            if "?" not in reply and not should_trigger_plan:
                reply = reply.rstrip(". ") + f". What would you like for {needed_meal} during the trip?"

    # ── Execute Planning ──────────────────────────────────────────────────────
    # Track map URLs for Echo response (initialized here, populated in is_echo block)
    _echo_map_url = None
    _echo_directions_url = None
    
    if should_trigger_plan:
        import asyncio
        # Standardize planning messages
        if ctx.active_trip:
            reply = "Got it! Give me just a few seconds while I update your itinerary with those new details. I'll make sure your route and stops are perfectly tailored for you!"
        else:
            reply = "Perfect, I've got all the details! I’m putting together an amazing road trip for you right now, complete with the best routes, convenient charging spots, and great places to eat. Just give me a few seconds to get everything ready!"

        from ..utils.agent_bus import bus
        params = {
            "origin": origin,
            "destination": destination,
            "battery_level": battery_level,
            "preference": preference,
            "departure_time": departure_time,
            "replan_food_only": ctx.trip_needs_replan,
            "existing_plan": ctx.current_trip_plan,
            "session_id": ctx.session_id
        }
        if checksum != ctx.last_mission_checksum or ctx.trip_needs_replan:
            print(f"DEBUG: mission_defined (replan={ctx.trip_needs_replan}) \u2192 {destination}")
            
            if is_echo:
                loop = asyncio.get_running_loop()
                plan_future = loop.create_future()
                
                async def plan_listener(data):
                    if data.get("session_id") == ctx.session_id and not plan_future.done():
                        plan_future.set_result(data)
                
                bus.subscribe("plan_complete", plan_listener)
            
            await bus.publish("mission_defined", params)
            ctx.trip_needs_replan = False
            ctx.last_mission_checksum = checksum
            ctx.active_trip = True
            
            if is_echo:
                try:
                    # Echo needs to wait longer for the plan to fully generate
                    final_plan = await asyncio.wait_for(plan_future, timeout=90.0)
                    ctx.current_trip_plan = final_plan
                    
                    # Format both the briefing and the timeline for Echo's text-only interface
                    base_reply = final_plan.get("jarvis_briefing", final_plan.get("curation", "Your trip is ready."))
                    
                    # Build a comprehensive trip summary
                    summary = final_plan.get("summary", "")
                    distance = final_plan.get("distance", "")
                    time_est = final_plan.get("time", "")
                    traffic = final_plan.get("traffic", "")
                    weather = final_plan.get("weather", "")
                    overview_polyline = final_plan.get("overview_polyline", "")
                    stops = final_plan.get("stops", [])
                    
                    trip_header = f"\n\n--- TRIP SUMMARY ---\nRoute: {summary}\nDistance: {distance} | ETA: {time_est}\nTraffic: {traffic} | Weather: {weather}\n"
                    
                    # ── Build Google Maps Static Map URL with polyline + markers ──
                    from urllib.parse import quote
                    from ..utils.config import Config
                    import googlemaps.convert as gc
                    maps_key = Config.GOOGLE_MAPS_API_KEY
                    
                    # Downsample polyline so the URL doesn't exceed 8192 characters
                    safe_poly = overview_polyline
                    if overview_polyline:
                        def robust_decode_polyline(polyline):
                            index, lat, lng = 0, 0, 0
                            coordinates = []
                            try:
                                while index < len(polyline):
                                    changes = {'latitude': 0, 'longitude': 0}
                                    for unit in ['latitude', 'longitude']: 
                                        shift, result = 0, 0
                                        while True:
                                            byte = ord(polyline[index]) - 63
                                            index += 1
                                            result |= (byte & 0x1f) << shift
                                            shift += 5
                                            if not byte >= 0x20:
                                                break
                                        if (result & 1):
                                            changes[unit] = ~(result >> 1)
                                        else:
                                            changes[unit] = (result >> 1)
                                    lat += changes['latitude']
                                    lng += changes['longitude']
                                    coordinates.append({'lat': lat / 100000.0, 'lng': lng / 100000.0})
                            except Exception:
                                pass
                            return coordinates

                        pts = robust_decode_polyline(overview_polyline)
                        if len(pts) > 150:
                            step = len(pts) // 150
                            ds = pts[::step]
                            if ds[-1] != pts[-1]: ds.append(pts[-1])
                            safe_poly = gc.encode_polyline(ds)
                        elif len(pts) > 0:
                            safe_poly = gc.encode_polyline(pts)
                    
                    static_map_url = ""
                    if safe_poly and maps_key:
                        # Base URL with dark-style map, route polyline in cyan
                        # Because we guarantee downsampling (max ~150 points), the encoded string is small
                        # and safely quote-encoded so markdown link parsers do not break on brackets []
                        encoded_poly = quote(safe_poly)
                        url_parts = [
                            f"https://maps.googleapis.com/maps/api/staticmap?size=800x500",
                            f"&path=weight:4%7Ccolor:0x00d2ffcc%7Cenc:{encoded_poly}",
                        ]
                        
                        # Add colored markers for each stop with labels
                        marker_colors = ["red", "blue", "green", "purple", "orange", "yellow"]
                        for i, stop in enumerate(stops):
                            loc = stop.get("location")
                            if loc:
                                color = marker_colors[i % len(marker_colors)]
                                label = str(i + 1) if i < 9 else chr(65 + (i - 9))  # 1-9, then A-Z
                                url_parts.append(
                                    f"&markers=color:{color}%7Clabel:{label}%7C{loc['lat']},{loc['lng']}"
                                )
                        
                        url_parts.append(f"&key={maps_key}")
                        static_map_url = "".join(url_parts)
                    
                    # ── Build Google Maps Directions URL with all stops as waypoints ──
                    directions_url = ""
                    nav_url = ""
                    if origin and destination:
                        origin_enc = quote(str(origin))
                        dest_enc = quote(str(destination))
                        
                        # Build waypoints from all stops with locations
                        waypoint_parts = []
                        for stop in stops:
                            loc = stop.get("location")
                            if loc:
                                waypoint_parts.append(f"{loc['lat']},{loc['lng']}")
                        
                        # Simple directions URL (for viewing)
                        directions_url = f"https://www.google.com/maps/dir/{origin_enc}/{dest_enc}"
                        
                        # Full navigation URL with waypoints (for starting the trip)
                        nav_url = f"https://www.google.com/maps/dir/?api=1&origin={origin_enc}&destination={dest_enc}&travelmode=driving"
                        if waypoint_parts:
                            waypoints_str = quote("|".join(waypoint_parts))
                            nav_url += f"&waypoints={waypoints_str}"
                    
                    # ── Build Timeline with clickable Google Maps links for each stop ──
                    timeline_text = "\n--- TRIP TIMELINE ---\n"
                    for i, stop in enumerate(stops, 1):
                        name = stop.get("name", "Stop")
                        dist = stop.get("distance_mark", "")
                        action = stop.get("action", "")
                        arrival = stop.get("arrival_time", "")
                        food = stop.get("food_name", "")
                        loc = stop.get("location")
                        
                        # Create clickable Google Maps link for the stop
                        if loc:
                            stop_url = f"https://www.google.com/maps/search/?api=1&query={loc['lat']},{loc['lng']}"
                            timeline_text += f"\nStop {i}: [{name}]({stop_url})\n"
                        else:
                            timeline_text += f"\nStop {i}: {name}\n"
                        
                        timeline_text += f"  📍 Distance: {dist}\n"
                        if arrival:
                            timeline_text += f"  🕐 Arrival: {arrival}\n"
                        timeline_text += f"  ⚡ Action: {action}\n"
                        
                        if food:
                            if loc:
                                food_url = f"https://www.google.com/maps/search/?api=1&query={quote(food)}@{loc['lat']},{loc['lng']}"
                                timeline_text += f"  🍽 Restaurant: [{food}]({food_url})\n"
                            else:
                                timeline_text += f"  🍽 Restaurant: {food}\n"
                    
                    timeline_text += "\n---\n"
                    
                    # ── Assemble full reply with map image and links ──
                    reply = base_reply + trip_header + timeline_text
                    
                    if static_map_url:
                        reply += f"\n📍 **Route Map:** {static_map_url}\n"
                        reply += f"![Route Map]({static_map_url})\n"
                        _echo_map_url = static_map_url
                    
                    if nav_url:
                        reply += f"\n🧭 **Start Trip on Google Maps (with all stops):** {nav_url}\n"
                        _echo_directions_url = nav_url
                    elif directions_url:
                        reply += f"\n🗺️ **Open in Google Maps:** {directions_url}\n"
                        _echo_directions_url = directions_url
                    
                except asyncio.TimeoutError:
                    print("DEBUG: Echo Timed out waiting for plan_complete")
                finally:
                    bus.unsubscribe("plan_complete", plan_listener)

    # ── Final Response Creation ──────────────────────────────────────────────
    # Universal Type Coercion to prevent 422 errors
    try:
        # Coerce battery
        final_battery = None
        if battery_level is not None:
            try:
                final_battery = int(
                    float(str(battery_level).split()[0].replace("%", ""))
                )
            except (ValueError, TypeError, IndexError):
                final_battery = None

        # Coerce strings
        final_origin = str(origin) if origin else None
        final_destination = str(destination) if destination else None
        final_preference = str(preference) if preference else None
        final_dep_time = str(departure_time) if departure_time else None

        # Strip the massive polyline from Echo's context to prevent token overflow
        safe_trip_plan = ctx.current_trip_plan
        if is_echo and safe_trip_plan and "overview_polyline" in safe_trip_plan:
            import copy
            safe_trip_plan = copy.deepcopy(safe_trip_plan)
            del safe_trip_plan["overview_polyline"]

        return ChatResponse(
            reply=str(reply),
            origin=final_origin,
            destination=final_destination,
            battery_level=final_battery,
            preference=final_preference,
            departure_time=final_dep_time,
            is_ready=bool(is_ready),
            active_trip=ctx.active_trip,
            trip_needs_replan=ctx.trip_needs_replan,
            current_trip_plan=safe_trip_plan,
            last_mission_checksum=ctx.last_mission_checksum,
            session_id=ctx.session_id,
            map_url=_echo_map_url,
            directions_url=_echo_directions_url
        ).model_dump()

    except ValidationError as e:
        print(f"CRITICAL: ChatResponse Validation Error: {e.json()}")
        return ChatResponse(
            reply="I encountered a data validation issue, but I've recovered. What can I help with?",
            origin=ctx.origin,
            destination=ctx.destination,
            is_ready=False,
            active_trip=ctx.active_trip,
        ).model_dump()
