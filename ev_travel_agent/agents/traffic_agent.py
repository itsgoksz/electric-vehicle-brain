from ..utils.agent_bus import bus
from ..services.maps_service import MapsService


class TrafficAgent:
    def __init__(self):
        self.maps_service = MapsService()
        bus.subscribe("get_traffic", self.handle_get_traffic)

    async def handle_get_traffic(self, message):
        print("Traffic Agent: Determining delays and congestion")
        origin = message["origin"]
        destination = message["destination"]

        import asyncio
        status = await asyncio.to_thread(
            self.maps_service.get_traffic_status, origin, destination
        )

        response = {
            "agent": "traffic_agent",
            "event": "traffic_retrieved",
            "status": status,
        }
        await bus.publish("traffic_data", response)
