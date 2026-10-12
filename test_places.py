import sys, json, asyncio; sys.path.append('.')
from ev_travel_agent.services.places_service import PlacesService

async def main():
    service = PlacesService()
    # Mock point somewhere near 277km (close to Chennai)
    point = {"lat": 13.0, "lng": 79.5, "distance_at_point": 277.8}
    res = service.find_nearby_restaurants(point, "biriyani")
    print(json.dumps(res, indent=2))

asyncio.run(main())
