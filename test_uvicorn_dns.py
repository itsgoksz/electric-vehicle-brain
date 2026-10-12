import asyncio
from fastapi import FastAPI
import requests
import uvicorn
import googlemaps
from ev_travel_agent.utils.config import Config

app = FastAPI()

@app.get("/")
async def root():
    def fetch():
        try:
            return requests.get("https://maps.googleapis.com").status_code
        except Exception as e:
            return str(e)
            
    res = await asyncio.to_thread(fetch)
    return {"status": res}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
