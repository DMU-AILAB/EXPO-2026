#!/usr/bin/env python3
"""gpio_controls.py — 버튼 하나로 Wi-Fi 홈 네트워크 ↔ 자체 핫스팟 전환.

이 Pi의 무선 칩/드라이버는 진짜 동시(AP+STA) 모드를 지원하지 않아, 홈 Wi-Fi와
자체 핫스팟(VisionGuide-AP) 중 하나만 wlan0에서 활성화할 수 있다(실측 확인됨).
버튼을 누르면 nmcli로 두 프로파일을 번갈아 전환한다.

이 스크립트는 Pi 로컬에서 D-Bus로 NetworkManager를 직접 호출하므로, 전환
대상이 되는 그 네트워크 연결 상태와 무관하게 항상 실행된다 (SSH로 원격에서
같은 작업을 하면 도중에 연결이 끊길 수 있지만, 이 방식은 그런 위험이 없다).

피드백 (시각장애인 보조 프로젝트 취지에 맞춰 시각+청각 이중 제공):
    LED(GPIO24)  상시 상태 표시 — 켜짐: 핫스팟 모드, 꺼짐: 홈 Wi-Fi 모드
    부저(GPIO25) 전환 시 비프  — 1회: 홈 Wi-Fi로 전환, 2회: 핫스팟으로 전환,
                                 3회(빠르게): 전환 실패

배선:
    전환 버튼  한쪽 → GPIO17 (물리 11번 핀), 다른쪽 → GND (물리 9번 핀), 내부 풀업 사용
    상태 LED   GPIO24 (물리 18번 핀) → 저항(220~330Ω) → LED → GND
    부저       GPIO25 (물리 22번 핀) → 부저(+) / 부저(-) → GND
               (액티브 부저 모듈 가정 — GPIO만 HIGH 주면 소리남. VCC/GND/신호 3핀
                모듈이면 신호선만 GPIO25에 연결하고 VCC는 5V/3.3V에 별도 연결.
                패시브 피에조라 소리가 안 나면 알려주면 TonalBuzzer로 교체)

실행: systemd(visionguide-controls.service)로 root 권한 상시 구동
      (root라야 sudo 없이 nmcli 시스템 연결 제어 가능).
"""
from __future__ import annotations

import subprocess
import time

from gpiozero import Buzzer, Button, LED
from signal import pause

TOGGLE_PIN = 17
STATE_LED_PIN = 24
BUZZER_PIN = 25
DEBOUNCE_SEC = 0.3

HOME_WIFI_CONNECTION = "204_WIFI"
HOTSPOT_CONNECTION = "VisionGuide-AP"
WIFI_DEVICE = "wlan0"


def _active_connection() -> str:
    result = subprocess.run(
        ["nmcli", "-g", "GENERAL.CONNECTION", "device", "show", WIFI_DEVICE],
        capture_output=True, text=True, check=False,
    )
    return result.stdout.strip()


def _beep(buzzer: "Buzzer", times: int, on_time: float = 0.12, gap: float = 0.12) -> None:
    for i in range(times):
        buzzer.on()
        time.sleep(on_time)
        buzzer.off()
        if i < times - 1:
            time.sleep(gap)


def toggle_wifi(led: "LED", buzzer: "Buzzer") -> None:
    current = _active_connection()
    switching_to_hotspot = current != HOTSPOT_CONNECTION
    target = HOTSPOT_CONNECTION if switching_to_hotspot else HOME_WIFI_CONNECTION

    print(f"[GPIO] Wi-Fi 전환 요청: {current or '(알 수 없음)'} → {target}")
    result = subprocess.run(["nmcli", "connection", "up", target], check=False)

    if result.returncode == 0:
        led.value = switching_to_hotspot
        _beep(buzzer, 2 if switching_to_hotspot else 1)
        print(f"[GPIO] 전환 완료 — {'핫스팟' if switching_to_hotspot else '홈 Wi-Fi'} 모드")
    else:
        _beep(buzzer, 3, on_time=0.08, gap=0.08)
        print(f"[GPIO] 전환 실패 (exit={result.returncode})")


def main() -> None:
    button = Button(TOGGLE_PIN, bounce_time=DEBOUNCE_SEC, pull_up=True)
    led = LED(STATE_LED_PIN)
    buzzer = Buzzer(BUZZER_PIN)

    led.value = _active_connection() == HOTSPOT_CONNECTION
    button.when_pressed = lambda: toggle_wifi(led, buzzer)

    print(f"[GPIO] Wi-Fi 전환 버튼 대기 중 (GPIO{TOGGLE_PIN}) — 현재: "
          f"{'핫스팟' if led.value else '홈 Wi-Fi'}")
    pause()


if __name__ == "__main__":
    main()
