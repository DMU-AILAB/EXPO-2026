"""검증 재생 — roi_editor `/api/replay/*` 중계.

저장된 영상을 **배포와 같은 게이트 체인**으로 재생해 "이 ROI면 안내가 나갔을까"를
화면으로 확인한다(`device/replay_engine.py`). ROI는 Pi의 현재 `rois.json`을 그대로
쓰므로 ROI 탭에서 저장한 직후 여기서 확인하는 흐름이 된다.

재생 세션은 Pi에 **하나뿐**이다 — 새로 시작하면 이전 세션이 멈춘다(Pi 쪽 동작).
"""

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..services.pi_client import ROI_EDITOR_PORT, PiClient
from ..services.stream_fanout import STREAM_CONTENT_TYPE, stream_fanout
from .cameras import _get_device, get_current_user_or_query

router = APIRouter(prefix="/api/devices", tags=["Replay"])


class ReplayStartRequest(BaseModel):
    """Pi의 `ReplayStart`와 같은 필드. 기본값은 Pi가 정하도록 보내지 않는다."""
    video: str = Field(..., min_length=1)
    conf: Optional[float] = Field(None, ge=0.05, le=0.95)
    model_variant: Optional[str] = None
    require_person: Optional[bool] = None
    speed: Optional[float] = Field(None, gt=0, le=8)
    loop: Optional[bool] = None
    debug_gates: Optional[bool] = None


class ReplayPauseRequest(BaseModel):
    paused: Optional[bool] = None   # None이면 토글


def _client(db: Session, device_id: str) -> PiClient:
    return PiClient(_get_device(db, device_id).ip)


@router.get("/{device_id}/replay/videos")
async def replay_videos(device_id: str, db: Session = Depends(get_db),
                        current_user=Depends(get_current_user)):
    videos = await _client(db, device_id).replay_videos()
    return {"data": videos, "total": len(videos), "ok": True}


@router.post("/{device_id}/replay/start")
async def replay_start(device_id: str, payload: ReplayStartRequest,
                       db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    body = payload.model_dump(exclude_none=True)
    return {"data": await _client(db, device_id).replay_start(body), "ok": True}


@router.post("/{device_id}/replay/pause")
async def replay_pause(device_id: str, payload: ReplayPauseRequest,
                       db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return {"data": await _client(db, device_id).replay_pause(payload.paused), "ok": True}


@router.post("/{device_id}/replay/step")
async def replay_step(device_id: str, db: Session = Depends(get_db),
                      current_user=Depends(get_current_user)):
    return {"data": await _client(db, device_id).replay_step(), "ok": True}


@router.post("/{device_id}/replay/stop")
async def replay_stop(device_id: str, db: Session = Depends(get_db),
                      current_user=Depends(get_current_user)):
    return {"data": await _client(db, device_id).replay_stop(), "ok": True}


@router.get("/{device_id}/replay/status")
async def replay_status(device_id: str, db: Session = Depends(get_db),
                        current_user=Depends(get_current_user)):
    return {"data": await _client(db, device_id).replay_status(), "ok": True}


@router.get("/{device_id}/replay/stream")
async def replay_stream(device_id: str, db: Session = Depends(get_db),
                        current_user=Depends(get_current_user_or_query)):
    """라이브 스트림과 같은 팬아웃을 쓴다 — 여러 탭이 열어도 Pi 연결은 하나."""
    device = _get_device(db, device_id)
    target = f"http://{device.ip}:{ROI_EDITOR_PORT}/api/replay/stream.mjpg"
    channel, queue = await stream_fanout.subscribe(target)

    async def generator():
        try:
            while True:
                yield await queue.get()
        except asyncio.CancelledError:
            raise
        finally:
            await stream_fanout.unsubscribe(channel, queue)

    return StreamingResponse(generator(), media_type=STREAM_CONTENT_TYPE)
