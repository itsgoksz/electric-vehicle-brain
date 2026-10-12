import re
import asyncio
from datetime import datetime, timedelta
from ..utils.agent_bus import bus
from ..utils.route_utils import points_from_polyline, distance_to_polyline


class OptimizationAgent:
    def __init__(self):
        bus.subscribe("optimize_plan", self.handle_optimize_plan)

    async def handle_optimize_plan(self, message):
        print("Optimization Agent: Choosing best stops based on EV battery and Time")

        battery_percent = message.get("battery_level", 70)
        departure_time_str = message.get("departure_time")
        total_distance = message.get("distance_km", 0)
        duration_minutes = message.get("duration_minutes", 0)
        chargers_raw = message.get("chargers", [])
        restaurants_raw = message.get("restaurants", [])
        polyline_str = message.get("polyline", "")
        range_per_percent = message.get("efficiency_km_per_percent", 4.0)

        # 0. STRICT ON-ROUTE FILTERING
        # Decode polyline once
        route_points, is_truncated = points_from_polyline(polyline_str)
        if is_truncated:
            print(f"Optimization Agent: WARNING - Polyline data was truncated. {len(route_points)} points recovered.")
        
        # Filter candidates to be within 2km of the actual polyline
        # This ensures they are directly on the highway or immediate service roads
        chargers = [c for c in chargers_raw if distance_to_polyline(c["location"], route_points) < 2.0]
        restaurants = [r for r in restaurants_raw if distance_to_polyline(r["location"], route_points) < 2.0]
        
        print(f"Optimization Agent: Filtered {len(chargers_raw)} -> {len(chargers)} chargers and {len(restaurants_raw)} -> {len(restaurants)} restaurants based on 2km route buffer.")

        from dateutil import parser as dt_parser

        def _parse_time(s):
            """Robustly parse time strings like '2 PM', '14:00', '02:00 PM', 'now', '8am tomorrow'"""
            if not s:
                return datetime.now()
            s = str(s).strip()
            if s.lower() in ["not specified", "now", "immediately", "asap", "right now"]:
                return datetime.now()
            
            # Pre-process for "tomorrow" to ensure correct date anchoring
            base_date = datetime.now()
            if "tomorrow" in s.lower():
                base_date += timedelta(days=1)
                s = s.lower().replace("tomorrow", "").replace("at", "").strip()
            elif "today" in s.lower():
                s = s.lower().replace("today", "").replace("at", "").strip()

            # Try dateutil parser
            try:
                # default=base_date ensures it anchors to today or tomorrow correctly
                return dt_parser.parse(s, default=base_date, fuzzy=True)
            except Exception:
                pass

            # Fallback to strptime patterns
            for fmt in ["%I:%M %p", "%H:%M", "%I %p", "%I%p", "%H:%M %p"]:
                try:
                    t = datetime.strptime(s.upper(), fmt)
                    return base_date.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
                except Exception:
                    continue
            
            print(f"Optimization Agent: Falling back to {base_date.strftime('%Y-%m-%d')} for '{s}'")
            return base_date

        start_time = _parse_time(departure_time_str)
        print(f"Optimization Agent: Trip Start Time Parsed as: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        start_hour = start_time.hour + start_time.minute / 60.0

        # Meal windows per user spec:
        # Breakfast 8am-10:30am, Lunch 1pm-3pm, Coffee 4pm-6pm, Dinner 8pm-10:30pm
        meal_windows = [
            ("Breakfast",         7.5,  10.5), # 7:30 AM to 10:30 AM
            ("Lunch",            11.5,  15.0), # 11:30 AM to 3:00 PM
            ("Coffee/Snacks Break", 15.5,  19.0), # 3:30 PM to 7:00 PM
            ("Dinner",           19.5,  22.5), # 7:30 PM to 10:30 PM
            ("Late Night Snack", 23.5,   4.0),
        ]

        # Skip any meal whose window has *fully passed* before departure
        def _window_ended_before_departure(start, end):
            # If trip starts tomorrow, no meal today was skipped (they were just never eligible)
            # But the logic uses hours, so we check if the departure DATE is today.
            is_today = start_time.date() == datetime.now().date()
            if not is_today:
                return False # Starting in the future, don't skip meals based on current hour
            
            if start > end:  # overnight wrap
                return False
            return end <= start_hour

        skip_meals_pre_departure = {
            name for name, s, e in meal_windows if _window_ended_before_departure(s, e)
        }
        print(f"Optimization Agent: Start Date: {start_time.date()} vs Today: {datetime.now().date()}")
        print(f"Optimization Agent: Skipping meals already passed: {skip_meals_pre_departure}")

        # ── Parse user-specified coffee time and adjust window ────────────────
        if duration_minutes > 0 and total_distance > 0:
            avg_speed = total_distance / (duration_minutes / 60.0)
        else:
            avg_speed = 70.0  # fallback
        target_coffee_km = None  # km from start
        _pref_raw = str(message.get("preference", ""))
        # Extract coffee time
        _cb_m = re.search(
            r'coffee_break[:\s]+(\d{1,2})[:\s]*(\d{0,2})\s*(a\.?m\.?|p\.?m\.?)?'
            r'|coffee.*?(\d{1,2})[:\s]*(\d{0,2})\s*(a\.?m\.?|p\.?m\.?)',
            _pref_raw, re.IGNORECASE
        )
        if _cb_m:
            try:
                g = _cb_m.groups()
                _h_str = g[0] or g[3]
                _m_str = g[1] or g[4] or "0"
                _ampm = (g[2] or g[5] or "").upper()
                _h = int(_h_str)
                _mi = int(_m_str) if _m_str else 0
                if _ampm == "PM" and _h < 12:
                    _h += 12
                elif _ampm == "AM" and _h == 12:
                    _h = 0
                _coffee_hour = _h + _mi / 60.0
                # Tighten window to ±45 min
                _w45 = 0.75
                meal_windows = [
                    (name, (_coffee_hour - _w45) if name == "Coffee/Snacks Break" else s,
                     (_coffee_hour + _w45) if name == "Coffee/Snacks Break" else e)
                    for name, s, e in meal_windows
                ]
                # Target km = hours after departure × avg_speed
                target_coffee_km = (_coffee_hour - start_hour) * avg_speed
                print(f"Optimization Agent: Coffee stop targeted at hour={_coffee_hour:.1f} → ~{target_coffee_km:.0f}km from start")
            except Exception as _ex:
                print(f"Optimization Agent: Could not parse coffee time: {_ex}")

        # Anticipation Logic: Calculate target distance for specific meal times (like Coffee)
        target_coffee_km = None
        _cb_m = re.search(r"(\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?))", str(message.get("preference", "")), re.IGNORECASE)
        _coffee_hour = 16.5 # Default 4:30 PM
        if _cb_m:
            try:
                _ct = dt_parser.parse(_cb_m.group(1))
                _coffee_hour = _ct.hour + _ct.minute / 60.0
            except: pass
        
        # If trip is long enough to reach coffee time
        if _coffee_hour > start_hour:
            target_coffee_km = (_coffee_hour - start_hour) * avg_speed
            # If target is beyond trip, cap it
            if target_coffee_km > total_distance - 30:
                target_coffee_km = total_distance - 40
            
            # Update the window to be centered around this target
            meal_windows = [
                (name, (_coffee_hour - 0.75) if name == "Tea/Coffee/Snacks" else s, 
                 (_coffee_hour + 0.75) if name == "Tea/Coffee/Snacks" else e)
                for name, s, e in meal_windows
            ]

        stops = []
        current_soc = battery_percent
        distance_covered = 0
        safety_margin = 40.0  # km
        suggested_meals = set()
        start_soc = battery_percent

        # score_food defined here so it's accessible in both FANCY_FOOD and HYBRID branches
        from ..utils.preference_utils import parse_food_preference, get_meal_keywords
        pref_dict = parse_food_preference(message.get("preference", ""))

        import math as _math
        MIN_REVIEWS = 30 # Lowered to capture good highway spots with fewer reviews
        
        def score_food(r, current_meal_name):
            rating = float(r.get("rating", 0) or 0)
            reviews = int(r.get("user_ratings_total", 0) or 0)
            if reviews < MIN_REVIEWS or rating < 3.5:
                return -1000.0  # Reject poor or unverified locations
            
            # Bayesian-style score
            base_score = rating * _math.log10(max(reviews, 1))
            
            # Boost for meal-specific keyword match
            meal_keywords = get_meal_keywords(pref_dict, current_meal_name)
            name_lower = r.get("name", "").lower()
            for kw in meal_keywords:
                if kw in name_lower:
                    base_score += 150.0  # Increased boost
                    
            # Specific user request: Heavily prefer "coffee shop" over generic cafes/bistros
            if current_meal_name == "Tea/Coffee/Snacks" and "coffee shop" in name_lower:
                base_score += 100.0

            # Advanced Non-Veg & Fast Food Intelligence
            pref_val_specific = pref_dict.get(current_meal_name.lower())
            pref_val_default = pref_dict.get("default") if current_meal_name.lower() in ("lunch", "dinner") else None
            pref_val = str(pref_val_specific if pref_val_specific else pref_val_default or "").lower()
            is_non_veg_requested = any(x in pref_val for x in ["non veg", "non-veg", "biryani", "biriyani", "chicken", "mutton", "meat"])
            is_fast_food_requested = any(x in pref_val for x in ["fast food", "burger", "pizza", "sandwich", "kfc", "mcdonald"])
            
            # Known fast-food chain names (exact matches for boosting)
            fast_food_chains = [
                "mcdonald", "kfc", "burger king", "subway", "domino", "pizza hut",
                "wendy", "taco bell", "popeye", "five guys", "chick-fil-a",
                "burger", "pizza", "fries", "wrap", "grill",
            ]
            
            # Traditional/generic keywords to avoid when seeking fast food
            non_fast_food_keywords = [
                "bhavan", "saravana", "aryaas", "sangeetha", "pure veg", "vegetarian",
                "pure-veg", "annapoorna", "ananda", "divine", "cafe", "bistro",
                "dhaba", "mess", "tiffin", "garden", "family", "palace",
            ]

            if is_fast_food_requested:
                # Heavy boost for actual fast-food chains
                if any(x in name_lower for x in fast_food_chains):
                    base_score += 500.0
                else:
                    # Penalize everything that is NOT a fast-food chain
                    base_score -= 200.0
                    
                # Additional heavy penalty for traditional/generic restaurants
                if any(x in name_lower for x in non_fast_food_keywords):
                    base_score -= 400.0
                    
            elif is_non_veg_requested:
                if any(x in name_lower for x in ["non-veg", "non veg", "biryani", "biriyani", "meat", "kebab", "darbar", "hotel"]):
                    base_score += 300.0
                if any(x in name_lower for x in ["pure veg", "vegetarian", "pure-veg", "bhavan", "saravana", "aryaas", "sangeetha"]):
                    base_score -= 500.0
                
                # Penalize cafes for dinner if biryani/non-veg is wanted
                if current_meal_name == "Dinner" and any(x in name_lower for x in ["cafe", "bakery", "tea", "coffee"]):
                    base_score -= 600.0
            
            # Boost South Indian explicitly
            is_south_indian_requested = "south indian" in pref_val or "dosa" in pref_val or "idli" in pref_val
            if "south indian" in name_lower or any(x in name_lower for x in ["bhavan", "saravana", "aryaas", "sangeetha"]):
                if is_south_indian_requested:
                    base_score += 400.0  # Strong boost for requested cuisine
                elif is_non_veg_requested or is_fast_food_requested:
                    base_score -= 300.0  # Strong penalty if we want non-veg/fast food
            elif is_south_indian_requested:
                base_score -= 200.0  # Penalize non-matching places if South Indian requested
            
            return base_score

        while distance_covered < total_distance - 5:  # Scan right up to the destination
            # Determine Strategy based on SOC
            if current_soc > 60:
                strategy = "FANCY_FOOD"
            elif current_soc >= 30:
                strategy = "HYBRID"
            else:
                strategy = "EMERGENCY"

            # ── Dynamic Consumption Modeling ──────────────────────────────────
            from ..utils.vehicle_dynamics import VehicleDynamics
            dynamics = VehicleDynamics(range_per_percent)
            
            # Simulate environment factors based on destination/time
            is_hill_climb = any(x in str(total_distance).lower() or x in "ooty coorg munnar" for x in ["ooty", "coorg", "munnar"])
            env_factors = {
                "terrain": "mountain" if is_hill_climb and distance_covered > (total_distance * 0.7) else "highway",
                "temperature": 32, # Simulated hot day
                "ac_usage": "medium",
                "payload_kg": 200, # 2 people + luggage
                "style": "normal"
            }
            multiplier = dynamics.calculate_consumption_multiplier(env_factors)
            
            # Update range based on dynamic factors
            remaining_range = (current_soc * range_per_percent) / multiplier - safety_margin
            max_reachable_at = distance_covered + remaining_range
            
            # ── Progression Logic ─────────────────────────────────────────────
            # Higher granularity (5km) ensures we find meals right at the destination
            step_km = 5.0
            
            # Current state at the START of this step
            travel_time_h = distance_covered / avg_speed
            arrival_time = start_time + timedelta(hours=travel_time_h)
            arrival_hour = arrival_time.hour + arrival_time.minute / 60.0
            arrival_hour_24 = arrival_hour % 24.0

            # 1. MEAL CHECK (Proactive)
            meal_needed = None
            
            # Check if user explicitly defined a structured meal plan
            has_specific_meals = any(k in ["breakfast", "lunch", "dinner", "tea"] for k in pref_dict.keys())

            for name, start, end in meal_windows:
                if name in suggested_meals:
                    continue
                if name in skip_meals_pre_departure:
                    suggested_meals.add(name)
                    continue
                
                meal_key = name.lower()
                if "tea" in meal_key or "coffee" in meal_key or "snacks" in meal_key:
                    meal_key = "tea"
                    
                # If user gave a specific meal plan but omitted this meal, skip scheduling it
                if has_specific_meals and meal_key not in pref_dict:
                    suggested_meals.add(name)
                    continue
                
                # Broaden Coffee/Tea window slightly
                eff_start, eff_end = start, end
                if name == "Coffee/Snacks Break":
                    eff_start, eff_end = 15.0, 19.0 # 3 PM to 7 PM
                
                # Check window
                match = False
                if eff_start > eff_end: # Overnight wrap
                    if arrival_hour_24 >= eff_start or arrival_hour_24 <= eff_end: match = True
                else:
                    if eff_start <= arrival_hour_24 <= eff_end: match = True
                
                # Proactive target check (e.g. coffee at 4:30 PM)
                if name == "Coffee/Snacks Break" and target_coffee_km:
                    if abs(distance_covered - target_coffee_km) < 30.0:
                        match = True
                
                if match:
                    print(f"Optimization Agent: Found match for {name} at {distance_covered:.1f}km ({arrival_time.strftime('%H:%M')})")
                    meal_needed = (name, start, end)
                    break
            
            # Skip zone logic: If battery > 35%, avoid technical stops in first 30km
            # BUT: Always allow meal stops.
            min_stop_dist = 30 if start_soc > 35 else 0
            is_technical_allowed = distance_covered >= min_stop_dist

            if meal_needed:
                name, start, end = meal_needed
                
                # A. USER PREFERENCE (Highest Priority)
                preferred_res = None
                if name == "Breakfast" and start_hour < 8.0:
                    for r in restaurants:
                        if "Murugan Idli" in r["name"] and max(distance_covered, min_stop_dist) < r["route_distance_km"] < distance_covered + 150:
                            preferred_res = r
                            break
                
                if preferred_res:
                    best_res = preferred_res
                    nearby_charger = next((c for c in chargers if abs(c["route_distance_km"] - best_res["route_distance_km"]) < 5), None)
                    
                    food_info = f"{best_res['name']} (User Favorite)"
                    charger_info = ""
                    if nearby_charger:
                        p = nearby_charger.get('power_kw', 'Unknown')
                        op = nearby_charger.get('operator', 'Unknown')
                        charger_info = f" with {p}kW {op} Charging"

                    stops.append({
                        "type": "hub" if nearby_charger else "food",
                        "name": best_res["name"],
                        "location": best_res["location"],
                        "distance_mark": f"{best_res['route_distance_km']:.1f}km",
                        "arrival_time": arrival_time.strftime("%H:%M"),
                        "action": f"{name} at {food_info}{charger_info}",
                        "strategy_used": "USER_PREFERENCE"
                    })
                    jump_dist = best_res["route_distance_km"] - distance_covered
                    if jump_dist > 0:
                        current_soc -= (jump_dist * multiplier) / range_per_percent
                    
                    distance_covered = best_res["route_distance_km"]
                    current_soc = 90 if nearby_charger else current_soc
                    suggested_meals.add(name)
                    continue

                # B. FANCY_FOOD / HYBRID (Normal Flow)
                if strategy == "FANCY_FOOD":
                    # Look further ahead for meals (up to 80km) to ensure we find good spots
                    nearby_food = [r for r in restaurants if max(distance_covered, min_stop_dist) < r["route_distance_km"] < distance_covered + 80]
                    
                    if name == "Breakfast":
                        # Prioritize South Indian for breakfast
                        bfast_food = [r for r in nearby_food if "south indian" in r.get("name", "").lower() or r.get("place_type") == "breakfast"]
                        nearby_food = bfast_food if bfast_food else [r for r in nearby_food if r.get("place_type") in ["main", "cafe"]]
                    elif name == "Coffee/Snacks Break":
                        nearby_food = [r for r in nearby_food if r.get("place_type") in ["cafe", "main", "breakfast"]]
                    else:
                        nearby_food = [r for r in nearby_food if r.get("place_type", "main") == "main"]

                    # FALLBACK: If no food found in primary window, broaden search to 150km and relax all types
                    if not nearby_food:
                        print(f"Optimization Agent: {name} fallback triggered at {distance_covered:.1f}km")
                        nearby_food = [r for r in restaurants if max(distance_covered, min_stop_dist) < r["route_distance_km"] < distance_covered + 150]
                    
                    if not nearby_food:
                        print(f"Optimization Agent: No {name} spots found even in fallback within 150km of {distance_covered:.1f}km")
                    
                    if name == "Breakfast":
                        # For breakfast, prioritize getting food EARLIER rather than just the highest rating
                        # if there are multiple good options.
                        nearby_food.sort(key=lambda x: (x["route_distance_km"], -score_food(x, name)))
                    else:
                        nearby_food.sort(key=lambda x: score_food(x, name), reverse=True)
                    if nearby_food:
                        best_res = nearby_food[0]
                        # Find closest charger
                        nearby_charger = None
                        min_c_dist = 999
                        for c in chargers:
                            dist = abs(c["route_distance_km"] - best_res["route_distance_km"])
                            if dist < 3.0 and dist < min_c_dist:
                                nearby_charger = c
                                min_c_dist = dist
                        
                        food_info = f"{best_res['name']} ({best_res.get('rating', 'N/A')}★, {'$'*best_res.get('price_level', 2)})"
                        charger_info = ""
                        if nearby_charger:
                            p = nearby_charger.get('power_kw', 'Unknown')
                            op = nearby_charger.get('operator', 'Unknown')
                            addr = nearby_charger.get('address', 'nearby')
                            dist_to_c = abs(nearby_charger["route_distance_km"] - best_res["route_distance_km"])
                            
                            if dist_to_c < 0.3:
                                charger_info = f" with {p}kW {op} Charging on-site"
                            else:
                                charger_info = f" with {p}kW {op} Charging nearby ({dist_to_c:.1f}km away)"

                        # Merge if at same distance as last stop
                        if stops and stops[-1]["distance_mark"] == f"{best_res['route_distance_km']:.1f}km":
                            last = stops[-1]
                            last["name"] = f"{last['name']} & {best_res['name']}"
                            last["action"] = f"{last['action']} | {name} at {food_info}{charger_info}"
                        else:
                            stops.append({
                                "type": "hub" if nearby_charger else "food",
                                "name": best_res["name"],
                                "location": best_res["location"],
                                "distance_mark": f"{best_res['route_distance_km']:.1f}km",
                                "arrival_time": arrival_time.strftime("%H:%M"),
                                "action": f"{name} at {food_info}{charger_info}",
                                "strategy_used": strategy
                            })
                        jump_dist = best_res["route_distance_km"] - distance_covered
                        if jump_dist > 0:
                            current_soc -= (jump_dist * multiplier) / range_per_percent
                            
                        distance_covered = best_res["route_distance_km"]
                        current_soc = 90 if nearby_charger else current_soc
                        suggested_meals.add(name)
                        continue

                elif strategy == "HYBRID":
                    effective_strategy = strategy
                    best_hybrid = None
                    
                    if effective_strategy == "HYBRID":
                        nearby_chargers = [c for c in chargers if max(distance_covered, min_stop_dist) < c["route_distance_km"] < distance_covered + 100 and c.get("route_distance_km") <= max_reachable_at]
                        
                        def get_power(c):
                            p = c.get("power_kw", 0)
                            try: return float(p)
                            except (ValueError, TypeError): return 0.0

                        best_hybrid_score = -999999
                        
                        for c in sorted(nearby_chargers, key=get_power, reverse=True):
                            nearby_food = [r for r in restaurants if abs(r["location"]["lat"] - c["location"]["lat"]) < 0.012]
                            if name == "Breakfast":
                                bfast_food = [r for r in nearby_food if "south indian" in r.get("name", "").lower() or r.get("place_type") == "breakfast"]
                                nearby_food = bfast_food if bfast_food else [r for r in nearby_food if r.get("place_type") in ["main", "cafe"]]
                            elif name == "Tea/Coffee/Snacks":
                                nearby_food = [r for r in nearby_food if r.get("place_type") in ["cafe", "main", "breakfast"]]
                            else:
                                nearby_food = [r for r in nearby_food if r.get("place_type", "main") == "main"]
                                
                            if nearby_food:
                                best_food = max(nearby_food, key=lambda x: score_food(x, name))
                                score = score_food(best_food, name)
                                if score > best_hybrid_score:
                                    best_hybrid_score = score
                                    best_hybrid = (c, best_food)
                        
                        if best_hybrid:
                            c, f = best_hybrid
                            p = c.get('power_kw', 'Unknown')
                            op = c.get('operator', 'Unknown')
                            addr = c.get('address', 'nearby')
                            
                            stops.append({
                                "type": "hub",
                                "name": f"{f['name']} (with {c['name']} Charger)",
                                "location": c["location"],
                                "distance_mark": f"{c['route_distance_km']:.1f}km",
                                "arrival_time": arrival_time.strftime("%H:%M"),
                                "action": f"{name} & {p}kW {op} Fast Charge at {f['name']} ({addr})",
                                "strategy_used": strategy
                            })
                            jump_dist = c["route_distance_km"] - distance_covered
                            if jump_dist > 0:
                                current_soc -= (jump_dist * multiplier) / range_per_percent
                                
                            distance_covered = c["route_distance_km"]
                            current_soc = 90
                            suggested_meals.add(name)
                            continue
                        else:
                            # Fallback to FOOD_FIRST if no hybrid stop exists
                            effective_strategy = "FOOD_FIRST"
                            
                    if effective_strategy == "FOOD_FIRST":
                        nearby_food = [r for r in restaurants if abs(r["route_distance_km"] - distance_covered) < 2.0]
                        
                        # FALLBACK: If no food found within 2km, look ahead up to 150km
                        if not nearby_food:
                            nearby_food = [r for r in restaurants if max(distance_covered, min_stop_dist) < r["route_distance_km"] < distance_covered + 150]

                        if name == "Breakfast":
                            bfast_food = [r for r in nearby_food if "south indian" in r.get("name", "").lower() or r.get("place_type") == "breakfast"]
                            nearby_food = bfast_food if bfast_food else [r for r in nearby_food if r.get("place_type") in ["main", "cafe"]]
                        elif name == "Tea/Coffee/Snacks":
                            nearby_food = [r for r in nearby_food if r.get("place_type") in ["cafe", "main", "breakfast"]]
                        else:
                            nearby_food = [r for r in nearby_food if r.get("place_type", "main") == "main"]
                        
                        if name == "Breakfast":
                            nearby_food.sort(key=lambda x: (x["route_distance_km"], -score_food(x, name)))
                        else:
                            nearby_food.sort(key=lambda x: score_food(x, name), reverse=True)
                            
                        if nearby_food:
                            best_res = nearby_food[0]
                            # Find closest charger
                            nearby_charger = None
                            min_c_dist = 999
                            for c in chargers:
                                dist = abs(c["route_distance_km"] - best_res["route_distance_km"])
                                if dist < 3.0 and dist < min_c_dist:
                                    nearby_charger = c
                                    min_c_dist = dist
                            
                            food_info = f"{best_res['name']} ({best_res.get('rating', 'N/A')}★, {'$'*best_res.get('price_level', 2)})"
                            charger_info = ""
                            if nearby_charger:
                                p = nearby_charger.get('power_kw', 'Unknown')
                                op = nearby_charger.get('operator', 'Unknown')
                                addr = nearby_charger.get('address', 'nearby')
                                dist_to_c = abs(nearby_charger["route_distance_km"] - best_res["route_distance_km"])
                                
                                if dist_to_c < 0.3:
                                    charger_info = f" with {p}kW {op} Charging on-site"
                                else:
                                    charger_info = f" with {p}kW {op} Charging nearby ({dist_to_c:.1f}km away)"

                            # Merge if at same distance as last stop
                            if stops and stops[-1]["distance_mark"] == f"{best_res['route_distance_km']:.1f}km":
                                last = stops[-1]
                                last["name"] = f"{last['name']} & {best_res['name']}"
                                last["action"] = f"{last['action']} | {name} at {food_info}{charger_info}"
                            else:
                                stops.append({
                                    "type": "hub" if nearby_charger else "food",
                                    "name": best_res["name"],
                                    "location": best_res["location"],
                                    "distance_mark": f"{best_res['route_distance_km']:.1f}km",
                                    "arrival_time": arrival_time.strftime("%H:%M"),
                                    "action": f"{name} at {food_info}{charger_info}",
                                    "strategy_used": effective_strategy
                                })
                            jump_dist = best_res["route_distance_km"] - distance_covered
                            if jump_dist > 0:
                                current_soc -= (jump_dist * multiplier) / range_per_percent
                                
                            distance_covered = best_res["route_distance_km"]
                            current_soc = 90 if nearby_charger else current_soc
                            suggested_meals.add(name)
                            continue

            # 2. EMERGENCY / TECHNICAL CHARGING CHECK
            # We only stop for technical reasons if it's allowed (min_stop_dist) 
            # OR if it's actually an emergency (SOC < 30)
            can_stop_for_charge = is_technical_allowed or current_soc < 30
            
            if can_stop_for_charge and (current_soc < 30 or max_reachable_at < total_distance):
                # Ensure we only pick chargers FORWARD of current position and respecting min_stop_dist
                reachable = [c for c in chargers if max(distance_covered, min_stop_dist) < c["route_distance_km"] <= max_reachable_at]
                if reachable:
                    def get_power(c):
                        p = c.get("power_kw", 0)
                        try:
                            return float(p)
                        except (ValueError, TypeError):
                            return 0.0

                    # If HYBRID, try to find a hub even if SOC is lower before settling for a technical stop
                    if strategy == "HYBRID":
                        next_hub = None
                        for c in sorted(reachable, key=lambda x: x["route_distance_km"], reverse=True):
                            nearby_food = [r for r in restaurants if abs(r["location"]["lat"] - c["location"]["lat"]) < 0.012]
                            if nearby_food:
                                # For technical stops, use 'Tea/Coffee/Snacks' as a neutral context for scoring
                                next_hub = (c, max(nearby_food, key=lambda x: score_food(x, "Tea/Coffee/Snacks")))
                                break
                        if next_hub:
                            c, f = next_hub
                            stops.append({
                                "type": "hub",
                                "name": c["name"],
                                "food_name": f["name"],
                                "location": c["location"],
                                "distance_mark": f"{c['route_distance_km']:.1f}km",
                                "arrival_time": arrival_time.strftime("%H:%M"),
                                "action": f"Hub Charge: {f['name']} at {c['name']}",
                                "strategy_used": strategy
                            })
                            jump_dist = c["route_distance_km"] - distance_covered
                            if jump_dist > 0:
                                current_soc -= (jump_dist * multiplier) / range_per_percent
                                
                            distance_covered = c["route_distance_km"]
                            current_soc = 90
                            continue

                    # Fallback to technical stop
                    best_c = min(reachable, key=lambda x: x["route_distance_km"]) if strategy == "EMERGENCY" else max(reachable, key=get_power)
                    stops.append({
                        "type": "charging",
                        "name": best_c["name"],
                        "location": best_c["location"],
                        "distance_mark": f"{best_c['route_distance_km']:.1f}km",
                        "arrival_time": arrival_time.strftime("%H:%M"),
                        "action": f"Charge: {best_c['name']} at {best_c['route_distance_km']:.1f}km",
                        "strategy_used": strategy
                    })
                    print(f"Optimization Agent: Added stop at {best_c['route_distance_km']:.1f}km (Strategy: {strategy})")
                    jump_dist = best_c["route_distance_km"] - distance_covered
                    if jump_dist > 0:
                        current_soc -= (jump_dist * multiplier) / range_per_percent
                        
                    distance_covered = best_c["route_distance_km"]
                    current_soc = 90
                    continue
                else:
                    print(f"Optimization Agent: WARNING - No reachable chargers found in window [{max(distance_covered, min_stop_dist):.1f}, {max_reachable_at:.1f}]")

            # Progression
            distance_covered += step_km
            current_soc -= (step_km * multiplier) / range_per_percent

        # ── Destination Meal Check (Handle meals at the end of short trips) ──
        for name, start, end in meal_windows:
            if name in suggested_meals or name in skip_meals_pre_departure:
                continue
            
            # Check if arrival at destination matches this meal window
            travel_time_h = total_distance / avg_speed
            arrival_time = start_time + timedelta(hours=travel_time_h)
            arrival_hour = arrival_time.hour + arrival_time.minute / 60.0
            
            # If we arrive within 1 hour of a meal window, suggest it at destination
            if (start - 0.5) <= arrival_hour <= (end + 1.0) and route_points:
                # Search for food near the destination coordinate (last point in polyline)
                dest_loc = route_points[-1]
                nearby_food = [
                    r for r in restaurants 
                    if abs(r["location"]["lat"] - dest_loc["lat"]) < 0.04 
                    and abs(r["location"]["lng"] - dest_loc["lng"]) < 0.04
                ] # Wider 4km radius for destination
                
                if nearby_food:
                    best_res = max(nearby_food, key=lambda x: score_food(x, name))
                    food_info = f"{best_res['name']} ({best_res.get('rating', '?.?')}\u2605)"
                    
                    stops.append({
                        "type": "food",
                        "name": f"Destination {name}: {best_res['name']}",
                        "location": best_res["location"],
                        "distance_mark": f"{total_distance:.1f}km",
                        "arrival_time": arrival_time.strftime("%H:%M"),
                        "action": f"Enjoy your {name} in your destination city at {food_info}",
                        "strategy_used": "DESTINATION_GOAL"
                    })
                    suggested_meals.add(name)
                    print(f"Optimization Agent: Added destination meal {name} at {best_res['name']}")

        response = {
            "agent": "optimization_agent",
            "event": "plan_optimized",
            "stops": stops,
            "needs_charging": len(stops) > 0,
        }
        await bus.publish("optimization_data", response)
