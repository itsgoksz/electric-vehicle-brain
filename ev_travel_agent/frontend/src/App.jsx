import { useState, useEffect, useRef } from 'react'
import { APIProvider, Map, useMap, useMapsLibrary, Marker, InfoWindow } from '@vis.gl/react-google-maps'
import SpeechRecognition, { useSpeechRecognition } from 'react-speech-recognition'
import './App.css'

// Helper component to draw the route polyline
const RoutePolyline = ({ encodedPolyline }) => {
  const map = useMap();
  const maps = useMapsLibrary('maps');
  const [polyline, setPolyline] = useState(null);

  useEffect(() => {
    if (!map || !maps || !encodedPolyline) return;

    // Remove existing polyline if any
    if (polyline) polyline.setMap(null);

    const path = google.maps.geometry.encoding.decodePath(encodedPolyline);
    const newPolyline = new google.maps.Polyline({
      path: path,
      geodesic: true,
      strokeColor: '#00d2ff',
      strokeOpacity: 0.8,
      strokeWeight: 4,
    });

    newPolyline.setMap(map);
    setPolyline(newPolyline);

    // Fit bounds to polyline
    const bounds = new google.maps.LatLngBounds();
    path.forEach(point => bounds.extend(point));
    map.fitBounds(bounds);

    return () => newPolyline.setMap(null);
  }, [map, maps, encodedPolyline]);

  return null;
}

