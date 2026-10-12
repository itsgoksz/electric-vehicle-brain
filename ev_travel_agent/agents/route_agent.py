from ..utils.agent_bus import bus
from ..services.maps_service import MapsService


class RouteAgent:
    def __init__(self):
        self.maps_service = MapsService()
        bus.subscribe("find_route", self.handle_find_route)

    async def handle_find_route(self, message):
        print(
            f"Route Agent: Generating route from {message['origin']} to {message['destination']}"
        )
        
        if message['origin'] == "Not specified" or message['destination'] == "Not specified":
             await bus.publish(
                "error", {"agent": "route_agent", "message": "Origin or Destination is missing. Please specify start and end points."}
            )
             return
        import asyncio
        result = await asyncio.to_thread(
            self.maps_service.get_directions,
            message["origin"], message["destination"]
        )

        if "error" in result:
            await bus.publish(
                "error", {"agent": "route_agent", "message": result["error"]}
            )
            return

        response = {
            "agent": "route_agent",
            "event": "route_generated",
            "origin": message["origin"],
            "destination": message["destination"],
            "distance_km": result["distance_km"],
            "duration_minutes": result["duration_minutes"],
            "polyline": result["polyline"],
            "steps": result["steps"],
        }
        await bus.publish("route_data", response)
