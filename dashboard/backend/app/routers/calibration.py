"""오탐 관리 — 구조물 마스크·오탐 지점 제안·폐장 시간 수집의 중계.

세 가지 모두 **제안-확인** 구조다(CLAUDE.md "구조물 마스크", "오탐지 핫스팟"):
Pi가 후보를 모으고 운영자가 골라 적용한다. **자동 적용 경로를 만들지 말 것** —
수집 중 청소 인력이 지나가면 그 자리가 구조물로 굳어 사각지대가 된다.

- 마스크 후보·적용·적중 기록, 오탐 지점: roi_editor(5000), `?camera=<id>`
- 수집 시작·취소·상태: 카메라 MJPEG 포트(`/calibrate/*`) — 프레임을 쥔 프로세스만 할 수 있다
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..services.calibration_service import invalidate_active_cache
from ..services.pi_client import PiClient
from .cameras import _get_camera, _get_device, get_current_user_or_query, validate_camera_id

router = APIRouter(prefix="/api/devices", tags=["Calibration"])


class CalibrationStartRequest(BaseModel):
    seconds: float = Field(300, ge=10, le=1800)   # Pi가 같은 범위로 자른다


class MaskApplyRequest(BaseModel):
    ids: list[int]   # 선택한 후보만 남는다 — 빈 목록이면 마스크 전부 해제


def _editor(db: Session, device_id: str, camera_id: str) -> PiClient:
    _get_camera(db, device_id, camera_id)   # 서버가 모르는 카메라를 Pi에 묻지 않는다
    return PiClient(_get_device(db, device_id).ip)


def _camera_port(db: Session, device_id: str, camera_id: str) -> PiClient:
    device = _get_device(db, device_id)
    return PiClient(device.ip, _get_camera(db, device_id, camera_id).port)


# ------------------------------------------------------------------ 수집

@router.get("/{device_id}/cameras/{camera_id}/calibration")
async def calibration_status(device_id: str, camera_id: str = Depends(validate_camera_id),
                             db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return {"data": await _camera_port(db, device_id, camera_id).calibration_status(), "ok": True}


@router.post("/{device_id}/cameras/{camera_id}/calibration/start")
async def calibration_start(device_id: str, payload: CalibrationStartRequest,
                            camera_id: str = Depends(validate_camera_id),
                            db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    client = _camera_port(db, device_id, camera_id)
    result = await client.calibration_start(payload.seconds)
    invalidate_active_cache()
    return {"data": result, "ok": True}


@router.post("/{device_id}/cameras/{camera_id}/calibration/cancel")
async def calibration_cancel(device_id: str, camera_id: str = Depends(validate_camera_id),
                             db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    result = await _camera_port(db, device_id, camera_id).calibration_cancel()
    invalidate_active_cache()
    return {"data": result, "ok": True}


# ------------------------------------------------------------------ 구조물 마스크

@router.get("/{device_id}/cameras/{camera_id}/static-mask")
async def mask_candidates(device_id: str, camera_id: str = Depends(validate_camera_id),
                          db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """후보마다 `applied`(현재 켜짐)·`recommend`(지팡이=기본 선택, 사람=기본 해제)가 붙어 온다."""
    rows = await _editor(db, device_id, camera_id).mask_candidates(camera_id)
    return {"data": rows, "total": len(rows), "ok": True}


@router.put("/{device_id}/cameras/{camera_id}/static-mask")
async def mask_apply(device_id: str, payload: MaskApplyRequest,
                     camera_id: str = Depends(validate_camera_id),
                     db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    client = _editor(db, device_id, camera_id)
    return {"data": await client.mask_apply(camera_id, payload.ids), "ok": True}


@router.delete("/{device_id}/cameras/{camera_id}/static-mask")
async def mask_clear(device_id: str, camera_id: str = Depends(validate_camera_id),
                     db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return {"data": await _editor(db, device_id, camera_id).mask_clear(camera_id), "ok": True}


@router.get("/{device_id}/cameras/{camera_id}/static-mask/hits")
async def mask_hits(device_id: str, camera_id: str = Depends(validate_camera_id),
                    db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """마스크가 걸러낸 트랙 수 — 사람 적중이 늘면 그 마스크가 진짜 사람을 가리고 있다."""
    return {"data": await _editor(db, device_id, camera_id).mask_hits(camera_id), "ok": True}


@router.get("/{device_id}/cameras/{camera_id}/static-mask/thumb")
async def mask_thumb(device_id: str, name: str, camera_id: str = Depends(validate_camera_id),
                     db: Session = Depends(get_db), current_user=Depends(get_current_user_or_query)):
    # Pi와 같은 규칙(`get_static_mask_thumb`) — 파일명만, .jpg만.
    if "/" in name or "\\" in name or not name.endswith(".jpg"):
        raise HTTPException(status_code=400,
                            detail={"error": "INVALID_FILENAME", "message": "잘못된 파일명입니다"})
    client = _editor(db, device_id, camera_id)
    _status, content_type, length, body = await client.stream_file(
        "/api/static-mask/thumb", params={"name": name, "camera": camera_id})
    headers = {"Content-Length": length} if length else {}
    return StreamingResponse(body, media_type=content_type, headers=headers)


# ------------------------------------------------------------------ 오탐 지점

@router.get("/{device_id}/cameras/{camera_id}/fp-hotspots")
async def fp_hotspots(device_id: str, min_count: int = 30, limit: int = 5,
                      camera_id: str = Depends(validate_camera_id),
                      db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    rows = await _editor(db, device_id, camera_id).fp_hotspots(camera_id, min_count, limit)
    return {"data": rows, "total": len(rows), "ok": True}


@router.delete("/{device_id}/cameras/{camera_id}/fp-hotspots")
async def fp_hotspots_clear(device_id: str, camera_id: str = Depends(validate_camera_id),
                            db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return {"data": await _editor(db, device_id, camera_id).fp_hotspots_clear(camera_id), "ok": True}
