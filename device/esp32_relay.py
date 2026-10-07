"""BLE control client for a VisionGuide ESP32 output board.

The camera loop only enqueues pulses. BLE discovery, commands and dashboard
polling happen on this module's worker thread so camera inference never waits
for radio or network I/O.
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

SERVICE_UUID = "8a7c0001-3f72-4a1d-9c10-56495347554e"
INFO_UUID = "8a7c0002-3f72-4a1d-9c10-56495347554e"
RX_UUID = "8a7c0003-3f72-4a1d-9c10-56495347554e"
RESULT_UUID = "8a7c0004-3f72-4a1d-9c10-56495347554e"

_MAX_PULSE_MS = 10_000
_MAX_PENDING_PULSES = 64
_FRAGMENT_DATA = 18  # 20-byte default ATT write minus index/count bytes
_SCAN_INTERVAL_SEC = 5.0
_CONTROL_INTERVAL_SEC = 4.0
_STATUS_INTERVAL_SEC = 8.0
_IDLE_DISCONNECT_SEC = 12.0


class RoiEntryPulseTracker:
    """Emit one pulse for each debounced ROI occupancy transition.

    Audio cooldown/debounce state is deliberately independent. A ROI can
    produce a relay pulse even while its audio dispatcher is cooling down.
    """

    def __init__(self) -> None:
        self._started: dict[str, float] = {}
        self._fired: set[str] = set()

    def update(self, roi_name: str, occupied: bool, now: float,
               debounce_sec: float) -> bool:
        if not occupied:
            self._started.pop(roi_name, None)
            self._fired.discard(roi_name)
            return False
        if roi_name in self._fired:
            return False
        started = self._started.setdefault(roi_name, now)
        if now - started < max(0.0, debounce_sec):
            return False
        self._fired.add(roi_name)
        return True


def default_status_path(base: Path | None = None) -> Path:
    return (base or Path.cwd()) / "esp32_status.json"


def read_status_snapshot(path: Path | None = None) -> dict[str, Any]:
    """Read the camera process' latest BLE state for the ROI editor heartbeat."""
    try:
        value = json.loads((path or default_status_path()).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"state": "unpaired", "candidates": []}
    return value if isinstance(value, dict) else {"state": "unpaired", "candidates": []}


