# VisionGuide — Raspberry Pi 4 배선 가이드

물리 버튼·LED·팬·RF 모듈의 GPIO 배선을 정리한 문서.  
소프트웨어 설정은 `gpio_controls.py`, `fan_controller.py`, `camera_live_pi.py` 상단 docstring과
[`docs/si4432-kics-integration.md`](si4432-kics-integration.md)를 참고한다.

---

## Pi 4 40핀 헤더 레이아웃

```
       3.3V  [ 1] [ 2]  5V
 GPIO2(SDA1) [ 3] [ 4]  5V
 GPIO3(SCL1) [ 5] [ 6]  GND  ◀ 전원버튼 GND (PoE 없음)
      GPIO4  [ 7] [ 8]  GPIO14
        GND  [ 9] [10]  GPIO15  ◀ 핀 9: 전원버튼 GND (PoE 있음) + Wi-Fi 버튼 GND
     GPIO17  [11] [12]  GPIO18
     GPIO27  [13] [14]  GND  ◀ 핀 14: LED GND
     GPIO22  [15] [16]  GPIO23
      3.3V   [17] [18]  GPIO24
GPIO10(MOSI) [19] [20]  GND  ◀ 핀 20: 부저 GND
 GPIO9(MISO) [21] [22]  GPIO25
GPIO11(SCLK) [23] [24]  GPIO8(CE0)
        GND  [25] [26]  GPIO7(CE1)  ◀ 핀 25: RF SDN GND
     ID_SDA  [27] [28]  ID_SCL
      GPIO5  [29] [30]  GND  ◀ 핀 30: RF 모듈 GND
      GPIO6  [31] [32]  GPIO12
     GPIO13  [33] [34]  GND  ◀ 핀 34: 팬 트랜지스터 GND (PoE 없음)
     GPIO19  [35] [36]  GPIO16
     GPIO26  [37] [38]  GPIO20
        GND  [39] [40]  GPIO21
```

> **PoE 어댑터 사용 시**: 물리 핀 1~6이 PoE HAT에 점유되어 핀 6 GND를 사용할 수 없다.  
> 핀 6이 필요한 자리(전원 버튼)는 핀 9 GND로 대체하고, GPIO22 팬 회로와 5V 직결 팬의
> GND는 USB-A 검은선으로 뽑는다.

---

## 핀 배정 전체 요약

| 기능 | BCM | 물리(신호) | 물리(GND) | 비고 |
|------|-----|-----------|-----------|------|
| 전원 버튼 — PoE **없음** | GPIO3 | 5 | **6** | `dtoverlay=gpio-shutdown` |
| 전원 버튼 — PoE **있음** | GPIO4 | 7 | **9** | `dtoverlay=gpio-shutdown,gpio_pin=4` |
| Wi-Fi 전환 버튼 | GPIO17 | 11 | **9** | 내부 풀업, 핀 9 공유 |
| 상태 LED | GPIO27 | 13 | **14** | 220~330 Ω 직렬 필수 |
| 부저 | GPIO25 | 22 | **20** | 액티브 부저 모듈 |
| 냉각팬 스위칭 | GPIO22 | 15 | **34** (PoE 없음) / **USB GND** (PoE 있음) | 트랜지스터/MOSFET 이미터·소스 |
| RF 모듈 VCC | — | 17 (3.3V) | — | 3.3V 전용, 5V 금지 |
| RF 모듈 GND (pad 1) | — | — | **30** | 모듈 공통 접지 |
| RF 모듈 SDN (pad 2) | — | — | **25** | SDN → GND 고정 (항상 활성화) |
| RF 모듈 NSEL/CS | GPIO8 / CE0 | 24 | — | SPI 칩 선택 |
| RF 모듈 SCLK | GPIO11 / SCLK | 23 | — | SPI 클록 |
| RF 모듈 MOSI | GPIO10 / MOSI | 19 | — | Pi → 모듈 |
| RF 모듈 MISO | GPIO9 / MISO | 21 | — | 모듈 → Pi |
| RF 모듈 GPIO2 (RX DATA) | GPIO23 | 16 | — | 직접 복조 데이터 출력 |

---

## 1. 전원(종료) 버튼

### PoE 어댑터 없는 경우 — GPIO3 · 물리 5번

```
[모멘터리 버튼]
  한쪽   ──── GPIO3  (물리 핀 5)
  다른쪽 ────  GND   (물리 핀 6)
```

- 외부 저항 불필요. `gpio-shutdown` 오버레이의 내부 풀업 사용.
- `/boot/firmware/config.txt` (Bookworm 이전은 `/boot/config.txt`)에 추가:

  ```ini
  dtoverlay=gpio-shutdown
  ```

