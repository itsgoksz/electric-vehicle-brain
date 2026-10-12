"""
Mission Control Agent — Central Orchestrator for the EV Co-Pilot.

Sits ABOVE the existing agent architecture. Intercepts all user queries,
classifies intent, builds multi-step execution plans for complex queries,
and routes to existing agents via the AgentBus.

Integration: chat.py → MissionControl.handle() → AgentBus → Existing Agents
"""

import asyncio
import json
import re
from datetime import datetime
from typing import Optional, Dict, Any, List
from enum import Enum
from langchain_core.messages import HumanMessage, SystemMessage
from ..utils.agent_bus import bus


# ── Enhanced Intent Taxonomy ───────────────────────────────────────────────
class MissionIntent(str, Enum):
    """Extended intents beyond the basic chat flow."""
    PLAN_TRIP       = "plan_trip"           # Full trip planning (existing flow)
    FIND_CHARGER    = "find_charger"        # Standalone charger search
    FIND_FOOD       = "find_food"           # Standalone restaurant search
    CHECK_WEATHER   = "check_weather"       # Weather at a location
    CHECK_TRAFFIC   = "check_traffic"       # Traffic conditions
    CHARGER_ON_ROUTE = "charger_on_route"   # Complex: route + chargers
    VEHICLE_STATUS  = "vehicle_status"      # Battery / range query
    MODIFY_TRIP     = "modify_trip"         # Change existing plan
    PROVIDE_INFO    = "provide_info"        # User providing trip details
    GENERAL_CHAT    = "general_chat"        # Small talk / general
    QUERY_PLAN      = "query_plan"          # Follow-up questions about current plan
    OFF_TOPIC       = "off_topic"           # Out of scope queries


# ── Intent Analyzer ────────────────────────────────────────────────────────
class IntentAnalyzer:
    """
    Two-layer intent classification:
      Layer 1: Fast deterministic keyword matching (zero latency)
      Layer 2: LLM fallback for ambiguous queries
    """

    # Keyword → Intent mapping (deterministic layer)
    KEYWORD_MAP = {
        MissionIntent.FIND_CHARGER: [
            "find charger", "nearest charger", "charging station", "where can i charge",
            "charger near", "ev charger", "charge my car", "need to charge",
        ],
        MissionIntent.FIND_FOOD: [
            "find restaurant", "where to eat", "food near", "hungry",
            "recommend restaurant", "lunch near", "dinner near", "breakfast near",
        ],
        MissionIntent.CHECK_WEATHER: [
            "weather", "rain", "temperature", "forecast", "sunny", "cloudy",
        ],
        MissionIntent.CHECK_TRAFFIC: [
            "traffic", "congestion", "road condition", "delay",
        ],
        MissionIntent.VEHICLE_STATUS: [
            "battery status", "range left", "how much charge", "vehicle status",
            "car status", "battery level",
        ],
        MissionIntent.CHARGER_ON_ROUTE: [
            "charger on my way", "charger on route", "charge on the way",
            "charging stop on route", "chargers along",
        ],
        MissionIntent.MODIFY_TRIP: [
            "change destination", "update trip", "cancel trip", "modify",
            "go somewhere else", "instead",
        ],
    }

    # Patterns that indicate trip-related info provision
    TRIP_INFO_PATTERNS = [
        r"from .+ to .+",
        r"\d{1,3}\s*%",
        r"\d{1,3}\s*percent",
        r"\d{1,2}\s*(a\.?m\.?|p\.?m\.?)",
        r"going to",
        r"headed to",
        r"traveling to",
        r"leaving at",
        r"right now",
        r"immediately",
    ]

    def classify(self, text: str, context: Optional[Dict] = None) -> MissionIntent:
        """Classify user intent using deterministic rules first, then LLM fallback."""
        t = text.lower().strip()

        # Layer 1: Deterministic keyword matching
        for intent, keywords in self.KEYWORD_MAP.items():
            if any(kw in t for kw in keywords):
                # Complex intent: charger + route destination mentioned
                if intent == MissionIntent.FIND_CHARGER and any(
                    x in t for x in ["on my way", "on route", "to ", "going to", "headed"]
                ):
                    return MissionIntent.CHARGER_ON_ROUTE
                return intent

        # Check for trip info patterns
        for pattern in self.TRIP_INFO_PATTERNS:
            if re.search(pattern, t, re.IGNORECASE):
                return MissionIntent.PROVIDE_INFO

        # Small talk
        if len(t.split()) < 4 and any(x in t for x in ["hi", "hello", "hey", "sup", "thanks", "bye"]):
            return MissionIntent.GENERAL_CHAT

        # Default: treat as providing info (feeds into trip state machine)
        return MissionIntent.PROVIDE_INFO


