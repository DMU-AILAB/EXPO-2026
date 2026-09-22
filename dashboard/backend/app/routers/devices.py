from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import hashlib
import secrets
from ..database import get_db
from ..models import Device
from ..schemas.device import DeviceCreate, DeviceUpdate
from ..deps import get_current_user

router = APIRouter(prefix="/api/devices", tags=["devices"])

@router.get("")
def get_devices(search: str = None, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    query = db.query(Device)
    if search:
        query = query.filter(Device.name.contains(search) | Device.ip.contains(search))
    devices = query.all()
    
    data = []
    for d in devices:
        data.append({
            "id": d.id, "name": d.name, "ip": d.ip, "location": d.location, 
            "status": d.status, "last_seen": d.last_seen
        })
    return {"data": data, "total": len(data), "ok": True}

@router.post("", status_code=201)
def create_device(device_in: DeviceCreate, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    existing = db.query(Device).filter(Device.id == device_in.id).first()
    if existing:
        raise HTTPException(status_code=409, detail={"error": "DEVICE_ALREADY_EXISTS", "ok": False})
    
    raw_api_key = f"vg_{secrets.token_urlsafe(16)}"
    api_key_hash = hashlib.sha256(raw_api_key.encode()).hexdigest()
    
    new_device = Device(
        id=device_in.id,
        name=device_in.name,
        ip=device_in.ip,
        location=device_in.location,
        api_key_hash=api_key_hash
    )
    db.add(new_device)
    db.commit()
    
    return {"data": {"id": new_device.id, "api_key": raw_api_key}, "ok": True}

@router.get("/{device_id}")
def get_device(device_id: str, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail={"error": "DEVICE_NOT_FOUND", "ok": False})
    
    cameras_data = [{"id": c.id, "port": c.port, "capture_preset": c.capture_preset, 
                     "fps": c.fps, "model_variant": c.model_variant, "rotation": c.rotation, 
                     "require_person": c.require_person, "is_active": c.is_active} for c in device.cameras]
                     
    data = {
        "id": device.id, "name": device.name, "ip": device.ip, "location": device.location,
        "status": device.status, "last_seen": device.last_seen,
        "cameras": cameras_data
    }
    return {"data": data, "ok": True}

@router.patch("/{device_id}")
def update_device(device_id: str, device_in: DeviceUpdate, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail={"error": "DEVICE_NOT_FOUND", "ok": False})
    
    update_data = device_in.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(device, key, value)
        
    db.commit()
    db.refresh(device)
    
    return {"data": {"id": device.id, "name": device.name}, "ok": True}

from ..services.heartbeat_service import remove_device_from_buffer

@router.delete("/{device_id}", status_code=204)
async def delete_device(device_id: str, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail={"error": "DEVICE_NOT_FOUND", "ok": False})
    
    # Step 2 방어: 삭제 시점에 버퍼에서도 명시적으로 제거하여 팬텀 기기 참조 무결성(FK) 오류 방지
    await remove_device_from_buffer(device_id)
    
    db.delete(device)
    db.commit()
    return

from ..deps import get_device_by_api_key
from ..services.heartbeat_service import update_heartbeat_buffer, get_buffered_status
from pydantic import BaseModel
from typing import List, Optional

class HeartbeatPayload(BaseModel):
    status: str
    load_avg: List[float]
    cpu_temp_c: float
    memory: dict
    uptime_seconds: int
    latency_ms: Optional[int] = None
    npu_ms: Optional[int] = None
    cameras: List[dict]

@router.patch("/me/heartbeat")
async def heartbeat(payload: HeartbeatPayload, device: Device = Depends(get_device_by_api_key)):
    # DB I/O 없이 인메모리 버퍼에만 상태 갱신
    await update_heartbeat_buffer(device.id, payload.dict())
    return {"ok": True}

from ..models.device import DeviceStatusCache

@router.get("/{device_id}/status")
async def get_device_status(device_id: str, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail={"error": "DEVICE_NOT_FOUND", "ok": False})

    # 버퍼(메모리) 우선 조회, 없으면 DB(캐시) 조회
    buffered = await get_buffered_status(device_id)
    if buffered:
        return {
            "data": {
                "status": buffered.get("status"),
                "load_avg": buffered.get("load_avg"),
                "cpu_temp_c": buffered.get("cpu_temp_c"),
                "memory": buffered.get("memory"),
                "uptime_seconds": buffered.get("uptime_seconds"),
                "latency_ms": buffered.get("latency_ms"),
                "npu_ms": buffered.get("npu_ms"),
                "last_seen": buffered.get("updated_at")
            },
            "ok": True
        }
    
    # DB Fallback
    cached = db.query(DeviceStatusCache).filter(DeviceStatusCache.device_id == device_id).first()
    if cached:
        return {
            "data": {
                "status": "online",
                "load_avg": [cached.load_avg_1m, cached.load_avg_5m, cached.load_avg_15m],
                "cpu_temp_c": cached.cpu_temp_c,
                "memory": {"used_mb": cached.memory_used_mb, "total_mb": cached.memory_total_mb},
                "uptime_seconds": cached.uptime_seconds,
                "latency_ms": cached.latency_ms,
                "npu_ms": cached.npu_ms,
                "last_seen": cached.updated_at
            },
            "ok": True
        }
        
    return {"data": {}, "ok": True}

import httpx
from fastapi.responses import JSONResponse

@router.post("/{device_id}/reboot", status_code=202)
async def reboot_device(device_id: str, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail={"error": "DEVICE_NOT_FOUND", "ok": False})
    
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post(f"http://{device.ip}:5000/reboot")
    except (httpx.RequestError, httpx.RemoteProtocolError, httpx.ReadTimeout):
        # Step 2 방어: 기기가 명령을 받자마자 죽어서 연결이 끊긴 경우를 성공(202)으로 간주
        pass
        
    return JSONResponse(status_code=202, content={
        "data": {
            "job_id": f"reboot-{device_id}",
            "estimated_seconds": 60,
            "message": "디바이스 재부팅 요청이 전송되었습니다. 약 60초 후 온라인 상태가 됩니다."
        },
        "ok": True
    })

@router.post("/{device_id}/restart", status_code=202)
async def restart_device(device_id: str, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail={"error": "DEVICE_NOT_FOUND", "ok": False})
    
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post(f"http://{device.ip}:5000/restart")
    except (httpx.RequestError, httpx.RemoteProtocolError, httpx.ReadTimeout):
        pass
        
    return JSONResponse(status_code=202, content={
        "data": {
            "job_id": f"restart-{device_id}",
            "estimated_seconds": 10,
            "message": "서비스 재시작 요청이 전송되었습니다."
        },
        "ok": True
    })
