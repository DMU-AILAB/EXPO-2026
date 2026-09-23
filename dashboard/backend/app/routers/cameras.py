"""카메라 설정 — **Pi가 원본, 서버 DB는 캐시** (명세 §13.0).

이 라우터는 저장소가 아니라 중계자다. 조회는 Pi를 읽어 캐시를 갱신한 뒤 반환하고,
변경은 검증 후 Pi에 **전체 치환**으로 쓴 다음 성공 응답을 받고서야 캐시를 갱신한다.

이전 구현은 `PATCH /api/cameras/{id}`와 `PUT /api/cameras/{id}/rois`를 불렀는데
**Pi에는 둘 다 없다.** 실제 계약은 `app/services/pi_client.py`에 정리돼 있다.
"""

import asyncio
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any, Optional

import httpx
import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Path as PathParam, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import get_current_user
from ..models import User
from ..models.audio import AudioDeployment
from ..models.camera import Camera
from ..models.device import Device
from ..models.roi import Roi
from ..schemas.camera import CameraResponse, CameraUpdate, DetectionParamsUpdate
from ..services.heartbeat_service import get_buffered_cameras
from ..services.pi_client import PiClient
from ..services.pi_sync import build_roi_payload, merge_camera_profiles, validate_profiles_locally

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/devices", tags=["Cameras"])

CAMERA_ID_REGEX = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

# 명세 §14 — 동시 5개 초과 시 신규 연결 거부.
# 전역 정수 카운터는 증감이 원자적이지 않아 실제로 5를 넘길 수 있다. 세마포어로 센다.
_STREAM_SLOTS = asyncio.Semaphore(5)


def validate_camera_id(camera_id: str = PathParam(...)):
    """camera_id는 URL 경로와 **파일명**(`rois.<id>.json`)에 그대로 들어간다.

    Pi의 `validate_camera_config()`는 id 중복만 보고 문자 구성은 검사하지 않으므로
    (명세 §4), 공백이나 `/`가 섞인 id가 경로를 깨뜨리기 전에 여기서 끊는다.
    """
    if not CAMERA_ID_REGEX.match(camera_id):
        raise HTTPException(
            status_code=400,
            detail={"error": "INVALID_CAMERA_ID",
                    "message": "camera_id는 ^[A-Za-z0-9_-]{1,32}$ 형식이어야 합니다"},
        )
    return camera_id


def verify_etag(device: Device, if_match: Optional[str]):
    """낙관적 잠금 — 대시보드가 낡은 스냅샷으로 현장 편집을 덮어쓰는 것을 막는다.

    이 기기는 AP 모드·캡티브 포털로 **오프라인 현장 설정**을 전제로 만들어져 있어
    (명세 §13.0), 방금 현장에서 바꾼 값을 서버가 모르고 덮는 상황이 실제로 생긴다.
    Pi의 변경 감지는 mtime뿐이라 병합도 버전 비교도 없이 나중에 쓴 쪽이 이긴다.
    """
    if if_match is None:
        raise HTTPException(
            status_code=400,
            detail={"error": "VALIDATION_ERROR",
                    "message": "변경에는 If-Match 헤더(config_etag)가 필요합니다"},
        )
    if device.config_etag and device.config_etag != if_match.strip('"'):
        raise HTTPException(
            status_code=412,
            detail={"error": "ETAG_MISMATCH",
                    "message": "설정이 그 사이 변경되었습니다. 다시 불러온 뒤 시도하세요"},
        )


def _get_device(db: Session, device_id: str) -> Device:
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404,
                            detail={"error": "DEVICE_NOT_FOUND", "message": "디바이스를 찾을 수 없습니다"})
    return device


def _get_camera(db: Session, device_id: str, camera_id: str) -> Camera:
    camera = db.query(Camera).filter(Camera.device_id == device_id,
                                     Camera.id == camera_id).first()
    if not camera:
        raise HTTPException(status_code=404,
                            detail={"error": "CAMERA_NOT_FOUND", "message": "카메라를 찾을 수 없습니다"})
    return camera


# ---------------------------------------------------------------------------
# 오디오 — 서버 파일명 → Pi 로컬 절대경로
# ---------------------------------------------------------------------------

