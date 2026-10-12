import sys, json, asyncio; sys.path.append('.')
from ev_travel_agent.services.places_service import PlacesService

# Get actual waypoints from a recent mock plan or route agent
async def main():
    service = PlacesService()
    # The route goes from Bangalore to Chennai. Let's pick a coordinate near 203km and 236km.
    # 203km is roughly near Ambur / Vaniyambadi (famous for Biryani!)
    # Let's see if places_service finds anything.
    point1 = {"lat": 12.78, "lng": 78.71, "distance_at_point": 203.0} # Vaniyambadi area
    point2 = {"lat": 12.87, "lng": 79.13, "distance_at_point": 236.0} # Vellore area

    print("--- 203km ---")
    res1 = service.find_nearby_restaurants(point1, "biriyani")
    print(json.dumps([r.get("name") for r in res1], indent=2))
    
    print("--- 236km ---")
    res2 = service.find_nearby_restaurants(point2, "biriyani")
    print(json.dumps([r.get("name") for r in res2], indent=2))

asyncio.run(main())
