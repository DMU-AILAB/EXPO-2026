"""Short-lived, in-memory commands sent from the dashboard to a Pi/ESP32 pair.

Wi-Fi passwords are held only until the Pi acknowledges the command or its TTL
expires. Public status helpers never return the password.
"""

from __future__ import annotations

import secrets
import threading
import time
from typing import Any

COMMAND_TTL_SEC = 10 * 60
_lock = threading.RLock()
_pending: dict[str, dict[str, Any]] = {}
_last_result: dict[str, dict[str, Any]] = {}


class Esp32CommandError(ValueError):
    pass


def queue_wifi_config(device_id: str, ssid: str, password: str) -> dict[str, Any]:
    now = time.time()
    with _lock:
        current = _pending.get(device_id)
        if current and now < current["expires_at"]:
            raise Esp32CommandError("이 기기에 처리 중인 ESP32 명령이 있습니다")
        command = {
            "id": secrets.token_urlsafe(18),
            "op": "wifi_config",
            "ssid": ssid,
            "password": password,
            "created_at": now,
            "expires_at": now + COMMAND_TTL_SEC,
            "state": "queued",
        }
        _pending[device_id] = command
        _last_result.pop(device_id, None)
        return _public(command)


def get_pending_command(device_id: str) -> dict[str, Any] | None:
    now = time.time()
    with _lock:
        command = _pending.get(device_id)
        if not command:
            return None
        if now >= command["expires_at"]:
            _pending.pop(device_id, None)
            _last_result[device_id] = {
                "id": command["id"], "state": "expired", "ssid": command["ssid"],
                "updated_at": now, "message": "명령 유효 시간이 만료됐습니다",
            }
            return None
        command["state"] = "delivered"
        return {key: command[key] for key in ("id", "op", "ssid", "password")}


def complete_command(device_id: str, command_id: str, state: str,
                     message: str = "") -> bool:
    if state not in {"ok", "failed"}:
        return False
    with _lock:
        command = _pending.get(device_id)
        if not command or command["id"] != command_id:
            return False
        _last_result[device_id] = {
            "id": command_id,
            "state": state,
            "ssid": command["ssid"],
            "updated_at": time.time(),
            "message": str(message)[:160],
        }
        # Drop credential-bearing data immediately after acknowledgement.
        _pending.pop(device_id, None)
        return True


def public_command_status(device_id: str) -> dict[str, Any] | None:
    with _lock:
        command = _pending.get(device_id)
        if command and time.time() >= command["expires_at"]:
            _pending.pop(device_id, None)
            _last_result[device_id] = {
                "id": command["id"], "state": "expired", "ssid": command["ssid"],
                "updated_at": time.time(), "message": "명령 유효 시간이 만료됐습니다",
            }
            command = None
        if command:
            return _public(command)
        result = _last_result.get(device_id)
        return dict(result) if result else None


def clear_device(device_id: str) -> None:
    with _lock:
        _pending.pop(device_id, None)
        _last_result.pop(device_id, None)


def _public(command: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": command["id"],
        "state": command["state"],
        "ssid": command["ssid"],
        "expires_at": command["expires_at"],
    }
