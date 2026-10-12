import math

class VehicleDynamics:
    """
    Simulates dynamic battery consumption based on environmental and vehicle factors.
    Used for predictive intelligence and planning.
    """
    
    def __init__(self, base_efficiency_km_per_percent: float = 3.5):
        self.base_efficiency = base_efficiency_km_per_percent
    
    def calculate_consumption_multiplier(self, factors: dict) -> float:
        """
        Calculates a multiplier for battery consumption. 
        1.0 = standard, >1.0 = higher consumption.
        """
        multiplier = 1.0
        
        # 1. Elevation (Steep climbs increase consumption significantly)
        # In a real app, this would use DEM data. Here we simulate based on segment type.
        is_mountain = factors.get("terrain") == "mountain"
        if is_mountain:
            multiplier += 0.20  # +20% for steep climbs
            
        # 2. Weather (Extreme cold or high heat)
        temp = factors.get("temperature", 25) # Celsius
        if temp > 35: # Hot: AC usage
            multiplier += 0.08
        elif temp < 10: # Cold: Battery heater/Cabin heater
            multiplier += 0.15
            
        # 3. AC Usage
        ac_setting = factors.get("ac_usage", "medium") # eco, medium, high
        ac_map = {"eco": 0.02, "medium": 0.05, "high": 0.10}
        multiplier += ac_map.get(ac_setting, 0.05)
        
        # 4. Traffic (Stop-and-go is less efficient for EVs than steady highway, or vice-versa depending on regen)
        # Steady highway (70km/h+) is actually higher drag (speed^2)
        traffic = factors.get("traffic", "Clear")
        if traffic == "Heavy":
            multiplier += 0.05 # Idle drain + acceleration cycles
        
        # 5. Riding Style (Aggressive vs Chill)
        style = factors.get("style", "normal")
        if style == "aggressive":
            multiplier += 0.12
        elif style == "chill":
            multiplier -= 0.05
            
        # 6. Payload (Weight)
        payload = factors.get("payload_kg", 150) # Driver + passengers + luggage
        # Every 100kg extra adds ~2-3% consumption
        extra_weight = max(0, payload - 100)
        multiplier += (extra_weight / 100) * 0.02
        
        return round(multiplier, 2)

    def get_dynamic_range(self, current_soc: float, multiplier: float) -> float:
        """Returns the predicted range in km based on current battery and dynamic factors."""
        return (current_soc * self.base_efficiency) / multiplier

    def get_proactive_insight(self, multiplier: float) -> str:
        """Generates a human-readable insight about the consumption."""
        if multiplier > 1.25:
            return f"Extreme conditions detected. Combined factors (terrain/AC/weather) are increasing consumption by {int((multiplier-1)*100)}%."
        elif multiplier > 1.10:
            return f"Moderate consumption increase (+{int((multiplier-1)*100)}%) due to current conditions."
        return "Vehicle is operating at optimal efficiency."
