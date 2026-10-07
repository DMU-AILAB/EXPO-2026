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
# 이만큼 누르고 있으면 Wi-Fi 전환 대신 블루투스 페어링 창을 연다(ble_provisioning.py).
BLE_HOLD_SEC = 3.0
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
    # 블루투스 페어링 창이 열려 있는 동안 — 다른 패턴과 헷갈리지 않게 느리고 고른 점멸.
    "ble_pairing": ((True, 0.5), (False, 0.5)),
}

try:
    from device_metrics import read_metrics
except ImportError:
    read_metrics = None

try:
    # 창 함수만 쓴다 — dbus-next는 ble_provisioning이 실제로 광고할 때만 import한다.
    from ble_provisioning import open_window as open_ble_window, pairing_window_open
except ImportError:
    open_ble_window = None
    pairing_window_open = None


def _active_connection() -> str:
    result = subprocess.run(
        ["nmcli", "-g", "GENERAL.CONNECTION", "device", "show", WIFI_DEVICE],
        capture_output=True, text=True, check=False,
    )
    return result.stdout.strip()


def _home_wifi_candidates() -> list[str]:
    """홈 Wi-Fi로 돌아갈 프로필 후보 — **최근에 연결했던 순서**.

    프로필 이름은 이미지·현장마다 달라(``netplan-wlan0-Home_5G`` 등) 고정 이름을 쓸 수
    없다. 예전에는 `nmcli connection show`의 **첫 번째** Wi-Fi 프로필을 골랐는데, 그 순서는
    활성 연결이 먼저이고 나머지는 이름순이라 핫스팟 모드(홈이 비활성)에서는 이 현장에
    없는 예전 프로필(``204_WIFI``)이 골려 복귀가 실패했다(실기기). 마지막 연결 시각
    (TIMESTAMP)으로 정렬한다. 환경 변수로 지정하면 그것만 쓴다.
    """
    if HOME_WIFI_CONNECTION != "204_WIFI":
        return [HOME_WIFI_CONNECTION]

    result = subprocess.run(
        ["nmcli", "-t", "-f", "NAME,TYPE,TIMESTAMP", "connection", "show"],
        capture_output=True, text=True, check=False,
    )
    found: list[tuple[int, str]] = []
    for line in result.stdout.splitlines():
        parts = line.rsplit(":", 2)
        if len(parts) != 3:
            continue
        name, connection_type, stamp = parts
        name = name.replace("\\:", ":")      # nmcli -t는 이름 속 ':'를 '\:'로 적는다
        if connection_type != "802-11-wireless" or name == HOTSPOT_CONNECTION:
            continue
        try:
            found.append((int(stamp), name))
        except ValueError:
            found.append((0, name))
    found.sort(reverse=True)
    return [name for _, name in found] or [HOME_WIFI_CONNECTION]


def _home_wifi_connection() -> str:
    return _home_wifi_candidates()[0]


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
    # 페어링 패턴은 **실제로 광고하는 동안만** 보인다 — ble_provisioning은 홈 Wi-Fi에
    # 붙어 있으면 창이 열려 있어도 광고하지 않는다. 그때 깜빡이면 "켜졌다"고 오해하고,
    # 아래 오류 패턴까지 가려진다.
    if (pairing_window_open is not None and pairing_window_open()
            and _pairing_possible(connection)):
        return "ble_pairing"

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
    # 홈으로 돌아갈 때는 가장 최근 프로필이 실패하면 다음 하나만 더 시도한다 — 실패 한 번에
    # nmcli가 25초가량 걸려 후보를 다 돌면 버튼 반응이 몇 분씩 늦어진다.
    targets = [HOTSPOT_CONNECTION] if switching_to_hotspot else _home_wifi_candidates()[:2]

    try:
        for target in targets:
            print(f"[GPIO] Wi-Fi 전환 요청: {current or '(알 수 없음)'} → {target}")
            result = subprocess.run(["nmcli", "connection", "up", target], check=False)
            if result.returncode == 0:
                break
            print(f"[GPIO] '{target}' 연결 실패 (exit={result.returncode})")

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


