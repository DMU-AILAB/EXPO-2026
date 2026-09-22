import asyncio
import logging
from typing import Dict, Any
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy.dialects.sqlite import insert
from ..models.device import DeviceStatusCache
from ..database import SessionLocal

logger = logging.getLogger(__name__)

# 인메모리 버퍼: device_id -> dict
_heartbeat_buffer: Dict[str, Dict[str, Any]] = {}
_lock = asyncio.Lock()

async def update_heartbeat_buffer(device_id: str, payload: dict):
    """DB I/O 없이 인메모리 버퍼에만 갱신"""
    async with _lock:
        _heartbeat_buffer[device_id] = {
            **payload,
            "updated_at": datetime.utcnow()
        }

async def get_buffered_status(device_id: str) -> dict:
    """메모리에 있는 최신 상태값 조회"""
    async with _lock:
        return _heartbeat_buffer.get(device_id)

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
                "load_avg_1m": data.get("load_avg", [None, None, None])[0] if data.get("load_avg") else None,
                "load_avg_5m": data.get("load_avg", [None, None, None])[1] if data.get("load_avg") else None,
                "load_avg_15m": data.get("load_avg", [None, None, None])[2] if data.get("load_avg") else None,
                "cpu_temp_c": data.get("cpu_temp_c"),
                "memory_used_mb": data.get("memory", {}).get("used_mb"),
                "memory_total_mb": data.get("memory", {}).get("total_mb"),
                "uptime_seconds": data.get("uptime_seconds"),
                "latency_ms": data.get("latency_ms"),
                "npu_ms": data.get("npu_ms"),
                "updated_at": data.get("updated_at")
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
            db.commit()
            logger.debug(f"Flushed {len(values_to_insert)} heartbeats to DB.")
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
