from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
import json
from pydantic import BaseModel

from ..database import get_db
from ..deps import get_current_user
from ..models.device import Device
from ..models.schedule import ScheduledReboot
from ..services.scheduler_service import add_schedule_job, remove_schedule_job

router = APIRouter(prefix="/api/devices", tags=["schedules"])

class ScheduleCreate(BaseModel):
    days: List[int]
    hour: int
    is_enabled: bool = True

class ScheduleUpdate(BaseModel):
    days: List[int] = None
    hour: int = None
    is_enabled: bool = None

@router.get("/{device_id}/schedules")
def get_schedules(device_id: str, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail={"error": "DEVICE_NOT_FOUND", "message": "디바이스를 찾을 수 없습니다"})
        
    schedules = db.query(ScheduledReboot).filter(ScheduledReboot.device_id == device_id).all()
    
    data = []
    for sched in schedules:
        days = json.loads(sched.days)
        
        # 간단한 display 문자열 생성
        day_names = ["일", "월", "화", "수", "목", "금", "토"]
        day_str = "·".join([day_names[d] for d in sorted(days)])
        display = f"{day_str} {sched.hour:02d}:00"
        
        data.append({
            "id": sched.id,
            "days": days,
            "hour": sched.hour,
            "is_enabled": sched.is_enabled,
            "display": display
        })
        
    return {"data": data, "ok": True}

@router.post("/{device_id}/schedules", status_code=201)
def create_schedule(device_id: str, payload: ScheduleCreate, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail={"error": "DEVICE_NOT_FOUND", "message": "디바이스를 찾을 수 없습니다"})
        
    # days는 0=일 … 6=토 (명세 §11). APScheduler는 0=월이라 서버가 변환한다.
    if not payload.days or not all(0 <= d <= 6 for d in payload.days):
        raise HTTPException(status_code=400, detail={
            "error": "VALIDATION_ERROR", "message": "days는 0~6 사이 정수가 최소 1개 필요합니다"})
    if not (0 <= payload.hour <= 23):
        raise HTTPException(status_code=400, detail={
            "error": "VALIDATION_ERROR", "message": "hour는 0~23이어야 합니다"})
        
    new_schedule = ScheduledReboot(
        device_id=device_id,
        days=json.dumps(list(set(payload.days))),
        hour=payload.hour,
        is_enabled=payload.is_enabled
    )
    db.add(new_schedule)
    db.commit()
    db.refresh(new_schedule)
    
    # 메모리 스케줄러 등록
    if new_schedule.is_enabled:
        add_schedule_job(new_schedule.id, device_id, json.loads(new_schedule.days), new_schedule.hour)
        
    day_names = ["일", "월", "화", "수", "목", "금", "토"]
    days = json.loads(new_schedule.days)
    day_str = "·".join([day_names[d] for d in sorted(days)])
    
    return {
        "data": {
            "id": new_schedule.id,
            "days": days,
            "hour": new_schedule.hour,
            "is_enabled": new_schedule.is_enabled,
            "display": f"{day_str} {new_schedule.hour:02d}:00"
        },
        "ok": True
    }

@router.patch("/{device_id}/schedules/{schedule_id}")
def update_schedule(device_id: str, schedule_id: int, payload: ScheduleUpdate, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    sched = db.query(ScheduledReboot).filter(ScheduledReboot.id == schedule_id, ScheduledReboot.device_id == device_id).first()
    if not sched:
        raise HTTPException(status_code=404, detail={"error": "SCHEDULE_NOT_FOUND", "message": "예약을 찾을 수 없습니다"})
        
    if payload.days is not None:
        if not payload.days or not all(0 <= d <= 6 for d in payload.days):
            raise HTTPException(status_code=400, detail={
                "error": "VALIDATION_ERROR", "message": "days는 0~6 사이 정수가 최소 1개 필요합니다"})
        sched.days = json.dumps(list(set(payload.days)))
        
    if payload.hour is not None:
        if not (0 <= payload.hour <= 23):
            raise HTTPException(status_code=400, detail={
                "error": "VALIDATION_ERROR", "message": "hour는 0~23이어야 합니다"})
        sched.hour = payload.hour
        
    if payload.is_enabled is not None:
        sched.is_enabled = payload.is_enabled
        
    db.commit()
    db.refresh(sched)
    
    days = json.loads(sched.days)
    if sched.is_enabled:
        add_schedule_job(sched.id, device_id, days, sched.hour)
    else:
        remove_schedule_job(sched.id)
        
    day_names = ["일", "월", "화", "수", "목", "금", "토"]
    day_str = "·".join([day_names[d] for d in sorted(days)])
    
    return {
        "data": {
            "id": sched.id,
            "days": days,
            "hour": sched.hour,
            "is_enabled": sched.is_enabled,
            "display": f"{day_str} {sched.hour:02d}:00"
        },
        "ok": True
    }

@router.delete("/{device_id}/schedules/{schedule_id}", status_code=204)
def delete_schedule(device_id: str, schedule_id: int, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    sched = db.query(ScheduledReboot).filter(ScheduledReboot.id == schedule_id, ScheduledReboot.device_id == device_id).first()
    if not sched:
        raise HTTPException(status_code=404, detail={"error": "SCHEDULE_NOT_FOUND", "message": "예약을 찾을 수 없습니다"})
        
    # 메모리 스케줄러에서 삭제 (Step 4 방어)
    remove_schedule_job(sched.id)
    
    db.delete(sched)
    db.commit()
    return