async def ensure_audio_on_pi(db: Session, device: Device, filename: str) -> Optional[str]:
    """mp3를 기기에 올려두고 Pi 로컬 절대경로를 돌려준다. 이미 올렸으면 캐시를 쓴다.

    경로를 서버가 계산할 수 없는 이유: Pi의 업로드 API가 파일명을 새니타이즈하고
    중복이면 `_1`·`_2`를 붙인 뒤 **자기가 정한 절대경로**를 응답에 실어준다.
    """
    if not filename or filename.startswith("/"):
        return filename or None

    cached = db.query(AudioDeployment).filter(
        AudioDeployment.device_id == device.id,
        AudioDeployment.filename == filename,
    ).first()
    if cached:
        return cached.pi_path

    local = Path(settings.audio_dir) / Path(filename).name
    if not local.is_file():
        logger.warning("오디오 파일이 서버에 없습니다: %s", local)
        return None

    pi_path = await PiClient(device.ip).upload_audio(local, filename=Path(filename).name)
    db.add(AudioDeployment(device_id=device.id, filename=filename, pi_path=pi_path))
    db.flush()
    return pi_path


async def sync_rois_to_pi(db: Session, device: Device, camera: Camera) -> dict:
    """해당 카메라의 ROI 전체 + 탐지 파라미터를 Pi에 **한 번에 치환**한다.

    ROI 배열만 쓰고 `conf`·`cooldown`·`debounce`를 빠뜨리면 기존 값이 사라진다(명세 §5).
    """
    rois = db.query(Roi).filter(Roi.device_id == device.id,
                                Roi.camera_id == camera.id).all()

    # 오디오를 먼저 전부 올려두고, 파일명 → 절대경로 표를 만들어 변환에 넘긴다.
    resolved: dict[str, Optional[str]] = {}
    for roi in rois:
        if roi.is_active and roi.audio_file and not roi.audio_file.startswith("/"):
            if roi.audio_file not in resolved:
                resolved[roi.audio_file] = await ensure_audio_on_pi(db, device, roi.audio_file)

    payload = build_roi_payload(
        rois,
        conf_white_cane=camera.conf_white_cane,
        conf_person=camera.conf_person,
        cooldown=camera.cooldown,
        debounce=camera.debounce,
        audio_path_resolver=resolved.get,
    )
    await PiClient(device.ip).put_rois(camera.id, payload)
    return payload


# ---------------------------------------------------------------------------
# 조회 — Pi 스냅샷으로 캐시 갱신 후 반환 (§13.0)
# ---------------------------------------------------------------------------

async def refresh_cameras_from_pi(db: Session, device: Device) -> bool:
    """Pi의 `camera_config.json`을 읽어 서버 캐시를 맞춘다. 성공하면 True.

    **디바이스를 등록한 직후 이걸 하지 않으면 cameras 테이블이 영원히 비어 있다** —
    서버에는 카메라를 만드는 API가 없고, 만들 수도 없다(원본이 Pi다).
    """
    try:
        profiles = await PiClient(device.ip).get_cameras()
    except HTTPException as exc:
        logger.info("카메라 스냅샷 갱신 실패 (%s): %s", device.id, exc.detail)
        return False

    seen: set[str] = set()
    for profile in profiles:
        cam_id = profile.get("id")
        if not cam_id:
            continue
        seen.add(cam_id)
        camera = db.query(Camera).filter(Camera.device_id == device.id,
                                         Camera.id == cam_id).first()
        if camera is None:
            camera = Camera(id=cam_id, device_id=device.id, port=profile.get("port", 8080))
            db.add(camera)
        camera.port = profile.get("port", camera.port)
        camera.capture_preset = profile.get("capture_preset", camera.capture_preset)
        camera.model_variant = profile.get("model_variant", camera.model_variant)
        camera.rotation = profile.get("rotation", camera.rotation)
        camera.require_person = profile.get("require_person_for_trigger", camera.require_person)
        camera.is_active = profile.get("enabled", camera.is_active)

    # Pi에서 사라진 카메라는 캐시에서도 지운다 — 캐시는 스냅샷이지 이력이 아니다.
    for stale in db.query(Camera).filter(Camera.device_id == device.id).all():
        if stale.id not in seen:
            db.delete(stale)

    if not device.config_etag:
        device.config_etag = uuid.uuid4().hex
    db.commit()
    return True


