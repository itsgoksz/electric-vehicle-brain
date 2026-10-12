# 🚗 Electric Vehicle Brain (EV Travel Co-Pilot)

An intelligent, multi-agent autonomous co-pilot and trip planning system designed for electric vehicles. Powered by LLMs, real-time telemetry, routing, weather, charging optimization, and interactive voice intelligence.

---

## 🌟 Features

- **Multi-Agent Orchestration**: Specialized agents communicate via an asynchronous `AgentBus` for real-time trip coordination.
- **EV Battery & Charging Intelligence**: Predicts range degradation, monitors state of charge (SoC), and schedules optimal charging stops via OpenChargeMap.
- **Curated Food & Lifestyle Stops**: Intelligently identifies dining and rest stops based on user preferences and schedule.
- **Real-Time Traffic & Weather**: Adapts routes on the fly using Google Maps traffic metrics and OpenWeather data.
- **Conversational Jarvis Assistant**: Voice-enabled Jarvis-style interface with real-time speech recognition and text-to-speech feedback.
- **Autonomous Vehicle Brain**: Continuous telemetry streaming, route health diagnostics, and proactive driver notifications.
- **Modern Glassmorphic UI**: High-fidelity dark mode dashboard with interactive Google Maps integration.

---

## 🏛️ System Architecture

```
                       ┌─────────────────────────┐
                       │   Mission Control /     │
                       │   Travel Manager Agent  │
                       └────────────┬────────────┘
                                    │ (Agent Bus)
     ┌──────────────┬───────────────┼───────────────┬──────────────┐
     ▼              ▼               ▼               ▼              ▼
┌──────────┐  ┌──────────┐   ┌─────────────┐  ┌───────────┐  ┌──────────┐
│  Route   │  │ Charging │   │ Food / Stop │  │  Traffic  │  │ Weather  │
│  Agent   │  │  Agent   │   │ Curation    │  │   Agent   │  │  Agent   │
└──────────┘  └──────────┘   └─────────────┘  └───────────┘  └──────────┘
                    │               │
                    ▼               ▼
         ┌──────────────────────────────────┐
         │ Vehicle Brain & Telemetry Agent  │
         └──────────────────────────────────┘
```

---

## 🛠️ Tech Stack

- **Backend**: Python 3.10+, FastAPI, LangChain, LangGraph, Google Gemini, SQLAlchemy, aiosqlite, Edge-TTS, ElevenLabs.
- **Frontend**: React 18, Vite, Google Maps JavaScript API (@vis.gl/react-google-maps), React Speech Recognition.
- **Database**: SQLite (Async).

---

## 🚀 Getting Started

### 1. Clone the Repository
```bash
git clone https://github.com/itsgoksz/electric-vehicle-brain.git
cd electric-vehicle-brain
```

### 2. Environment Configuration
Copy the example environment file and configure your API keys:
```bash
cp ev_travel_agent/.env.example ev_travel_agent/.env
```

Set the following variables in `ev_travel_agent/.env`:
```env
GOOGLE_MAPS_API_KEY=your_google_maps_key
VITE_GOOGLE_MAPS_API_KEY=your_google_maps_key
OPEN_CHARGE_MAP_API_KEY=your_openchargemap_key
OPEN_WEATHER_API_KEY=your_openweathermap_key
GOOGLE_API_KEY=your_gemini_api_key
ELEVENLABS_API_KEY=your_elevenlabs_key   # Optional (falls back to edge-tts)
```

### 3. Backend Setup
```bash
pip install -r requirements.txt
python -m ev_travel_agent.main
```
The FastAPI backend will start at `http://localhost:8000`.

### 4. Frontend Setup
```bash
cd ev_travel_agent/frontend
npm install
npm run dev
```
The React frontend dashboard will run at `http://localhost:5173`.

---

## 📄 License
MIT License.
