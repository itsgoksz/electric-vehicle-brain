import googlemaps
from typing import Dict, Any
from ..utils.config import Config


class MapsService:
    def __init__(self):
        self.mock_mode = not Config.GOOGLE_MAPS_API_KEY
        if self.mock_mode:
            print("WARNING: GOOGLE_MAPS_API_KEY missing. Running in MOCK MODE.")

    @property
    def gmaps(self):
        if self.mock_mode:
            return None
        # Disable proxy resolution to fix macOS DNS resolution issues in threads
        return googlemaps.Client(
            key=Config.GOOGLE_MAPS_API_KEY,
            requests_kwargs={"proxies": {"http": "", "https": ""}}
        )

    @staticmethod
    def _normalize_location(loc: str) -> str:
        """Append ', India' to short location names that lack a country/state hint.
        Google Maps needs enough context to disambiguate neighbourhood names like
        'Madiwala' or city names like 'Salem'."""
        loc = loc.strip()
        if not loc:
            return loc
        # Already has country or state context (has a comma → probably OK)
        if "," in loc:
            return loc
        # Add India hint so Maps API resolves within India
        return f"{loc}, India"

    def get_directions(self, origin: str, destination: str) -> Dict[str, Any]:
        if self.mock_mode:
            return {
                "distance_km": 345.0,
                "duration_minutes": 350.0,
                "summary": "NH44",
                "polyline": "mock_polyline",
                "steps": [
                    {
                        "end_location": {"lat": 12.8, "lng": 77.5},
                        "distance": {"value": 69000, "text": "69 km"},
                        "duration": {"value": 4200, "text": "1 hr 10 mins"},
                    },
                    {
                        "end_location": {"lat": 12.6, "lng": 77.3},
                        "distance": {"value": 69000, "text": "69 km"},
                        "duration": {"value": 4200, "text": "1 hr 10 mins"},
                    },
                    {
                        "end_location": {"lat": 12.4, "lng": 76.9},
                        "distance": {"value": 69000, "text": "69 km"},
                        "duration": {"value": 4200, "text": "1 hr 10 mins"},
                    },
                    {
                        "end_location": {"lat": 12.3, "lng": 76.5},
                        "distance": {"value": 69000, "text": "69 km"},
                        "duration": {"value": 4200, "text": "1 hr 10 mins"},
                    },
                    {
                        "end_location": {"lat": 12.2, "lng": 75.9},
                        "distance": {"value": 69000, "text": "69 km"},
                        "duration": {"value": 4200, "text": "1 hr 10 mins"},
                    },
                ],
            }
        try:
            o_query = self._normalize_location(origin)
            d_query = self._normalize_location(destination)

            o_final, d_final = o_query, d_query

            # Attempt Local Bias Geocoding
            try:
                # 1. Geocode origin to get a concrete starting point
                o_results = self.gmaps.geocode(o_query, region="IN")
                if o_results:
                    o_loc = o_results[0]["geometry"]["location"]
                    o_final = f"place_id:{o_results[0]['place_id']}"
                    
                    # 2. Geocode destination with a soft bounds bias around the origin (~100km)
                    bounds = {
                        "northeast": {"lat": o_loc["lat"] + 1.0, "lng": o_loc["lng"] + 1.0},
                        "southwest": {"lat": o_loc["lat"] - 1.0, "lng": o_loc["lng"] - 1.0}
                    }
                    d_results = self.gmaps.geocode(d_query, region="IN", bounds=bounds)
                    if d_results:
                        d_final = f"place_id:{d_results[0]['place_id']}"
            except Exception as geo_e:
                print(f"Geocoding API failed (fallback to text routing): {geo_e}")

            directions = self.gmaps.directions(o_final, d_final, mode="driving", region="IN")
            if not directions:
                return {"error": f"No route found between '{origin}' and '{destination}'. Please check the place names."}

            route = directions[0]
            leg = route["legs"][0]

            return {
                "distance_km": leg["distance"]["value"] / 1000,
                "duration_minutes": leg["duration"]["value"] / 60,
                "summary": route["summary"],
                "polyline": route["overview_polyline"]["points"],
                "steps": leg["steps"],
            }
        except Exception as e:
            return {"error": str(e)}

    def get_traffic_status(self, origin: str, destination: str) -> str:
        # Simplified traffic status based on duration vs duration_in_traffic
        # Simplified traffic status based on duration vs duration_in_traffic
        try:
            o_query = self._normalize_location(origin)
            d_query = self._normalize_location(destination)

            o_final, d_final = o_query, d_query

            try:
                # 1. Geocode origin first
                o_results = self.gmaps.geocode(o_query, region="IN")
                if o_results:
                    o_loc = o_results[0]["geometry"]["location"]
                    o_final = f"place_id:{o_results[0]['place_id']}"
                    
                    # 2. Geocode destination with a soft bounds bias around the origin (~100km)
                    bounds = {
                        "northeast": {"lat": o_loc["lat"] + 1.0, "lng": o_loc["lng"] + 1.0},
                        "southwest": {"lat": o_loc["lat"] - 1.0, "lng": o_loc["lng"] - 1.0}
                    }
                    d_results = self.gmaps.geocode(d_query, region="IN", bounds=bounds)
                    if d_results:
                        d_final = f"place_id:{d_results[0]['place_id']}"
            except Exception as geo_e:
                print(f"Geocoding API failed (fallback to text routing): {geo_e}")

            directions = self.gmaps.directions(
                o_final, d_final, departure_time="now", traffic_model="best_guess", region="IN"
            )
            if not directions:
                return "Unknown"

            leg = directions[0]["legs"][0]
            if "duration_in_traffic" in leg:
                delay = (
                    leg["duration_in_traffic"]["value"] - leg["duration"]["value"]
                ) / 60
                if delay > 20:
                    return "Heavy Traffic"
                if delay > 10:
                    return "Moderate Traffic"
            return "Clear Traffic"
        except Exception:
            return "Unknown"
