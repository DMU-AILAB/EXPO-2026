"""ROI — **Pi가 원본, 서버 DB는 캐시** (명세 §13.0).

Pi의 저장 API는 배열을 통째로 받으므로, 한 건을 고치든 지우든 **그 카메라의 ROI 전체 +
`conf`·`cooldown`·`debounce`를 한 번에** 다시 쓴다. 그래서 CRUD 세 경로가 전부 같은
`sync_rois_to_pi()`로 수렴한다.

**기기가 오프라인이면 변경을 실패시킨다(503).** 큐에 쌓아 나중에 적용하지 않는다 —
그 순간 사실상 서버가 원본이 되어, Pi의 변경 감지가 mtime뿐이라는 문제가 그대로
드러난다(현장 편집이 나중 쓰기에 덮여 사라진다).
"""

import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models.camera import Camera
from ..models.device import Device
from ..models.roi import Roi
from ..schemas.roi import RoiCreate, RoiResponse, RoiUpdate
from ..services.pi_client import PiClient
from ..services.pi_sync import pi_roi_to_server
from .cameras import (_get_camera, _get_device, sync_rois_to_pi, validate_camera_id,
                      verify_etag)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/devices", tags=["ROIs"])


def _roi_dict(r: Roi) -> dict:
    return {
        "id": r.id,
        "device_id": r.device_id,
        "camera_id": r.camera_id,
        "name": r.name,
        "zone_type": r.zone_type,
        "priority": r.priority,
        "announcement_text": r.announcement_text,
        "audio_file": r.audio_file,
        "polygon": json.loads(r.polygon) if isinstance(r.polygon, str) else r.polygon,
        "color": r.color,
        "is_active": r.is_active,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


async def refresh_rois_from_pi(db: Session, device: Device, camera: Camera) -> bool:
    """Pi의 `rois.json`을 읽어 캐시를 맞춘다. 성공하면 True.

    **ROI의 식별자는 서버에서 `id`, Pi에서 `이름`이다.** 그래서 이름으로 대조한다.
    Pi에 없는 이름은 캐시에서 지우고, 서버에만 있는 **비활성 ROI는 남긴다** —
    비활성은 애초에 Pi로 내려보내지 않는 서버 전용 상태라, 지우면 꺼둔 ROI가
    조회 한 번에 사라진다.
    """
    try:
        data = await PiClient(device.ip).get_rois(camera.id)
    except HTTPException as exc:
        logger.info("ROI 스냅샷 갱신 실패 (%s/%s): %s", device.id, camera.id, exc.detail)
        return False

    by_name = {}
    for item in data.get("rois", []) or []:
        converted = pi_roi_to_server(item)
        if converted["name"]:
            by_name[converted["name"]] = converted

    existing = db.query(Roi).filter(Roi.device_id == device.id,
                                    Roi.camera_id == camera.id).all()
    for roi in existing:
        incoming = by_name.pop(roi.name, None)
        if incoming is None:
            if roi.is_active:
                db.delete(roi)          # 기기에서 사라진 활성 ROI
            continue
        roi.polygon = json.dumps(incoming["polygon"])
        roi.priority = incoming["priority"]
        roi.announcement_text = incoming["announcement_text"]
        roi.audio_file = incoming["audio_file"]
        roi.zone_type = incoming["zone_type"]

    for name, incoming in by_name.items():
        db.add(Roi(device_id=device.id, camera_id=camera.id, name=name,
                   polygon=json.dumps(incoming["polygon"]),
                   priority=incoming["priority"],
                   announcement_text=incoming["announcement_text"],
                   audio_file=incoming["audio_file"],
                   zone_type=incoming["zone_type"], is_active=True))

    # 탐지 파라미터도 같은 파일에 있으므로 함께 캐시한다.
    conf = data.get("conf")
    if isinstance(conf, dict):
        camera.conf_white_cane = conf.get("white_cane", camera.conf_white_cane)
        camera.conf_person = conf.get("person", camera.conf_person)
    elif isinstance(conf, (int, float)):
        camera.conf_white_cane = camera.conf_person = float(conf)
    if data.get("cooldown") is not None:
        camera.cooldown = float(data["cooldown"])
    if data.get("debounce") is not None:
        camera.debounce = float(data["debounce"])

    db.commit()
    return True


@router.get("/{device_id}/rois", response_model=dict)
async def get_rois(device_id: str, camera_id: Optional[str] = None,
                   db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    device = _get_device(db, device_id)

    cameras = db.query(Camera).filter(Camera.device_id == device_id)
    if camera_id:
        cameras = cameras.filter(Camera.id == camera_id)

    fresh = True
    for camera in cameras.all():
        if not await refresh_rois_from_pi(db, device, camera):
            fresh = False

    query = db.query(Roi).filter(Roi.device_id == device_id)
    if camera_id:
        query = query.filter(Roi.camera_id == camera_id)

    return {"data": [_roi_dict(r) for r in query.all()],
            "stale": not fresh, "etag": device.config_etag, "ok": True}


async def _write_through(db: Session, device: Device, camera: Camera):
    """Pi에 먼저 쓰고, 성공해야 커밋한다.

    실패하면 롤백해 **서버 캐시와 기기가 갈라지는 것**을 막는다 — 갈라지면 화면의
    값과 실제 동작이 다른데 아무도 모르는 상태가 된다.
    """
    try:
        await sync_rois_to_pi(db, device, camera)
    except HTTPException as exc:
        db.rollback()
        if exc.status_code in (502, 503, 504):
            raise HTTPException(
                status_code=503,
                detail={"error": "DEVICE_OFFLINE",
                        "message": "기기에 연결할 수 없어 설정을 적용하지 못했습니다"},
            ) from exc
        raise
    device.config_etag = str(uuid.uuid4())
    db.commit()


@router.post("/{device_id}/rois", response_model=RoiResponse, status_code=201)
async def create_roi(device_id: str, roi_in: RoiCreate,
                     if_match: Optional[str] = Header(None),
                     db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    device = _get_device(db, device_id)
    verify_etag(device, if_match)
    validate_camera_id(roi_in.camera_id)
    camera = _get_camera(db, device_id, roi_in.camera_id)

    roi = Roi(
        device_id=device_id, camera_id=roi_in.camera_id, name=roi_in.name,
        zone_type=roi_in.zone_type, priority=roi_in.priority,
        announcement_text=roi_in.announcement_text, audio_file=roi_in.audio_file,
        color=roi_in.color, polygon=json.dumps(roi_in.polygon), is_active=roi_in.is_active,
    )
    db.add(roi)
    db.flush()

    await _write_through(db, device, camera)
    db.refresh(roi)
    return roi


@router.patch("/{device_id}/rois/{roi_id}", response_model=RoiResponse)
async def update_roi(device_id: str, roi_id: int, roi_in: RoiUpdate,
                     if_match: Optional[str] = Header(None),
                     db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    device = _get_device(db, device_id)
    verify_etag(device, if_match)

    roi = db.query(Roi).filter(Roi.device_id == device_id, Roi.id == roi_id).first()
    if not roi:
        raise HTTPException(status_code=404,
                            detail={"error": "ROI_NOT_FOUND", "message": "ROI를 찾을 수 없습니다"})
    camera = _get_camera(db, device_id, roi.camera_id)

    # ⚠ 이름은 Pi의 식별자이자 이벤트 로그의 비정규화 키다(`detection_events.roi_name`).
    # 바꾸면 과거 이벤트·통계와의 연결이 끊긴다 — UI가 경고해야 한다(명세 §5).
    update_data = roi_in.model_dump(exclude_unset=True)
    if "polygon" in update_data and update_data["polygon"] is not None:
        update_data["polygon"] = json.dumps(update_data["polygon"])
    for key, value in update_data.items():
        setattr(roi, key, value)
    db.flush()

    await _write_through(db, device, camera)
    db.refresh(roi)
    return roi


@router.delete("/{device_id}/rois/{roi_id}", status_code=204)
async def delete_roi(device_id: str, roi_id: int,
                     if_match: Optional[str] = Header(None),
                     db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    device = _get_device(db, device_id)
    verify_etag(device, if_match)

    roi = db.query(Roi).filter(Roi.device_id == device_id, Roi.id == roi_id).first()
    if not roi:
        raise HTTPException(status_code=404,
                            detail={"error": "ROI_NOT_FOUND", "message": "ROI를 찾을 수 없습니다"})
    camera = _get_camera(db, device_id, roi.camera_id)

    db.delete(roi)
    db.flush()
    # 삭제도 전체 치환으로 반영된다 — Pi의 DELETE /api/rois/{name}을 따로 부를 필요가 없다.
    await _write_through(db, device, camera)
    return None