class Esp32RelayController:
    """Single process-wide BLE worker; methods called by camera threads are nonblocking."""

    def __init__(self, identity_path: Path, status_path: Path | None = None,
                 pulse_ms: int = 500) -> None:
        self.identity_path = Path(identity_path)
        self.status_path = status_path or default_status_path()
        self.pulse_ms = max(1, min(int(pulse_ms), _MAX_PULSE_MS))
        self._pulses: queue.Queue[dict[str, Any]] = queue.Queue(_MAX_PENDING_PULSES)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._status_lock = threading.Lock()
        self._status: dict[str, Any] = {
            "state": "unpaired", "device_id": None, "wifi_connected": False,
            "wifi_ssid": None, "ip": None, "relay_state": "off",
            "last_error": None, "last_seen": None, "candidates": [],
        }

    def start(self) -> "Esp32RelayController":
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._thread_main,
                                        name="Esp32RelayBLE", daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=max(0.0, timeout))

    def enqueue_pulse(self, event_id: str, roi_name: str,
                      duration_ms: int | None = None) -> bool:
        duration = self.pulse_ms if duration_ms is None else int(duration_ms)
        if duration <= 0 or duration > _MAX_PULSE_MS:
            return False
        try:
            self._pulses.put_nowait({
                "event_id": event_id[:96], "roi": roi_name[:64],
                "duration_ms": duration, "queued_at": time.monotonic(),
            })
            return True
        except queue.Full:
            self._set_error("릴레이 명령 대기열이 가득 찼습니다")
            return False

    def snapshot(self) -> dict[str, Any]:
        with self._status_lock:
            return json.loads(json.dumps(self._status))

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception as exc:  # noqa: BLE001 — BLE must not terminate camera inference
            self._set_error(f"BLE 작업자 오류: {type(exc).__name__}")

    async def _run(self) -> None:
        try:
            from bleak import BleakClient, BleakScanner
        except ImportError:
            self._set_error("Pi 패키지가 없습니다: bleak")
            while not self._stop.wait(30):
                pass
            return

        client = None
        bound_id: str | None = None
        candidates: list[dict[str, Any]] = []
        address_by_id: dict[str, Any] = {}
        active_wifi: dict[str, Any] | None = None
        last_control_poll = 0.0
        control_task: asyncio.Task | None = None
        last_scan = 0.0
        last_status_read = 0.0
        last_activity = 0.0
        wifi_retry_at = 0.0
        last_info: dict[str, Any] = {}

        while not self._stop.is_set():
            now = time.monotonic()
            if now - last_control_poll >= _CONTROL_INTERVAL_SEC and (
                    control_task is None or control_task.done()):
                last_control_poll = now
                control_task = asyncio.create_task(asyncio.to_thread(self._poll_control))

            if control_task is not None and control_task.done():
                try:
                    control = control_task.result()
                except Exception:  # noqa: BLE001
                    control = None
                control_task = None
                if control is not None:
                    new_binding = control.get("binding") or None
                    if new_binding != bound_id:
                        if client and client.is_connected:
                            await client.disconnect()
                        client = None
                        bound_id = new_binding
                        active_wifi = None
                        address_by_id.clear()
                        last_info = {}
                    command = control.get("command")
                    if isinstance(command, dict) and command.get("op") == "wifi_config":
                        if not active_wifi or active_wifi.get("id") != command.get("id"):
                            active_wifi = command
                            wifi_retry_at = 0.0
                    elif "command" in control:
                        # The queue was acknowledged, canceled, expired, or lost on
                        # a backend restart. Do not keep retrying stale credentials.
                        active_wifi = None

            if bound_id and (client is None or not client.is_connected):
                client = None
                if now - last_scan >= _SCAN_INTERVAL_SEC:
                    last_scan = now
                    candidates, address_by_id = await self._discover(BleakScanner, BleakClient)
                    match = address_by_id.get(bound_id)
                    if match is not None:
                        self._set_state("connecting", bound_id, candidates)
                        try:
                            # Command/result characteristics require an encrypted
                            # BLE link. Pair here so BlueZ performs bonding before
                            # the first protected GATT write.
                            client = BleakClient(match, timeout=20.0, pair=True)
                            await client.connect()
                            last_activity = time.monotonic()
                            last_info = await self._read_info(client)
                            if last_info.get("device_id") != bound_id:
                                await client.disconnect()
                                client = None
                                self._set_error("BLE 기기 ID가 승인된 ESP32와 일치하지 않습니다")
                            else:
                                self._apply_info(last_info, candidates)
                        except Exception as exc:  # noqa: BLE001
                            client = None
                            self._set_error(f"ESP32 BLE 연결 실패: {type(exc).__name__}")
                else:
                    self._write_snapshot({"state": "offline" if bound_id else "unpaired",
                                          "device_id": bound_id, "candidates": candidates})
            elif not bound_id and now - last_scan >= _SCAN_INTERVAL_SEC:
                last_scan = now
                candidates, address_by_id = await self._discover(BleakScanner, BleakClient)
                self._write_snapshot({"state": "unpaired", "device_id": None,
                                      "candidates": candidates})

            if client and client.is_connected:
                try:
                    pulse = self._pulses.get_nowait()
                except queue.Empty:
                    pulse = None
                if pulse:
                    if time.monotonic() - pulse["queued_at"] > 10.0:
                        self._set_error("ESP32 연결 대기 시간이 지나 오래된 트리거를 버렸습니다")
                    else:
                        last_activity = time.monotonic()
                        await self._send_pulse(client, pulse)
                if active_wifi and time.monotonic() >= wifi_retry_at:
                    last_activity = time.monotonic()
                    result = await self._configure_wifi(client, active_wifi)
                    if result is not None:
                        ok = result.get("state") == "wifi_configured"
                        await asyncio.to_thread(self._ack_control, active_wifi["id"],
                                                "ok" if ok else "failed",
                                                "ESP32 Wi‑Fi 연결 완료" if ok else
                                                str(result.get("error") or "Wi‑Fi 연결 실패")[:140])
                        active_wifi = None
                    else:
                        wifi_retry_at = time.monotonic() + 5.0
                if now - last_status_read >= _STATUS_INTERVAL_SEC:
                    last_status_read = now
                    try:
                        last_info = await self._read_info(client)
                        self._apply_info(last_info, candidates)
                    except Exception as exc:  # noqa: BLE001
                        self._set_error(f"ESP32 상태 조회 실패: {type(exc).__name__}")
                if (not active_wifi and self._pulses.empty()
                        and time.monotonic() - last_activity > _IDLE_DISCONNECT_SEC):
                    await client.disconnect()
                    client = None
                    self._write_snapshot({"state": "offline", "device_id": bound_id,
                                          "candidates": candidates})
            await asyncio.sleep(0.15)

        if client and client.is_connected:
            await client.disconnect()

    async def _discover(self, scanner_cls, client_cls) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        found: list[dict[str, Any]] = []
        addresses: dict[str, Any] = {}
        try:
            advertisements = await scanner_cls.discover(timeout=2.5, return_adv=True)
        except TypeError:  # Older Bleak without return_adv
            advertisements = await scanner_cls.discover(timeout=2.5)
        except Exception as exc:  # noqa: BLE001
            self._set_error(f"BLE 검색 실패: {type(exc).__name__}")
            return found, addresses

        rows = []
        if isinstance(advertisements, dict):
            for device, adv in advertisements.values():
                uuids = {str(value).lower() for value in (getattr(adv, "service_uuids", None) or [])}
                if SERVICE_UUID in uuids:
                    rows.append((device, adv))
        else:
            rows = [(device, None) for device in advertisements]

        for device, _adv in rows[:12]:
            client = client_cls(device, timeout=5.0)
            try:
                await client.connect()
                info = await self._read_info(client)
                device_id = info.get("device_id")
                if device_id:
                    addresses[str(device_id)] = device
                    found.append({
                        "device_id": str(device_id),
                        "name": str(info.get("name") or getattr(device, "name", "") or "ESP32")[:48],
                        "rssi": getattr(device, "rssi", None),
                    })
                await client.disconnect()
            except Exception:
                try:
                    if client.is_connected:
                        await client.disconnect()
                except Exception:
                    pass
        self._write_snapshot({"state": "scanning", "candidates": found})
        return found, addresses

    async def _read_info(self, client) -> dict[str, Any]:
        raw = await client.read_gatt_char(INFO_UUID)
        result = json.loads(bytes(raw).decode("utf-8"))
        if not isinstance(result, dict):
            raise ValueError("invalid ESP32 info")
        return result

    async def _send_pulse(self, client, pulse: dict[str, Any]) -> None:
        command = {
            "op": "pulse", "request_id": pulse["event_id"],
            "event_id": pulse["event_id"], "roi": pulse["roi"],
            "duration_ms": pulse["duration_ms"],
        }
        for attempt in range(2):
            try:
                result = await self._send_command(client, command, timeout=3.0)
                if result.get("ok") or result.get("already_processed"):
                    self._set_relay_state(str(result.get("commanded_state", "on")))
                    return
                if result.get("error") == "relay_busy" and attempt == 0:
                    wait_ms = min(int(result.get("retry_after_ms") or 250), 5000)
                    await asyncio.sleep(wait_ms / 1000.0 + 0.05)
                    continue
                self._set_error(str(result.get("error") or "ESP32가 펄스 명령을 거부했습니다")[:160])
                return
            except Exception as exc:  # noqa: BLE001 — retry the same idempotency key once
                if attempt:
                    self._set_error(f"ESP32 펄스 전달 실패: {type(exc).__name__}")
                    return
                await asyncio.sleep(0.2)

    async def _configure_wifi(self, client, command: dict[str, Any]) -> dict[str, Any] | None:
        wire = {
            "op": "configure_wifi", "request_id": command["id"],
            "ssid": command["ssid"], "password": command.get("password", ""),
        }
        try:
            result = await self._send_command(client, wire, timeout=35.0)
        except Exception as exc:  # noqa: BLE001
            self._set_error(f"ESP32 Wi‑Fi 설정 전달 실패: {type(exc).__name__}")
            return None
        if result.get("state") == "wifi_configured":
            self._write_snapshot({
                **self.snapshot(), "state": "online", "wifi_connected": True,
                "wifi_ssid": result.get("ssid", command["ssid"]), "ip": result.get("ip"),
                "last_error": None, "last_seen": time.time(),
            })
            return result
        if result.get("state") == "wifi_failed":
            self._set_error(str(result.get("error") or "ESP32가 새 Wi‑Fi에 연결되지 않았습니다")[:160])
            return result
        if result.get("state") == "error" or result.get("ok") is False:
            message = str(result.get("message") or result.get("error")
                          or "ESP32가 Wi‑Fi 설정 명령을 거부했습니다")[:160]
            self._set_error(message)
            return {**result, "state": "wifi_failed", "error": message}
        return None

    async def _send_command(self, client, command: dict[str, Any], timeout: float) -> dict[str, Any]:
        raw = json.dumps(command, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        pieces = [raw[i:i + _FRAGMENT_DATA] for i in range(0, len(raw), _FRAGMENT_DATA)]
        if len(pieces) > 255:
            raise ValueError("BLE 명령이 너무 깁니다")
        for index, piece in enumerate(pieces):
            frame = bytes((index, len(pieces))) + piece
            await client.write_gatt_char(RX_UUID, frame, response=True)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = json.loads(bytes(await client.read_gatt_char(RESULT_UUID)).decode("utf-8"))
            if isinstance(value, dict) and value.get("request_id") == command.get("request_id"):
                if command.get("op") == "configure_wifi" and value.get("state") == "wifi_connecting":
                    await asyncio.sleep(0.25)
                    continue
                return value
            await asyncio.sleep(0.25)
        raise TimeoutError("ESP32 응답 시간 초과")

    def _poll_control(self) -> dict[str, Any] | None:
        identity = self._load_identity()
        if not identity:
            return None
        return self._http_json(identity, "/api/devices/me/esp32/control")

    def _ack_control(self, command_id: str, state: str, message: str) -> None:
        identity = self._load_identity()
        if not identity:
            return
        body = json.dumps({"state": state, "message": message}).encode("utf-8")
        self._http_json(identity, f"/api/devices/me/esp32/commands/{command_id}/result",
                        method="POST", body=body)

    def _load_identity(self) -> dict[str, str] | None:
        try:
            value = json.loads(self.identity_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(value, dict) or not all(value.get(k) for k in ("server_url", "api_key")):
            return None
        return {"server_url": str(value["server_url"]).rstrip("/"),
                "api_key": str(value["api_key"])}

    @staticmethod
    def _http_json(identity: dict[str, str], path: str, method: str = "GET",
                   body: bytes | None = None) -> dict[str, Any] | None:
        request = urllib.request.Request(
            identity["server_url"] + path, data=body, method=method,
            headers={"X-API-Key": identity["api_key"], "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=3.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return payload.get("data", payload) if isinstance(payload, dict) else None
        except (OSError, ValueError, urllib.error.URLError, urllib.error.HTTPError):
            return None

    def _apply_info(self, info: dict[str, Any], candidates: list[dict[str, Any]]) -> None:
        self._write_snapshot({
            "state": "online", "device_id": info.get("device_id"),
            "wifi_connected": bool(info.get("wifi_connected")),
            "wifi_ssid": info.get("wifi_ssid"), "ip": info.get("ip"),
            "relay_state": info.get("relay_state", "off"),
            "last_error": None, "last_seen": time.time(), "candidates": candidates,
        })

    def _set_state(self, state: str, device_id: str | None,
                   candidates: list[dict[str, Any]]) -> None:
        self._write_snapshot({"state": state, "device_id": device_id,
                              "candidates": candidates})

    def _set_relay_state(self, state: str) -> None:
        self._write_snapshot({**self.snapshot(), "relay_state": state,
                              "last_seen": time.time(), "last_error": None})

    def _set_error(self, message: str) -> None:
        self._write_snapshot({**self.snapshot(), "last_error": message[:160]})

    def _write_snapshot(self, update: dict[str, Any]) -> None:
        with self._status_lock:
            self._status.update(update)
            snapshot = dict(self._status)
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.status_path.parent), prefix=".esp32_status.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(snapshot, handle, ensure_ascii=False)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.status_path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
