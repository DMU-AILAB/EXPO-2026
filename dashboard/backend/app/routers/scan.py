"""네트워크 탐색 — 로컬 망에서 VisionGuide 기기를 찾아 등록을 돕는다 (명세 §12).

**"404가 아니면 있음" 식으로 판별하면 안 된다.** Pi에는 Wi-Fi 온보딩용 캡티브 포털
catch-all이 있어 존재하지 않는 경로도 조건에 따라 302를 반환한다. 판별은
`GET /api/version`의 200 + `product == "VisionGuide"`로 한다
(`app/services/pi_client.probe()`).
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models.device import Device
from ..services.pi_client import PiClient, probe

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scan", tags=["scan"])

# 스캔 결과는 프로세스 메모리에만 둔다 — 재시작하면 사라져도 되는, 등록 중 한 번
# 쓰고 버리는 값이다.
_scan_results: Dict[str, Dict[str, Any]] = {}

# 공유기 NAT 테이블 병목과 MJPEG 스트림 간섭을 피하려고 동시 접속을 묶는다.
# 15 이상이면 /24 스캔이 살아 있는 기기를 **무작위로 놓친다**(실측, Wi-Fi + WSL 미러 네트워크:
# 기기 2대 중 1대만 찾은 스캔이 반복됐다). 8에서는 같은 조건으로 3회 모두 2대를 찾았다
# (/24 약 33초). 속도를 올리려고 키우기 전에 같은 방식으로 재현율부터 잴 것.
_CONCURRENCY = 8


class ScanNetworkRequest(BaseModel):
    subnet: str
    port: int = 5000


class ScanVerifyRequest(BaseModel):
    ip: str
    port: int = 5000


def _hosts(subnet: str) -> list[str]:
    """CIDR을 제대로 파싱한다 — 이전에는 앞 3옥텟만 떼어 /24로 가정했다."""
    try:
        network = ipaddress.ip_network(subnet, strict=False)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "VALIDATION_ERROR", "message": f"subnet 형식이 잘못되었습니다: {exc}"},
        ) from exc
    if network.num_addresses > 4096:
        raise HTTPException(
            status_code=400,
            detail={"error": "VALIDATION_ERROR",
                    "message": "한 번에 스캔할 수 있는 범위는 4096개 주소까지입니다"},
        )
    return [str(h) for h in network.hosts()]


async def _probe_one(ip: str, port: int, sem: asyncio.Semaphore,
                     state: dict) -> Optional[dict]:
    async with sem:
        try:
            found = await probe(ip, port)
        finally:
            # 진행률은 **응답이 올 때마다** 올린다. 이전에는 0에 머물다 끝나는 순간
            # 254로 뛰어서 진행 표시줄이 의미가 없었다.
            state["progress"] += 1
        if not found:
            return None
        return {
            "ip": ip,
            "hostname": None,          # Pi가 자기 호스트명을 노출하지 않는다
            "port": port,
            "version": found.get("version"),
            "registered": found.get("registered"),
            "device_id": found.get("device_id"),
            "already_registered": False,   # 호출부가 DB와 대조해 채운다
        }


async def _run_network_scan(scan_id: str, subnet: str, port: int, known_ips: set[str]):
    state = _scan_results[scan_id]
    try:
        hosts = _hosts(subnet)
    except HTTPException as exc:
        state["status"] = "failed"
        state["error"] = exc.detail
        return

    state.update(status="running", total=len(hosts), progress=0)
    sem = asyncio.Semaphore(_CONCURRENCY)
    results = await asyncio.gather(*(_probe_one(ip, port, sem, state) for ip in hosts))

    discovered = []
    for item in results:
        if item is None:
            continue
        item["already_registered"] = item["ip"] in known_ips
        discovered.append(item)

    state["status"] = "completed"
    state["discovered"] = discovered


@router.post("/network", status_code=202)
async def scan_network(payload: ScanNetworkRequest, background_tasks: BackgroundTasks,
                       db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _hosts(payload.subnet)            # 형식 오류는 202를 주기 전에 걸러낸다

    scan_id = f"scan-{uuid.uuid4().hex[:8]}"
    _scan_results[scan_id] = {
        "scan_id": scan_id, "status": "pending", "progress": 0,
        "total": 0, "discovered": [],
    }

    # 이미 등록된 기기의 IP를 미리 떠 둔다 — 백그라운드 태스크에 DB 세션을 들고
    # 들어가면 요청이 끝난 뒤 닫힌 세션을 쓰게 된다.
    known_ips = {ip for (ip,) in db.query(Device.ip).all()}
    background_tasks.add_task(_run_network_scan, scan_id, payload.subnet,
                              payload.port, known_ips)

    return {"data": {"scan_id": scan_id, "status": "running"}, "ok": True}


def suggest_subnet(public_base_url: str) -> Optional[str]:
    """서버 자신의 주소(`PUBLIC_BASE_URL`)가 속한 /24 대역. 사설 IPv4가 아니면 None.

    기기는 이 주소로 서버에 보고하므로 같은 망에 있을 가능성이 가장 높다 — 운영자가
    대역을 매번 손으로 맞추지 않게 탐색 화면의 기본값으로 쓴다.
    """
    from urllib.parse import urlsplit
    host = urlsplit(public_base_url).hostname or ""
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return None
    if addr.version != 4 or not addr.is_private or addr.is_loopback:
        return None
    return str(ipaddress.ip_network(f"{addr}/24", strict=False))


# ★ "/{scan_id}"보다 먼저 등록해야 한다 — 뒤에 두면 "suggest"가 scan_id로 잡힌다.
@router.get("/suggest")
async def get_suggested_subnet(current_user=Depends(get_current_user)):
    from ..config import settings
    return {"data": {"subnet": suggest_subnet(settings.public_base_url)}, "ok": True}


@router.get("/{scan_id}")
async def get_scan_status(scan_id: str, current_user=Depends(get_current_user)):
    result = _scan_results.get(scan_id)
    if not result:
        raise HTTPException(status_code=404,
                            detail={"error": "SCAN_NOT_FOUND", "message": "스캔 결과가 없습니다"})
    return {"data": result, "ok": True}


@router.post("/verify")
async def verify_device(payload: ScanVerifyRequest, db: Session = Depends(get_db),
                        current_user=Depends(get_current_user)):
    """수동 입력한 주소가 VisionGuide 기기인지 확인한다."""
    found = await probe(payload.ip, payload.port)
    if not found:
        return {"data": {"reachable": False}, "ok": True}

    # 카메라 수는 실제로 물어본다 — 이전에는 1로 하드코딩돼 있었다.
    camera_count = None
    try:
        camera_count = len(await PiClient(payload.ip, payload.port).get_cameras())
    except HTTPException:
        pass

    # IP만 보면 안 된다 — Wi-Fi를 바꿔(블루투스 페어링 등) 주소가 달라진 기기가 하트비트로
    # IP가 갱신되기 전이면 "새 기기"로 보인다. 기기가 스스로 밝힌 device_id도 대조한다.
    already = db.query(Device).filter(Device.ip == payload.ip).first() is not None
    if not already and found.get("device_id"):
        already = db.query(Device).filter(Device.id == found["device_id"]).first() is not None
    return {
        "data": {
            "reachable": True,
            "hostname": None,
            "version": found.get("version"),
            "registered": found.get("registered"),
            "device_id": found.get("device_id"),
            "camera_count": camera_count,
            "already_registered": already,
        },
        "ok": True,
    }