def _pairing_possible(connection: str) -> bool:
    """블루투스로 Wi-Fi를 받을 수 있는 상태인가 — `ble_provisioning.pairing_allowed`와 같은
    기준(wlan0이 홈 Wi-Fi에 붙어 있지 않음). 핫스팟이거나 연결이 없을 때만 참이다."""
    return connection in ("", HOTSPOT_CONNECTION)


def open_ble_pairing(buzzer: "Buzzer") -> None:
    """3초 누름 — 블루투스 페어링 창을 연다. 길게 한 번 울려 짧은 누름과 구분한다.

    홈 Wi-Fi에 연결돼 있으면 열지 않고 짧게 3번 울린다 — 열어 봐야 광고하지 않으므로
    "켜졌다"는 신호를 주면 안 된다. 먼저 짧게 눌러 핫스팟으로 바꾼 뒤 다시 누르면 된다.
    """
    if open_ble_window is None:
        _beep(buzzer, 3, on_time=0.08, gap=0.08)
        print("[GPIO] ble_provisioning 모듈이 없어 페어링 창을 열 수 없습니다")
        return
    connection = _active_connection()
    if not _pairing_possible(connection):
        _beep(buzzer, 3, on_time=0.08, gap=0.08)
        print(f"[GPIO] 홈 Wi-Fi({connection})에 연결돼 있어 블루투스 페어링을 열지 않습니다 "
              "— 짧게 눌러 핫스팟으로 바꾼 뒤 다시 3초 누르세요")
        return
    open_ble_window()
    _beep(buzzer, 1, on_time=0.8, gap=0.0)
    print("[GPIO] 블루투스 페어링 창 열림 (3분)")


TAKEOVER_POLL_SEC = 1.0


def _takeover_beep_loop(buzzer: "Buzzer", stop: threading.Event) -> None:
    """신원이 기존 키 없이 덮어써지면(인수) 부저를 한 번 길게 울린다.

    `roi_editor`가 남긴 신호 파일을 집어간다 — 부저(GPIO25)는 이 프로세스가 단독
    소유라 `roi_editor`가 직접 울릴 수 없다. 페어링 창(길게 1회 0.8초)과 같은 소리지만
    버튼을 누르지 않았는데 울리므로 구분된다.
    """
    try:
        from device_identity import consume_takeover
    except ImportError:
        print("[GPIO] device_identity 모듈이 없어 인수 알림을 쓰지 않습니다")
        return
    while not stop.wait(TAKEOVER_POLL_SEC):
        if consume_takeover(_HERE):
            print("[GPIO] 기기 신원 인수 감지 — 부저 알림")
            _beep(buzzer, 1, on_time=0.8, gap=0.0)


def main() -> None:
    # 짧게 누름 = 놓을 때 Wi-Fi 전환, 3초 누름 = 블루투스 페어링 창.
    # 누르는 순간 전환하면 길게 누르려던 사람도 전환이 먼저 일어나므로, 판정을
    # "놓을 때"로 미룬다(체감 차이는 전환이 손을 뗀 뒤 시작된다는 것뿐이다).
    button = Button(TOGGLE_PIN, bounce_time=DEBOUNCE_SEC, pull_up=True, hold_time=BLE_HOLD_SEC)
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
    threading.Thread(target=_takeover_beep_loop, args=(buzzer, stop),
                     name="takeover-beep", daemon=True).start()
    gesture = {"held": False}

    def _pressed() -> None:
        gesture["held"] = False

    def _held() -> None:
        gesture["held"] = True
        open_ble_pairing(buzzer)

    def _released() -> None:
        if not gesture["held"]:
            handle_button_press(buzzer, switching, transition, transition_lock)

    button.when_pressed = _pressed
    button.when_held = _held
    button.when_released = _released

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
