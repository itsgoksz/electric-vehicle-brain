import requests
from typing import Dict, Any
from ..utils.config import Config


class WeatherService:
    def __init__(self):
        self.api_key = Config.OPEN_WEATHER_API_KEY
        self.base_url = "https://api.openweathermap.org/data/2.5/weather"
        self.mock_mode = not self.api_key

    def get_weather(self, lat: float, lon: float) -> Dict[str, Any]:
        if self.mock_mode:
            return {"temp": 28.5, "condition": "Clear Sky", "city": "Mock City"}
        try:
            params = {"lat": lat, "lon": lon, "appid": self.api_key, "units": "metric"}
            response = requests.get(self.base_url, params=params)
            response.raise_for_status()
            data = response.json()

            return {
                "temp": data["main"]["temp"],
                "condition": data["weather"][0]["description"],
                "city": data["name"],
            }
        except Exception as e:
            return {"error": str(e)}
