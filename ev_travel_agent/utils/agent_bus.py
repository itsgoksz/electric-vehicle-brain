import asyncio
import json
import logging
import contextvars
from typing import Callable, Dict, List, Any

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("AgentBus")

# Context variable for session tracking across async tasks
session_var = contextvars.ContextVar('session_id', default=None)


class AgentBus:
    def __init__(self):
        self.listeners: Dict[str, List[Callable]] = {}

    def subscribe(self, event_type: str, callback: Callable):
        if event_type not in self.listeners:
            self.listeners[event_type] = []
        self.listeners[event_type].append(callback)
        logger.info(f"Subscribed to {event_type}")

    def clear_all_listeners(self):
        self.listeners = {}
        logger.info("All AgentBus listeners cleared.")

    def unsubscribe(self, event_type: str, callback: Callable):
        if event_type in self.listeners and callback in self.listeners[event_type]:
            self.listeners[event_type].remove(callback)
            logger.info(f"Unsubscribed from {event_type}")

    async def publish(self, event_type: str, message: Dict[str, Any]):
        session_id = session_var.get()
        if session_id and isinstance(message, dict) and "session_id" not in message:
            message["session_id"] = session_id
            
        msg_str = json.dumps(message, indent=2)
        if len(msg_str) > 1000:
            msg_str = msg_str[:1000] + "\n... [TRUNCATED]"
        logger.info(f"Publishing {event_type}: {msg_str}")
        if event_type in self.listeners:
            tasks = [callback(message) for callback in self.listeners[event_type]]
            await asyncio.gather(*tasks)

        # Also notify wildcard listeners
        if "*" in self.listeners:
            tasks = [callback(event_type, message) for callback in self.listeners["*"]]
            await asyncio.gather(*tasks)


# Global bus instance
bus = AgentBus()
