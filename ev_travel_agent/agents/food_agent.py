import ast
import re
from datetime import datetime, timedelta
from ..utils.agent_bus import bus
from ..services.places_service import PlacesService
from ..utils.preference_utils import parse_food_preference


# Meal windows (must match optimization_agent.py and chat.py)
MEAL_WINDOWS = [
    ("breakfast", 7.5, 10.5),  # 7:30 AM to 10:30 AM
    ("lunch", 11.5, 15.0),  # 11:30 AM to 3:00 PM
    ("tea", 15.5, 19.0),  # 3:30 PM to 7:00 PM
    ("dinner", 19.5, 22.5),  # 7:30 PM to 10:30 PM
]

# Default search keywords per meal type — used when user hasn't specified a preference
BREAKFAST_KEYWORDS = [
    "breakfast restaurant",
    "South Indian restaurant",
    "idli dosa",
    "cafe",
]
LUNCH_KEYWORDS = ["restaurant"]
TEA_KEYWORDS = ["cafe", "coffee shop"]
DINNER_KEYWORDS = ["restaurant"]

# Route distance ranges per meal (fraction of total distance)
# EV routes are highly dynamic, so we must search across the entire route (0.0 to 1.0)
# to guarantee preferences are found regardless of when the meal stop actually occurs.
MEAL_ROUTE_RANGES = {
    "breakfast": (0.0, 1.0),
    "lunch": (0.0, 1.0),
    "tea": (0.0, 1.0),
    "dinner": (0.0, 1.0),
}


def _parse_preference(raw_pref):
    return parse_food_preference(raw_pref)


def _keyword_for_food_type(food_type: str) -> str:
    """Map a free-text food type to a search keyword."""
    if not food_type:
        return "restaurant"
    t = food_type.lower()

    # If the user gave a very long string, try to pick the most important noun
    if len(t.split()) > 3:
        # Priority mapping
        for kw in [
            "burger",
            "pizza",
            "biryani",
            "dosa",
            "idli",
            "south indian",
            "north indian",
            "chinese",
            "thai",
            "italian",
        ]:
            if kw in t:
                return kw

    if "burger" in t or "fast food" in t or "mcdonald" in t or "kfc" in t:
        return "fast food"
    if "south indian" in t or "idli" in t or "dosa" in t:
        return "South Indian restaurant"
    if "pizza" in t:
        return "pizza"
    if "north indian" in t:
        return "North Indian restaurant"
    if "biryani" in t:
        return "biryani"
    if "coffee" in t or "cafe" in t or "tea" in t:
        return "cafe"
    if "veg" in t and "non" not in t:
        return "vegetarian restaurant"

    return food_type


def _parse_departure_time(dep_str: str) -> datetime:
    """Parse departure time string robustly, handling 'tomorrow' and fuzzy text."""
    base_date = datetime.now()
    if not dep_str:
        return base_date
        
    s = dep_str.strip().lower()
    if s in ["now", "immediately", "asap", "right now"]:
        return base_date
        
    if "tomorrow" in s:
        base_date += timedelta(days=1)
        s = s.replace("tomorrow", "").replace("at", "").strip()
    elif "today" in s:
        s = s.replace("today", "").replace("at", "").strip()
        
    try:
        from dateutil import parser as dt_parser
        return dt_parser.parse(s, default=base_date, fuzzy=True)
    except Exception:
        pass
    return datetime.now()


def _meals_needed_for_trip(departure_time: datetime) -> list:
    """Determine which meals the driver will need during the trip based on departure time."""
    needed = []
    dep_hour = departure_time.hour + departure_time.minute / 60.0
    is_today = departure_time.date() == datetime.now().date()
    
    for meal_name, start, end in MEAL_WINDOWS:
        # If it's a future day, we assume they need all meals on that journey
        if not is_today:
            needed.append(meal_name)
        elif end > dep_hour:
            # A meal is needed if its window hasn't fully passed before departure
            needed.append(meal_name)
    return needed


