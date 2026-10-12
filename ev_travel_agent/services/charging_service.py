import requests
from typing import List, Dict, Any
from ..utils.config import Config


class ChargingService:
    def __init__(self):
        self.api_key = Config.OPEN_CHARGE_MAP_API_KEY
        self.base_url = "https://api.openchargemap.io/v3/poi/"
        self.mock_mode = not self.api_key

    def find_nearby_chargers(self, location: Dict[str, float]) -> List[Dict[str, Any]]:
        if self.mock_mode:
            return [
                {
                    "name": "Mock DC Fast Charger",
                    "address": "456 Charger Rd, Krishnagiri",
                    "distance_km": 5.0,
                    "location": {"lat": 12.5222, "lng": 78.2122},
                    "operator": "Mock Charge",
                }
            ]
        try:
            params = {
                "key": self.api_key,
                "latitude": location["lat"],
                "longitude": location["lng"],
                "distance": 30,  # 30km radius
                "distanceunit": "KM",
                "maxresults": 50,
            }
            headers = {
                "User-Agent": "EV-Trip-Planner/1.0",
                "Accept": "application/json"
            }
            response = requests.get(self.base_url, params=params, headers=headers, timeout=10.0)
            response.raise_for_status()

            chargers = []
            for poi in response.json():
                # Extract speed/power info - find the maximum power available
                connections = poi.get("Connections", [])
                max_power = 0.0
                best_conn_type = "Unknown"
                
                for conn in connections:
                    try:
                        p = float(conn.get("PowerKW", 0) or 0)
                        if p > max_power:
                            max_power = p
                            best_conn_type = conn.get("ConnectionType", {}).get("Title", "Unknown")
                    except (ValueError, TypeError):
                        continue

                chargers.append(
                    {
                        "name": poi["AddressInfo"]["Title"],
                        "address": poi["AddressInfo"].get("AddressLine1", "Unknown Address"),
                        "distance_km": poi["AddressInfo"].get("Distance", 0),
                        "location": {
                            "lat": poi["AddressInfo"]["Latitude"],
                            "lng": poi["AddressInfo"]["Longitude"],
                        },
                        "operator": poi.get("OperatorInfo", {}).get("Title", "Unknown"),
                        "power_kw": max_power if max_power > 0 else "Unknown",
                        "connection_type": best_conn_type,
                    }
                )
            return chargers
        except Exception as e:
            return [{"error": str(e)}]
