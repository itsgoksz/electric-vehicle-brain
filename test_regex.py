import re

text = "3 p.m. today"
match = re.search(r"\d{1,2}(?::\d{2})?\s*(am|pm)|\d{1,2}:\d{2}", text, re.IGNORECASE)
print(f"Match 1: {match}")

match2 = re.search(r"\b(\d{1,2})\s*:\s*(\d{2})\s*(am|pm)\b|\b(\d{1,2})\s*(am|pm)\b|\b(\d{2}):(\d{2})\b", text, re.IGNORECASE)
print(f"Match 2: {match2}")
