import os
from pathlib import Path
from dotenv import load_dotenv

# Resolve .env relative to the package root (ev_travel_agent/), not the CWD
_package_dir = Path(__file__).resolve().parent.parent
load_dotenv(_package_dir / ".env")

class Config:
    GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")
    OPEN_CHARGE_MAP_API_KEY = os.getenv("OPEN_CHARGE_MAP_API_KEY", "")
    OPEN_WEATHER_API_KEY = os.getenv("OPEN_WEATHER_API_KEY", "")
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")  # For LangChain/Gemini
    
    # Defaults
    DEFAULT_EV_RANGE_KM = 300
    DEFAULT_BATTERY_PERCENT = 70
    
    @classmethod
    def validate(cls):
        missing = []
        if not cls.GOOGLE_MAPS_API_KEY:
            missing.append("GOOGLE_MAPS_API_KEY")
        if not cls.GOOGLE_API_KEY:
            missing.append("GOOGLE_API_KEY")
        return missing