class FoodAgent:
    def __init__(self):
        self.places_service = PlacesService()
        bus.subscribe("find_food", self.handle_find_food)

    async def handle_find_food(self, message):
        raw_pref = message.get("preference", "vegetarian")
        pref_dict = _parse_preference(raw_pref)
        departure_time_str = message.get("departure_time", "")
        departure_time = _parse_departure_time(departure_time_str)

        # Determine which meals are needed for this trip
        needed_meals = _meals_needed_for_trip(departure_time)
        dep_hour = departure_time.hour + departure_time.minute / 60.0
        print(
            f"Food Agent: Departure at {dep_hour:.1f}h → Meals needed: {needed_meals}"
        )

        # Build meal-specific search plans
        # User preference applies to lunch/dinner. Breakfast gets breakfast-specific keywords.
        user_food_pref = None
        if isinstance(pref_dict, dict) and pref_dict:
            # Structured: {"lunch": "burger", "breakfast": "idli"}
            user_food_pref = pref_dict
        elif raw_pref and str(raw_pref).lower() not in ["none", "null", ""]:
            user_food_pref = {"default": str(raw_pref)}

        meal_search_plans = []
        for meal in needed_meals:
            # Check if user specified a preference for this meal
            user_kw = None
            if user_food_pref:
                # Check direct match: {"breakfast": "idli"}
                user_kw = user_food_pref.get(meal)
                # Check "default" fallback: {"default": "burger"} → applies to lunch/dinner
                if not user_kw and "default" in user_food_pref:
                    # Default user preference applies to lunch and dinner, NOT breakfast
                    if meal in ("lunch", "dinner"):
                        user_kw = user_food_pref["default"]

            if meal == "breakfast":
                # Breakfast gets dedicated breakfast keywords
                if user_kw:
                    keywords = [_keyword_for_food_type(user_kw)]
                else:
                    keywords = BREAKFAST_KEYWORDS
                place_type = "breakfast"
            elif meal == "tea":
                keywords = (
                    [_keyword_for_food_type(user_kw)] if user_kw else TEA_KEYWORDS
                )
                place_type = "cafe"
            elif meal == "lunch":
                keywords = (
                    [_keyword_for_food_type(user_kw)] if user_kw else LUNCH_KEYWORDS
                )
                place_type = "main"
            elif meal == "dinner":
                keywords = (
                    [_keyword_for_food_type(user_kw)] if user_kw else DINNER_KEYWORDS
                )
                place_type = "main"
            else:
                keywords = (
                    [_keyword_for_food_type(user_kw)] if user_kw else ["restaurant"]
                )
                place_type = "main"

            meal_search_plans.append(
                {
                    "meal": meal,
                    "keywords": keywords,
                    "place_type": place_type,
                    "route_range": MEAL_ROUTE_RANGES.get(meal, (0.20, 0.80)),
                }
            )

        print(
            f"Food Agent: Search plans: {[(p['meal'], p['keywords']) for p in meal_search_plans]}"
        )

        waypoints = message.get("waypoints", [])
        total_distance = waypoints[-1]["distance_at_point"] if waypoints else 0

        # Sample strategic waypoints (higher density to not miss highway chains)
        target_fractions = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
        sample_points = []
        for frac in target_fractions:
            target_dist = frac * total_distance
            closest = min(
                waypoints, key=lambda w: abs(w["distance_at_point"] - target_dist)
            )
            if closest not in sample_points:
                sample_points.append(closest)

        all_restaurants = []

        # Build a list of tasks to fetch restaurants concurrently
        import asyncio
        tasks = []
        sem = asyncio.Semaphore(5)

        for plan in meal_search_plans:
            route_min = plan["route_range"][0] * total_distance
            route_max = plan["route_range"][1] * total_distance

            for point in sample_points:
                dist = point.get("distance_at_point", 0)

                # Only search at points within this meal's route range
                if not (route_min <= dist <= route_max):
                    continue

                for keyword in plan["keywords"]:
                    # Create a closure-like structure to capture the plan and dist context
                    async def fetch_and_tag(pt=point, kw=keyword, p=plan, d=dist):
                        async with sem:
                            res = await asyncio.to_thread(
                                self.places_service.find_nearby_restaurants, pt, kw
                            )
                        valid_results = []
                        if isinstance(res, list):
                            for r in res:
                                if "error" not in r:
                                    r["place_type"] = p["place_type"]
                                    r["meal_type"] = p["meal"]
                                    r["route_distance_km"] = d
                                    valid_results.append(r)
                        return valid_results
                    
                    tasks.append(fetch_and_tag())

        # Execute all Places API requests concurrently
        if tasks:
            results_lists = await asyncio.gather(*tasks)
            for res_list in results_lists:
                all_restaurants.extend(res_list)

        # Deduplicate by place identity
        unique_food = {}
        for r in all_restaurants:
            key = f"{r['name']}-{r['location']['lat']:.4f}-{r['location']['lng']:.4f}"
            if key not in unique_food:
                unique_food[key] = r

        print(
            f"Food Agent: Found {len(unique_food)} unique restaurants across {len(meal_search_plans)} meal types"
        )

        response = {
            "agent": "food_agent",
            "event": "food_found",
            "restaurants": list(unique_food.values()),
        }
        await bus.publish("food_data", response)