@router.get("/{device_id}/cameras", response_model=dict)
async def get_cameras(device_id: str, db: Session = Depends(get_db),
                      current_user=Depends(get_current_user)):
    device = _get_device(db, device_id)
    fresh = await refresh_cameras_from_pi(db, device)

    runtime = await get_buffered_cameras(device_id)
    cameras = db.query(Camera).filter(Camera.device_id == device_id).all()

    data = []
    for cam in cameras:
        live = runtime.get(cam.id, {})
        data.append({
            "id": cam.id,
            "port": cam.port,
            "capture_preset": cam.capture_preset,
            # Pi에 대응 필드가 없어 서버가 보관만 한다 (명세 §4).
            "fps": cam.fps,
            "fps_applied": False,
            "model_variant": cam.model_variant,
            "rotation": cam.rotation,
            "require_person": cam.require_person,
            "is_active": cam.is_active,
            "roi_count": db.query(Roi).filter(Roi.camera_id == cam.id,
                                              Roi.device_id == device_id).count(),
            # ⚠ Pi의 today_detections는 카메라별이 아니라 기기 전체값이다.
            "today_detections": live.get("today_detections", 0),
            "is_streaming": bool(live.get("is_streaming", False)),
            "current_alert": live.get("current_alert"),
        })

    return {"data": data, "stale": not fresh, "etag": device.config_etag, "ok": True}


# ---------------------------------------------------------------------------
# 변경 — 읽기·병합·전체 치환
# ---------------------------------------------------------------------------

