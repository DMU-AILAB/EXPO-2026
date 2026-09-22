import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from ..services.ws_manager import manager
from ..config import settings
import jwt
import json

router = APIRouter(tags=["WebSocket"])

@router.websocket("/ws/events")
async def websocket_endpoint(websocket: WebSocket, token: str = Query(None)):
    # 1. JWT Authentication
    if not token:
        await websocket.close(code=1008)
        return
        
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        username: str = payload.get("sub")
        if not username:
            raise ValueError("Invalid sub")
    except Exception:
        # Step 1: 1008 Policy Violation for invalid token
        await websocket.close(code=1008)
        return

    await manager.connect(websocket)
    
    # Step 2: Zombie connection prevention (30s Ping)
    async def keep_alive():
        try:
            while True:
                await asyncio.sleep(30)
                await websocket.send_text(json.dumps({"type": "ping"}))
        except Exception:
            pass

    ping_task = asyncio.create_task(keep_alive())

    # Step 3: Resource cleanup via WebSocketDisconnect
    try:
        while True:
            data = await websocket.receive_text()
            try:
                parsed = json.loads(data)
                if parsed.get("type") == "subscribe":
                    # Step 4: device_ids replacement logic handled in manager
                    device_ids = parsed.get("device_ids", [])
                    if isinstance(device_ids, list):
                        manager.subscribe(websocket, device_ids)
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    finally:
        ping_task.cancel()
        manager.disconnect(websocket)
