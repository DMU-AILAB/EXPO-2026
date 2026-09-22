from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
import asyncio
import httpx
import uuid
from typing import Dict, Any
from pydantic import BaseModel

from ..deps import get_current_user

router = APIRouter(prefix="/api/scan", tags=["scan"])

# 글로벌 인메모리 스캔 결과 저장소
_scan_results: Dict[str, Dict[str, Any]] = {}

class ScanNetworkRequest(BaseModel):
    subnet: str
    port: int = 5000

class ScanVerifyRequest(BaseModel):
    ip: str
    port: int = 5000

async def _ping_ip(ip: str, port: int, sem: asyncio.Semaphore) -> dict | None:
    """단일 IP에 대해 비동기 Ping 수행"""
    # Step 1, 2 방어: Semaphore 획득 (동시 소켓 개수 제한)
    async with sem:
        # Step 3 방어: 세밀한 Fail-fast 타임아웃 튜닝
        timeout = httpx.Timeout(connect=0.5, read=2.0, write=1.0, pool=1.0)
        
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(f"http://{ip}:{port}/api/device/status")
                
                if response.status_code == 200:
                    data = response.json()
                    return {
                        "ip": ip,
                        "hostname": data.get("hostname", f"visionguide-{ip.split('.')[-1]}.local"),
                        "port": port,
                        "version": data.get("version", "VisionGuide v1.2"), # Pi 측 API 부재로 임시 하드코딩
                        "already_registered": False # 실제 구현 시 DB 대조 필요
                    }
        except (httpx.RequestError, httpx.TimeoutException):
            pass # 응답 없거나 타임아웃 시 무시 (Fail-fast)
            
        return None

async def _run_network_scan(scan_id: str, subnet: str, port: int):
    """백그라운드에서 서브넷 전체를 스캔"""
    base_ip = ".".join(subnet.split(".")[:3])
    ips = [f"{base_ip}.{i}" for i in range(1, 255)]
    
    # 동시 접속 수 제한 (공유기 NAT 테이블 병목 및 MJPEG 스트림 간섭 예방)
    sem = asyncio.Semaphore(15)
    
    _scan_results[scan_id]["status"] = "running"
    
    tasks = []
    for ip in ips:
        tasks.append(_ping_ip(ip, port, sem))
    
    # 병렬 실행 (gather)
    results = await asyncio.gather(*tasks)
    
    discovered = [res for res in results if res is not None]
    
    _scan_results[scan_id]["status"] = "completed"
    _scan_results[scan_id]["progress"] = 254
    _scan_results[scan_id]["discovered"] = discovered

@router.post("/network", status_code=202)
async def scan_network(payload: ScanNetworkRequest, background_tasks: BackgroundTasks, current_user = Depends(get_current_user)):
    scan_id = f"scan-{uuid.uuid4().hex[:8]}"
    
    _scan_results[scan_id] = {
        "scan_id": scan_id,
        "status": "pending",
        "progress": 0,
        "total": 254,
        "discovered": []
    }
    
    # 백그라운드 태스크로 넘기고 프론트엔드엔 즉시 202 반환 (Non-blocking)
    background_tasks.add_task(_run_network_scan, scan_id, payload.subnet, payload.port)
    
    return {
        "data": {
            "scan_id": scan_id,
            "status": "running"
        },
        "ok": True
    }

@router.get("/{scan_id}")
async def get_scan_status(scan_id: str, current_user = Depends(get_current_user)):
    result = _scan_results.get(scan_id)
    if not result:
        raise HTTPException(status_code=404, detail={"error": "SCAN_NOT_FOUND", "ok": False})
        
    return {"data": result, "ok": True}

@router.post("/verify")
async def verify_device(payload: ScanVerifyRequest, current_user = Depends(get_current_user)):
    # 수동 검증용 (세마포어 없이 1건 즉시 실행)
    sem = asyncio.Semaphore(1)
    res = await _ping_ip(payload.ip, payload.port, sem)
    
    if res:
        return {
            "data": {
                "reachable": True,
                "hostname": res["hostname"],
                "version": res["version"],
                "camera_count": 1
            },
            "ok": True
        }
    else:
        return {"data": {"reachable": False}, "ok": True}
