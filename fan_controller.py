#!/usr/bin/env python3
"""fan_controller.py — 2선(+/-) DC 팬을 Pi 부팅과 함께 상시 가동.

에듀이노 스마트홈 키트용 팬(정격 12V, 실측 5V 구동 확인됨)처럼 신호선 없이
+/- 2선만 있는 순수 DC 모터는 드라이버가 없어 GPIO에 직접 연결할 수 없다.
트랜지스터/MOSFET 스위치로 on/off만 제어하고, 5V 라인으로 팬 전원을 공급한다
(12V 정격이지만 5V에서도 정상 회전 확인됨 — 회전수만 낮아짐).

배선:
    GPIO22 --[1kΩ]-- 트랜지스터 베이스 (또는 MOSFET 게이트)
    팬(+) -- 5V (여유 USB-A 포트에서 탭 — GPIO 헤더 2/4번은 PoE 어댑터가 점유해 사용 불가)
    팬(-) -- 트랜지스터 컬렉터/드레인 -- 이미터/소스 -- GND (USB 포트 GND, 보드 내부에서 공통)
    팬 양단(+/-)에 플라이백 다이오드(1N4001 등, 팬(-)->팬(+) 방향) 필수 — 역기전력 보호

동작: systemd가 부팅 시 이 서비스를 시작하면 즉시 팬 ON, 상시 유지.
      Pi 종료(systemctl stop 또는 shutdown) 시 SIGTERM을 받아 팬을 끄고 종료 —
      전원 버튼(dtoverlay=gpio-shutdown)으로 종료해도 팬이 같이 꺼진다.
      (팬을 5V/GND에 직결하면 보드 대기전력 때문에 종료해도 안 꺼지므로,
       이렇게 GPIO로 스위칭해야 종료와 함께 팬도 정지한다.)
"""
from __future__ import annotations

import signal

from gpiozero import OutputDevice

FAN_PIN = 22


def main() -> None:
    fan = OutputDevice(FAN_PIN, initial_value=True)
    print(f"[FAN] 상시 가동 시작 (GPIO{FAN_PIN})")

    def _shutdown(signum, frame) -> None:
        print("[FAN] 종료 신호 수신 — OFF")
        fan.off()
        fan.close()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    signal.pause()


if __name__ == "__main__":
    main()
