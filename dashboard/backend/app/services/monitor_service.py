"""Device status and realtime alert monitoring."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models.camera import Camera
from ..models.device import Device
from ..utils.timeutil import utcnow
from .device_view import OFFLINE_AFTER_SEC, is_stale
from .heartbeat_service import get_buffered_cameras, get_buffered_status
from .ws_manager import manager

logger = logging.getLogger(__name__)

__all__ = ["sweep_offline_devices", "broadcast_camera_alerts"]

# Suppress the same alert until its condition changes.
_last_alerts: dict[tuple[str, str], str] = {}
_last_camera_health_alerts: dict[str, str] = {}


def _camera_health_message(
    db: Session, device: Device, heartbeat: dict | None
) -> str | None:
    """Return a warning when an online Pi has no usable camera stream.

    A missing heartbeat is handled by the device offline monitor. An existing
    heartbeat with ``cameras=[]`` means that the Pi is alive but no camera was
    reported, which is the condition this warning is intended to expose.
    """
    if heartbeat is None:
        return None

    configured = db.query(Camera).filter(
        Camera.device_id == device.id,
        Camera.is_active.is_(True),
    ).all()
    reported = {
        item.get("id"): item
        for item in (heartbeat.get("cameras") or [])
        if isinstance(item, dict) and item.get("id")
    }

    if not configured:
        return "카메라가 인식되지 않았습니다. 등록된 카메라 프로필이 없습니다."

    missing = [camera.id for camera in configured if camera.id not in reported]
    stopped = [
        camera.id
        for camera in configured
        if camera.id in reported and not reported[camera.id].get("is_streaming")
    ]
    if not missing and not stopped:
        return None

    details = []
    if missing:
        details.append(f"미감지: {', '.join(missing)}")
    if stopped:
        details.append(f"스트림 중지: {', '.join(stopped)}")
    return "카메라 상태를 확인하세요. " + "; ".join(details)


async def sweep_offline_devices() -> int:
    """Update device/camera health and broadcast state changes."""
    db: Session = SessionLocal()
    transitions: list[tuple[str, str]] = []
    camera_alerts: list[tuple[str, str]] = []
    try:
        now = utcnow()
        for device in db.query(Device).all():
            heartbeat = await get_buffered_status(device.id)
            heartbeat_time = heartbeat.get("updated_at") if heartbeat else None
            stale = is_stale(heartbeat_time or device.last_seen, now)
            camera_message = None if stale else _camera_health_message(
                db, device, heartbeat
            )

            if stale:
                # A device without any heartbeat remains unknown until its
                # first heartbeat; a previously seen device becomes offline.
                next_status = "offline" if device.last_seen is not None else device.status
            elif heartbeat is None and device.status == "warning":
                # Keep the warning between heartbeat packets. A fresh packet
                # is required to prove that the camera recovered.
                next_status = "warning"
            elif camera_message:
                next_status = "warning"
            elif device.status != "unknown":
                next_status = "online"
            else:
                next_status = device.status

            if next_status != device.status:
                device.status = next_status
                transitions.append((device.id, next_status))

            if camera_message:
                if _last_camera_health_alerts.get(device.id) != camera_message:
                    _last_camera_health_alerts[device.id] = camera_message
                    camera_alerts.append((device.id, camera_message))
            elif heartbeat is not None:
                _last_camera_health_alerts.pop(device.id, None)

        if transitions:
            db.commit()
    except Exception:
        logger.exception("health sweep failed")
        db.rollback()
        return 0
    finally:
        db.close()

    for device_id, status in transitions:
        await manager.broadcast_event(
            "device_status_change",
            {
                "device_id": device_id,
                "status": status,
                "timestamp": utcnow().isoformat() + "Z",
            },
            device_id,
        )

    for device_id, message in camera_alerts:
        await manager.broadcast_event(
            "alert",
            {
                "device_id": device_id,
                "camera_id": "__device__",
                "message": message,
                "timestamp": utcnow().isoformat() + "Z",
            },
            device_id,
        )

    if transitions:
        logger.info(
            "device status transitions: %d (threshold %ds)",
            len(transitions),
            OFFLINE_AFTER_SEC,
        )
    return len(transitions)


async def broadcast_camera_alerts() -> int:
    """Broadcast current alerts reported for individual cameras."""
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
                continue
            _last_alerts[key] = alert
            await manager.broadcast_event(
                "alert",
                {
                    "device_id": device_id,
                    "camera_id": camera_id,
                    "message": alert,
                    "timestamp": utcnow().isoformat() + "Z",
                },
                device_id,
            )
            sent += 1
    return sent
