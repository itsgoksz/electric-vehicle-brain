import asyncio
from typing import Optional, Literal
from pydantic import BaseModel, Field
from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate
from ..utils.agent_bus import bus


class BrainAction(BaseModel):
    alert_message: str = Field(
        description="The conversational voice alert to speak to the driver. Must be natural and mention history if relevant."
    )
    action_type: Optional[Literal["none", "request_reroute", "find_charger"]] = Field(
        description="Any backend action you need to trigger to solve the problem."
    )


class VehicleBrainAgent:
    def __init__(self):
        self.current_plan = None
        self.conversation_history = []
        self.state = {
            "battery": 100,
            "efficiency": "Normal",
            "traffic": "Clear",
            "distance_covered": 0,
        }

        # Initialize LLM for proactive conversational dialogue
        self.llm = ChatOllama(
            model="gemma3:4b",
            temperature=0.2,
        )

        bus.subscribe("plan_complete", self.on_plan)
        bus.subscribe("live_telemetry_update", self.on_telemetry)
        bus.subscribe("battery_update", self.on_battery)

    async def on_plan(self, plan):
        self.current_plan = plan
        print("Brain: Plan received. Entering monitoring loop...")
        asyncio.create_task(self.monitoring_loop())

    async def monitoring_loop(self):
        while True:
            await self.evaluate()
            await asyncio.sleep(5)

    async def on_telemetry(self, data):
        self.state["efficiency"] = data.get("efficiency", "Normal")
        self.state["traffic"] = data.get("traffic", "Clear")
        self.state["distance_covered"] = data.get("distance_covered", 0)
        await self.evaluate()

    async def on_battery(self, data):
        self.state["battery"] = data.get("soc", 100)
        await self.evaluate()

    async def evaluate(self):
        if not self.current_plan or not self.llm:
            return

        battery = self.state.get("battery", 100)
        efficiency = self.state.get("efficiency", "Normal")
        distance_covered = self.state.get("distance_covered", 0)
        trigger_alert = False
        raw_reasoning = ""

        # ── Dynamic Consumption Modeling ──────────────────────────────────────
        from ..utils.vehicle_dynamics import VehicleDynamics
        dynamics = VehicleDynamics(base_efficiency_km_per_percent=3.5)
        
        # Simulate environment factors from state
        env_factors = {
            "traffic": self.state.get("traffic", "Clear"),
            "terrain": "mountain" if "ooty" in str(self.current_plan).lower() else "highway",
            "style": "aggressive" if efficiency == "Poor" else "normal"
        }
        multiplier = dynamics.calculate_consumption_multiplier(env_factors)
        
        # Predictive Intelligence
        consumption_rate = 0
        if distance_covered > 0 and battery < 100:
            # Factor in the dynamic multiplier for future prediction
            consumption_rate = ((100 - battery) / distance_covered) * multiplier
        else:
            consumption_rate = (1.0 / 3.5) * multiplier # Default baseline

        remaining_dist = 40 # Assume looking at next 40km segment
        if isinstance(self.current_plan, dict) and "total_distance" in self.current_plan:
             remaining_dist = float(self.current_plan["total_distance"]) - distance_covered

        projected_final_soc = battery - (remaining_dist * consumption_rate)
        
        # Event Matrix - True autonomous evaluation
        if projected_final_soc < 10:
            trigger_alert = True
            raw_reasoning = f"Predictive Warning: At current consumption rate (multiplied by {multiplier} for conditions), you may reach your destination with only {max(0, int(projected_final_soc))}% battery remaining. Proactive charging is highly recommended."
        elif consumption_rate > 0.4: # Extremely high consumption
            trigger_alert = True
            raw_reasoning = f"Dynamic Alert: Mountain roads and driving style have increased battery consumption by {int((multiplier-1)*100)}%. We need to adjust our next charging stop."
        elif efficiency == "Poor":
            trigger_alert = True
            raw_reasoning = "Energy consumption is heavily dropping due to headwinds or aggressive driving style. Recommend slowing down to preserve range."

        if trigger_alert:
            # Prevent spamming the same alert
            if self.state.get("last_alert_reason") == raw_reasoning:
                return
            self.state["last_alert_reason"] = raw_reasoning

            goal_summary = (
                self.current_plan.get("summary", "Unknown Destination")
                if isinstance(self.current_plan, dict)
                else "Unknown Destination"
            )
            history_text = (
                "\\n".join(self.conversation_history)
                if self.conversation_history
                else "No previous alerts."
            )

            prompt = ChatPromptTemplate.from_template("""
            You are Jarvis, a highly intelligent and proactive AI vehicle co-pilot.
            You are currently monitoring the car's telemetry during a live trip.
            
            TRIP GOAL: {goal_summary}
            
            RECENT CONVERSATION HISTORY:
            {history}
            
            A critical event has triggered your attention: {raw_reasoning}
            
            Current state: 
            Battery: {battery}%
            Traffic: {traffic}
            
            Write a 1-to-2 sentence proactive voice alert addressed to "Gokul". 
            If you recently warned him about something similar, refer back to it naturally (e.g., "As I mentioned earlier...").
            If the situation requires finding a new charger or rerouting, specify the `action_type`. Otherwise, set it to "none".
            """)

            try:
                print(
                    "VehicleBrainAgent: Anomaly detected! Querying LLM for conversational framing and action execution..."
                )
                structured_llm = self.llm.with_structured_output(BrainAction)
                chain = prompt | structured_llm
                response = await chain.ainvoke(
                    {
                        "raw_reasoning": raw_reasoning,
                        "battery": battery,
                        "traffic": self.state.get("traffic", "Clear"),
                        "goal_summary": goal_summary,
                        "history": history_text,
                    }
                )

                alert_msg = response.alert_message

                # Update short term memory
                self.conversation_history.append(f"Jarvis: {alert_msg}")
                if len(self.conversation_history) > 3:
                    self.conversation_history.pop(0)

                await bus.publish("alert_driver", {"message": alert_msg})

                # Action Execution
                if response.action_type == "find_charger":
                    print(
                        "VehicleBrainAgent ACTION: Initiating emergency charger search."
                    )
                    await bus.publish(
                        "alert_driver",
                        {
                            "message": "⚡ *Initiating emergency charger search within range...*"
                        },
                    )
                    # (In a full app, we would publish find_chargers with our current dynamic location)
                elif response.action_type == "request_reroute":
                    print("VehicleBrainAgent ACTION: Initiating route re-optimization.")
                    await bus.publish(
                        "alert_driver",
                        {
                            "message": "🗺 *Calculating new optimal route to avoid traffic...*"
                        },
                    )

            except Exception as e:
                print(f"Vehicle Brain LLM Error: {e}")
                # Fallback purely mechanical alert
                await bus.publish(
                    "alert_driver", {"message": f"SYSTEM ALERT: {raw_reasoning}"}
                )
