import sys, json, asyncio; sys.path.append('.')
from ev_travel_agent.agents.food_agent import FoodAgent

async def test():
    agent = FoodAgent()
    message = {
        "preference": "South Indian food for breakfast and biriyani for lunch",
        "departure_time": "tomorrow at 08:00 AM",
        "waypoints": [
            {"lat": 13.0, "lng": 79.5, "distance_at_point": d}
            for d in [169.25, 203.1, 236.95, 270.8, 304.65]
        ]
    }
    
    # We just want to see the search plans and what it fetches
    # Let's mock the bus to capture the output
    from ev_travel_agent.utils.agent_bus import bus
    
    results = []
    async def mock_publish(event, data):
        if event == "food_data":
            results.append(data["restaurants"])
    
    bus.publish = mock_publish
    
    await agent.handle_find_food(message)
    print("Lunch places returned:")
    for r in results[0]:
        if r.get("meal_type") == "lunch":
            print(f"Name: {r['name']}, Distance: {r['route_distance_km']}, PlaceType: {r['place_type']}")

asyncio.run(test())
