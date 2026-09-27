"""디바이스 응답 조립 — 명세 §3의 대시보드 표시용 형태.

값의 출처가 셋이라 한 곳에 모은다.

- `devices` 행 (이름·IP·위치·status·last_seen)
- 하트비트 **인메모리 버퍼** (가장 최신, 최대 60초)
- `device_status_cache` (버퍼가 비었을 때의 폴백 — 재시작 직후 등)

단위 변환도 여기서만 한다. Pi는 메모리를 **MB**로 보내고(명세 §3), 프런트는 GB로
보여주고 싶어 하지만, API 경계에서는 명세대로 MB를 유지하고 변환은 화면이 한다 —
경계에서 바꾸면 `memory.used`가 무슨 단위인지 코드마다 달라진다.
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from ..models.device import Device, DeviceStatusCache
from ..utils.timeutil import format_uptime, utcnow

__all__ = ["build_status_payload", "build_device_summary", "effective_status",
           "is_stale", "OFFLINE_AFTER_SEC"]

# 하트비트는 15초 주기다(`device/event_logger.py:258`). 네 번을 놓치면 죽은 것으로 본다 —
# 한 번만 놓쳐도 오프라인으로 뒤집으면 일시적 패킷 손실에 배지가 깜빡인다.
OFFLINE_AFTER_SEC = 60


def _from_cache(cached: Optional[DeviceStatusCache]) -> dict:
    if cached is None:
        return {}
    return {
        "load_avg": [cached.load_avg_1m, cached.load_avg_5m, cached.load_avg_15m],
        "cpu_percent": cached.cpu_percent,
        "cpu_temp_c": cached.cpu_temp_c,
        "memory": {"used_mb": cached.memory_used_mb, "total_mb": cached.memory_total_mb},
        "uptime_seconds": cached.uptime_seconds,
        "latency_ms": cached.latency_ms,
        "npu_ms": cached.npu_ms,
        "updated_at": cached.updated_at,
    }


def _merge_sources(db: Session, device_id: str, buffered: Optional[dict]) -> dict:
    if buffered:
        return buffered
    cached = db.query(DeviceStatusCache).filter(
        DeviceStatusCache.device_id == device_id).first()
    return _from_cache(cached)


def effective_status(device: Device, buffered: Optional[dict]) -> str:
    """`devices.status`는 30초 주기 플러시가 갱신한다 — 그 사이를 버퍼로 메운다.

    버퍼에 방금 도착한 하트비트가 있는데 DB가 아직 'unknown'이면, 화면에는 최대 30초
    동안 "꺼진 기기"로 보인다. 저장은 여전히 배치로 하되 **응답은 지금 아는 것**을
    말한다.
    """
    if device.status == "offline" and (not buffered or is_stale(buffered.get("updated_at"))):
        return "offline"
    if device.status == "warning":
        return "warning"
    if buffered and not is_stale(buffered.get("updated_at")):
        return buffered.get("status") or "online"
    return device.status


def build_status_payload(db: Session, device: Device, buffered: Optional[dict]) -> dict:
    """`GET /api/devices/{id}/status` 응답 (명세 §3)."""
    src = _merge_sources(db, device.id, buffered)
    return {
        "status": effective_status(device, buffered),
        "load_avg": src.get("load_avg"),
        # 명세 §3은 "Pi가 CPU 사용률을 산출하지 않는다"고 적혀 있으나 이제 보내온다.
        "cpu_percent": src.get("cpu_percent"),
        "cpu_temp_c": src.get("cpu_temp_c"),
        "memory": src.get("memory"),
        "uptime_seconds": src.get("uptime_seconds"),
        "uptime_human": format_uptime(src.get("uptime_seconds")),
        "latency_ms": src.get("latency_ms"),
        "npu_ms": src.get("npu_ms"),
        "last_seen": device.last_seen or src.get("updated_at"),
    }


def build_device_summary(db: Session, device: Device, buffered: Optional[dict],
                         *, today_detections: int = 0,
                         cameras: Optional[list[dict]] = None) -> dict:
    """목록/상세 공통 필드 (명세 §3의 응답 예시).

    `cpu`·`temperature`·`memory`·`uptime`·`latency`·`npu_ms`는 대시보드가 쓰는 이름이고,
    원본 필드·단위는 `/status`가 그대로 낸다.
    """
    src = _merge_sources(db, device.id, buffered)
    memory = src.get("memory") or {}
    return {
        "id": device.id,
        "name": device.name,
        "ip": device.ip,
        "location": device.location,
        "status": effective_status(device, buffered),
        "last_seen": device.last_seen or src.get("updated_at"),
        "cpu": src.get("cpu_percent"),
        "temperature": src.get("cpu_temp_c"),
        "memory": {"used_mb": memory.get("used_mb"), "total_mb": memory.get("total_mb")},
        "uptime": format_uptime(src.get("uptime_seconds")),
        "uptime_seconds": src.get("uptime_seconds"),
        "latency": src.get("latency_ms"),
        "npu_ms": src.get("npu_ms"),
        "today_detections": today_detections,
        "cameras": cameras if cameras is not None else [],
    }


def is_stale(last_seen, now=None) -> bool:
    """마지막 하트비트가 임계를 넘었는지."""
    if last_seen is None:
        return True
    now = now or utcnow()
    return (now - last_seen).total_seconds() > OFFLINE_AFTER_SEC
