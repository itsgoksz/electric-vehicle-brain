import re
import ast
import json

def parse_food_preference(raw_pref):
    """
    Parse the preference field and extract meal-specific keywords.
    Handles both JSON/Dict and natural language strings.
    """
    if isinstance(raw_pref, dict):
        return raw_pref
    if not raw_pref or str(raw_pref).lower() in ["none", "null", "skip", ""]:
        return {}
    
    s = str(raw_pref).strip()
    
    # Try JSON/Dict parsing
    if s.startswith("{"):
        try:
            try: return ast.literal_eval(s)
            except: return json.loads(s)
        except Exception:
            pass

    # Natural Language Parsing
    # Look for patterns like "X for breakfast", "Y for lunch", etc.
    res = {"default": s}
    
    # Split by "and", "then", "also", "but", or commas to handle multiple preferences
    segments = re.split(r'\s+(?:and|then|also|but)\s+|,\s*', s, flags=re.IGNORECASE)
    
    meals = ["breakfast", "lunch", "dinner", "tea", "coffee", "snack"]
    
    for seg in segments:
        seg = seg.strip()
        # Find which meal this segment belongs to
        target_meal = None
        for m in meals:
            if m in seg.lower():
                target_meal = m
                break
        
        if target_meal:
            # Extract keywords from this segment
            # Strip the meal name itself and common phrases
            cleaned = re.sub(rf"\b(?:{target_meal}|for|at|during|want|i want|some|a|an|the|give me|i would like)\b", "", seg, flags=re.IGNORECASE)
            cleaned = cleaned.strip().strip(":,.-")
            if cleaned:
                res[target_meal if target_meal not in ["coffee", "snack"] else "tea"] = cleaned

    return res

def get_meal_keywords(pref_dict, meal_name):
    """Get relevant keywords for a specific meal from the preference dict."""
    meal_key = meal_name.lower()
    if "tea" in meal_key or "coffee" in meal_key or "snack" in meal_key:
        meal_key = "tea"
        
    kw = pref_dict.get(meal_key)
    if not kw and meal_key in ("lunch", "dinner"):
        kw = pref_dict.get("default")
        
    res = {w for w in str(kw).lower().split() if len(w) > 2} if kw else set()
    
    # Add default keywords for coffee/tea breaks to ensure we find cafes
    if meal_key == "tea":
        res.update({"coffee", "tea", "cafe", "snack", "bakery"})
        
    return res
