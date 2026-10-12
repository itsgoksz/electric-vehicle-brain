from ..utils.agent_bus import bus
from ..database.db import AsyncSessionLocal, TripRecord, UserPreference
from sqlalchemy.future import select


class MemoryAgent:
    def __init__(self):
        bus.subscribe("trip_completed", self.handle_trip_completed)
        bus.subscribe("get_user_preferences", self.handle_get_preferences)

    async def handle_trip_completed(self, message):
        async with AsyncSessionLocal() as session:
            new_trip = TripRecord(
                origin=message["origin"],
                destination=message["destination"],
                battery_level=message["battery_level"],
                preferences=message["preferences"],
                plan=message["plan"],
            )
            session.add(new_trip)
            await session.commit()
            print(
                f"Memory Agent: Stored trip from {message['origin']} to {message['destination']}"
            )

    async def handle_get_preferences(self, message):
        user_id = message.get("user_id", "default")
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(UserPreference).where(UserPreference.user_id == user_id)
            )
            pref = result.scalars().first()

            response = {
                "agent": "memory_agent",
                "event": "preferences_retrieved",
                "fav_restaurants": pref.fav_restaurants if pref else [],
                "fav_charging_networks": pref.fav_charging_networks if pref else [],
            }
            await bus.publish("preferences_data", response)
