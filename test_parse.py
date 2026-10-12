import re
from ev_travel_agent.utils.preference_utils import parse_food_preference

s = "I want fast food for lunch, coffee at around 5pm, and I want South Indian food for dinner"
print(parse_food_preference(s))