@router.patch("/{device_id}/cameras/{camera_id}", response_model=CameraResponse)
async def update_camera(
    device_id: str,
    camera_update: CameraUpdate,
    camera_id: str = Depends(validate_camera_id),
    if_match: Optional[str] = Header(None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    device = _get_device(db, device_id)
    verify_etag(device, if_match)
    camera = _get_camera(db, device_id, camera_id)

    updates = camera_update.model_dump(exclude_unset=True)
    if not updates:
        return camera

    client = PiClient(device.ip)

    # 열거값의 단일 출처는 Pi다 — 백엔드가 하드코딩하면 모델을 추가할 때 어긋난다(명세 §4).
    await _assert_enum_values(client, updates)

    # 1) 읽고 2) 백엔드가 아는 필드만 덮고 3) 목록 전체를 되돌려준다.
    pi_profiles = await client.get_cameras()
    try:
        merged = merge_camera_profiles(pi_profiles, camera_id, updates)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail={"error": "CAMERA_NOT_FOUND",
                    "message": f"기기에 '{camera_id}' 카메라 프로필이 없습니다"},
        )

    # Pi는 유효성 오류가 있으면 **갱신 전체를 무시**한다 — 먼저 같은 규칙으로 거른다.
    errors = validate_profiles_locally(merged)
    if errors:
        raise HTTPException(status_code=400,
                            detail={"error": "VALIDATION_ERROR", "message": errors})

    await client.put_cameras(merged)

    # Pi가 받아들인 뒤에만 캐시를 갱신한다.
    for key, value in updates.items():
        setattr(camera, key, value)
    device.config_etag = str(uuid.uuid4())
    db.commit()
    db.refresh(camera)
    return camera


async def _assert_enum_values(client: PiClient, updates: dict[str, Any]) -> None:
    """`capture_preset`·`model_variant`가 그 기기가 아는 값인지 Pi에 물어 확인한다."""
    if "model_variant" in updates and updates["model_variant"] is not None:
        variants = await client.get_model_variants()
        keys = {v.get("key") for v in variants.get("variants", [])}
        if keys and updates["model_variant"] not in keys:
            raise HTTPException(
                status_code=400,
                detail={"error": "VALIDATION_ERROR",
                        "message": f"model_variant가 기기의 목록에 없습니다: {sorted(keys)}"},
            )
    if "capture_preset" in updates and updates["capture_preset"] is not None:
        presets = await client.get_capture_presets()
        keys = {p.get("key") for p in presets.get("presets", [])}
        if keys and updates["capture_preset"] not in keys:
            raise HTTPException(
                status_code=400,
                detail={"error": "VALIDATION_ERROR",
                        "message": f"capture_preset이 기기의 목록에 없습니다: {sorted(keys)}"},
            )


# ---------------------------------------------------------------------------
# 탐지 파라미터 — Pi에서는 rois.json 최상위에 산다
# ---------------------------------------------------------------------------

@router.get("/{device_id}/cameras/{camera_id}/detection-params", response_model=dict)
def get_detection_params(
    device_id: str,
    camera_id: str = Depends(validate_camera_id),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    camera = _get_camera(db, device_id, camera_id)
    return {
        "data": {
            "conf": {"white_cane": camera.conf_white_cane, "person": camera.conf_person},
            "cooldown": camera.cooldown,
            "debounce": camera.debounce,
        },
        "ok": True,
    }


@router.patch("/{device_id}/cameras/{camera_id}/detection-params", response_model=dict)
async def update_detection_params(
    device_id: str,
    params: DetectionParamsUpdate,
    camera_id: str = Depends(validate_camera_id),
    if_match: Optional[str] = Header(None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    device = _get_device(db, device_id)
    verify_etag(device, if_match)
    camera = _get_camera(db, device_id, camera_id)

    update_data = params.model_dump(exclude_unset=True)
    conf = update_data.get("conf")
    if isinstance(conf, dict):
        if conf.get("white_cane") is not None:
            camera.conf_white_cane = conf["white_cane"]
        if conf.get("person") is not None:
            camera.conf_person = conf["person"]
    if update_data.get("cooldown") is not None:
        camera.cooldown = update_data["cooldown"]
    if update_data.get("debounce") is not None:
        camera.debounce = update_data["debounce"]

    db.flush()
    # ROI 배열과 같은 파일에 살기 때문에 한 번에 전체를 다시 쓴다.
    await sync_rois_to_pi(db, device, camera)

    device.config_etag = str(uuid.uuid4())
    db.commit()

    return {
        "data": {
            "conf": {"white_cane": camera.conf_white_cane, "person": camera.conf_person},
            "cooldown": camera.cooldown,
            "debounce": camera.debounce,
        },
        "ok": True,
    }


# ---------------------------------------------------------------------------
# MJPEG 프록시
# ---------------------------------------------------------------------------

async def get_current_user_or_query(
    authorization: Optional[str] = Header(None),
    token: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """`<img src>`는 헤더를 붙일 수 없어 쿼리 토큰도 받는다."""
    jwt_token = None
    if authorization and authorization.startswith("Bearer "):
        jwt_token = authorization.split(" ", 1)[1]
    elif token:
        jwt_token = token

    if not jwt_token:
        raise HTTPException(status_code=401, detail={"error": "UNAUTHORIZED", "message": "인증이 필요합니다"})

    try:
        payload = jwt.decode(jwt_token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        username = payload.get("sub")
        if not username:
            raise ValueError("sub 없음")
        user = db.query(User).filter(User.username == username).first()
        if not user:
            raise ValueError("사용자 없음")
        return user
    except Exception:
        raise HTTPException(status_code=401, detail={"error": "UNAUTHORIZED", "message": "토큰이 유효하지 않습니다"})


@router.get("/{device_id}/cameras/{camera_id}/stream")
async def proxy_mjpeg_stream(
    device_id: str,
    camera_id: str = Depends(validate_camera_id),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user_or_query),
):
    device = _get_device(db, device_id)
    camera = _get_camera(db, device_id, camera_id)

    if _STREAM_SLOTS.locked():
        raise HTTPException(status_code=503,
                            detail={"error": "STREAM_CAPACITY_FULL",
                                    "message": "동시 스트림 5개를 초과했습니다"})

    target = f"http://{device.ip}:{camera.port}/stream.mjpg"
    # boundary는 기기 응답에서 받아 그대로 쓴다 — 'frame'으로 하드코딩하면 Pi가
    # 다른 값을 쓸 때 브라우저가 프레임을 못 자른다.
    content_type = "multipart/x-mixed-replace; boundary=frame"

    async def generator():
        async with _STREAM_SLOTS:
            client = httpx.AsyncClient(follow_redirects=False)
            try:
                async with client.stream("GET", target, timeout=None) as response:
                    if response.status_code != 200:
                        logger.warning("스트림 응답 %s: %s", response.status_code, target)
                        return
                    async for chunk in response.aiter_bytes():
                        yield chunk
            except asyncio.CancelledError:
                raise                      # 클라이언트가 끊은 것 — 정상 종료 경로다
            except httpx.HTTPError as exc:
                logger.info("스트림 중단 (%s): %s", target, exc)
            finally:
                await client.aclose()

    return StreamingResponse(generator(), media_type=content_type)
