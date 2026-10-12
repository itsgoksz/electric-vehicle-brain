from ..utils.agent_bus import bus
from ..services.weather_service import WeatherService


class WeatherAgent:
    def __init__(self):
        self.weather_service = WeatherService()
        bus.subscribe("get_weather", self.handle_get_weather)

    async def handle_get_weather(self, message):
        print("Weather Agent: Retrieving weather conditions along route")
        locations = message.get("locations", [])
        weather_reports = []

        import asyncio
        weather_reports = []
        sem = asyncio.Semaphore(5)

        async def fetch_weather(loc):
            async with sem:
                return await asyncio.to_thread(
                    self.weather_service.get_weather, loc["lat"], loc["lng"]
                )
            
        tasks = [fetch_weather(loc) for loc in locations]
        if tasks:
            weather_reports = await asyncio.gather(*tasks)

        response = {
            "agent": "weather_agent",
            "event": "weather_retrieved",
            "reports": weather_reports,
        }
        await bus.publish("weather_data", response)
