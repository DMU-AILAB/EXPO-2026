# VisionGuide — Raspberry Pi 4 배선 가이드

물리 버튼/LED/팬 GPIO와 AS4432-SMD RF 모듈 배선을 정리한 문서. 소프트웨어 설정은 `CLAUDE.md`의
"물리 버튼 / LED (GPIO)" 절과 각 스크립트(`gpio_controls.py`, `fan_controller.py`,
`camera_live_pi.py`) 상단 docstring 및 [`docs/si4432-kics-integration.md`](si4432-kics-integration.md)를 참고.

> **PoE 어댑터 사용 여부**: 현재 장비처럼 PoE 어댑터가 40핀 헤더의 **물리 핀 1~6번(3.3V, 5V×2,
> GND, GPIO2/3)을 점유**하면 아래의 PoE용 전원 버튼 배선을 사용한다. PoE 어댑터가 없다면 GPIO3(물리
> 핀 5)의 기본 전원 버튼 배선을 사용할 수 있으며, 이 경우 GND 물리 핀 6도 사용 가능하다.
> 기존 버튼·LED·팬 배선은 충돌을 피하기 위해 어느 경우에도 7번 이후의 지정 핀을 유지한다.

## 핀 배정 요약

| 기능 | GPIO(BCM) | 물리 핀 번호 | GND 핀 | 비고 |
|------|-----------|--------------|--------|------|
| 전원(종료) 버튼 — PoE 있음 | GPIO4 | 7 | 9 | `dtoverlay=gpio-shutdown,gpio_pin=4` |
| 전원(종료) 버튼 — PoE 없음 | GPIO3 | 5 | 6 | `dtoverlay=gpio-shutdown` 기본값 |
| Wi-Fi 전환 버튼 | GPIO17 | 11 | 9 | 내부 풀업 사용 — 외부 저항 불필요 |
| Wi-Fi 모드 상태 LED | GPIO24 | 18 | 아무 GND 핀 | 저항 필수 (220~330Ω), 켜짐=핫스팟/꺼짐=홈Wi-Fi |
| Wi-Fi 전환 부저 | GPIO25 | 22 | 아무 GND 핀 | 액티브 부저 가정 (신호만 주면 소리남) |
| 동작 확인 LED | GPIO27 | 13 | 아무 GND 핀 | 저항 필수 (220~330Ω) |
| 냉각팬 (스위칭 신호) | GPIO22 | 15 | — (아래 참고) | 트랜지스터/MOSFET 통해서만 연결, 전원은 USB 5V |

라즈베리파이 4 GND 핀은 6, 9, 14, 20, 25, 30, 34, 39번이다. PoE 어댑터를 사용하면 1~6번이
점유되므로 6번을 제외하고, PoE가 없으면 6번도 사용할 수 있다.

---

## 1. 전원(종료) 버튼

### PoE 어댑터가 없는 경우 — GPIO3 (물리 5번, 기본 연결)

PoE HAT가 없고 GPIO3을 I2C 등 다른 기능으로 사용하지 않는다면, Raspberry Pi의 기본
`gpio-shutdown` 핀을 사용한다.

```
[모멘터리 버튼]
   한쪽 ──── GPIO3 (물리 핀 5)
   다른쪽 ── GND   (물리 핀 6)
```

- 외부 저항 불필요. `gpio-shutdown` 오버레이의 내부 풀업을 사용한다.
- `/boot/config.txt` (Bookworm 이후는 `/boot/firmware/config.txt`)에 다음 한 줄을 추가한다.

  ```ini
  dtoverlay=gpio-shutdown
  ```

- 짧게 누르면 안전 종료하고, 종료된 상태에서 다시 누르면 GPIO3의 wake 기능으로 부팅할 수 있다.
- 이 연결은 전원을 물리적으로 차단하는 스위치가 아니다. 종료 후에도 대기전력이 남는다.
- GPIO3을 I2C SCL 등 다른 용도로 이미 사용한다면 버튼과 그 기능을 동시에 연결하지 않는다.

### PoE 어댑터가 있는 경우 — GPIO4 (물리 7번)

```
[모멘터리 버튼]
   한쪽 ──── GPIO4 (물리 핀 7)
   다른쪽 ── GND (물리 핀 9)
```

