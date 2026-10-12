import googlemaps
from typing import List, Dict, Any
from ..utils.config import Config


class PlacesService:
    def __init__(self):
        self.mock_mode = not Config.GOOGLE_MAPS_API_KEY
        if self.mock_mode:
            print("WARNING: GOOGLE_MAPS_API_KEY missing. Running Places in MOCK MODE.")
            self.gmaps = None
        else:
            self.gmaps = googlemaps.Client(key=Config.GOOGLE_MAPS_API_KEY)

    def find_nearby_restaurants(
        self, location: Dict[str, float], cuisine: str = "vegetarian"
    ) -> List[Dict[str, Any]]:
        if self.mock_mode:
            return [
                {
                    "name": "Mock Veg Restaurant",
                    "rating": 4.5,
                    "address": "123 Mock St, Bangalore",
                    "location": location,
                }
            ]
        try:
            params = {
                "location": (location["lat"], location["lng"]),
                "radius": 5000,
            }
            # Always prioritize restaurant/cafe type to filter out jewelry stores/noise
            params["type"] = "restaurant"
            if cuisine == "cafe":
                params["type"] = "cafe"

            if " " in cuisine or cuisine == "everything" or cuisine == "Murugan Idli Shop":
                 # Increase radius for specific named places to avoid missing them between route points
                 if " " in cuisine:
                     params["radius"] = 15000 
            
            if cuisine != "everything":
                params["keyword"] = cuisine

            places_result = self.gmaps.places_nearby(**params)

            restaurants = []
            for place in places_result.get("results", []):
                restaurants.append(
                    {
                        "name": place["name"],
                        "rating": place.get("rating", 0),
                        "user_ratings_total": place.get("user_ratings_total", 0),
                        "price_level": place.get("price_level", 2), # Default to moderate if missing
                        "address": place.get("vicinity", ""),
                        "location": place["geometry"]["location"],
                        "open_now": place.get("opening_hours", {}).get(
                            "open_now", "Unknown"
                        ),
                    }
                )
            return restaurants
        except Exception as e:
            return [{"error": str(e)}]
