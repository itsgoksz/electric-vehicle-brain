from ..utils.agent_bus import bus


class TelemetryAgent:
    def __init__(self):
        bus.subscribe("get_telemetry", self.handle_get_telemetry)

    async def handle_get_telemetry(self, message):
        """
        Calculates EV efficiency adjustment based on external factors.
        Reference: 1% battery = 4km (base)
        """
        weather_reports = message.get("weather_reports", [])
        base_efficiency = 4.0  # km per %

        # Simple Physics Model
        # High heat or cold reduces efficiency due to HVAC (Heating, Ventilation, Air Conditioning)
        temp_adjustment = 0.0
        if weather_reports:
            # For simplicity, check the first report (often the midpoint)
            temp = weather_reports[0].get("temp", 25)  # Celsius

            if temp > 35:  # Hot: AC usage
                temp_adjustment = -0.5
            elif temp < 15:  # Cold: Heating usage
                temp_adjustment = -0.8
            elif 20 <= temp <= 25:  # Optimal
                temp_adjustment = 0.2

        adjusted_efficiency = base_efficiency + temp_adjustment

        print(
            f"Telemetry Agent: Calculated dynamic efficiency: {adjusted_efficiency:.2f} km/% (Adj: {temp_adjustment})"
        )

        response = {
            "agent": "telemetry_agent",
            "event": "telemetry_data",
            "efficiency_km_per_percent": adjusted_efficiency,
        }
        await bus.publish("telemetry_data", response)
