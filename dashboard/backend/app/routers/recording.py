"""녹화 — Pi 카메라 MJPEG 포트의 `/recording/*` 중계.

녹화는 프레임을 쥔 탐지 프로세스(`camera_live_pi.py`)에만 있어서 roi_editor(5000)가
아니라 **카메라마다 다른 포트**로 간다. 그래서 `PiClient(device.ip, camera.port)`다.

클립 파일은 `<video src>`/`<img src>`로 열리므로 헤더를 붙일 수 없어 쿼리 토큰을
받는다(MJPEG 프록시와 같은 방식, `cameras.get_current_user_or_query`).
"""

import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..services.pi_client import PiClient
from .cameras import _get_camera, _get_device, get_current_user_or_query, validate_camera_id

router = APIRouter(prefix="/api/devices", tags=["Recording"])

# Pi의 `ClipRecorder._ID_RE`와 같은 규칙 + 확장자 — 경로 탈출을 여기서 먼저 끊는다.
CLIP_FILE_RE = re.compile(r"^clip_\d{8}_\d{6}\.(mp4|jpg)$")


def _camera_client(db: Session, device_id: str, camera_id: str) -> PiClient:
    device = _get_device(db, device_id)
    camera = _get_camera(db, device_id, camera_id)
    return PiClient(device.ip, camera.port)


@router.get("/{device_id}/cameras/{camera_id}/recording")
async def recording_status(device_id: str, camera_id: str = Depends(validate_camera_id),
                           db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return {"data": await _camera_client(db, device_id, camera_id).recording_status(), "ok": True}


@router.post("/{device_id}/cameras/{camera_id}/recording/start")
async def recording_start(device_id: str, raw: bool = False,
                          camera_id: str = Depends(validate_camera_id),
                          db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """`raw=true`는 학습용 촬영 — 오버레이가 그려진 프레임으로 학습하면 모델이 박스를
    단서로 배우므로 학습 데이터는 반드시 이쪽으로 찍는다."""
    return {"data": await _camera_client(db, device_id, camera_id).recording_start(raw), "ok": True}


@router.post("/{device_id}/cameras/{camera_id}/recording/stop")
async def recording_stop(device_id: str, camera_id: str = Depends(validate_camera_id),
                         db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return {"data": await _camera_client(db, device_id, camera_id).recording_stop(), "ok": True}


@router.get("/{device_id}/cameras/{camera_id}/recording/clips")
async def recording_list(device_id: str, camera_id: str = Depends(validate_camera_id),
                         db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    clips = await _camera_client(db, device_id, camera_id).recording_list()
    return {"data": clips, "total": len(clips), "ok": True}


@router.get("/{device_id}/cameras/{camera_id}/recording/clips/{filename}")
async def recording_clip_file(device_id: str, filename: str,
                              camera_id: str = Depends(validate_camera_id),
                              db: Session = Depends(get_db),
                              current_user=Depends(get_current_user_or_query)):
    if not CLIP_FILE_RE.match(filename):
        raise HTTPException(status_code=400,
                            detail={"error": "INVALID_FILENAME", "message": "잘못된 클립 파일명입니다"})
    client = _camera_client(db, device_id, camera_id)
    _status, content_type, length, body = await client.stream_file(f"/recording/clips/{filename}")
    headers = {"Content-Length": length} if length else {}
    if filename.endswith(".mp4"):
        headers["Content-Disposition"] = f'inline; filename="{filename}"'
    return StreamingResponse(body, media_type=content_type, headers=headers)
