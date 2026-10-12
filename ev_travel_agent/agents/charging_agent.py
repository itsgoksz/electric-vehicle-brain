from ..utils.agent_bus import bus
from ..services.charging_service import ChargingService


class ChargingAgent:
    def __init__(self):
        self.charging_service = ChargingService()
        bus.subscribe("find_chargers", self.handle_find_chargers)

    async def handle_find_chargers(self, message):
        print(
            "Charging Agent: Finding EV charging stations along the actual route path"
        )
        waypoints = message.get(
            "waypoints", []
        )  # List of {'lat', 'lng', 'distance_at_point'}

        import asyncio
        all_chargers = []
        
        # Downsample waypoints: OpenChargeMap searches a 30km radius, 
        # so we only need to query every ~40km to get full coverage without rate limits
        sparse_waypoints = []
        last_dist = -999
        for pt in waypoints:
            dist = pt.get("distance_at_point", 0)
            if dist - last_dist >= 40:
                sparse_waypoints.append(pt)
                last_dist = dist
                
        sem = asyncio.Semaphore(5)
        
        async def fetch_chargers(pt):
            async with sem:
                await asyncio.sleep(0.5)  # Stagger requests to avoid rate limits
                res = await asyncio.to_thread(
                    self.charging_service.find_nearby_chargers, pt
                )
            valid = []
            if isinstance(res, list):
                for c in res:
                    if "error" not in c:
                        c["route_distance_km"] = pt.get("distance_at_point", 0)
                        valid.append(c)
            return valid

        tasks = [fetch_chargers(pt) for pt in sparse_waypoints]
        if tasks:
            results = await asyncio.gather(*tasks)
            for res_list in results:
                all_chargers.extend(res_list)

        # Deduplicate and keep the one closest to the route if there are duplicates
        unique_chargers = {}
        for c in all_chargers:
            key = f"{c['name']}-{c['location']['lat']}-{c['location']['lng']}"
            if key not in unique_chargers:
                unique_chargers[key] = c

        response = {
            "agent": "charging_agent",
            "event": "chargers_found",
            "chargers": list(unique_chargers.values()),
        }
        await bus.publish("charging_data", response)
