import requests
import json
import time

print("Sending request to host.docker.internal...")
start = time.time()
try:
    r = requests.post("http://host.docker.internal:8000/api/v1/chat", json={
        "message": "I need to go to the airport immediately but I only have 35 percent charge and I dont want any food stops",
        "history": [],
        "context": {"origin": None}
    }, timeout=30)
    print(f"Status: {r.status_code}")
    print(r.text)
except Exception as e:
    print(f"Error: {e}")
print(f"Time taken: {time.time() - start:.2f}s")