- 외부 저항 불필요 (커널이 풀업 처리).
- 기본 GPIO(GPIO3, 물리 5번)는 PoE 어댑터가 점유해서 사용 불가 — `gpio_pin` 파라미터로 GPIO4 지정.
- `/boot/config.txt` (Bookworm 이후는 `/boot/firmware/config.txt`)에 `dtoverlay=gpio-shutdown,gpio_pin=4` 한 줄 추가 후 재부팅.
- 짧게 누르면 안전 종료한다. GPIO4 방식은 완전히 halt된 상태에서 같은 버튼으로 깨우는 GPIO3의 기본 wake 동작을 제공하지 않는다.
- 보드 자체 대기전력은 남아있는 상태(완전 차단 아님) — 필요하면 물리 전원 스위치를 병행.

## 2. Wi-Fi 전환 버튼 — GPIO17 (물리 11번) + 상태 LED — GPIO24 (물리 18번) + 부저 — GPIO25 (물리 22번)

이 Pi의 무선 칩은 홈 Wi-Fi(`204_WIFI`)와 자체 핫스팟(`VisionGuide-AP`)을 동시에 켤 수 없는 것으로
실측 확인되어(둘 다 wlan0 하나를 두고 경합), 버튼으로 두 모드를 전환하는 방식을 택했다.

```
[모멘터리 버튼]
   한쪽 ──── GPIO17 (물리 핀 11)
   다른쪽 ── GND (물리 핀 9)

GPIO24 (물리 핀 18) ──[저항 220~330Ω]── LED(+) ── LED(-) ── GND (아무 GND 핀)

GPIO25 (물리 핀 22) ──── 부저(+)
                        부저(-) ──── GND (아무 GND 핀)
```

- 내부 풀업 사용(`gpio_controls.py`에서 `pull_up=True`) — 버튼 쪽은 외부 저항 불필요.
- 누르면 `nmcli connection up`으로 홈 Wi-Fi ↔ 핫스팟 전환. Pi 로컬에서 D-Bus로 직접 호출하므로
  SSH 등 원격 작업과 달리 전환 도중 연결이 끊길 위험이 없음.
- 상태 LED: 켜짐 = 핫스팟 모드, 꺼짐 = 홈 Wi-Fi 모드 (상시 표시, 전환 성공 시에만 갱신).
- 부저: 전환 시 1회 = 홈 Wi-Fi, 2회 = 핫스팟, 3회(빠르게) = 전환 실패.
- 부저는 액티브 부저 모듈(신호선에 GPIO만 연결하면 소리남) 가정 — 패시브 피에조라 소리가 안 나면
  `TonalBuzzer`로 교체 필요.

## 3. 동작 확인 LED — GPIO27 (물리 13번)

```
GPIO27 (물리 핀 13) ──[저항 220~330Ω]── LED(+, 긴 다리/애노드)
LED(-, 짧은 다리/캐소드) ──────────────── GND (아무 GND 핀)
```

- 저항 없이 직결 금지 — LED/GPIO 손상 위험.
- 평소 고정 점등, 탐지 루프가 3초(기본값) 이상 멈추면 깜빡임으로 전환.

## 4. 냉각팬 — GPIO22 (물리 15번, 트랜지스터 스위칭)

팬이 신호선 없는 순수 2선(+/-) DC 모터라 GPIO에 직접 연결 불가 (GPIO 최대 전류로는
모터를 못 돌림). 팬 전원은 5V에서 공급하고, NPN 트랜지스터 또는 팬 기동전류를
감당하는 로직레벨 N-MOSFET으로 저측 스위칭한다.

### 5V를 꽂는 위치

- **현재처럼 PoE 어댑터가 장착된 경우**: GPIO 헤더의 물리 핀 2/4가 PoE HAT에
  가려져 있으므로, 여유 USB-A 포트에서 `빨간선=+5V`, `검은선=GND`를 탭한다.
- **PoE 어댑터가 없는 경우**: GPIO 헤더의 물리 핀 **2 또는 4(+5V)**를 팬의
  `+` 전원으로 사용한다. 팬 스위칭 회로의 GND는 물리 핀 **6, 9, 14, 20, 25,
  30, 34, 39 중 하나**에 연결한다. 전원 버튼이 물리 핀 5–6을 사용 중이어도
  물리 핀 6은 GND이므로 팬 회로와 공유할 수 있다.
- 팬 전류가 크거나 외부 5V 어댑터를 사용할 때는 외부 전원 `+5V`를 팬에만
  연결하고, 외부 전원 `GND`와 Pi GND만 공통으로 연결한다. 외부 5V와 Pi의
  5V 핀을 서로 연결해 역전류를 만들지 않는다.

