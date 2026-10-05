"""구조물 수집 — 여러 기기에 한꺼번에, 또는 예약 시각에.

카메라 한 대씩 시키는 경로는 기기 상세의 `routers/calibration.py`에 그대로 있다.
여기는 그것을 **대상 묶음**으로 확장한다: 전체 / 모델별 / 선택한 기기.

모델별 대상이 있는 이유: 구조물 후보는 **그 모델이 무엇을 오탐하느냐**에 달려 있다.
카메라의 모델을 바꾸면 이전 모델로 모은 후보가 맞지 않을 수 있어, 바꾼 모델을 쓰는
카메라만 골라 다시 모으는 일이 생긴다.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models.calibration import CalibrationRun, CalibrationSchedule
from ..models.device import Device
from ..schemas.camera import ModelVariant
from ..services.calibration_service import (MAX_SECONDS, MIN_SECONDS, TARGET_MODES,
                                            active_calibrations, resolve_targets, run_batch)
from ..services.device_view import effective_status
from ..services.heartbeat_service import get_buffered_status
from ..services.scheduler_service import add_calibration_job, remove_calibration_job

router = APIRouter(prefix="/api/calibration", tags=["Calibration (fleet)"])

DAY_NAMES = ["일", "월", "화", "수", "목", "금", "토"]


def _bad(message: str):
    raise HTTPException(status_code=400, detail={"error": "VALIDATION_ERROR", "message": message})


class Target(BaseModel):
    target_mode: str = "all"
    targets: list[str] = []


class RunRequest(Target):
    seconds: int = Field(300, ge=MIN_SECONDS, le=MAX_SECONDS)


class ScheduleCreate(Target):
    name: str = ""
    days: list[int]
    hour: int
    minute: int = 0
    seconds: int = Field(300, ge=MIN_SECONDS, le=MAX_SECONDS)
    is_enabled: bool = True


class ScheduleUpdate(BaseModel):
    name: Optional[str] = None
    days: Optional[list[int]] = None
    hour: Optional[int] = None
    minute: Optional[int] = None
    seconds: Optional[int] = Field(None, ge=MIN_SECONDS, le=MAX_SECONDS)
    target_mode: Optional[str] = None
    targets: Optional[list[str]] = None
    is_enabled: Optional[bool] = None


def _check_target(db: Session, mode: str, targets: list[str]) -> list[str]:
    if mode not in TARGET_MODES:
        _bad(f"target_mode는 {', '.join(TARGET_MODES)} 중 하나여야 합니다")
    if mode == "all":
        return []
    targets = sorted(set(targets))
    if not targets:
        _bad("대상을 하나 이상 고르세요")
    if mode == "model":
        known = {v.value for v in ModelVariant}
        unknown = [t for t in targets if t not in known]
        if unknown:
            _bad(f"알 수 없는 모델: {', '.join(unknown)}")
    else:
        found = {d.id for d in db.query(Device.id).filter(Device.id.in_(targets))}
        missing = [t for t in targets if t not in found]
        if missing:
            _bad(f"등록되지 않은 기기: {', '.join(missing)}")
    return targets


def _check_time(days: list[int], hour: int, minute: int) -> None:
    if not days or not all(0 <= d <= 6 for d in days):
        _bad("days는 0~6 사이 정수가 최소 1개 필요합니다")
    if not 0 <= hour <= 23:
        _bad("hour는 0~23이어야 합니다")
    if not 0 <= minute <= 59:
        _bad("minute는 0~59여야 합니다")


def _schedule_out(s: CalibrationSchedule) -> dict:
    days = json.loads(s.days)
    return {
        "id": s.id, "name": s.name, "days": days, "hour": s.hour, "minute": s.minute,
        "seconds": s.seconds, "target_mode": s.target_mode, "targets": json.loads(s.targets or "[]"),
        "is_enabled": s.is_enabled,
        "display": f"{'·'.join(DAY_NAMES[d] for d in sorted(days))} {s.hour:02d}:{s.minute:02d}",
    }


def _run_out(r: CalibrationRun, names: dict[str, str]) -> dict:
    return {
        "id": r.id, "batch_id": r.batch_id, "schedule_id": r.schedule_id,
        "device_id": r.device_id, "device_name": names.get(r.device_id, r.device_id),
        "camera_id": r.camera_id, "model_variant": r.model_variant, "seconds": r.seconds,
        "started_at": r.started_at.isoformat() if isinstance(r.started_at, datetime) else r.started_at,
        "ok": r.ok, "error": r.error,
    }


def _register(s: CalibrationSchedule) -> None:
    if s.is_enabled:
        add_calibration_job(s.id, json.loads(s.days), s.hour, s.minute)
    else:
        remove_calibration_job(s.id)


# ------------------------------------------------------------------ 대상·실행

@router.get("/targets")
async def list_targets(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """기기 × 카메라 목록과 각 카메라의 마지막 수집 기록."""
    last: dict[tuple[str, str], CalibrationRun] = {}
    for r in db.query(CalibrationRun).order_by(CalibrationRun.id.desc()).limit(500):
        last.setdefault((r.device_id, r.camera_id), r)
    names = {d.id: d.name for d in db.query(Device)}
    devices = []
    for d in db.query(Device).order_by(Device.name).all():
        devices.append({
            "id": d.id, "name": d.name, "location": d.location, "status": effective_status(d, await get_buffered_status(d.id)),
            "cameras": [{
                "id": c.id, "port": c.port, "model_variant": c.model_variant, "is_active": c.is_active,
                "last_run": _run_out(last[(d.id, c.id)], names) if (d.id, c.id) in last else None,
            } for c in sorted(d.cameras, key=lambda c: c.id)],
        })
    return {"data": {"devices": devices, "model_variants": [v.value for v in ModelVariant]}, "ok": True}


@router.get("/active")
async def list_active(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """지금 수집 중인 기기 — 디바이스 목록의 "오탐 관리 중" 표시용."""
    return {"data": await active_calibrations(db), "ok": True}


@router.post("/run")
async def run_now(payload: RunRequest, db: Session = Depends(get_db),
                  current_user=Depends(get_current_user)):
    targets = _check_target(db, payload.target_mode, payload.targets)
    pairs = resolve_targets(db, payload.target_mode, targets)
    if not pairs:
        _bad("대상에 해당하는 활성 카메라가 없습니다")
    return {"data": await run_batch(db, pairs, payload.seconds), "ok": True}


@router.get("/runs")
def list_runs(limit: int = 100, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    limit = max(1, min(500, limit))
    names = {d.id: d.name for d in db.query(Device)}
    rows = db.query(CalibrationRun).order_by(CalibrationRun.id.desc()).limit(limit).all()
    return {"data": [_run_out(r, names) for r in rows], "ok": True}


# ------------------------------------------------------------------ 예약

@router.get("/schedules")
def list_schedules(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    rows = db.query(CalibrationSchedule).order_by(CalibrationSchedule.hour,
                                                  CalibrationSchedule.minute).all()
    return {"data": [_schedule_out(s) for s in rows], "ok": True}


@router.post("/schedules", status_code=201)
def create_schedule(payload: ScheduleCreate, db: Session = Depends(get_db),
                    current_user=Depends(get_current_user)):
    _check_time(payload.days, payload.hour, payload.minute)
    targets = _check_target(db, payload.target_mode, payload.targets)
    s = CalibrationSchedule(name=payload.name.strip(), days=json.dumps(sorted(set(payload.days))),
                            hour=payload.hour, minute=payload.minute, seconds=payload.seconds,
                            target_mode=payload.target_mode, targets=json.dumps(targets),
                            is_enabled=payload.is_enabled)
    db.add(s)
    db.commit()
    db.refresh(s)
    _register(s)
    return {"data": _schedule_out(s), "ok": True}


@router.patch("/schedules/{schedule_id}")
def update_schedule(schedule_id: int, payload: ScheduleUpdate, db: Session = Depends(get_db),
                    current_user=Depends(get_current_user)):
    s = db.get(CalibrationSchedule, schedule_id)
    if s is None:
        raise HTTPException(status_code=404, detail={"error": "SCHEDULE_NOT_FOUND",
                                                     "message": "예약을 찾을 수 없습니다"})
    days = payload.days if payload.days is not None else json.loads(s.days)
    hour = payload.hour if payload.hour is not None else s.hour
    minute = payload.minute if payload.minute is not None else s.minute
    _check_time(days, hour, minute)
    mode = payload.target_mode if payload.target_mode is not None else s.target_mode
    raw_targets = payload.targets if payload.targets is not None else json.loads(s.targets or "[]")
    targets = _check_target(db, mode, raw_targets)

    s.days, s.hour, s.minute = json.dumps(sorted(set(days))), hour, minute
    s.target_mode, s.targets = mode, json.dumps(targets)
    if payload.name is not None:
        s.name = payload.name.strip()
    if payload.seconds is not None:
        s.seconds = payload.seconds
    if payload.is_enabled is not None:
        s.is_enabled = payload.is_enabled
    db.commit()
    db.refresh(s)
    _register(s)
    return {"data": _schedule_out(s), "ok": True}


@router.delete("/schedules/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: int, db: Session = Depends(get_db),
                    current_user=Depends(get_current_user)):
    s = db.get(CalibrationSchedule, schedule_id)
    if s is None:
        raise HTTPException(status_code=404, detail={"error": "SCHEDULE_NOT_FOUND",
                                                     "message": "예약을 찾을 수 없습니다"})
    remove_calibration_job(s.id)
    db.delete(s)
    db.commit()
