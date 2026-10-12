import sys, json; sys.path.append('.')
from ev_travel_agent.utils.preference_utils import get_meal_keywords

def score_food(r, current_meal_name, pref_dict):
    base_score = r.get("rating", 3.0) * 10.0
    name_lower = r.get("name", "").lower()
    
    meal_keywords = get_meal_keywords(pref_dict, current_meal_name)
    for kw in meal_keywords:
        if kw in name_lower:
            base_score += 150.0  # Increased boost

    pref_val_specific = pref_dict.get(current_meal_name.lower())
    pref_val_default = pref_dict.get("default") if current_meal_name.lower() in ("lunch", "dinner") else None
    pref_val = str(pref_val_specific if pref_val_specific else pref_val_default or "").lower()
    is_non_veg_requested = any(x in pref_val for x in ["non veg", "non-veg", "biryani", "biriyani", "chicken", "mutton", "meat"])
    is_fast_food_requested = any(x in pref_val for x in ["fast food", "burger", "pizza", "sandwich", "kfc", "mcdonald"])
    
    fast_food_chains = [
        "mcdonald", "kfc", "burger king", "subway", "domino", "pizza hut",
        "wendy", "taco bell", "popeye", "five guys", "chick-fil-a",
        "burger", "pizza", "fries", "wrap", "grill",
    ]
    non_fast_food_keywords = [
        "bhavan", "saravana", "aryaas", "sangeetha", "pure veg", "vegetarian",
        "pure-veg", "annapoorna", "ananda", "divine", "cafe", "bistro",
        "dhaba", "mess", "tiffin", "garden", "family", "palace",
    ]

    if is_fast_food_requested:
        if any(x in name_lower for x in fast_food_chains):
            base_score += 500.0
        else:
            base_score -= 200.0
        if any(x in name_lower for x in non_fast_food_keywords):
            base_score -= 400.0
    elif is_non_veg_requested:
        if any(x in name_lower for x in ["non-veg", "non veg", "biryani", "biriyani", "meat", "kebab", "darbar", "hotel"]):
            base_score += 300.0
        if any(x in name_lower for x in ["pure veg", "vegetarian", "pure-veg", "bhavan", "saravana", "aryaas", "sangeetha"]):
            base_score -= 500.0
        if current_meal_name == "Dinner" and any(x in name_lower for x in ["cafe", "bakery", "tea", "coffee"]):
            base_score -= 600.0
    
    is_south_indian_requested = "south indian" in pref_val or "dosa" in pref_val or "idli" in pref_val
    if "south indian" in name_lower or any(x in name_lower for x in ["bhavan", "saravana", "aryaas", "sangeetha"]):
        if is_south_indian_requested:
            base_score += 400.0
        elif is_non_veg_requested or is_fast_food_requested:
            base_score -= 300.0
    elif is_south_indian_requested:
        base_score -= 200.0
    
    return base_score

pref_dict = {'default': 'South Indian food for breakfast and biriyani for lunch', 'breakfast': 'South Indian food', 'lunch': 'biriyani'}
print("A2B:", score_food({"name": "Adyar Ananda Bhavan", "rating": 4.5}, "Lunch", pref_dict))
print("Biriyani:", score_food({"name": "Paradise Biryani", "rating": 4.5}, "Lunch", pref_dict))
print("Generic Main:", score_food({"name": "Some Restaurant", "rating": 4.5}, "Lunch", pref_dict))
