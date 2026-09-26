#!/usr/bin/env python3
"""gpio_controls.py — 버튼 하나로 Wi-Fi 홈 네트워크 ↔ 자체 핫스팟 전환.

이 Pi의 무선 칩/드라이버는 진짜 동시(AP+STA) 모드를 지원하지 않아, 홈 Wi-Fi와
자체 핫스팟(VisionGuide-AP) 중 하나만 wlan0에서 활성화할 수 있다(실측 확인됨).
버튼을 누르면 nmcli로 두 프로파일을 번갈아 전환한다.

이 스크립트는 Pi 로컬에서 D-Bus로 NetworkManager를 직접 호출하므로, 전환
대상이 되는 그 네트워크 연결 상태와 무관하게 항상 실행된다 (SSH로 원격에서
같은 작업을 하면 도중에 연결이 끊길 수 있지만, 이 방식은 그런 위험이 없다).

피드백 (시각장애인 보조 프로젝트 취지에 맞춰 시각+청각 이중 제공):
    LED(GPIO27)  통합 상태 표시 — 패턴으로 정상/핫스팟/전환/오류 구분
    부저(GPIO25) 전환 시 비프  — 1회: 홈 Wi-Fi로 전환, 2회: 핫스팟으로 전환,
                                 3회(빠르게): 전환 실패

배선:
    전환 버튼  한쪽 → GPIO17 (물리 11번 핀), 다른쪽 → GND (물리 9번 핀), 내부 풀업 사용
    통합 상태 LED GPIO27 (물리 13번 핀) → 저항(220~330Ω) → LED → GND
    부저       GPIO25 (물리 22번 핀) → 부저(+) / 부저(-) → GND
               (액티브 부저 모듈 가정 — GPIO만 HIGH 주면 소리남. VCC/GND/신호 3핀
                모듈이면 신호선만 GPIO25에 연결하고 VCC는 5V/3.3V에 별도 연결.
                패시브 피에조라 소리가 안 나면 알려주면 TonalBuzzer로 교체)

실행: systemd(visionguide-controls.service)로 root 권한 상시 구동
      (root라야 sudo 없이 nmcli 시스템 연결 제어 가능).
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

from gpiozero import Buzzer, Button, LED
from signal import pause

TOGGLE_PIN = 17
STATE_LED_PIN = 27
BUZZER_PIN = 25
DEBOUNCE_SEC = 0.3
BUTTON_TEST_MODE = os.environ.get("VISIONGUIDE_BUTTON_TEST", "0").lower() in {
    "1", "true", "yes", "on",
}

HOME_WIFI_CONNECTION = os.environ.get("VISIONGUIDE_HOME_WIFI_CONNECTION", "204_WIFI")
HOTSPOT_CONNECTION = "VisionGuide-AP"
WIFI_DEVICE = "wlan0"
DEVICE_SERVICE = "visionguide-device"
_HERE = Path(__file__).parent

_PATTERNS = {
    "home": ((True, 3.00),),
    "hotspot": ((True, 0.10), (False, 0.10), (True, 0.10), (False, 2.10)),
    "switching": ((True, 0.08), (False, 0.08)),
    "transition_home": ((True, 0.18), (False, 0.18), (True, 0.18), (False, 0.46)),
    "transition_hotspot": ((True, 0.10), (False, 0.10), (True, 0.10), (False, 0.10),
                            (True, 0.10), (False, 0.46)),
    "camera_error": ((True, 0.12), (False, 0.12), (True, 0.12), (False, 0.12),
                     (True, 0.12), (False, 1.50)),
    "network_error": ((True, 0.08), (False, 0.08), (True, 0.08), (False, 0.08),
                      (True, 0.08), (False, 0.08), (True, 0.08), (False, 1.50)),
    "boot": ((True, 0.08), (False, 0.22)),
}

try:
    from device_metrics import read_metrics
except ImportError:
    read_metrics = None


def _active_connection() -> str:
    result = subprocess.run(
        ["nmcli", "-g", "GENERAL.CONNECTION", "device", "show", WIFI_DEVICE],
        capture_output=True, text=True, check=False,
    )
    return result.stdout.strip()


def _home_wifi_connection() -> str:
    """Find the configured home Wi-Fi profile on fresh Pi images.

    NetworkManager profile names are image/site-specific (for example,
    ``netplan-wlan0-Home_5G``), so the old fixed ``204_WIFI`` name cannot be
    the only option. An explicit environment variable still wins.
    """
    if HOME_WIFI_CONNECTION != "204_WIFI":
        return HOME_WIFI_CONNECTION

    result = subprocess.run(
        ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"],
        capture_output=True, text=True, check=False,
    )
    for line in result.stdout.splitlines():
        name, separator, connection_type = line.rpartition(":")
        if separator and connection_type == "802-11-wireless" and name != HOTSPOT_CONNECTION:
            return name
    return HOME_WIFI_CONNECTION


def _service_active() -> bool:
    result = subprocess.run(
        ["systemctl", "is-active", "--quiet", DEVICE_SERVICE],
        check=False,
    )
    return result.returncode == 0


def _metric_db_paths() -> list[Path]:
    """Return camera DBs that contain the process health heartbeat."""
    raw_paths: list[str] = []
    configured = os.environ.get("VISIONGUIDE_TRAFFIC_DB", "").strip()
    if configured:
        raw_paths.append(configured)

    config_path = _HERE / "camera_config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        raw_paths.extend(
            str(item.get("traffic_db", ""))
            for item in config.get("cameras", [])
            if isinstance(item, dict)
        )
    except (OSError, ValueError, TypeError):
        pass

    raw_paths.append("foot_traffic.db")
    paths: list[Path] = []
    seen: set[Path] = set()
    for raw in raw_paths:
        if not raw:
            continue
        path = Path(raw)
        if not path.is_absolute():
            path = _HERE / path
        path = path.resolve()
        if path not in seen and path.is_file():
            paths.append(path)
            seen.add(path)
    return paths


def _camera_pipeline_active() -> bool:
    """Use the same per-camera metric that the dashboard heartbeat consumes."""
    if read_metrics is None:
        return _service_active()

    found_metrics = False
    for path in _metric_db_paths():
        try:
            metrics = read_metrics(path)
        except Exception:  # noqa: BLE001 - an LED must not kill GPIO controls
            continue
        if not metrics:
            continue
        found_metrics = True
        if any(item.get("streaming") for item in metrics):
            return True
    return _service_active() if not found_metrics else False


def _status_kind(switching: threading.Event, transition: dict,
                 transition_lock: threading.Lock) -> str:
    if switching.is_set():
        return "switching"

    with transition_lock:
        if transition["until"] > time.monotonic():
            return str(transition["kind"])
        transition["kind"] = None
        transition["until"] = 0.0

    connection = _active_connection()
    if connection == HOTSPOT_CONNECTION:
        return "hotspot"
    if not connection:
        return "network_error"
    if not _service_active() or not _camera_pipeline_active():
        return "camera_error"
    return "home"


def _status_led_loop(led: "LED", stop: threading.Event,
                     switching: threading.Event, transition: dict,
                     transition_lock: threading.Lock) -> None:
    """Drive the one status LED without blocking the Wi-Fi button callback."""
    kind = "boot"
    while not stop.is_set():
        if kind != "switching":
            kind = _status_kind(switching, transition, transition_lock)
        for value, duration in _PATTERNS[kind]:
            led.value = value
            if stop.wait(duration):
                led.off()
                return
        kind = _status_kind(switching, transition, transition_lock)


def _beep(buzzer: "Buzzer", times: int, on_time: float = 0.12, gap: float = 0.12) -> None:
    for i in range(times):
        buzzer.on()
        time.sleep(on_time)
        buzzer.off()
        if i < times - 1:
            time.sleep(gap)


def toggle_wifi(buzzer: "Buzzer", switching: threading.Event,
                transition: dict, transition_lock: threading.Lock) -> None:
    switching.set()
    current = _active_connection()
    switching_to_hotspot = current != HOTSPOT_CONNECTION
    target = HOTSPOT_CONNECTION if switching_to_hotspot else _home_wifi_connection()

    print(f"[GPIO] Wi-Fi 전환 요청: {current or '(알 수 없음)'} → {target}")
    try:
        result = subprocess.run(["nmcli", "connection", "up", target], check=False)

        if result.returncode == 0:
            with transition_lock:
                transition["kind"] = (
                    "transition_hotspot" if switching_to_hotspot else "transition_home"
                )
                transition["until"] = time.monotonic() + 3.0
            _beep(buzzer, 2 if switching_to_hotspot else 1)
            print(f"[GPIO] 전환 완료 — {'핫스팟' if switching_to_hotspot else '홈 Wi-Fi'} 모드")
        else:
            _beep(buzzer, 3, on_time=0.08, gap=0.08)
            print(f"[GPIO] 전환 실패 (exit={result.returncode})")
    finally:
        switching.clear()


def handle_button_press(buzzer: "Buzzer", switching: threading.Event,
                        transition: dict, transition_lock: threading.Lock) -> None:
    """Handle one press, with a safe hardware-only test mode."""
    if BUTTON_TEST_MODE:
        _beep(buzzer, 1, on_time=0.08, gap=0.0)
        print(f"[GPIO] 버튼 테스트 입력 감지 — GPIO{BUZZER_PIN} 짧은 비프")
        return
    toggle_wifi(buzzer, switching, transition, transition_lock)


def main() -> None:
    button = Button(TOGGLE_PIN, bounce_time=DEBOUNCE_SEC, pull_up=True)
    led = LED(STATE_LED_PIN)
    buzzer = Buzzer(BUZZER_PIN)

    stop = threading.Event()
    switching = threading.Event()
    transition = {"kind": None, "until": 0.0}
    transition_lock = threading.Lock()
    status_thread = threading.Thread(
        target=_status_led_loop,
        args=(led, stop, switching, transition, transition_lock),
        name="status-led", daemon=True,
    )
    status_thread.start()
    button.when_pressed = lambda: handle_button_press(
        buzzer, switching, transition, transition_lock,
    )

    print(f"[GPIO] Wi-Fi 전환 버튼 대기 중 (GPIO{TOGGLE_PIN}) — 통합 상태 LED GPIO{STATE_LED_PIN}")
    if BUTTON_TEST_MODE:
        print("[GPIO] BUTTON_TEST_MODE=1 — Wi-Fi 전환 없이 부저만 테스트")
    try:
        pause()
    finally:
        stop.set()
        status_thread.join(timeout=1.0)
        led.off()
        led.close()
        buzzer.off()
        buzzer.close()
        button.close()


if __name__ == "__main__":
    main()