function App() {
  const [formData, setFormData] = useState({
    origin: null,
    destination: null,
    battery_level: null,
    preference: null,
    departure_time: null
  })
  const [loading, setLoading] = useState(false)
  const [plan, setPlan] = useState(null)
  const [selectedStop, setSelectedStop] = useState(null)
  
  // Chat state
  const [messages, setMessages] = useState([
    { role: 'assistant', content: "Establishing secure connection to vehicle onboard GPS..." }
  ])
  const [chatInput, setChatInput] = useState('')
  const [chatLoading, setChatLoading] = useState(false)
  const [fallbackMode, setFallbackMode] = useState(false)
  const [connectionRequest, setConnectionRequest] = useState(null)
  const messagesEndRef = useRef(null)
  const { transcript, listening, resetTranscript, browserSupportsSpeechRecognition } = useSpeechRecognition()

  const [sessionId] = useState(() => Date.now().toString(36) + Math.random().toString(36).substr(2))

  // --- TTS Setup ---
  const ttsEnabledRef = useRef(true);
  const [ttsEnabled, setTtsEnabled] = useState(true);
  
  useEffect(() => {
    if ('speechSynthesis' in window) {
      // Pre-load voices
      window.speechSynthesis.getVoices();
      window.speechSynthesis.onvoiceschanged = () => window.speechSynthesis.getVoices();
    }
  }, []);

  // Create a persistent audio object outside the component to bypass autoplay gesture loss
  if (!window.ttsAudio) {
    window.ttsAudio = new Audio();
  }

  const speak = async (text) => {
    if (!ttsEnabledRef.current) return;
    // Clean up emojis and markdown formatting
    const cleanText = text.replace(/\*\*/g, '').replace(/🏎|🍽|🗺️|⚡|🚗|✅|❌/g, '');
    
    // Stop any ongoing audio playback immediately
    window.ttsAudio.pause();
    window.ttsAudio.currentTime = 0;
    
    window.speakCounter = (window.speakCounter || 0) + 1;
    const currentSpeakId = window.speakCounter;
    
    const API_BASE_URL = `http://${window.location.hostname}:8000`;
    const audioUrl = `${API_BASE_URL}/tts?text=${encodeURIComponent(cleanText)}`;
    
    try {
      const response = await fetch(audioUrl);
      if (!response.ok) throw new Error("Network response was not ok");
      const blob = await response.blob();
      const blobUrl = URL.createObjectURL(blob);
      
      if (currentSpeakId !== window.speakCounter) return;
      
      window.ttsAudio.src = blobUrl;
      window.ttsAudio.play().catch(e => console.error("TTS Auto-play prevented:", e));
    } catch (error) {
      console.error("Error playing TTS:", error);
    }
  };

  const toggleTts = () => {
    const newVal = !ttsEnabled;
    setTtsEnabled(newVal);
    ttsEnabledRef.current = newVal;
    if (!newVal && window.currentAudio) {
      window.currentAudio.pause();
    }
  };
  // ----------------

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }

  useEffect(() => {
    scrollToBottom()
  }, [messages])

  useEffect(() => {
    if (transcript) {
      setChatInput(transcript)
    }
  }, [transcript])

  // Replace with actual key from .env
  const GOOGLE_MAPS_API_KEY = import.meta.env.VITE_GOOGLE_MAPS_API_KEY

  useEffect(() => {
    if (navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(
        async (position) => {
          try {
            const { latitude, longitude } = position.coords;
            const geocodeUrl = `https://maps.googleapis.com/maps/api/geocode/json?latlng=${latitude},${longitude}&key=${GOOGLE_MAPS_API_KEY}`;
            const response = await fetch(geocodeUrl);
            const data = await response.json();
            
            if (data.results && data.results.length > 0) {
              let readableLocation = null;
              const sublocality = data.results[0].address_components.find(c => c.types.includes('sublocality'));
              const locality = data.results[0].address_components.find(c => c.types.includes('locality'));
              
              if (sublocality) readableLocation = sublocality.long_name;
              else if (locality) readableLocation = locality.long_name;
              else readableLocation = "your current location";

              setFormData(prev => ({ ...prev, origin: readableLocation }));
              setMessages([
                { role: 'assistant', content: `GPS Link established. I see we're currently parked in ${readableLocation}, Gokul. Where are we heading today?` }
              ]);
              // Speak initial message if browser allows without interaction (or fails silently)
              speak(`GPS Link established. I see we're currently parked in ${readableLocation}, Gokul. Where are we heading today?`);
            }
          } catch (err) {
            console.error("Geocoding failed", err);
            setMessages([
              { role: 'assistant', content: "Hi Gokul! Where are we headed today? (Could not ping GPS)" }
            ]);
          }
        },
        (error) => {
          console.error("Geolocation error:", error);
          setMessages([
            { role: 'assistant', content: "Hi Gokul! Where are we headed today? (Location access denied)" }
          ]);
        }
      );
    } else {
      setMessages([
        { role: 'assistant', content: "Hi Gokul! Where are we headed today?" }
      ]);
    }

    // Initialize Server-Sent Events for Vehicle Brain live telemetry alerts
    const API_BASE_URL = `http://${window.location.hostname}:8000`;
    const sse = new EventSource(`${API_BASE_URL}/stream?session_id=${sessionId}`);
    sse.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        console.log("SSE Received:", data);

        // 🤝 Handle Connection Requests
        if (data && data.type === 'connection_request') {
          console.log("Connection Request Received:", data);
          setConnectionRequest(data);
          return; // Skip other handlers
        }

        // 🏎 Handle Proactive Alerts
        if (data && data.message) {
          console.log("VehicleBrain Alert Received:", data.message);
          setMessages(prev => [...prev, { role: 'assistant', content: "🏎 **AUTONOMOUS ALERT:** " + data.message }]);
          speak("Autonomous Alert: " + data.message);
        }

        // 🗺 Handle Live Plan Updates
        if (data && data.stops && data.overview_polyline) {
          console.log("Live Plan Update Received");
          setPlan(data);
          // Only add to message history if not already present
          setMessages(prev => {
            const hasPlan = prev.some(m => m.type === 'plan' && m.content.summary === data.summary);
            if (hasPlan) return prev;
            speak("Your trip timeline has been successfully generated and optimized.");
            return [...prev, { role: 'assistant', type: 'plan', content: data }];
          });
          setLoading(false);
        }

        // 🧠 Handle Jarvis Briefing (Curation)
        if (data && data.curation) {
           console.log("Jarvis Briefing Received");
           setMessages(prev => {
             // Look for the last 'plan' message and update its briefing if available
             const lastIdx = prev.length - 1;
             if (lastIdx >= 0 && prev[lastIdx].type === 'plan') {
                const newHistory = [...prev];
                newHistory[lastIdx].content = { ...newHistory[lastIdx].content, jarvis_briefing: data.curation };
                return newHistory;
             }
             return prev;
           });
        }
      } catch (e) {
        console.error("SSE Parsing Error:", e);
      }
    };

    return () => {
      sse.close();
    };
  }, [sessionId]);

  const fetchPlan = async (tripData) => {
    setLoading(true)
    setPlan(null)
    setSelectedStop(null)

    try {
      const API_BASE_URL = `http://${window.location.hostname}:8000`;
      const response = await fetch(`${API_BASE_URL}/plan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...tripData, session_id: sessionId })
      })
      const data = await response.json()
      if (data.error) {
        alert(data.error)
        setPlan(null)
        setMessages(prev => [...prev, { role: 'assistant', content: `Error from planner: ${data.error}` }])
      } else {
        console.log("Plan received:", data)
        setPlan(data)
        setMessages(prev => [...prev, { role: 'assistant', type: 'plan', content: data }])
      }
    } catch (error) {
      console.error("Error fetching plan:", error)
      alert("Failed to connect to backend assistant.")
      setMessages(prev => [...prev, { role: 'assistant', content: 'Trip planning failed. Please try again later.'}])
    } finally {
      setLoading(false)
    }
  }

  const startDriveSimulation = async () => {
    try {
      const API_BASE_URL = `http://${window.location.hostname}:8000`;
      await fetch(`${API_BASE_URL}/simulate_trip`, { method: "POST" });
      setMessages(prev => [...prev, { role: 'assistant', content: 'Drive mode engaged. Telemetry and battery diagnostics are now streaming live to my Vehicle Brain.' }]);
    } catch (err) {
      console.error("Simulation trigger failed:", err);
    }
  };

  // Build Google Maps navigation URL with all stops as waypoints
  const buildGoogleMapsNavUrl = () => {
    if (!plan || !formData.origin || !formData.destination) return null;
    const origin = encodeURIComponent(formData.origin);
    const destination = encodeURIComponent(formData.destination);
    
    // Add all stops as waypoints using lat,lng for precision
    const waypoints = (plan.stops || [])
      .filter(stop => stop.location)
      .map(stop => `${stop.location.lat},${stop.location.lng}`)
      .join('|');
    
    let url = `https://www.google.com/maps/dir/?api=1&origin=${origin}&destination=${destination}&travelmode=driving`;
    if (waypoints) {
      url += `&waypoints=${encodeURIComponent(waypoints)}`;
    }
    return url;
  };

  const handleChat = async (e) => {
    e.preventDefault()
    if (!chatInput.trim()) return

    const userMsg = { role: 'user', content: chatInput }
    const newMessages = [...messages, userMsg]
    setMessages(newMessages)
    setChatInput('')
    resetTranscript()
    setChatLoading(true)

    // Synchronously unlock the audio object during this user gesture with a valid silent clip
    if (window.ttsAudio) {
      window.ttsAudio.src = "data:audio/wav;base64,UklGRigAAABXQVZFZm10IBIAAAABAAEARKwAAIhYAQACABAAAABkYXRhAgAAAAEA";
      window.ttsAudio.play().catch(() => {});
    }

    try {
      const API_BASE_URL = `http://${window.location.hostname}:8000`;
      const response = await fetch(`${API_BASE_URL}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: userMsg.content,
          history: messages,
          context: { ...formData, session_id: sessionId }
        })
      })
      const data = await response.json()
      
      // Update form data from extracted params (Flattened)
      const updatedData = {
        ...formData,
        origin: data.origin || formData.origin,
        destination: data.destination || formData.destination,
        battery_level: data.battery_level !== null ? data.battery_level : formData.battery_level,
        preference: data.preference || formData.preference,
        departure_time: data.departure_time || formData.departure_time,
        last_mission_checksum: data.last_mission_checksum || formData.last_mission_checksum,
      };
      setFormData(updatedData)

      setMessages([...newMessages, { role: 'assistant', content: data.reply }])
      speak(data.reply);

      // Plan arrives via SSE plan_complete — no need to call fetchPlan here

    } catch (error) {
      console.error("Chat error:", error)
      setMessages([...newMessages, { role: 'assistant', content: 'Connection to Jarvis lost. Switching to manual entry mode.' }])
      setFallbackMode(true)
    } finally {
      setChatLoading(false)
    }
  }

  const handleConnectionAction = async (status) => {
    if (!connectionRequest) return;
    try {
      const API_BASE_URL = `http://${window.location.hostname}:8000`;
      await fetch(`${API_BASE_URL}/api/agents/evai-callback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
          status: status,
          callback_url: connectionRequest.resolved_callback_url
        })
      });
    } catch (e) {
      console.error("Failed to send connection callback:", e);
    }
    setConnectionRequest(null);
  }

  const handleManualSubmit = async (e) => {
    e.preventDefault()
    if (!formData.origin || !formData.destination) {
      alert("Please provide both Origin and Destination.")
      return
    }
    fetchPlan(formData)
  }

  return (
    <div className="app-container">
      <header>
        <h1 className="glow-text">AI EV TRAVEL ASSISTANT</h1>
        <p className="subtitle">Your Jarvis-style multi-agent road trip co-pilot.</p>
      </header>

      <div className="main-layout">
        <section className="controls-section">
          {fallbackMode && (
            <form className="glass-card compact" onSubmit={handleManualSubmit} style={{ marginBottom: '1.5rem' }}>
              <h3 style={{ marginTop: 0, marginBottom: '1rem', fontSize: '0.8rem', color: 'var(--accent)', letterSpacing: '1px' }}>MANUAL FALLBACK MODE</h3>
              <div className="input-grid">
                <div className="field">
                  <label>Origin</label>
                  <input
                    type="text"
                    value={formData.origin || ''}
                    onChange={(e) => setFormData({ ...formData, origin: e.target.value })}
                    required
                  />
                </div>
                <div className="field">
                  <label>Destination</label>
                  <input
                    type="text"
                    value={formData.destination || ''}
                    onChange={(e) => setFormData({ ...formData, destination: e.target.value })}
                    required
                  />
                </div>
                <div className="field">
                  <label>Battery %</label>
                  <input
                    type="number"
                    value={formData.battery_level || ''}
                    onChange={(e) => setFormData({ ...formData, battery_level: e.target.value ? parseInt(e.target.value) : null })}
                  />
                </div>
                <div className="field">
                  <label>Diet</label>
                  <select
                    value={formData.preference || ''}
                    onChange={(e) => setFormData({ ...formData, preference: e.target.value })}
                  >
                    <option value="">Select...</option>
                    <option value="vegetarian">Veg</option>
                    <option value="vegan">Vegan</option>
                    <option value="non-vegetarian">Non-Veg</option>
                    <option value="everything">Everything</option>
                  </select>
                </div>
                <div className="field">
                  <label>Departure</label>
                  <input
                    type="time"
                    value={formData.departure_time || ''}
                    onChange={(e) => setFormData({ ...formData, departure_time: e.target.value })}
                  />
                </div>
              </div>
              <button type="submit" className="btn-primary full-width" disabled={loading}>
                <div className="btn-content">
                  {loading ? <div className="loader small"></div> : "GENERATE TIMELINE"}
                </div>
              </button>
            </form>
          )}

          <div className="glass-card chat-container animate-slide-up">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
              <h3 style={{ margin: 0, fontSize: '0.8rem', color: 'var(--primary)', letterSpacing: '1px' }}>CHAT WITH JARVIS</h3>
              <button 
                onClick={toggleTts} 
                style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '1.2rem', opacity: ttsEnabled ? 1 : 0.5 }}
                title={ttsEnabled ? "Mute Jarvis" : "Unmute Jarvis"}
              >
                {ttsEnabled ? '🔊' : '🔇'}
              </button>
            </div>
            <div className="chat-messages">
              {messages.map((msg, idx) => {
                if (msg.type === 'plan') {
                  const p = msg.content;
                  return (
                    <div key={idx} className="chat-bubble assistant animate-slide-up" style={{ alignSelf: 'stretch', maxWidth: '100%', padding: '0', background: 'transparent', border: 'none' }}>
                      {p.jarvis_briefing && (
                        <div className="glass-card jarvis-briefing" style={{ margin: '0 0 1rem 0' }}>
                          <div className="briefing-header">
                            <span className="jarvis-ai-badge">JARVIS INTELLIGENCE</span>
                          </div>
                          <p className="briefing-content">{p.jarvis_briefing}</p>
                        </div>
                      )}
                      {p.stops && (
                        <div className="glass-card stops-list" style={{ margin: 0 }}>
                          <h3>TRIP TIMELINE</h3>
                          <div className="timeline">
                            {p.stops.map((stop, sIdx) => (
                              <div key={sIdx} className={`timeline-item ${stop.type}`}>
                                <div className="timeline-marker"></div>
                                <div className="stop-details">
                                  <div className="stop-name-row">
                                    <span className="stop-name">
                                      {stop.location ? (
                                        <a 
                                          href={`https://www.google.com/maps/search/?api=1&query=${stop.location.lat},${stop.location.lng}`}
                                          target="_blank"
                                          rel="noopener noreferrer"
                                          className="map-link"
                                        >
                                          {stop.name}
                                        </a>
                                      ) : stop.name}
                                    </span>
                                    <span className="stop-dist">{stop.distance_mark}</span>
                                  </div>
                                  <div className="stop-action">{stop.action}</div>
                                  {stop.food_name && (
                                    <div className="stop-recommendation">
                                      🍽 {stop.location ? (
                                        <a 
                                          href={`https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(stop.food_name)}@${stop.location.lat},${stop.location.lng}`}
                                          target="_blank"
                                          rel="noopener noreferrer"
                                          className="map-link"
                                        >
                                          {stop.food_name}
                                        </a>
                                      ) : stop.food_name}
                                    </div>
                                  )}
                                </div>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  );
                }

                return (
                  <div key={idx} className={`chat-bubble ${msg.role}`}>
                    {msg.content}
                  </div>
                );
              })}
              {chatLoading && (
                <div className="chat-bubble assistant typing">
                  <div className="dot-pulse">...</div>
                </div>
              )}
              <div ref={messagesEndRef} />
            </div>
            <form className="chat-input-form" onSubmit={handleChat} style={{ display: fallbackMode ? 'none' : 'flex' }}>
              {browserSupportsSpeechRecognition && (
                <button 
                  type="button" 
                  className={`mic-btn ${listening ? 'listening' : ''}`}
                  onClick={() => listening ? SpeechRecognition.stopListening() : SpeechRecognition.startListening({ continuous: true })}
                  style={{ background: 'none', border: 'none', fontSize: '1.2rem', cursor: 'pointer', opacity: listening ? 1 : 0.6, padding: '0 8px' }}
                  title="Toggle voice input"
                >
                  {listening ? '🔴' : '🎤'}
                </button>
              )}
              <input
                type="text"
                placeholder="Type or speak your message..."
                value={chatInput}
                onChange={(e) => {
                  setChatInput(e.target.value)
                  resetTranscript()
                }}
                disabled={chatLoading || loading}
              />
              <button className="chat-send-btn" type="submit" disabled={chatLoading || loading || !chatInput.trim()}>
                ➜
              </button>
            </form>
          </div>
          {plan && (
             <button
               className="btn-primary full-width animate-slide-up"
               style={{ marginTop: '1rem', background: '#eab308' }}
               onClick={startDriveSimulation}
             >
               START AUTONOMOUS DRIVE
             </button>
          )}
        </section>

        <section className="map-section">
          <div className="glass-card map-container">
            <APIProvider apiKey={GOOGLE_MAPS_API_KEY} libraries={['geometry']}>
              <Map
                style={{ width: '100%', height: '100%', borderRadius: '15px' }}
                defaultCenter={{ lat: 12.9716, lng: 77.5946 }}
                defaultZoom={10}
                gestureHandling={'greedy'}
                disableDefaultUI={true}
                mapId="bf51a910020fa16d" // Optional premium map ID
                colorScheme="DARK"
              >
                {plan && plan.overview_polyline && (
                  <>
                    <RoutePolyline encodedPolyline={plan.overview_polyline} />
                    {plan.stops && plan.stops.map((stop, idx) => (
                      stop.location && (
                        <Marker 
                          key={idx}
                          position={{ lat: parseFloat(stop.location.lat), lng: parseFloat(stop.location.lng) }}
                          title={stop.name}
                          onClick={() => setSelectedStop(stop)}
                        />
                      )
                    ))}

                    {selectedStop && (
                      <InfoWindow
                        position={{ lat: parseFloat(selectedStop.location.lat), lng: parseFloat(selectedStop.location.lng) }}
                        onCloseClick={() => setSelectedStop(null)}
                      >
                        <div className="map-info-window">
                          <h4>{selectedStop.name}</h4>
                          <p className="info-action">{selectedStop.action}</p>
                          {selectedStop.arrival_time && <p><strong>Arrival:</strong> {selectedStop.arrival_time}</p>}
                        </div>
                      </InfoWindow>
                    )}
                  </>
                )}
              </Map>
            </APIProvider>
          </div>

          {plan && (
            <div className="stats-bar animate-fade-in">
              <div className="stat"><span>DISTANCE</span><strong>{plan.distance}</strong></div>
              <div className="stat"><span>EST TIME</span><strong>{plan.time}</strong></div>
              <div className="stat"><span>TRAFFIC</span><strong className={plan.traffic?.toLowerCase().replace(' ', '-') || 'unknown'}>{plan.traffic || 'Unknown'}</strong></div>
              <div className="stat"><span>WEATHER</span><strong>{plan.weather}</strong></div>
            </div>
          )}
          {plan && buildGoogleMapsNavUrl() && (
            <a
              href={buildGoogleMapsNavUrl()}
              target="_blank"
              rel="noopener noreferrer"
              className="start-trip-btn animate-slide-up"
            >
              <span className="start-trip-icon">🧭</span>
              <span>START TRIP ON GOOGLE MAPS</span>
              <span className="start-trip-stops">{plan.stops?.length || 0} stops planned</span>
            </a>
          )}
        </section>
      </div>

      {/* Connection Request Modal */}
      {connectionRequest && (
        <div className="modal-overlay">
          <div className="glass-modal animate-pop-in">
            <h3>Connection Request</h3>
            <p>An external agent <strong>"{connectionRequest.agent || 'echo'}"</strong> is trying to connect.</p>
            <div className="modal-actions">
              <button 
                className="btn-primary" 
                onClick={() => handleConnectionAction('approved')}
              >
                Approve
              </button>
              <button 
                className="btn-secondary" 
                onClick={() => handleConnectionAction('ignored')}
              >
                Reject
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  )
}

export default App
