"""기기 Wi-Fi — roi_editor `/api/network/*` 중계 (이미 망에 있는 기기용).

연결을 바꾸면 **그 순간 기기와의 연결이 끊긴다.** 새 망에서 IP가 바뀌어도 하트비트가
출발 주소로 `device.ip`를 따라가므로(`devices._follow_device_ip`) 다시 붙지만, 새 망에서
이 서버(`PUBLIC_BASE_URL`)에 닿지 않으면 기기는 대시보드에서 오프라인이 된다.

AP 전환(`/api/network/ap`)은 중계하지 않는다 — 원격에서 누르면 기기가 망에서 사라져
대시보드로는 되돌릴 수 없다. 망에 없는 기기의 첫 연결은 블루투스 페어링이 맡는다.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..services.pi_client import PiClient
from .cameras import _get_device

router = APIRouter(prefix="/api/devices", tags=["Network"])


class WifiConnectRequest(BaseModel):
    ssid: str = Field(..., min_length=1, max_length=32)       # SSID 최대 32바이트
    password: str = Field("", max_length=63)                   # WPA2-PSK 최대 63자


def _client(db: Session, device_id: str) -> PiClient:
    return PiClient(_get_device(db, device_id).ip)


@router.get("/{device_id}/network")
async def network_status(device_id: str, db: Session = Depends(get_db),
                         current_user=Depends(get_current_user)):
    return {"data": await _client(db, device_id).network_status(), "ok": True}


@router.get("/{device_id}/network/scan")
async def network_scan(device_id: str, db: Session = Depends(get_db),
                       current_user=Depends(get_current_user)):
    networks = await _client(db, device_id).network_scan()
    return {"data": networks, "total": len(networks), "ok": True}


@router.post("/{device_id}/network/connect", status_code=202)
async def network_connect(device_id: str, payload: WifiConnectRequest,
                          db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Pi는 응답을 먼저 돌려준 뒤 몇 초 후 전환한다 — 응답이 왔다고 연결된 것이 아니다."""
    client = _client(db, device_id)
    return {"data": await client.network_connect(payload.ssid.strip(), payload.password), "ok": True}


@router.get("/{device_id}/network/connect-result")
async def network_connect_result(device_id: str, db: Session = Depends(get_db),
                                 current_user=Depends(get_current_user)):
    return {"data": await _client(db, device_id).network_connect_result(), "ok": True}
