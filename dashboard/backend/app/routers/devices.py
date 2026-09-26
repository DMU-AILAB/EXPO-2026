from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import hashlib
import json
import logging
import secrets
import time
from typing import Optional
from ..database import get_db
from ..config import settings
from ..models import Device
from ..models.camera import Camera
from ..models.event import DetectionEvent
from ..models.roi import Roi
from ..schemas.device import DeviceCreate, DeviceUpdate, ProvisionDeviceRequest
from ..deps import get_current_user
from ..services.device_view import build_device_summary, build_status_payload, is_stale
from ..services.pi_client import PiClient
from ..services.heartbeat_service import get_buffered_cameras, get_buffered_status
from ..utils.timeutil import kst_day_bounds_utc, utcnow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/devices", tags=["devices"])

# Camera configuration is relatively expensive to read from a Pi. Heartbeats
# already provide live status, so the device list only refreshes this cache
# periodically.
_CAMERA_REFRESH_INTERVAL_SEC = 30.0
_camera_refresh_at: dict[str, float] = {}


def _camera_refresh_due(device_id: str, now: float | None = None) -> bool:
    now = time.monotonic() if now is None else now
    last_refresh = _camera_refresh_at.get(device_id)
    return last_refresh is None or now - last_refresh >= _CAMERA_REFRESH_INTERVAL_SEC

