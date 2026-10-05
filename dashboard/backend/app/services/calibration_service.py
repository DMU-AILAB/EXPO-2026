"""구조물 수집을 여러 카메라에 한꺼번에 시킨다 — 지금 바로, 또는 예약 시각에.

수집은 기기마다 카메라 MJPEG 포트의 `/calibrate/start`가 한다(`PiClient(ip, camera.port)`).
이 모듈은 **대상 해석 → 동시 시작 → 결과 기록**만 맡는다.

- **시작만 시키고 끝을 기다리지 않는다.** 관측은 기기가 혼자 끝내고 후보를 저장한다.
  서버가 수십 분짜리 요청을 붙잡고 있을 이유가 없다.
- **후보를 적용하지 않는다.** 예약 수집이 곧 자동 적용 경로가 되면 그 시각에 지나간
  청소 인력의 자리가 구조물로 굳는다(CLAUDE.md "구조물 마스크"). 적용은 기기 상세의
  "오탐 관리" 탭에서 운영자가 한다.
- **오프라인 기기는 시도하지 않고 기록만 남긴다.** 꺼진 기기마다 연결 타임아웃을
  기다리면 예약 한 번이 그만큼 늘어지고, 결과 목록에서 "왜 빠졌는지"가 보여야 한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models.calibration import CalibrationRun, CalibrationSchedule
from ..models.camera import Camera
from ..models.device import Device
from .device_view import effective_status
from .heartbeat_service import get_buffered_status
from .pi_client import PiClient

logger = logging.getLogger(__name__)

TARGET_MODES = ("all", "model", "devices")
MIN_SECONDS, MAX_SECONDS = 10, 1800     # 기기(`camera_live_pi.py`)가 같은 범위로 자른다
MAX_CONCURRENCY = 8                     # 기기 수십 대에 한꺼번에 연결을 열지 않는다
START_TIMEOUT = 5.0
KEEP_RUNS = 1000                        # 기록 테이블이 끝없이 자라지 않게


def resolve_targets(db: Session, mode: str, targets: list[str]) -> list[tuple[Device, Camera]]:
    """활성 카메라만 대상이다 — 꺼둔 카메라는 프레임을 받지 않아 수집할 것이 없다."""
    query = (db.query(Device, Camera)
             .join(Camera, Camera.device_id == Device.id)
             .filter(Camera.is_active.is_(True)))
    if mode == "model":
        query = query.filter(Camera.model_variant.in_(targets))
    elif mode == "devices":
        query = query.filter(Device.id.in_(targets))
    return query.order_by(Device.name, Camera.id).all()


def _error_text(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        d = exc.detail
        if isinstance(d, dict):
            return str(d.get("message") or d.get("error") or d)
        return str(d)
    return str(exc) or exc.__class__.__name__


async def _start_one(device: Device, camera: Camera, seconds: int,
                     sem: asyncio.Semaphore) -> tuple[bool, str | None]:
    # DB의 status는 30초 주기로만 갱신된다 — 방금 돌아온 기기를 건너뛰지 않게 버퍼까지 본다.
    if effective_status(device, await get_buffered_status(device.id)) == "offline":
        return False, "기기가 오프라인이라 건너뛰었습니다"
    async with sem:
        try:
            res = await PiClient(device.ip, camera.port, timeout=START_TIMEOUT).calibration_start(seconds)
        except Exception as exc:                   # noqa: BLE001 — 한 대의 실패가 나머지를 막지 않는다
            return False, _error_text(exc)
    if isinstance(res, dict) and res.get("ok") is False:
        # 이미 수집 중이면 기기가 ok=false로 답한다 — 실패가 아니라 "건너뜀"에 가깝지만
        # 이번 요청이 관측 시간을 정하지 못했다는 점은 같아 실패로 남긴다.
        return False, str(res.get("error") or "기기가 거절했습니다")
    return True, None


async def run_batch(db: Session, targets: list[tuple[Device, Camera]], seconds: int,
                    schedule_id: int | None = None) -> dict:
    """대상 카메라 전부에 수집을 시작시키고, 카메라별 결과를 기록해 돌려준다."""
    seconds = max(MIN_SECONDS, min(MAX_SECONDS, int(seconds)))
    batch_id = uuid.uuid4().hex
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    outcomes = await asyncio.gather(*[_start_one(d, c, seconds, sem) for d, c in targets])

    rows = []
    for (device, camera), (ok, error) in zip(targets, outcomes):
        run = CalibrationRun(batch_id=batch_id, schedule_id=schedule_id, device_id=device.id,
                             camera_id=camera.id, model_variant=camera.model_variant,
                             seconds=seconds, ok=ok, error=error)
        db.add(run)
        rows.append({"device_id": device.id, "device_name": device.name, "camera_id": camera.id,
                     "model_variant": camera.model_variant, "ok": ok, "error": error})
    db.commit()
    _prune(db)

    invalidate_active_cache()
    started = sum(1 for r in rows if r["ok"])
    logger.info("구조물 수집 일괄 시작 %s: %d/%d대 (schedule=%s, %ds)",
                batch_id, started, len(rows), schedule_id, seconds)
    return {"batch_id": batch_id, "seconds": seconds, "total": len(rows),
            "started": started, "results": rows}


def _prune(db: Session) -> None:
    cutoff = (db.query(CalibrationRun.id).order_by(CalibrationRun.id.desc())
              .offset(KEEP_RUNS).limit(1).scalar())
    if cutoff is not None:
        db.query(CalibrationRun).filter(CalibrationRun.id <= cutoff).delete(synchronize_session=False)
        db.commit()


async def execute_schedule(schedule_id: int) -> None:
    """APScheduler 작업 본체. 예약 시각의 대상(그때 등록된 카메라)을 다시 해석한다 —
    예약을 만든 뒤 추가된 기기도 '전체' 예약에 자연히 포함된다."""
    from ..database import SessionLocal
    db = SessionLocal()
    try:
        sched = db.get(CalibrationSchedule, schedule_id)
        if sched is None or not sched.is_enabled:
            return
        targets = resolve_targets(db, sched.target_mode, json.loads(sched.targets or "[]"))
        if not targets:
            logger.warning("구조물 수집 예약 %s: 대상 카메라가 없습니다", schedule_id)
            return
        await run_batch(db, targets, sched.seconds, schedule_id=schedule_id)
    except Exception:                              # noqa: BLE001 — 다음 예약을 막지 않는다
        logger.exception("구조물 수집 예약 %s 실행 중 오류", schedule_id)
    finally:
        db.close()


# ---------------------------------------------------------------- 진행 중인 수집

STATUS_TIMEOUT = 1.5
STATUS_TTL = 3.0              # 목록 화면 여러 개가 같은 순간에 기기 전부를 두드리지 않게
_active_cache: tuple[float, dict] | None = None


async def _camera_running(device: Device, camera: Camera,
                          sem: asyncio.Semaphore) -> tuple[str, float | None] | None:
    async with sem:
        try:
            st = await PiClient(device.ip, camera.port, timeout=STATUS_TIMEOUT).calibration_status()
        except Exception:                          # noqa: BLE001 — 못 물어본 카메라는 "모름"이다
            return None
    if isinstance(st, dict) and st.get("running"):
        return device.id, st.get("remaining_sec")
    return None


async def active_calibrations(db: Session) -> dict[str, dict]:
    """수집 중인 기기 → {remaining_sec, cameras}. **기기에 직접 묻는다.**

    대시보드의 실행 기록만으로 추정하면 Pi 자체 화면(:5000)에서 시작하거나 취소한 수집,
    예정보다 일찍 끝난 수집을 틀리게 보여준다. 오프라인 기기는 묻지 않는다.
    """
    global _active_cache
    now = time.monotonic()
    if _active_cache is not None and now - _active_cache[0] < STATUS_TTL:
        return _active_cache[1]

    pairs = []
    for device, camera in resolve_targets(db, "all", []):
        if effective_status(device, await get_buffered_status(device.id)) != "offline":
            pairs.append((device, camera))
    sem = asyncio.Semaphore(MAX_CONCURRENCY * 2)
    found = await asyncio.gather(*[_camera_running(d, c, sem) for d, c in pairs])

    active: dict[str, dict] = {}
    for hit in found:
        if hit is None:
            continue
        device_id, remaining = hit
        entry = active.setdefault(device_id, {"remaining_sec": None, "cameras": 0})
        entry["cameras"] += 1
        if remaining is not None:
            entry["remaining_sec"] = max(entry["remaining_sec"] or 0, remaining)
    _active_cache = (now, active)
    return active


def invalidate_active_cache() -> None:
    """수집을 시작·취소한 직후 목록이 3초 동안 옛 상태를 보이지 않게."""
    global _active_cache
    _active_cache = None
