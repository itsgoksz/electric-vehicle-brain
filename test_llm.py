import sys, json, asyncio; sys.path.append('.')
from langchain_ollama import ChatOllama
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

llm = ChatOllama(
    model="gemma3:4b",
    temperature=0,
    format="json",
    num_ctx=8192,
    num_predict=256,
)

system_prompt = """You are Jarvis, the driver's AI co-pilot. Your job is to extract trip details and chat naturally.
Always respond in this JSON format:
{
  "reply": "Your natural language response here",
  "origin": null or "place name",
  "destination": null or "place name",
  "battery_level": null or number,
  "departure_time": null or "time string",
  "preference": null or "EXACT words of their preference",
  "is_ready": true or false
}"""

messages = [
    SystemMessage(content=system_prompt),
    HumanMessage(content="Hi! I'm thinking of travelling from Bangalore to Chennai at 8am."),
    AIMessage(content='{"reply": "Okay, a trip to Chennai from J.P. Nagar tomorrow at 8:00 AM! Just one quick question – what’s the current battery percentage on your EV?", "origin": "Bangalore", "destination": "Chennai", "battery_level": null, "departure_time": "tomorrow at 08:00 AM", "preference": null, "is_ready": false}'),
    HumanMessage(content="Yeah, I think it's about 61%.")
]

res = llm.invoke(messages)
print(res.content)
