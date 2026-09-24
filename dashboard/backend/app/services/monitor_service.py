"""기기 생사 판정과 실시간 알림 — 명세 §7의 `device_status_change` · `alert`.

이벤트만으로는 "조용한 것"과 "죽은 것"을 구분할 수 없다. 하트비트가 끊긴 것을 보고
전이를 만들어 주는 주체가 없으면 대시보드의 상태 배지는 마지막으로 켜졌던 값에서
영원히 멈춘다(실제로 `devices.status`에 쓰는 코드가 한 줄도 없어 항상 'unknown'이었다).

**전이가 일어날 때만 브로드캐스트한다.** 매 주기 현재 상태를 쏘면 클라이언트가
변화를 구분할 수 없고 트래픽만 는다.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models.device import Device
from ..utils.timeutil import utcnow
from .device_view import OFFLINE_AFTER_SEC, is_stale
from .heartbeat_service import get_buffered_cameras
from .ws_manager import manager

logger = logging.getLogger(__name__)

__all__ = ["sweep_offline_devices", "broadcast_camera_alerts"]


async def sweep_offline_devices() -> int:
    """하트비트가 끊긴 기기를 offline으로 내리고 WS로 알린다. 전이 건수를 돌려준다."""
    db: Session = SessionLocal()
    transitions = []
    try:
        now = utcnow()
        for device in db.query(Device).all():
            stale = is_stale(device.last_seen, now)
            # 'unknown'은 아직 한 번도 하트비트를 받지 못한 상태다. 이를 offline으로
            # 바꾸면 "등록만 하고 아직 안 켠 기기"와 "죽은 기기"가 섞인다.
            if stale and device.status == "online":
                device.status = "offline"
                transitions.append((device.id, "offline"))
            elif not stale and device.status not in ("online",):
                device.status = "online"
                transitions.append((device.id, "online"))
        if transitions:
            db.commit()
    except Exception:
        logger.exception("오프라인 스윕 실패")
        db.rollback()
        return 0
    finally:
        db.close()

    for device_id, status in transitions:
        await manager.broadcast_event(
            "device_status_change",
            {"device_id": device_id, "status": status,
             "timestamp": utcnow().isoformat() + "Z"},
            device_id,
        )
    if transitions:
        logger.info("기기 상태 전이 %d건 (임계 %d초)", len(transitions), OFFLINE_AFTER_SEC)
    return len(transitions)


# 같은 경보를 매 주기 다시 쏘지 않기 위한 직전 값 (device_id, camera_id) -> message
_last_alerts: dict[tuple[str, str], str] = {}


async def broadcast_camera_alerts() -> int:
    """하트비트가 실어 온 `cameras[].current_alert`를 §7의 `alert`로 내보낸다.

    ⚠ **현재 Pi는 이 값을 항상 `null`로 보낸다**(`device/event_logger.py:306`에 채우는
    코드가 없다). 경로만 만들어 두고, Pi가 채우기 시작하면 그대로 흐르게 한다.
    """
    db: Session = SessionLocal()
    try:
        device_ids = [d.id for d in db.query(Device.id).all()]
    finally:
        db.close()

    sent = 0
    for device_id in device_ids:
        cameras = await get_buffered_cameras(device_id)
        for camera_id, info in cameras.items():
            alert = info.get("current_alert")
            key = (device_id, camera_id)
            if not alert:
                _last_alerts.pop(key, None)
                continue
            if _last_alerts.get(key) == alert:
                continue                     # 같은 경보가 계속되는 중
            _last_alerts[key] = alert
            await manager.broadcast_event(
                "alert",
                {"device_id": device_id, "camera_id": camera_id, "message": alert,
                 "timestamp": utcnow().isoformat() + "Z"},
                device_id,
            )
            sent += 1
    return sent