- 짧게 누르면 안전 종료, 종료 상태에서 다시 누르면 wake(부팅).

### PoE 어댑터 있는 경우 — GPIO4 · 물리 7번

```
[모멘터리 버튼]
  한쪽   ──── GPIO4  (물리 핀 7)
  다른쪽 ────  GND   (물리 핀 9)
```

- 외부 저항 불필요 (커널 풀업).
- 물리 핀 5(GPIO3)와 6(GND)은 PoE HAT가 점유 → 사용 불가.
- `/boot/firmware/config.txt`에 추가:

  ```ini
  dtoverlay=gpio-shutdown,gpio_pin=4
  ```

- GPIO4 방식은 완전 halt 후 같은 버튼으로 wake하는 GPIO3 기본 동작을 지원하지 않는다.

---

## 2. Wi-Fi 전환 버튼 · 상태 LED · 부저

```
[모멘터리 버튼 — Wi-Fi 전환]
  한쪽   ──── GPIO17 (물리 핀 11)
  다른쪽 ────  GND   (물리 핀 9)       ← 전원버튼(PoE 있음)과 공유 가능

[상태 LED]
  GPIO27 (물리 핀 13) ──[220~330Ω]── LED(+) ── LED(-) ──  GND (물리 핀 14)

[부저 — 액티브 부저]
  GPIO25 (물리 핀 22) ──── 부저(+)
                           부저(-) ──── GND (물리 핀 20)
```

- Wi-Fi 버튼: 내부 풀업(`pull_up=True`)으로 외부 저항 불필요.
- LED: 저항 없이 GPIO에 직결 **금지** — 저항 없으면 GPIO 손상 위험.
- 부저: 액티브 부저(신호만 넣으면 소리) 가정. 패시브 피에조는 `TonalBuzzer` 필요.

**LED 패턴:**
| 패턴 | 의미 |
|------|------|
| 1회 짧은 점멸 반복 | 홈 Wi-Fi 연결 + 카메라 정상 |
| 2회 짧은 점멸 반복 | AP(핫스팟) 모드 |
| 3회 점멸 반복 | 카메라 파이프라인 오류 |

**부저 패턴:** 전환 후 1회 = 홈 Wi-Fi, 2회 = 핫스팟, 3회(빠르게) = 전환 실패.

---

## 3. 냉각팬

### A. 5V 직결 — 항상 켜짐

```
팬(+) ──── 5V
            PoE 있음: USB-A 빨간선
            PoE 없음: 물리 핀 2 또는 4
팬(-) ────  GND
            PoE 있음: USB-A 검은선
            PoE 없음: 물리 핀 6 또는 9
```

- `fan_controller.py` / `visionguide-fan.service` 제어 불가.
- 사용 시 `sudo systemctl disable --now visionguide-fan`으로 GPIO22 서비스 비활성화 권장.

### B. GPIO22 스위칭 — 소프트웨어 제어

5V 전원으로 팬을 구동하고, GPIO22가 NPN 트랜지스터/N-MOSFET 저측을 스위칭한다.

```
              +5V
    PoE 있음: USB-A 빨간선
    PoE 없음: 물리 핀 2 또는 4
                 │
               [팬 +]
                 │
               [팬 -]
                 │
            ┌───┴──────────┐
            │  콜렉터/드레인  │
GPIO22 ─[1kΩ]─ 베이스/게이트  │  ← 물리 핀 15
            │  이미터/소스   │
            └───┬──────────┘
                │
               GND
    PoE 있음: USB-A 검은선
    PoE 없음: 물리 핀 34

플라이백 다이오드 (1N4001 등, 필수):
  캐소드(띠) ──── 팬(+) / +5V 쪽
  애노드      ──── 팬(-) / 콜렉터·드레인 쪽
```

- 플라이백 다이오드 생략 시 팬 OFF 순간 역기전력으로 GPIO 손상 위험 — 반드시 연결.
- 부팅 시 자동 ON, `systemctl stop` 시 SIGTERM → OFF.

---

## 4. AS4432-SMD RF 모듈

### 연결표

