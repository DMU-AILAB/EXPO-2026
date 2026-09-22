import asyncio
from fastapi import WebSocket
from typing import Dict, List, Set, Any
import json

class ConnectionManager:
    def __init__(self):
        # Maps websocket to a set of device_ids it is subscribed to.
        # If the set is empty, it means the client is opted-in to ALL devices (default).
        self.active_connections: Dict[WebSocket, Set[str]] = {}

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        # Default state: receive events from all devices (empty set means no filter)
        self.active_connections[websocket] = set()

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            del self.active_connections[websocket]

    def subscribe(self, websocket: WebSocket, device_ids: List[str]):
        if websocket in self.active_connections:
            # Replace logic: overwrites existing subscriptions
            self.active_connections[websocket] = set(device_ids)

    async def broadcast_event(self, message_type: str, data: dict, device_id: str = None):
        message = {
            "type": message_type,
            "data": data
        }
        json_message = json.dumps(message)
        
        dead_connections = []
        
        for ws, subscribed_devices in self.active_connections.items():
            if not subscribed_devices or (device_id and device_id in subscribed_devices):
                try:
                    await ws.send_text(json_message)
                except Exception:
                    dead_connections.append(ws)
                    
        for dead_ws in dead_connections:
            self.disconnect(dead_ws)

manager = ConnectionManager()