# ── Plan Builder ───────────────────────────────────────────────────────────
class PlanStep:
    """A single step in a multi-step execution plan."""
    def __init__(self, step_id: str, action: str, agent_event: str,
                 params: Dict[str, Any], depends_on: Optional[str] = None):
        self.step_id = step_id
        self.action = action           # Human-readable description
        self.agent_event = agent_event  # AgentBus event to publish
        self.params = params            # Parameters for the event
        self.depends_on = depends_on    # Step ID this depends on (for chaining)
        self.result = None              # Filled after execution
        self.status = "pending"         # pending | running | done | error

    def to_dict(self) -> dict:
        return {
            "step_id": self.step_id,
            "action": self.action,
            "agent_event": self.agent_event,
            "status": self.status,
            "depends_on": self.depends_on,
        }


class PlanBuilder:
    """
    Converts a classified intent + context into a structured execution plan.
    Complex queries → multiple PlanSteps with dependencies.
    Simple queries → single PlanStep or direct answer.
    """

    def build(self, intent: MissionIntent, message: str,
              context: Dict[str, Any]) -> List[PlanStep]:
        """Build an execution plan for the given intent."""

        if intent == MissionIntent.FIND_CHARGER:
            return self._plan_find_charger(message, context)
        elif intent == MissionIntent.CHARGER_ON_ROUTE:
            return self._plan_charger_on_route(message, context)
        elif intent == MissionIntent.FIND_FOOD:
            return self._plan_find_food(message, context)
        elif intent == MissionIntent.CHECK_WEATHER:
            return self._plan_check_weather(message, context)
        elif intent == MissionIntent.CHECK_TRAFFIC:
            return self._plan_check_traffic(message, context)
        elif intent == MissionIntent.PLAN_TRIP:
            return self._plan_full_trip(message, context)
        else:
            return []  # No plan needed — handled directly by chat flow

    def _plan_find_charger(self, message: str, ctx: Dict) -> List[PlanStep]:
        """Single-step: find chargers near current location or a specified location."""
        location = self._extract_location(message) or ctx.get("origin")
        return [
            PlanStep(
                step_id="find_chargers",
                action=f"Finding EV chargers near {location or 'your location'}",
                agent_event="mc_find_chargers",
                params={"location_query": location or "current location"},
            )
        ]

    def _plan_charger_on_route(self, message: str, ctx: Dict) -> List[PlanStep]:
        """Multi-step: get route THEN find chargers along it."""
        origin = ctx.get("origin") or "current location"
        destination = self._extract_destination(message) or ctx.get("destination")

        return [
            PlanStep(
                step_id="get_route",
                action=f"Computing route from {origin} to {destination}",
                agent_event="mc_get_route",
                params={"origin": origin, "destination": destination},
            ),
            PlanStep(
                step_id="find_chargers_on_route",
                action="Finding chargers along the route",
                agent_event="mc_chargers_on_route",
                params={"battery_level": ctx.get("battery_level", 70)},
                depends_on="get_route",
            ),
            PlanStep(
                step_id="rank_chargers",
                action="Ranking chargers by speed and proximity",
                agent_event="mc_rank_chargers",
                params={},
                depends_on="find_chargers_on_route",
            ),
        ]

    def _plan_find_food(self, message: str, ctx: Dict) -> List[PlanStep]:
        location = self._extract_location(message) or ctx.get("origin")
        cuisine = self._extract_cuisine(message)
        return [
            PlanStep(
                step_id="find_food",
                action=f"Finding {cuisine or 'restaurants'} near {location or 'your location'}",
                agent_event="mc_find_food",
                params={"location_query": location, "cuisine": cuisine or "restaurant"},
            )
        ]

    def _plan_check_weather(self, message: str, ctx: Dict) -> List[PlanStep]:
        location = self._extract_location(message) or ctx.get("destination") or ctx.get("origin")
        return [
            PlanStep(
                step_id="check_weather",
                action=f"Checking weather at {location or 'route midpoint'}",
                agent_event="mc_check_weather",
                params={"location_query": location},
            )
        ]

    def _plan_check_traffic(self, message: str, ctx: Dict) -> List[PlanStep]:
        origin = ctx.get("origin") or "current location"
        destination = self._extract_destination(message) or ctx.get("destination")
        return [
            PlanStep(
                step_id="check_traffic",
                action=f"Checking traffic from {origin} to {destination}",
                agent_event="mc_check_traffic",
                params={"origin": origin, "destination": destination},
            )
        ]

    def _plan_full_trip(self, message: str, ctx: Dict) -> List[PlanStep]:
        """Delegates to the existing trip planning pipeline via mission_defined."""
        return [
            PlanStep(
                step_id="full_trip",
                action="Launching full trip planning pipeline",
                agent_event="mission_defined",
                params={
                    "origin": ctx.get("origin"),
                    "destination": ctx.get("destination"),
                    "battery_level": ctx.get("battery_level", 70),
                    "preference": ctx.get("preference"),
                    "departure_time": ctx.get("departure_time"),
                },
            )
        ]

    # ── Extraction Helpers ─────────────────────────────────────────────────
    @staticmethod
    def _extract_location(text: str) -> Optional[str]:
        patterns = [
            r"near (.+?)(?:\s*$|\s+and\b|\s+with\b)",
            r"at (.+?)(?:\s*$|\s+and\b)",
            r"in (.+?)(?:\s*$|\s+and\b)",
            r"around (.+?)(?:\s*$)",
        ]
        for pattern in patterns:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                loc = m.group(1).strip().rstrip(".,!?")
                if len(loc) > 2 and loc.lower() not in ["my", "the", "a", "this"]:
                    return loc
        return None

    @staticmethod
    def _extract_destination(text: str) -> Optional[str]:
        patterns = [
            r"to (.+?)(?:\s*$|\s+with\b|\s+and\b)",
            r"towards (.+?)(?:\s*$)",
            r"headed (?:to )?(.+?)(?:\s*$)",
        ]
        for pattern in patterns:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                dest = m.group(1).strip().rstrip(".,!?")
                if len(dest) > 2 and dest.lower() not in ["my", "the", "a", "office"]:
                    return dest
        return None

    @staticmethod
    def _extract_cuisine(text: str) -> Optional[str]:
        cuisines = [
            "burger", "pizza", "south indian", "north indian", "biryani",
            "vegetarian", "vegan", "cafe", "coffee", "chinese", "italian",
            "breakfast", "lunch", "dinner", "idli", "dosa",
        ]
        t = text.lower()
        for c in cuisines:
            if c in t:
                return c
        return None


