import asyncio
from ev_travel_agent.services.maps_service import MapsService

async def main():
    service = MapsService()
    result = await asyncio.to_thread(service.get_directions, "JP Nagar", "Coorg")
    print(result.keys() if isinstance(result, dict) else result)

asyncio.run(main())
