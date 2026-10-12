from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate
from ..utils.agent_bus import bus


class CurationAgent:
    def __init__(self):
        self.llm = ChatOllama(model="gemma3:4b", temperature=0.5)
        bus.subscribe("curate_plan", self.handle_curate_plan)

    async def handle_curate_plan(self, message):
        print("Curation Agent: Generating Jarvis intelligence briefing...")
        plan_data = message.get("plan", {})

        # LLM check removed since local Ollama requires no API key

        prompt = ChatPromptTemplate.from_template("""
        You are Jarvis, a sophisticated AI travel co-pilot.
        Generate a brief, elegant "Intelligence Briefing" for the driver based on the plan below.
        
        Route: {summary}
        Total Distance: {distance}
        Traffic: {traffic}
        Weather: {weather}
        Dynamic Insight: {consumption_insight}
        
        STOPS:
        {stops}
        
        INSTRUCTIONS:
        1) Explain why each stop was chosen in a natural, conversational way.
        2) Address the driver as "Gokul".
        3) IMPORTANT: Do NOT guess or hallucinate regions, districts, or city names unless they appear in STOPS above.
        4) NEVER mention internal terms like "FANCY_FOOD", "HYBRID", "EMERGENCY", "strategy", or "Normal". These are system internals.
        5) Keep it to 3-4 sentences max. Sound like a sophisticated personal assistant, not a robot.
        6) Use the correct time of day greeting (morning/afternoon/evening) based on the departure time context.
        """)

        # Calculate dynamic insight for the prompt
        from ..utils.vehicle_dynamics import VehicleDynamics
        dynamics = VehicleDynamics()
        multiplier = 1.0
        if "ooty" in str(plan_data.get("summary")).lower() or "mountain" in str(plan_data.get("weather")).lower():
            multiplier = 1.18
        
        insight = dynamics.get_proactive_insight(multiplier)
        if multiplier > 1.1:
            insight += f" Mountain roads may increase battery consumption by {int((multiplier-1)*100)}%."

        try:
            formatted_stops = "\n".join(
                [
                    f"- {s['name']} ({s['type']}): {s.get('action', '')}"
                    for s in plan_data.get("stops", [])
                ]
            )

            chain = prompt | self.llm
            response = await chain.ainvoke(
                {
                    "summary": plan_data.get("summary"),
                    "distance": plan_data.get("distance"),
                    "traffic": plan_data.get("traffic"),
                    "weather": plan_data.get("weather"),
                    "consumption_insight": insight,
                    "stops": formatted_stops,
                }
            )

            curation_text = response.content
            if isinstance(curation_text, list):
                curation_text = " ".join(
                    [
                        b.get("text", "")
                        for b in curation_text
                        if isinstance(b, dict) and "text" in b
                    ]
                )

            print("Curation Agent: Briefing generated successfully.")

            await bus.publish("curation_data", {"curation": curation_text})
        except Exception as e:
            print(f"Curation Agent Error: {e}")
            await bus.publish(
                "curation_data",
                {
                    "curation": "Jarvis is currently processing high-level data. Have a safe trip!"
                },
            )