@router.get("")
async def get_devices(search: str = None, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    query = db.query(Device)
    if search:
        # 명세 §3은 이름·IP·**위치** 부분 검색이다 — location이 빠져 있었다.
        query = query.filter(
            Device.name.contains(search)
            | Device.ip.contains(search)
            | Device.location.contains(search)
        )
    devices = query.all()

    data = []
    for d in devices:
        buffered = await get_buffered_status(d.id)
        camera_rows = db.query(Camera).filter(Camera.device_id == d.id).all()
        if (buffered and not is_stale(buffered.get("updated_at"))
                and _camera_refresh_due(d.id)):
            # Keep the device list in sync when a camera is connected after
            # registration.  The camera detail endpoint already does this;
            # doing it here makes the dashboard overview update as well. Do
            # this at most once per interval so every list poll stays local.
            from .cameras import refresh_cameras_from_pi
            _camera_refresh_at[d.id] = time.monotonic()
            await refresh_cameras_from_pi(db, d)
            camera_rows = db.query(Camera).filter(Camera.device_id == d.id).all()
        runtime = await get_buffered_cameras(d.id)
        cameras = [
            {
                "id": c.id,
                "port": c.port,
                "is_streaming": bool(runtime.get(c.id, {}).get("is_streaming", False)),
            }
            for c in camera_rows
        ]
        data.append(build_device_summary(
            db, d, buffered,
            today_detections=_today_detections(db, d.id),
            cameras=cameras,
        ))
    return {"data": data, "total": len(data), "ok": True}


def _today_detections(db: Session, device_id: str) -> int:
    """오늘(KST) 이 기기에서 발생한 ROI 트리거 수.

    하트비트가 실어 오는 Pi 자체 집계는 **기기 전체값이 카메라마다 복사된 것**이라
    (`device/event_logger.py:298,307`) 카메라별로 쓸 수 없다. 서버는 ingest된 이벤트를
    직접 세어 쓴다 — 기기가 잠시 꺼져 있어도 과거 값이 남는다는 장점도 있다.
    """
    start, end = kst_day_bounds_utc()
    return db.query(DetectionEvent).filter(
        DetectionEvent.device_id == device_id,
        DetectionEvent.timestamp >= start,
        DetectionEvent.timestamp <= end,
    ).count()

@router.post("", status_code=201)
async def create_device(device_in: DeviceCreate, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    existing = db.query(Device).filter(Device.id == device_in.id).first()
    if existing:
        raise HTTPException(status_code=409, detail={"error": "DEVICE_ALREADY_EXISTS", "message": "같은 id의 디바이스가 이미 등록되어 있습니다"})
    
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

    # 기기에 신원을 심는다. **이걸 하지 않으면 등록해도 Pi는 서버를 모른다** —
    # device_id·api_key·server_url이 전부 있어야 Pi가 전송을 시작한다.
    provisioned, reason = await _provision_device(
        new_device, raw_api_key,
    )
    if provisioned:
        # 이후 재시작·재부팅과 하트비트 제어에 같은 키가 필요하다.
        # 초기 sync가 실패해도 신원 주입 결과는 보존해야 한다.
        db.commit()

    # 카메라·ROI 스냅샷을 곧바로 끌어온다. 서버에는 카메라를 **만드는** API가 없으므로
    # (원본이 Pi다) 이 동기화를 건너뛰면 cameras 테이블이 영원히 비어 있고,
    # 카메라·ROI 화면 전체가 빈 테이블 위에서 404가 된다.
    synced = await _initial_sync(db, new_device)

    return {
        "data": {
            "id": new_device.id,
            "api_key": raw_api_key,
            "provisioned": provisioned,
            "provision_error": reason,
            "synced": synced,
        },
        "ok": True,
    }


async def _initial_sync(db: Session, device: Device) -> bool:
    """등록 직후 Pi에서 카메라 프로필과 카메라별 ROI를 끌어와 캐시에 심는다."""
    from ..models.camera import Camera
    from ..routers.cameras import refresh_cameras_from_pi
    from ..routers.rois import refresh_rois_from_pi

    if not await refresh_cameras_from_pi(db, device):
        return False
    ok = True
    for camera in db.query(Camera).filter(Camera.device_id == device.id).all():
        if not await refresh_rois_from_pi(db, device, camera):
            ok = False
    return ok


async def _provision_device(device: Device, raw_api_key: str) -> tuple[bool, Optional[str]]:
    """Pi에 신원을 심고 성공 여부를 돌려준다.

    **실패해도 등록 자체는 성공으로 둔다** — 기기가 아직 꺼져 있거나 주소를 잘못 적은
    경우가 흔한데, 그때마다 등록을 되돌리면 평문 api_key를 다시 볼 방법이 없어진다
    (등록 응답에서 1회만 노출된다). 대신 사유를 응답에 실어 UI가 재시도를 안내한다.
    """
    client = PiClient(device.ip)
    try:
        await client.post_identity(
            device_id=device.id,
            api_key=raw_api_key,
            server_url=settings.public_base_url.rstrip("/"),
            name=device.name or "",
            location=device.location or "",
            registered_at=utcnow().isoformat(timespec="seconds") + "Z",
            current_key=device.control_key or "",
        )
        # 기기가 받아들인 키만 보관한다 — 실패한 키를 저장하면 서버와 기기가 갈라진다.
        device.control_key = raw_api_key
        return True, None
    except HTTPException as exc:
        logger.warning("기기 신원 주입 실패 (%s): %s", device.id, exc.detail)
        return False, str(exc.detail)


@router.post("/{device_id}/provision", status_code=200)
async def provision_device(device_id: str, db: Session = Depends(get_db),
                           current_user = Depends(get_current_user),
                           payload: ProvisionDeviceRequest | None = None):
    """신원 재주입 — 기기가 꺼져 있어 등록 시 실패했거나, 키를 교체할 때 쓴다.

    **새 api_key를 발급한다.** 기존 키는 해시만 보관하므로 평문을 복원할 수 없어,
    다시 심으려면 새로 만드는 수밖에 없다. Pi는 덮어쓰기를 허용한다.
    """
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail={"error": "DEVICE_NOT_FOUND", "message": "디바이스를 찾을 수 없습니다"})

    raw_api_key = f"vg_{secrets.token_urlsafe(16)}"
    provisioned, reason = await _provision_device(device, raw_api_key)
    if not provisioned:
        raise HTTPException(status_code=503, detail=reason)

    # Pi가 새 키를 받은 뒤에만 서버 쪽 해시를 바꾼다 — 순서가 바뀌면 주입에 실패했을 때
    # 기기의 옛 키가 서버에서 거부되어 연동이 끊긴다.
    device.api_key_hash = hashlib.sha256(raw_api_key.encode()).hexdigest()
    db.commit()

    return {"data": {"id": device.id, "api_key": raw_api_key, "provisioned": True}, "ok": True}

@router.get("/{device_id}")
async def get_device(device_id: str, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    """단일 디바이스 상세 — 카메라·ROI·최근 이벤트까지 (명세 §3).

    DeviceDetail 화면이 이 세 목록을 한 번에 요구한다. 이전에는 카메라만 있어서
    ROI 탭과 개요 탭이 목 데이터로 남아 있었다.
    """
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404,
                            detail={"error": "DEVICE_NOT_FOUND", "message": "디바이스를 찾을 수 없습니다"})

    buffered = await get_buffered_status(device_id)
    runtime = await get_buffered_cameras(device_id)

    cameras_data = []
    for c in device.cameras:
        live = runtime.get(c.id, {})
        cameras_data.append({
            "id": c.id, "port": c.port, "capture_preset": c.capture_preset,
            "fps": 10, "fps_applied": True,
            "model_variant": c.model_variant, "rotation": c.rotation,
            "require_person": c.require_person, "is_active": c.is_active,
            "roi_count": db.query(Roi).filter(Roi.device_id == device_id,
                                              Roi.camera_id == c.id).count(),
            "is_streaming": bool(live.get("is_streaming", False)),
            "current_alert": live.get("current_alert"),
            "today_detections": live.get("today_detections", 0),
        })

    rois_data = [{
        "id": r.id, "camera_id": r.camera_id, "name": r.name, "zone_type": r.zone_type,
        "priority": r.priority, "announcement_text": r.announcement_text,
        "audio_file": r.audio_file, "color": r.color, "is_active": r.is_active,
        "polygon": json.loads(r.polygon) if isinstance(r.polygon, str) else r.polygon,
    } for r in db.query(Roi).filter(Roi.device_id == device_id).all()]

    recent = db.query(DetectionEvent).filter(
        DetectionEvent.device_id == device_id
    ).order_by(DetectionEvent.timestamp.desc()).limit(20).all()
    events_data = [{
        "id": e.id,
        "time": e.timestamp.strftime("%H:%M:%S"),
        "camera": e.camera_id,
        "roi": e.roi_name,
        "confidence": e.confidence,
        "event_type": e.event_type,
        "timestamp": e.timestamp,
    } for e in recent]

    data = build_device_summary(db, device, buffered,
                                today_detections=_today_detections(db, device_id),
                                cameras=cameras_data)
    data["rois"] = rois_data
    data["recent_events"] = events_data
    data["etag"] = device.config_etag
    return {"data": data, "ok": True}

@router.patch("/{device_id}")
def update_device(device_id: str, device_in: DeviceUpdate, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail={"error": "DEVICE_NOT_FOUND", "message": "디바이스를 찾을 수 없습니다"})
    
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
        raise HTTPException(status_code=404, detail={"error": "DEVICE_NOT_FOUND", "message": "디바이스를 찾을 수 없습니다"})
    
    # Step 2 방어: 삭제 시점에 버퍼에서도 명시적으로 제거하여 팬텀 기기 참조 무결성(FK) 오류 방지
    await remove_device_from_buffer(device_id)
    
    db.delete(device)
    db.commit()
    _camera_refresh_at.pop(device_id, None)
    return

from ..deps import get_device_by_api_key
from ..services.heartbeat_service import update_heartbeat_buffer, get_buffered_status
from pydantic import BaseModel
from typing import Any, Dict, List, Optional

class HeartbeatPayload(BaseModel):
    """Pi의 `HeartbeatSender.build_payload()`에 맞춘다 (device/event_logger.py:278-327).

    **전 필드가 None일 수 있다.** Pi는 `/proc`·`/sys` 읽기에 실패한 항목을 `None`으로
    두고 그대로 보낸다 — `memory`는 `mem_total_mb`를 못 읽으면 **객체 통째로** None이고,
    `latency_ms`/`npu_ms`는 살아있는 카메라가 없으면 None이다. 여기를 필수로 두면
    개발 PC나 센서 읽기 실패 시 422가 나고, Pi는 하트비트를 **버퍼링하지 않으므로**
    (`event_logger.py:254-255`) 그 주기의 상태는 그냥 사라진다.
    """

    status: str = "online"
    load_avg: Optional[List[Optional[float]]] = None
    # Pi가 새로 보내기 시작한 값 — 명세 §3의 "미산출" 서술은 갱신이 필요하다.
    cpu_percent: Optional[float] = None
    cpu_temp_c: Optional[float] = None
    memory: Optional[Dict[str, Optional[float]]] = None
    uptime_seconds: Optional[float] = None
    latency_ms: Optional[int] = None
    npu_ms: Optional[int] = None
    cameras: List[dict] = []

@router.patch("/me/heartbeat")
async def heartbeat(payload: HeartbeatPayload, device: Device = Depends(get_device_by_api_key)):
    # DB I/O 없이 인메모리 버퍼에만 상태 갱신
    await update_heartbeat_buffer(device.id, payload.model_dump())
    return {"ok": True}

from ..models.device import DeviceStatusCache

@router.get("/{device_id}/status")
async def get_device_status(device_id: str, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    """기기 실시간 상태 (명세 §3).

    인메모리 버퍼가 가장 최신이고, 비어 있으면 `device_status_cache`로 떨어진다
    (서버 재시작 직후 등). 조립은 `services/device_view.py` 한 곳에서 한다 — 예전에는
    같은 필드 목록이 이 함수 안에 두 벌 적혀 있어 `cpu_percent`를 추가할 때 한쪽만
    고쳐지기 쉬웠다.
    """
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404,
                            detail={"error": "DEVICE_NOT_FOUND", "message": "디바이스를 찾을 수 없습니다"})

    buffered = await get_buffered_status(device_id)
    return {"data": build_status_payload(db, device, buffered), "ok": True}


from fastapi.responses import JSONResponse


async def _device_key(db: Session, device: Device) -> str:
    """Pi의 제어 엔드포인트가 요구하는 키.

    서버는 api_key의 **해시만** 보관하므로 평문을 복원할 수 없다. 그래서 제어는
    기기에 심어둔 키를 그대로 되돌려 보내는 방식이 아니라, `POST .../provision`으로
    키를 새로 심은 뒤에만 가능하다 — 이 함수는 그 사실을 한 곳에서 설명하기 위한
    자리이고, 실제 키는 `device_control_key` 컬럼에 보관한다.
    """
    if not device.control_key:
        raise HTTPException(
            status_code=409,
            detail={"error": "NOT_PROVISIONED",
                    "message": "기기에 신원이 심어지지 않아 원격 제어를 할 수 없습니다. "
                               "먼저 POST /api/devices/{id}/provision 을 호출하세요"},
        )
    return device.control_key


@router.post("/{device_id}/reboot", status_code=202)
async def reboot_device(device_id: str, db: Session = Depends(get_db),
                        current_user = Depends(get_current_user)):
    """Pi 전체 재부팅 (명세 §10).

    **실패를 삼키지 않는다.** 이전 구현은 존재하지도 않는 `POST /reboot`을 부르고
    모든 예외를 무시한 뒤 항상 202를 돌려줬다 — 기기가 꺼져 있어도 "재부팅 요청이
    전송되었습니다"가 떴다.
    """
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404,
                            detail={"error": "DEVICE_NOT_FOUND", "message": "디바이스를 찾을 수 없습니다"})

    key = await _device_key(db, device)
    await PiClient(device.ip).post_control("/api/system/reboot", key, params={"confirm": "true"})

    return JSONResponse(status_code=202, content={
        "data": {
            "job_id": f"reboot-{device_id}",
            "estimated_seconds": 60,
            "message": "디바이스 재부팅 요청이 전송되었습니다. 약 60초 후 온라인 상태가 됩니다."
        },
        "ok": True
    })


@router.post("/{device_id}/restart", status_code=202)
async def restart_device(device_id: str, db: Session = Depends(get_db),
                         current_user = Depends(get_current_user)):
    """`visionguide-device` 서비스만 재시작 (명세 §10)."""
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404,
                            detail={"error": "DEVICE_NOT_FOUND", "message": "디바이스를 찾을 수 없습니다"})

    key = await _device_key(db, device)
    await PiClient(device.ip).post_control("/api/service/restart", key)

    return JSONResponse(status_code=202, content={
        "data": {
            "job_id": f"restart-{device_id}",
            "estimated_seconds": 10,
            "message": "서비스 재시작 요청이 전송되었습니다."
        },
        "ok": True
    })
