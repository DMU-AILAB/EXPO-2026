"""ESP32 pairing, Wi-Fi configuration and Pi-pulled control commands."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, get_device_by_api_key
from ..models import Device
from ..services.esp32_control import (
    Esp32CommandError,
    clear_device,
    complete_command,
    get_pending_command,
    public_command_status,
    queue_wifi_config,
)
from ..services.heartbeat_service import get_buffered_status
from ..services.device_view import is_stale

router = APIRouter(prefix="/api/devices", tags=["ESP32"])


class BindRequest(BaseModel):
    esp32_id: str = Field(min_length=4, max_length=64)


class WifiConfigRequest(BaseModel):
    ssid: str = Field(min_length=1, max_length=32)
    password: str = Field(default="", max_length=63)


class CommandResultRequest(BaseModel):
    state: str
    message: str = ""


@router.get("/{device_id}/esp32")
async def esp32_status(device_id: str, db: Session = Depends(get_db),
                       current_user=Depends(get_current_user)):
    device = _get_device(db, device_id)
    buffered = await get_buffered_status(device_id)
    return {
        "data": {
            "binding": device.esp32_device_id,
            "status": (buffered or {}).get("esp32"),
            "command": public_command_status(device_id),
            "stale": is_stale((buffered or {}).get("updated_at")),
        },
        "ok": True,
    }


@router.post("/{device_id}/esp32/binding")
async def bind_esp32(device_id: str, payload: BindRequest,
                     db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    device = _get_device(db, device_id)
    buffered = await get_buffered_status(device_id)
    esp32_status = (buffered or {}).get("esp32") or {}
    if is_stale((buffered or {}).get("updated_at")):
        raise HTTPException(status_code=409, detail="Pi의 ESP32 검색 상태가 오래됐습니다. Pi 연결을 확인하고 다시 시도하세요.")
    candidates = esp32_status.get("candidates") or []
    if not any(isinstance(item, dict) and item.get("device_id") == payload.esp32_id
               for item in candidates):
        raise HTTPException(status_code=409, detail="ESP32가 최근 발견 목록에 없습니다. Pi와 가까이 두고 다시 검색하세요.")
    owner = db.query(Device).filter(
        Device.esp32_device_id == payload.esp32_id,
        Device.id != device.id,
    ).first()
    if owner:
        raise HTTPException(status_code=409, detail="이 ESP32는 이미 다른 Pi에 연결 승인되어 있습니다.")
    if device.esp32_device_id != payload.esp32_id:
        clear_device(device_id)
    device.esp32_device_id = payload.esp32_id
    db.commit()
    return {"data": {"binding": device.esp32_device_id}, "ok": True}


@router.delete("/{device_id}/esp32/binding")
def unbind_esp32(device_id: str, db: Session = Depends(get_db),
                 current_user=Depends(get_current_user)):
    device = _get_device(db, device_id)
    device.esp32_device_id = None
    db.commit()
    clear_device(device_id)
    return {"data": {"binding": None}, "ok": True}


@router.post("/{device_id}/esp32/wifi", status_code=202)
def configure_esp32_wifi(device_id: str, payload: WifiConfigRequest,
                         db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    device = _get_device(db, device_id)
    if not device.esp32_device_id:
        raise HTTPException(status_code=409, detail="먼저 대시보드에서 ESP32 연결을 승인하세요.")
    ssid = payload.ssid.strip()
    if not ssid or len(ssid.encode("utf-8")) > 32:
        raise HTTPException(status_code=422, detail="SSID는 UTF-8 기준 1~32바이트여야 합니다.")
    if payload.password and len(payload.password) < 8:
        raise HTTPException(status_code=422, detail="비밀번호는 8자 이상이어야 합니다.")
    try:
        command = queue_wifi_config(device_id, ssid, payload.password)
    except Esp32CommandError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"data": command, "ok": True}


@router.get("/me/esp32/control")
def esp32_control(device: Device = Depends(get_device_by_api_key)):
    """Pi-initiated pull keeps working when the Pi is behind NAT."""
    return {
        "data": {
            "binding": device.esp32_device_id,
            "command": get_pending_command(device.id),
        },
        "ok": True,
    }


@router.post("/me/esp32/commands/{command_id}/result")
def esp32_command_result(command_id: str, payload: CommandResultRequest,
                         device: Device = Depends(get_device_by_api_key)):
    if not complete_command(device.id, command_id, payload.state, payload.message):
        raise HTTPException(status_code=404, detail="명령이 없거나 이미 만료됐습니다.")
    return {"ok": True}


def _get_device(db: Session, device_id: str) -> Device:
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="기기를 찾을 수 없습니다.")
    return device
