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
_CONCURRENCY = 15


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

    already = db.query(Device).filter(Device.ip == payload.ip).first() is not None
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
