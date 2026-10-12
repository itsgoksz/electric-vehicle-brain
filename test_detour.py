import sys, json, math, asyncio; sys.path.append('.')
from ev_travel_agent.services.places_service import PlacesService

async def main():
    service = PlacesService()
    point1 = {"lat": 12.78, "lng": 78.71, "distance_at_point": 203.0}
    res1 = service.find_nearby_restaurants(point1, "biriyani")
    
    for r in res1:
        if "location" in r:
            r_lat = r["location"]["lat"]
            r_lng = r["location"]["lng"]
            detour_m = math.sqrt((point1["lat"] - r_lat)**2 + (point1["lng"] - r_lng)**2) * 111000
            print(f"{r['name']}: {detour_m:.0f}m")

asyncio.run(main())
