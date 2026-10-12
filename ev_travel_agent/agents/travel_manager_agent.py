import asyncio
from ..utils.agent_bus import bus


class TravelManagerAgent:
    def __init__(self):
        self.sessions = {}
        bus.subscribe("mission_defined", self.start_planning)
        bus.subscribe("route_data", self.on_route_data)
        bus.subscribe("traffic_data", self.on_traffic_data)
        bus.subscribe("weather_data", self.on_weather_data)
        bus.subscribe("charging_data", self.on_charging_data)
        bus.subscribe("food_data", self.on_food_data)
        bus.subscribe("telemetry_data", self.on_telemetry_data)
        bus.subscribe("optimization_data", self.on_optimization_data)
        bus.subscribe("curation_data", self.on_curation_data)

    def _get_session(self, data):
        session_id = data.get("session_id")
        if not session_id or session_id not in self.sessions:
            return None
        return self.sessions[session_id]

    async def start_planning(self, request):
        session_id = request.get("session_id")
        is_replan = request.get("replan_food_only", False)
        print(f"\n--- Travel Manager Agent: Starting trip planning (replan={is_replan}, session={session_id}) ---")
        
        if session_id not in self.sessions:
            self.sessions[session_id] = {
                "current_trip": request,
                "results": {},
                "_planning_triggered": False
            }
        else:
            self.sessions[session_id]["current_trip"] = request
            self.sessions[session_id]["_planning_triggered"] = False
            
        session = self.sessions[session_id]
        
        if is_replan and session["results"].get("route"):
            print("Travel Manager: Partial Re-plan. Reusing existing route data...")
            # Re-run food search and re-trigger check_readiness
            await self.on_route_data(session["results"]["route"])
        else:
            session["results"] = {}
            # 1. Get Route
            await bus.publish(
                "find_route",
                {"origin": request["origin"], "destination": request["destination"]},
            )

    async def on_route_data(self, data):
        session = self._get_session(data)
        if not session: return
        session["results"]["route"] = data
        steps = data["steps"]

        # Calculate cumulative distance at each step
        cum_dist = 0
        waypoints_with_dist = []
        for step in steps:
            cum_dist += step["distance"]["value"] / 1000.0
            waypoints_with_dist.append(
                {
                    "lat": step["end_location"]["lat"],
                    "lng": step["end_location"]["lng"],
                    "distance_at_point": cum_dist,
                }
            )

        # Sample points along the route (e.g., every 10-15km for better coverage)
        num_samples = 50
        sampled_waypoints = []
        for i in range(num_samples):
            idx = int((i / (num_samples - 1)) * (len(waypoints_with_dist) - 1))
            sampled_waypoints.append(waypoints_with_dist[idx])

        mid_point = data["steps"][len(data["steps"]) // 2]["end_location"]

        # Parallel requests for data
        tasks = [
            bus.publish(
                "get_traffic",
                {"origin": data["origin"], "destination": data["destination"]},
            ),
            bus.publish("get_weather", {"locations": [mid_point]}),
            bus.publish("find_chargers", {"waypoints": sampled_waypoints}),
            bus.publish(
                "find_food",
                {
                    "waypoints": sampled_waypoints,
                    "preference": session["current_trip"].get("preference") or "vegetarian",
                    "departure_time": session["current_trip"].get("departure_time", ""),
                },
            ),
        ]
        await asyncio.gather(*tasks)

    async def on_traffic_data(self, data):
        session = self._get_session(data)
        if not session: return
        session["results"]["traffic"] = data
        self.check_readiness(data)

    async def on_weather_data(self, data):
        session = self._get_session(data)
        if not session: return
        session["results"]["weather"] = data
        # Now we have weather, we can get telemetry (AC/Heating usage etc)
        await bus.publish("get_telemetry", {"weather_reports": data.get("reports", [])})
        self.check_readiness(data)

    async def on_telemetry_data(self, data):
        session = self._get_session(data)
        if not session: return
        session["results"]["telemetry"] = data
        self.check_readiness(data)

    async def on_charging_data(self, data):
        session = self._get_session(data)
        if not session: return
        session["results"]["charging"] = data
        self.check_readiness(data)

    async def on_food_data(self, data):
        session = self._get_session(data)
        if not session: return
        session["results"]["food"] = data
        self.check_readiness(data)

    def check_readiness(self, data):
        session = self._get_session(data)
        if not session: return
        
        required = ["route", "traffic", "weather", "charging", "food", "telemetry"]
        if all(k in session["results"] for k in required) and not session["_planning_triggered"]:
            session["_planning_triggered"] = True
            session_id = data.get("session_id")
            asyncio.create_task(self.trigger_optimization(session_id))

    async def trigger_optimization(self, session_id):
        session = self.sessions.get(session_id)
        if not session: return
        await bus.publish(
            "optimize_plan",
            {
                "battery_level": session["current_trip"].get("battery_level", 70),
                "departure_time": session["current_trip"].get("departure_time"),
                "preference": session["current_trip"].get("preference", ""),
                "distance_km": session["results"]["route"]["distance_km"],
                "duration_minutes": session["results"]["route"].get("duration_minutes", 0),
                "chargers": session["results"]["charging"]["chargers"],
                "restaurants": session["results"]["food"]["restaurants"],
                "polyline": session["results"]["route"].get("polyline", ""),
                "efficiency_km_per_percent": session["results"]["telemetry"][
                    "efficiency_km_per_percent"
                ],
                "session_id": session_id,
            },
        )

    async def on_optimization_data(self, data):
        session = self._get_session(data)
        if not session: return
        session["results"]["optimization"] = data
        # Step 4: AI Curation (Jarvis Briefing)
        await bus.publish("curate_plan", {"plan": self.format_final_plan(session)})

    async def on_curation_data(self, data):
        session = self._get_session(data)
        if not session: return
        session["results"]["curation"] = data
        final_plan = self.format_final_plan(session)
        final_plan["jarvis_briefing"] = data["curation"]
        await bus.publish("plan_complete", final_plan)

    def format_final_plan(self, session):
        route = session["results"]["route"]
        opt = session["results"]["optimization"]

        plan = {
            "summary": f"{route['origin']} → {route['destination']}",
            "distance": f"{route['distance_km']:.1f} km",
            "time": f"{route['duration_minutes'] // 60:.0f}h {route['duration_minutes'] % 60:.0f}m",
            "traffic": session["results"]["traffic"]["status"],
            "weather": session["results"]["weather"]["reports"][0]["condition"]
            if session["results"]["weather"]["reports"]
            else "Unknown",
            "overview_polyline": route.get("polyline", ""),
            "stops": opt["stops"],
        }
        return plan