| AS4432-SMD 패드 | 신호 | BCM | 물리 핀 | 비고 |
|---:|---|---|---|---|
| 1 | GND | — | **30** | 모듈 공통 접지 |
| 2 | SDN | — | **25** | GND에 연결 — 항상 활성화 |
| 3 | NIRQ | 미연결 | — | direct-mode에서 미사용 |
| 4 | NSEL/CS | GPIO8 / CE0 | 24 | SPI 칩 선택 |
| 5 | SCLK | GPIO11 / SCLK | 23 | SPI 클록 |
| 6 | SDI/MOSI | GPIO10 / MOSI | 19 | Pi → 모듈 |
| 7 | SDO/MISO | GPIO9 / MISO | 21 | 모듈 → Pi |
| 8 | VCC | 3.3V | **17** | 3.3V 전용 (5V 금지) |
| 9 | GPIO2 | GPIO23 | 16 | RX DATA 출력 |
| 10 | GPIO1 | 미연결 | — | 미사용 |
| 11 | GPIO0 | 미연결 | — | 미사용 |
| 12 | GND | 미연결 | — | pad 1 GND가 이미 연결됨 |

### 배선 다이어그램

```
AS4432 pad  1 GND   ────  Pi GND   (물리 핀 30)
AS4432 pad  2 SDN   ────  Pi GND   (물리 핀 25)  ← SDN=0 → 활성화
AS4432 pad  3 NIRQ  ────  미연결
AS4432 pad  4 NSEL  ────  Pi GPIO8  (물리 핀 24 / SPI0 CE0)
AS4432 pad  5 SCLK  ────  Pi GPIO11 (물리 핀 23 / SPI0 SCLK)
AS4432 pad  6 SDI   ────  Pi GPIO10 (물리 핀 19 / SPI0 MOSI)
AS4432 pad  7 SDO   ────  Pi GPIO9  (물리 핀 21 / SPI0 MISO)
AS4432 pad  8 VCC   ────  Pi 3.3V  (물리 핀 17)
AS4432 pad  9 GPIO2 ────  Pi GPIO23 (물리 핀 16)  ← RX DATA
AS4432 pad 10 GPIO1 ────  미연결
AS4432 pad 11 GPIO0 ────  미연결
AS4432 pad 12 GND   ────  미연결  (pad 1로 접지됨)
```

### 전원·신호 안전 수칙

- **VCC는 반드시 3.3V.** AS4432-SMD 허용 전원 1.8~3.6V — 5V 연결 시 모듈 손상.
- VCC/GND 근처에 `100nF + 10μF` 디커플링 캐패시터 배치.
- SDN은 반드시 물리 핀 25 GND에 연결. 부유 상태로 두지 않는다.
- 전원 인가 전 멀티미터로 VCC-GND 단락 확인.
- SPI 신호에 5V 레벨 변환기를 연결하지 않는다.

### 소프트웨어 초기 설정

```bash
sudo raspi-config             # Interface Options → SPI → Enable
ls -l /dev/spidev0.0          # SPI0 CE0 장치 확인

cp rf_config_example.json rf_config.json
# rf_config.json: enabled=true, detection_mode="rssi", frequency_mhz=356.635
python camera_live_pi.py --headless --port 8080
```

SPI 장치 ID가 읽히지 않으면 전원을 끄고 VCC·GND·MISO/MOSI·NSEL·SDN을 먼저 점검한다.

---

## 5. USB 스피커 전원 제어

스피커는 USB-A를 전원으로만 사용하고, 음성 신호는 Pi의 3.5mm 오디오 잭으로 출력한다.

```
서비스 시작 → USB 2.0 전원 ON → 0.3초 안정화 → 음성 재생
```

감지나 재생 요청이 없어도 스피커 전원은 ON 유지.

환경변수:
```ini
VISIONGUIDE_USB_AUDIO_HUB=1-1
VISIONGUIDE_USB_AUDIO_SETTLE=0.3
VISIONGUIDE_USB_AUDIO_ALWAYS_ON=1
```

Pi 4 USB 2.0·3.0 포트는 전원 그룹을 공유하므로 `uhubctl`이 그룹 단위로 전원을 제어한다.  
`sudo uhubctl`로 실제 허브 위치와 전원 전환 지원 여부를 먼저 확인한다.

---

## 부품 체크리스트

| 부품 | 수량 | 비고 |
|------|------|------|
| 모멘터리 푸시버튼 | × 2 | 전원 버튼, Wi-Fi 전환 버튼 |
| LED | × 1 | 상태 표시 (GPIO27) |
| 저항 220~330 Ω | × 1 | LED 직렬 저항 (필수) |
| 액티브 부저 모듈 | × 1 | GPIO25 |
| NPN 트랜지스터 또는 로직레벨 N-MOSFET | × 1 | 팬 스위칭 시만 |
| 저항 1 kΩ | × 1 | 트랜지스터 베이스/게이트 |
| 플라이백 다이오드 (1N4001 등) | × 1 | 팬 스위칭 시 필수 |
| 점퍼 와이어, 브레드보드 또는 만능기판 | — | |
