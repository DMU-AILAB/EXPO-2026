"""하트비트 수신 버퍼 → DB 플러시.

Pi는 15초마다 상태를 보내는데(`device/event_logger.py:258`) 매번 sqlite에 쓰면 기기가
늘어날수록 쓰기가 잦아진다. 수신은 **메모리에만** 하고 주기적으로 한 번에 UPSERT한다.

**하트비트는 버려도 되는 데이터다.** 5분 전의 CPU 온도는 쓸모가 없고, Pi도 전송에
실패하면 그 주기 값을 버리고 다음에 최신값을 보낸다(`event_logger.py:254-255`).
그래서 버퍼가 날아가도 손해가 작다 — 반대로 이벤트는 outbox에 보관된다.
"""

import asyncio
import logging
from typing import Any, Dict, Optional
from sqlalchemy.orm import Session
from sqlalchemy.dialects.sqlite import insert
from ..models.device import Device, DeviceStatusCache
from ..database import SessionLocal
from ..utils.timeutil import utcnow

logger = logging.getLogger(__name__)

# 인메모리 버퍼: device_id -> dict
_heartbeat_buffer: Dict[str, Dict[str, Any]] = {}
_lock = asyncio.Lock()


def _load_avg_at(payload: dict, index: int) -> Optional[float]:
    """`load_avg`는 3-tuple이지만 Pi가 읽기에 실패하면 통째로 None이다.

    길이를 가정하고 인덱싱하면 그 자리에서 터진다 — 플러시 루프 전체가 멈춰
    모든 기기의 상태가 DB에 남지 않게 된다.
    """
    values = payload.get("load_avg")
    if not isinstance(values, (list, tuple)) or len(values) <= index:
        return None
    value = values[index]
    return value if isinstance(value, (int, float)) else None


def _memory_at(payload: dict, key: str) -> Optional[float]:
    """`memory`는 dict이거나 **None**이다 (`event_logger.py:285-287`)."""
    memory = payload.get("memory")
    if not isinstance(memory, dict):
        return None
    value = memory.get(key)
    return value if isinstance(value, (int, float)) else None


async def update_heartbeat_buffer(device_id: str, payload: dict):
    """DB I/O 없이 인메모리 버퍼에만 갱신"""
    async with _lock:
        _heartbeat_buffer[device_id] = {
            **payload,
            "updated_at": utcnow(),
        }


async def get_buffered_status(device_id: str) -> Optional[dict]:
    """메모리에 있는 최신 상태값 조회"""
    async with _lock:
        return _heartbeat_buffer.get(device_id)


async def get_buffered_cameras(device_id: str) -> Dict[str, dict]:
    """하트비트가 실어 온 카메라별 런타임 상태를 id로 색인해 준다.

    ⚠ `today_detections`는 **카메라별 값이 아니라 기기 전체값이 복사된 것**이다
    (`device/event_logger.py:298,307`) — 카메라마다 다른 숫자처럼 보여주면 안 된다.
    """
    async with _lock:
        payload = _heartbeat_buffer.get(device_id)
    if not payload:
        return {}
    cameras = payload.get("cameras") or []
    return {c["id"]: c for c in cameras if isinstance(c, dict) and c.get("id")}


async def remove_device_from_buffer(device_id: str):
    """기기 삭제 시 참조 무결성을 위해 버퍼에서도 즉시 삭제"""
    async with _lock:
        if device_id in _heartbeat_buffer:
            del _heartbeat_buffer[device_id]


async def bulk_flush_heartbeats():
    """버퍼에 쌓인 상태값을 DB(device_status_cache)에 일괄 병합(UPSERT)"""
    async with _lock:
        if not _heartbeat_buffer:
            return

        snapshot = _heartbeat_buffer.copy()
        _heartbeat_buffer.clear()

    db: Session = SessionLocal()
    try:
        # SQLite UPSERT (ON CONFLICT DO UPDATE)
        values_to_insert = []
        for device_id, data in snapshot.items():
            values_to_insert.append({
                "device_id": device_id,
                "load_avg_1m": _load_avg_at(data, 0),
                "load_avg_5m": _load_avg_at(data, 1),
                "load_avg_15m": _load_avg_at(data, 2),
                "cpu_percent": data.get("cpu_percent"),
                "cpu_temp_c": data.get("cpu_temp_c"),
                "memory_used_mb": _memory_at(data, "used_mb"),
                "memory_total_mb": _memory_at(data, "total_mb"),
                "uptime_seconds": data.get("uptime_seconds"),
                "latency_ms": data.get("latency_ms"),
                "npu_ms": data.get("npu_ms"),
                "updated_at": data.get("updated_at"),
            })

        if values_to_insert:
            stmt = insert(DeviceStatusCache).values(values_to_insert)

            # 업데이트할 필드들 정의 (device_id 제외)
            update_dict = {
                c.name: c
                for c in stmt.excluded
                if c.name != "device_id"
            }

            stmt = stmt.on_conflict_do_update(
                index_elements=["device_id"],
                set_=update_dict
            )

            db.execute(stmt)

        # 기기 행의 status·last_seen도 여기서 갱신한다.
        # 이전에는 이 두 컬럼에 쓰는 코드가 **한 줄도 없어** status가 영원히
        # 'unknown'이었고, 대시보드의 온라인 배지가 전부 죽은 값이었다.
        for device_id, data in snapshot.items():
            device = db.query(Device).filter(Device.id == device_id).first()
            if device is None:
                continue                       # 삭제된 기기의 잔여 하트비트
            device.status = data.get("status") or "online"
            device.last_seen = data.get("updated_at")

        db.commit()
        logger.debug("Flushed %d heartbeats to DB.", len(values_to_insert))
    except Exception as e:
        logger.error(f"Error during bulk heartbeat flush: {e}")
        db.rollback()
        # 플러시 실패 시 데이터 유실 방지를 위해 다시 버퍼에 롤백
        async with _lock:
            for device_id, data in snapshot.items():
                if device_id not in _heartbeat_buffer: # 새로 갱신된 값이 우선순위를 가지도록 방어
                    _heartbeat_buffer[device_id] = data
    finally:
        db.close()
