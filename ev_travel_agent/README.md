# EV Travel Assistant - Jarvis for Road Trips

A modular, multi-agent AI system to plan EV road trips with charging stops, food, weather, and traffic insights.

## Architecture

The system uses a **Travel Manager Agent** to coordinate specialized agents via an asynchronous **Agent Bus**:
- **Route Agent**: Google Maps Directions.
- **Charging Agent**: OpenChargeMap POIs.
- **Food Agent**: Google Places API.
- **Weather Agent**: OpenWeather API.
- **Traffic Agent**: Google Maps Traffic data.
- **Optimization Agent**: EV battery logic & stop selection.
- **Memory Agent**: Persistence (SQLite) for trips & preferences.

## Tech Stack
- **Backend**: Python, FastAPI, LangChain/LangGraph, SQLAlchemy.
- **Frontend**: React (Vite), CSS (Glassmorphism).
- **Database**: SQLite (Async).

## Setup Instructions

### 1. Prerequisites
- Python 3.9+ 
- Node.js & npm

### 2. Environment Variables
Create a `.env` file in the root directory:
```env
GOOGLE_MAPS_API_KEY=your_google_maps_key
OPEN_CHARGE_MAP_API_KEY=your_openchargemap_key
OPEN_WEATHER_API_KEY=your_openweathermap_key
GOOGLE_API_KEY=your_google_ai_key
```

### 3. Backend Setup
```bash
pip install -r ev_travel_agent/requirements.txt
python -m ev_travel_agent.main
```
The backend will run at `http://localhost:8000`.

### 4. Frontend Setup
```bash
cd ev_travel_agent/frontend
npm install
npm run dev
```
The frontend will run at `http://localhost:5173`.

## Features
- **Intelligent Routing**: Optimized paths between cities.
- **EV Battery Logic**: Automatically adds charging stops if battery is low.
- **Dietary Preferences**: Finds restaurants based on your needs (e.g., Vegetarian).
- **Real-time Insights**: Integrates traffic and weather conditions along the route.
- **Premium UI**: Modern dark-mode dashboard with subtle animations.
