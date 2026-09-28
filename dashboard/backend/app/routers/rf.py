"""RF 리모컨 — 누를 때 재생할 안내 음성 목록 (**Pi가 원본**, 명세 §13.0).

리모컨을 한 번 누르면 목록의 음성을 순서대로 이어서 재생한다. 목록은 Pi의
`rf_config.json`의 `audio_files`(Pi 로컬 절대경로)에 저장되고, `camera_live_pi.py`가
mtime으로 감지해 재시작 없이 반영한다.

항목은 두 종류다.
- `library`: 서버 음성 라이브러리의 파일명 — 필요하면 기기에 올리고 절대경로로 바꾼다.
- `pi`: 기기에 이미 있는 파일의 절대경로 — 그대로 쓴다.
"""

import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models.audio import AudioDeployment
from ..services.pi_client import PiClient
from .cameras import _get_device, ensure_audio_on_pi

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/devices", tags=["RF"])


class RfAudioItem(BaseModel):
    source: Literal["library", "pi"]
    filename: Optional[str] = None
    path: Optional[str] = None


class RfAudioUpdate(BaseModel):
    items: list[RfAudioItem]


@router.get("/{device_id}/rf", response_model=dict)
async def get_rf(device_id: str, db: Session = Depends(get_db),
                 current_user=Depends(get_current_user)):
    device = _get_device(db, device_id)
    pi = PiClient(device.ip)
    rf = await pi.get_rf_config()
    pi_audio = await pi.list_audio()
    return {"data": {"config": rf.get("config", {}), "audio_files": rf.get("audio_files", []),
                     "pi_audio": pi_audio}, "ok": True}


@router.put("/{device_id}/rf/audio", response_model=dict)
async def update_rf_audio(device_id: str, body: RfAudioUpdate, db: Session = Depends(get_db),
                          current_user=Depends(get_current_user)):
    device = _get_device(db, device_id)
    pi = PiClient(device.ip)
    on_pi = {f["path"] for f in await pi.list_audio()}

    paths: list[str] = []
    for item in body.items:
        if item.source == "pi":
            if not item.path:
                raise HTTPException(status_code=422, detail="pi 항목에는 path가 필요합니다")
            paths.append(item.path)
            continue
        if not item.filename:
            raise HTTPException(status_code=422, detail="library 항목에는 filename이 필요합니다")
        cached = db.query(AudioDeployment).filter(AudioDeployment.device_id == device.id,
                                                  AudioDeployment.filename == item.filename)
        if cached.first() is not None and cached.first().pi_path not in on_pi:
            # 캐시된 경로의 파일이 기기에서 지워졌다 — 캐시를 버리고 다시 올린다.
            cached.delete()
            db.flush()
        path = await ensure_audio_on_pi(db, device, item.filename)
        if not path:
            raise HTTPException(status_code=404, detail=f"서버에 음성 파일이 없습니다: {item.filename}")
        paths.append(path)

    result = await pi.put_rf_audio(paths)
    db.commit()
    return {"data": {"audio_files": result.get("audio_files", paths)}, "ok": True}


class RfGroupUpdate(BaseModel):
    group_enabled: bool
    group_priority: int = Field(ge=0, le=9999)


@router.put("/{device_id}/rf/group", response_model=dict)
async def update_rf_group(device_id: str, body: RfGroupUpdate, db: Session = Depends(get_db),
                          current_user=Depends(get_current_user)):
    """같은 누름을 들은 기기들이 이 우선순위 순서(작을수록 먼저)로 한 대씩 재생한다."""
    device = _get_device(db, device_id)
    result = await PiClient(device.ip).put_rf_group(body.group_enabled, body.group_priority)
    return {"data": {"group_enabled": result.get("group_enabled", body.group_enabled),
                     "group_priority": result.get("group_priority", body.group_priority)},
            "ok": True}