5V 팬의 `+`는 위 5V 전원에 연결하고, `-`는 아래 트랜지스터/MOSFET의
콜렉터/드레인으로 연결한다. 팬을 5V와 GND에 직접 연결하면 항상 회전하고
`fan_controller.py`의 종료 시 OFF 제어가 동작하지 않는다.

```
                    +5V
          (PoE 있음: USB-A 빨간선 / PoE 없음: 물리 핀 2 또는 4)
                          │
                        [팬 +]
                          │
                        [팬 -]
                          │
                     ┌────┴────┐
                     │ 콜렉터/드레인 │
GPIO22 ──[1kΩ 저항]── │ 베이스/게이트 │  (트랜지스터/MOSFET)
                     │ 이미터/소스   │
                     └────┬────┘
                          │
                         GND (PoE 있음: USB 검은선 / PoE 없음: 물리 핀 6/9 등)

플라이백 다이오드 (1N4001 등, 필수):
  캐소드(띠 있는 쪽) ── 팬(+) / +5V
  애노드(띠 없는 쪽) ── 팬(-) / 트랜지스터 콜렉터·드레인
```

- 팬 정격은 12V이나 **5V로도 정상 회전 확인됨** — 별도 12V 전원 없이 5V로 구동.
- 5V 팬이라면 반드시 5V에만 연결한다. 팬 라벨이 12V인 경우 5V에서 저속으로 동작할 수
  있지만, 정상 성능은 보장되지 않는다.
- GPIO 헤더의 5V 핀(2/4번)을 사용할 때는 PoE HAT가 없는지 확인한다. PoE HAT가
  있으면 **여유 USB-A 포트에서 5V/GND를 탭**한다(USB 케이블의 빨간선/검은선 또는
  USB 전원 브레이크아웃 사용).
- 플라이백 다이오드 생략 시 팬 off 순간 역기전력으로 트랜지스터/GPIO 손상 위험 — 반드시 연결.
- 2N2222/2N7000은 소형 팬의 측정된 기동전류가 부품 정격 안에 있을 때만 사용하고,
  그보다 큰 팬은 정격 전류·발열 여유가 있는 로직레벨 MOSFET을 선택한다.
- 부팅 시 자동 ON, `systemctl stop`(종료 시 자동 호출) 때 SIGTERM으로 OFF — 팬을 5V/GND에 직결하면
  안 되는 이유이기도 함(직결 시 종료해도 안 꺼짐).

---

## 부품 체크리스트

- 모멘터리 푸시버튼 × 2 (전원/Wi-Fi 전환)
- LED × 2 (Wi-Fi 상태용, 동작확인용) + 저항 220~330Ω × 2
- 액티브 부저 모듈 × 1
- NPN 트랜지스터(2N2222 등) 또는 로직레벨 MOSFET × 1
- 저항 1kΩ × 1 (트랜지스터 베이스/게이트용)
- 플라이백 다이오드(1N4001/1N4148 등) × 1
- 점퍼 와이어, 브레드보드 또는 만능기판

---

## 5. AS4432-SMD RF 모듈 — Raspberry Pi SPI/GPIO

### 먼저 확인할 점: 358.5000MHz 호환성

현재 소프트웨어는 KICS `358.5000MHz`를 수신하도록 설정되어 있다. 그러나
AS4432-SMD V4 제품 사양은 동작 대역을 `425~525MHz`로 명시하고, 기본 스프링
안테나와 매칭 회로도 433MHz용이다. 따라서 아래 배선이 전기적으로 맞더라도
이 모듈로 358.5000MHz가 수신된다고 보장할 수 없다. 실제 제품에서는
358.5MHz용 매칭 네트워크/안테나가 적용된 모듈 또는 해당 대역용 Si4432 RF
보드를 사용해야 한다. 칩 자체의 주파수 범위만 보고 모듈의 RF 성능을 판단하지
말고, 공급업체에 358.5MHz 수신 가능 여부를 확인한 뒤 시험한다.

아래 핀 번호는 12-pad `AS4432-SMD` 형식 기준이다. 14-pin Si4432 모듈의
핀맵과 혼동하지 말고, 실장 전 모듈 실크와 구매처 데이터시트를 대조한다.

### 권장 연결표

