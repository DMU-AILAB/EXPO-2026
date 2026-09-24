from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta, timezone

from ..utils.timeutil import to_naive_utc, utcnow
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
        # 429는 Pi가 **재시도하는** 유일한 4xx다(`device/event_logger.py:240`).
        # 다른 4xx로 내면 그 이벤트는 outbox에서 삭제되어 영영 사라진다.
        raise HTTPException(
            status_code=429,
            detail="Too Many Requests: Rate limit exceeded (600 requests / minute)",
            headers={"Retry-After": str(RATE_LIMIT_WINDOW_SEC)},
        )

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
        
    # 보관 기간 제한 (명세 §6).
    #
    # 이전에는 start·end가 **둘 다** 있을 때만 검사해서 `?start=2020-01-01` 한 줄로
    # 전량 조회가 가능했다. 지금은 실제 조회 창을 먼저 확정하고 그 길이를 잰다.
    #
    # start를 생략하면 **거절하지 않고 90일로 자른다** — 목록 화면의 기본 호출
    # (`GET /api/events`)에 인자가 없어서, 거절하면 첫 화면부터 400이 난다.
    now = utcnow()
    end = to_naive_utc(end)
    if start is None:
        # 기본 창은 최근 90일. **상한은 걸지 않는다** — 기기가 타임스탬프를 찍으므로
        # (`device/event_logger.py`) 시계가 조금 빠른 Pi의 최신 이벤트가 `<= now`에
        # 걸려 목록에서 통째로 사라질 수 있다. 시계 동기화는 `make setup-ntp`가 맡고,
        # 조회는 그 오차에 관대해야 한다.
        start = (end or now) - timedelta(days=90)
    else:
        start = to_naive_utc(start)
        if ((end or now) - start).days > 90:
            raise HTTPException(
                status_code=400,
                detail={"error": "DATE_RANGE_TOO_LARGE",
                        "message": "조회 기간은 90일을 넘을 수 없습니다"},
            )

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