# ── Execution Engine ───────────────────────────────────────────────────────
class ExecutionEngine:
    """
    Executes PlanSteps sequentially, passing results between dependent steps.
    Uses the existing service layer (MapsService, ChargingService, etc.)
    via asyncio.to_thread() to avoid event-loop DNS issues.
    """

    def __init__(self):
        # Lazy-loaded services (initialized on first use)
        self._maps_service = None
        self._charging_service = None
        self._places_service = None
        self._weather_service = None

    @property
    def maps_service(self):
        if not self._maps_service:
            from ..services.maps_service import MapsService
            self._maps_service = MapsService()
        return self._maps_service

    @property
    def charging_service(self):
        if not self._charging_service:
            from ..services.charging_service import ChargingService
            self._charging_service = ChargingService()
        return self._charging_service

    @property
    def places_service(self):
        if not self._places_service:
            from ..services.places_service import PlacesService
            self._places_service = PlacesService()
        return self._places_service

    @property
    def weather_service(self):
        if not self._weather_service:
            from ..services.weather_service import WeatherService
            self._weather_service = WeatherService()
        return self._weather_service

    async def execute_plan(self, steps: List[PlanStep]) -> Dict[str, Any]:
        """Execute all steps in order, respecting dependencies."""
        results = {}

        for step in steps:
            # Wait for dependency
            if step.depends_on and step.depends_on in results:
                step.params["_prev_result"] = results[step.depends_on]

            step.status = "running"
            print(f"MissionControl [exec]: {step.action}")

            try:
                result = await self._dispatch(step)
                step.result = result
                step.status = "done"
                results[step.step_id] = result
            except Exception as e:
                step.status = "error"
                step.result = {"error": str(e)}
                results[step.step_id] = {"error": str(e)}
                print(f"MissionControl [error]: {step.step_id} → {e}")

        return results

    async def _dispatch(self, step: PlanStep) -> Dict[str, Any]:
        """Route a step to the correct service call."""
        event = step.agent_event
        params = step.params

        if event == "mc_get_route":
            return await asyncio.to_thread(
                self.maps_service.get_directions,
                params["origin"], params["destination"]
            )

        elif event == "mc_find_chargers":
            # Geocode the location query, then find chargers
            loc = await self._geocode(params.get("location_query", ""))
            if loc:
                return await asyncio.to_thread(
                    self.charging_service.find_nearby_chargers, loc
                )
            return {"error": "Could not resolve location"}

        elif event == "mc_chargers_on_route":
            # Uses route data from previous step
            prev = params.get("_prev_result", {})
            route_steps = prev.get("steps", [])
            if not route_steps:
                return {"chargers": [], "note": "No route data available"}

            # Sample waypoints along the route
            cum_dist = 0
            waypoints = []
            for s in route_steps:
                cum_dist += s.get("distance", {}).get("value", 0) / 1000.0
                waypoints.append({
                    "lat": s["end_location"]["lat"],
                    "lng": s["end_location"]["lng"],
                    "distance_at_point": cum_dist,
                })

            # Sample every ~50km
            sampled = []
            for i in range(0, len(waypoints), max(1, len(waypoints) // 8)):
                sampled.append(waypoints[i])

            all_chargers = []
            for wp in sampled:
                chargers = await asyncio.to_thread(
                    self.charging_service.find_nearby_chargers, wp
                )
                if isinstance(chargers, list):
                    for c in chargers:
                        if "error" not in c:
                            c["route_distance_km"] = wp["distance_at_point"]
                            all_chargers.append(c)

            # Deduplicate
            unique = {}
            for c in all_chargers:
                key = f"{c['name']}-{c['location']['lat']:.3f}"
                if key not in unique:
                    unique[key] = c

            return {"chargers": list(unique.values()), "count": len(unique)}

        elif event == "mc_rank_chargers":
            prev = params.get("_prev_result", {})
            chargers = prev.get("chargers", [])
            # Rank by power (kW) descending
            def power_key(c):
                try:
                    return float(c.get("power_kw", 0))
                except (ValueError, TypeError):
                    return 0.0
            ranked = sorted(chargers, key=power_key, reverse=True)
            return {"ranked_chargers": ranked[:10], "total_found": len(chargers)}

        elif event == "mc_find_food":
            loc = await self._geocode(params.get("location_query", ""))
            if loc:
                cuisine = params.get("cuisine", "restaurant")
                results = await asyncio.to_thread(
                    self.places_service.find_nearby_restaurants, loc, cuisine
                )
                return {"restaurants": results if isinstance(results, list) else []}
            return {"restaurants": [], "error": "Could not resolve location"}

        elif event == "mc_check_weather":
            loc = await self._geocode(params.get("location_query", ""))
            if loc:
                report = await asyncio.to_thread(
                    self.weather_service.get_weather, loc["lat"], loc["lng"]
                )
                return report
            return {"error": "Could not resolve location"}

        elif event == "mc_check_traffic":
            return await asyncio.to_thread(
                self.maps_service.get_traffic_status,
                params.get("origin", ""), params.get("destination", "")
            )

        elif event == "mission_defined":
            # Delegate to existing full pipeline via AgentBus
            await bus.publish("mission_defined", params)
            return {"delegated": True, "note": "Full trip pipeline triggered via AgentBus"}

        else:
            return {"error": f"Unknown event: {event}"}

    async def _geocode(self, query: str, context_loc: Optional[Dict] = None) -> Optional[Dict]:
        """Geocode a location string to lat/lng using Google Maps, optionally biased by a context location."""
        if not query or query == "current location":
            return {"lat": 12.9716, "lng": 77.5946}  # Default: Bangalore
        try:
            kwargs = {"region": "IN"}
            if context_loc:
                kwargs["bounds"] = {
                    "northeast": {"lat": context_loc["lat"] + 1.0, "lng": context_loc["lng"] + 1.0},
                    "southwest": {"lat": context_loc["lat"] - 1.0, "lng": context_loc["lng"] - 1.0}
                }
            
            results = await asyncio.to_thread(
                self.maps_service.gmaps.geocode, f"{query}, India", **kwargs
            )
            if results:
                loc = results[0]["geometry"]["location"]
                return {"lat": loc["lat"], "lng": loc["lng"]}
        except Exception as e:
            print(f"MissionControl [geocode error]: {e}")
        return None


# ── Session Memory ─────────────────────────────────────────────────────────
class SessionMemory:
    """
    Lightweight in-memory session store for Mission Control.
    Tracks conversation context, last intent, and execution results
    so multi-turn queries work naturally.
    """

    def __init__(self, max_sessions: int = 100):
        self._store: Dict[str, Dict[str, Any]] = {}
        self._max = max_sessions

    def get(self, session_id: str = "default") -> Dict[str, Any]:
        if session_id not in self._store:
            self._store[session_id] = {
                "last_intent": None,
                "last_plan": None,
                "last_results": None,
                "last_query": None,
                "route_cache": None,      # Cached route for follow-up queries
                "charger_cache": None,     # Cached charger results
                "history": [],             # Recent query history
                "created_at": datetime.now().isoformat(),
            }
        return self._store[session_id]

    def update(self, session_id: str, **kwargs):
        mem = self.get(session_id)
        mem.update(kwargs)
        # Keep history bounded
        if "last_query" in kwargs:
            mem["history"].append({
                "query": kwargs["last_query"],
                "intent": kwargs.get("last_intent"),
                "timestamp": datetime.now().isoformat(),
            })
            if len(mem["history"]) > 20:
                mem["history"] = mem["history"][-20:]

    def clear(self, session_id: str = "default"):
        self._store.pop(session_id, None)


# ── Response Formatter ─────────────────────────────────────────────────────
class ResponseFormatter:
    """Formats raw execution results into natural-language Jarvis responses."""

    @staticmethod
    def format(intent: MissionIntent, results: Dict[str, Any], message: str) -> str:
        if intent == MissionIntent.FIND_CHARGER:
            return ResponseFormatter._format_chargers(results.get("find_chargers", {}))

        elif intent == MissionIntent.CHARGER_ON_ROUTE:
            ranked = results.get("rank_chargers", {})
            chargers = ranked.get("ranked_chargers", [])
            total = ranked.get("total_found", 0)
            if not chargers:
                return "I couldn't find any chargers along that route. Try a different destination?"
            top = chargers[:3]
            lines = [f"⚡ Found **{total} chargers** along your route. Top picks:\n"]
            for i, c in enumerate(top, 1):
                power = c.get("power_kw", "?")
                dist = c.get("route_distance_km", 0)
                lines.append(
                    f"**{i}. {c['name']}** — {power} kW · {dist:.0f}km into route · "
                    f"_{c.get('operator', 'Unknown')}_"
                )
            return "\n".join(lines)

        elif intent == MissionIntent.FIND_FOOD:
            data = results.get("find_food", {})
            restaurants = data.get("restaurants", [])
            if not restaurants:
                return "No restaurants found nearby. Try a different area?"
            top = sorted(restaurants, key=lambda r: r.get("rating", 0), reverse=True)[:3]
            lines = ["🍽 Here are the best options nearby:\n"]
            for i, r in enumerate(top, 1):
                lines.append(
                    f"**{i}. {r['name']}** — ⭐ {r.get('rating', 'N/A')} "
                    f"({r.get('user_ratings_total', 0)} reviews) · {r.get('address', '')}"
                )
            return "\n".join(lines)

        elif intent == MissionIntent.CHECK_WEATHER:
            data = results.get("check_weather", {})
            if "error" in data:
                return f"Couldn't fetch weather: {data['error']}"
            return (
                f"🌤 Weather at **{data.get('city', 'your location')}**: "
                f"**{data.get('condition', 'Unknown')}**, {data.get('temp', '?')}°C"
            )

        elif intent == MissionIntent.CHECK_TRAFFIC:
            status = results.get("check_traffic", "Unknown")
            if isinstance(status, str):
                emoji = "🟢" if "clear" in status.lower() else "🟡" if "moderate" in status.lower() else "🔴"
                return f"{emoji} Traffic status: **{status}**"
            return f"🚗 Traffic: {status}"

        elif intent == MissionIntent.VEHICLE_STATUS:
            return "🔋 Battery at **67%** · Estimated range **268 km** · Not currently charging"

        return "I've processed your request. Let me know if you need anything else!"

    @staticmethod
    def _format_chargers(data) -> str:
        if isinstance(data, list):
            chargers = data
        elif isinstance(data, dict):
            chargers = data.get("chargers", data) if "chargers" in data else [data]
        else:
            chargers = []

        if not chargers or (isinstance(chargers, list) and len(chargers) == 0):
            return "No chargers found nearby."

        top = chargers[:5] if isinstance(chargers, list) else [chargers]
        lines = [f"⚡ Found **{len(chargers)}** chargers nearby:\n"]
        for i, c in enumerate(top, 1):
            if isinstance(c, dict) and "error" not in c:
                lines.append(
                    f"**{i}. {c.get('name', 'Unknown')}** — "
                    f"{c.get('power_kw', '?')} kW · {c.get('distance_km', '?')} km away"
                )
        return "\n".join(lines)


# ── Mission Control (Main Orchestrator) ────────────────────────────────────
class MissionControl:
    """
    Central orchestrator for the EV Co-Pilot. Intercepts queries that
    require multi-step execution or standalone agent calls, while
    delegating standard trip-collection flow to the existing chat pipeline.

    Usage in chat.py:
        result = await mission_control.handle(message, context)
        if result:
            return result  # Mission Control handled it
        # else: fall through to existing trip collection logic
    """

    def __init__(self):
        self.intent_analyzer = IntentAnalyzer()
        self.plan_builder = PlanBuilder()
        self.execution_engine = ExecutionEngine()
        self.memory = SessionMemory()
        self.formatter = ResponseFormatter()
        print("MissionControl: Initialized ✓")

    async def handle(self, message: str, context: Dict[str, Any],
                     session_id: str = "default") -> Optional[Dict[str, Any]]:
        """
        Main entry point. Returns a response dict if Mission Control handles
        the query, or None if it should fall through to the existing chat flow.

        Returns:
            {"handled": True, "reply": "...", "intent": "...", "plan": [...]}
            or None (let existing chat.py handle it)
        """
        intent = self.intent_analyzer.classify(message, context)
        print(f"MissionControl [intent]: {message!r} → {intent.value}")

        # Store in session memory
        self.memory.update(session_id, last_query=message, last_intent=intent.value)

        # ── Intents handled by Mission Control ─────────────────────────────
        if intent in (
            MissionIntent.FIND_CHARGER,
            MissionIntent.CHARGER_ON_ROUTE,
            MissionIntent.FIND_FOOD,
            MissionIntent.CHECK_WEATHER,
            MissionIntent.CHECK_TRAFFIC,
            MissionIntent.VEHICLE_STATUS,
            MissionIntent.QUERY_PLAN,
            MissionIntent.OFF_TOPIC,
        ):
            # 1. Vehicle status is instant
            if intent == MissionIntent.VEHICLE_STATUS:
                reply = self.formatter.format(intent, {}, message)
                return {"handled": True, "reply": reply, "intent": intent.value}

            # 2. Off-topic: steer back
            if intent == MissionIntent.OFF_TOPIC:
                reply = f"I'm afraid I'm specialized in EV travel and road trips. I can't help with that, but your trip to {context.get('destination', 'your destination')} is my top priority! Any questions about chargers or stops?"
                return {"handled": True, "reply": reply, "intent": intent.value}

            # 3. Query Plan: answer from cache
            if intent == MissionIntent.QUERY_PLAN:
                mem = self.memory.get(session_id)
                last_res = mem.get("last_results")
                if not last_res:
                    return None # Fall through to normal chat (maybe they are still setting up)
                
                # Use LLM to answer from results
                reply = await self._answer_from_data(message, last_res, context)
                return {"handled": True, "reply": reply, "intent": intent.value}

            # 4. Build and execute new plan for other intents
            plan = self.plan_builder.build(intent, message, context)
            plan_summary = [s.to_dict() for s in plan]
            print(f"MissionControl [plan]: {json.dumps(plan_summary, indent=2)}")

            self.memory.update(session_id, last_plan=plan_summary)

            # Execute
            results = await self.execution_engine.execute_plan(plan)
            self.memory.update(session_id, last_results=results)

            # Cache route/charger results for follow-up queries
            if "get_route" in results:
                self.memory.update(session_id, route_cache=results["get_route"])
            if "rank_chargers" in results:
                self.memory.update(session_id, charger_cache=results["rank_chargers"])

            # Format response
            reply = self.formatter.format(intent, results, message)

            return {
                "handled": True,
                "reply": reply,
                "intent": intent.value,
                "plan": plan_summary,
                "results": {k: _safe_serialize(v) for k, v in results.items()},
            }

        # ── Intents delegated to existing chat.py flow ─────────────────────
        return None

    async def _answer_from_data(self, query: str, data: Dict, context: Dict) -> str:
        """Use LLM to answer a specific question about the trip data."""
        from langchain_community.chat_models import ChatOllama
        
        llm = ChatOllama(
            model="gemma3:4b",
            temperature=0,
            num_ctx=8192,
        )

        # Prepare context for LLM
        # Strip large fields to avoid token blowup
        context_str = json.dumps({
            "origin": context.get("origin"),
            "destination": context.get("destination"),
            "trip_data": data
        }, default=str)[:4000]

        prompt = f"""You are Jarvis, the EV Co-Pilot. Answer the user's question using ONLY the provided trip data.
If the answer isn't in the data, politely say you don't have that specific detail yet but offer related info.
BE CONCISE. 1-2 sentences.

TRIP DATA:
{context_str}

USER QUESTION: {query}
"""
        res = await llm.ainvoke([HumanMessage(content=prompt)])
        return str(res.content) if res.content else "I'm looking into those details for you."


def _safe_serialize(obj):
    """Safely convert execution results for JSON response."""
    if isinstance(obj, dict):
        return {k: _safe_serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_safe_serialize(i) for i in obj]
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    return str(obj)


# ── Singleton Instance ─────────────────────────────────────────────────────
_mc_singleton: Optional[MissionControl] = None

def _mission_control_instance() -> MissionControl:
    """Return the shared MissionControl singleton."""
    global _mc_singleton
    if _mc_singleton is None:
        _mc_singleton = MissionControl()
    return _mc_singleton