| AS4432-SMD 패드 | 신호 | Raspberry Pi BCM | 물리 핀 | 연결 목적 |
|---:|---|---:|---:|---|
| 1 | GND | — | 9 | 공통 접지 |
| 2 | GPIO0 | GPIO23 | 16 | 직접 복조된 RX DATA 입력 |
| 3 | GPIO1 | 연결 안 함 | — | 현재 드라이버에서 사용하지 않음 |
| 4 | GPIO2 | 연결 안 함 | — | 현재 드라이버에서 사용하지 않음 |
| 5 | VCC | 3.3V | 17 | 모듈 전원 |
| 6 | MISO/SDO | GPIO9 / SPI0_MISO | 21 | 모듈 → Pi SPI 데이터 |
| 7 | MOSI/SDI | GPIO10 / SPI0_MOSI | 19 | Pi → 모듈 SPI 데이터 |
| 8 | SCK/SCLK | GPIO11 / SPI0_SCLK | 23 | SPI 클록 |
| 9 | nSEL/CS | GPIO8 / SPI0_CE0 | 24 | SPI 칩 선택, active-low |
| 10 | IRQ | 연결 안 함 | — | 현재 direct-mode 드라이버에서 사용하지 않음 |
| 11 | SDN | — | 25 | GND에 연결해 모듈을 항상 활성화 |
| 12 | GND | — | 25 | 공통 접지 |

```text
AS4432-SMD pad 5 VCC   ───────── Pi 3.3V (physical 17)
AS4432-SMD pad 1 GND   ───────── Pi GND  (physical 9)
AS4432-SMD pad 12 GND  ───────── Pi GND  (physical 25)
AS4432-SMD pad 11 SDN  ───────── GND     (physical 25, active-low)

AS4432-SMD pad 8 SCK   ───────── Pi GPIO11 / physical 23
AS4432-SMD pad 7 MOSI  ───────── Pi GPIO10 / physical 19
AS4432-SMD pad 6 MISO  ───────── Pi GPIO9  / physical 21
AS4432-SMD pad 9 nSEL  ───────── Pi GPIO8  / physical 24 (SPI0 CE0)
AS4432-SMD pad 2 GPIO0 ───────── Pi GPIO23 / physical 16 (RX DATA)
```

`GPIO0`은 카메라 ROI와 연결되는 신호가 아니다. SI4432 direct RX 모드에서
모듈이 출력하는 디지털 데이터 펄스를 GPIO23으로 읽으며, RF 트리거는 전역
음성 이벤트로 처리된다. 기존 배선의 GPIO4, GPIO17, GPIO22, GPIO24, GPIO25,
GPIO27과 충돌하지 않는다.

### 전원·신호 안전 수칙

- VCC는 반드시 3.3V로 연결한다. AS4432-SMD의 허용 전원은 1.8~3.6V이며,
  5V를 VCC나 GPIO에 연결하면 모듈이 손상될 수 있다.
- 모듈은 수신만 하더라도 전원 변동에 민감하다. VCC와 GND 가까이에
  `100nF + 10uF` 디커플링을 배치하고, 전원 공급원은 RF 모듈의 순간 전류를
  감당할 수 있어야 한다. Pi의 3.3V 레일을 사용할 때 다른 장치 부하를 함께
  확인한다.
- SDN을 부유 상태로 두지 말고 GND로 고정한다. IRQ, GPIO1, GPIO2는 현재
  프로그램에서 사용하지 않으므로 연결하지 않는다.
- 안테나는 금속물과 케이스에서 떨어뜨리고 외부로 세운다. 433MHz용 스프링
  안테나를 358.5MHz에서 그대로 사용하지 말고, 목표 주파수에 맞는 안테나와
  50Ω RF 매칭을 사용한다.
- 전원을 넣기 전 멀티미터로 VCC-GND 단락과 VCC 전압을 확인한다. SPI 신호에
  5V 레벨 변환기를 연결하지 않는다.

### Raspberry Pi에서 점검

```bash
sudo raspi-config                 # Interface Options → SPI → Enable
ls -l /dev/spidev0.0              # SPI0 CE0 장치 확인
cp rf_config_example.json rf_config.json
# rf_config.json에서 enabled=true와 audio_file을 설정
python camera_live_pi.py --headless --port 8080 --rf-config rf_config.json
```

시작 시 SPI 장치 ID가 읽히지 않으면 전원을 끄고 VCC/GND, MISO/MOSI,
`nSEL(CE0)`, `SDN`을 먼저 점검한다. SPI가 정상이어도 358.5MHz 수신이 되지
않으면 안테나/매칭 회로와 모듈의 주파수 사양이 목표 대역에 맞는지 확인한다.
