from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta, timezone
from typing import List, Optional
import time

from ..database import get_db
from ..models.event import DetectionEvent
from ..models.device import Device
from ..models.stats import HourlyStats
from ..schemas.event import EventIngestRequest, EventResponse
from ..deps import get_current_user, get_device_by_api_key

router = APIRouter(prefix="/api/events", tags=["Events"])

# In-memory rate limiting for ingest
# Structure: { device_id: [timestamp1, timestamp2, ...] }
_RATE_LIMIT_STORE = {}
RATE_LIMIT_MAX_REQUESTS = 600
RATE_LIMIT_WINDOW_SEC = 60

def check_rate_limit(device_id: str) -> bool:
    now = time.time()
    # Cleanup logic (TTL)
    if device_id not in _RATE_LIMIT_STORE:
        _RATE_LIMIT_STORE[device_id] = []
        
    # Remove timestamps older than the window
    _RATE_LIMIT_STORE[device_id] = [ts for ts in _RATE_LIMIT_STORE[device_id] if now - ts < RATE_LIMIT_WINDOW_SEC]
    
    if len(_RATE_LIMIT_STORE[device_id]) >= RATE_LIMIT_MAX_REQUESTS:
        return False
        
    _RATE_LIMIT_STORE[device_id].append(now)
    
    # Periodically clean up entirely stale keys to prevent memory leak
    # We do a quick check every time, but only if store is large. For this small scale, 
    # we can just clean occasionally or rely on the above line.
    # A full cleanup pass:
    if len(_RATE_LIMIT_STORE) > 1000:
        keys_to_delete = [k for k, v in _RATE_LIMIT_STORE.items() if not v or now - v[-1] >= RATE_LIMIT_WINDOW_SEC]
        for k in keys_to_delete:
            del _RATE_LIMIT_STORE[k]
            
    return True


@router.post("/ingest", status_code=201)
async def ingest_event(
    event_in: EventIngestRequest,
    device: Device = Depends(get_device_by_api_key),
    db: Session = Depends(get_db)
):
    if not check_rate_limit(device.id):
        raise HTTPException(status_code=429, detail="Too Many Requests: Rate limit exceeded (600 requests / minute)")

    # 1. Create DetectionEvent
    new_event = DetectionEvent(
        device_id=device.id,
        camera_id=event_in.camera_id,
        roi_id=event_in.roi_id,
        roi_name=event_in.roi_name,
        class_name=event_in.class_name,
        confidence=event_in.confidence,
        event_type=event_in.event_type.value,
        timestamp=event_in.timestamp
    )
    db.add(new_event)
    
    # 2. UPSERT into HourlyStats
    event_hour = event_in.timestamp.replace(minute=0, second=0, microsecond=0)
    
    stat = db.query(HourlyStats).filter(
        HourlyStats.device_id == device.id,
        HourlyStats.camera_id == event_in.camera_id,
        HourlyStats.hour == event_hour
    ).first()
    
    if not stat:
        stat = HourlyStats(
            device_id=device.id,
            camera_id=event_in.camera_id,
            hour=event_hour,
            foot_traffic_count=0,
            cane_user_count=0,
            detection_count=1
        )
        db.add(stat)
    else:
        stat.detection_count += 1
        
    db.commit()
    db.refresh(new_event)
    
    # Push to WebSocket subscribers
    from ..services.ws_manager import manager
    from ..schemas.event import EventResponse
    import asyncio
    
    event_data = EventResponse.model_validate(new_event).model_dump()
    event_data["timestamp"] = event_data["timestamp"].isoformat()
    
    await manager.broadcast_event("detection_event", event_data, device.id)
    
    return {"id": new_event.id, "ok": True}

@router.get("", response_model=dict)
def get_events(
    device_id: Optional[str] = None,
    camera_id: Optional[str] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    query = db.query(DetectionEvent)
    
    if device_id:
        query = query.filter(DetectionEvent.device_id == device_id)
    if camera_id:
        query = query.filter(DetectionEvent.camera_id == camera_id)
        
    # Check max query period of 90 days
    if start and end:
        if (end - start).days > 90:
            raise HTTPException(status_code=400, detail="Query period cannot exceed 90 days")
            
    if start:
        query = query.filter(DetectionEvent.timestamp >= start)
    if end:
        query = query.filter(DetectionEvent.timestamp <= end)
        
    total = query.count()
    events = query.order_by(DetectionEvent.timestamp.desc()).offset(offset).limit(limit).all()
    
    # Use Pydantic model to automatically generate time_display
    event_data = [EventResponse.model_validate(e).model_dump() for e in events]
    
    return {
        "data": event_data,
        "total": total,
        "ok": True
    }
