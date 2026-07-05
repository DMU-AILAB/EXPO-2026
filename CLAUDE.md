# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## 프로젝트 개요

**VisionGuide** — 컴퓨터 비전 기반 시각장애인 보조 공학용 자동 음성 안내 시스템 (EXPO-2026 캡스톤 프로젝트)

두 개의 독립된 서브시스템으로 구성됩니다.

1. **현장 디바이스 (Edge)** — Raspberry Pi 4에서 YOLOv8n으로 흰 지팡이를 탐지하고, ROI 위치 판별 후 GPIO 릴레이 또는 TTS로 음성 안내를 트리거
2. **관리자 대시보드** — FastAPI 백엔드 + React/TypeScript 프론트엔드로 ROI 설정, 음성 매핑, 실시간 모니터링, 통계 제공

---

## 현재 구현 상태

### 구현 완료

| 파일/디렉토리 | 설명 |
|------|------|
| `camera_live_pi.py` | Pi 전용 추론 뷰어 — Coral EdgeTPU / TFLite INT8 / PyTorch 자동 선택, MJPEG 스트리밍 |
| `camera_live.py` | PC용 추론 뷰어 (PyTorch) |
| `detect.py` | `WhiteCaneDetector` 클래스 |
| `edgetpu_infer.py` | Coral Edge TPU Python 3.9 서브프로세스 워커 |
| `audio_trigger.py` | `StandaloneDispatcher` (디바운스/쿨다운) + `AudioPlayer` (논블로킹 MP3 재생, mpg123/pygame) |
| `simulator/app.py` | Streamlit PC 시뮬레이터 — ROI 폴리곤 편집, 실시간 탐지, 오디오 트리거 |
| `simulator/detector.py` | 시뮬레이터용 탐지기 |
| `simulator/roi_manager.py` | `ROIManager` (Shapely Point-in-Polygon) + `ROI` dataclass (audio_file 포함) |
| `simulator/trigger_dispatcher.py` | Streamlit 전용 디바운스/쿨다운 (시뮬레이터만 사용) |
| `roi_editor/server.py` | Pi 로컬 FastAPI 서버(포트 5000) — ROI CRUD + 오디오 파일 업로드(`/api/audio/upload`), `rois.json` atomic write |
| `roi_editor/static/index.html` | 브라우저 ROI 웹 에디터 — MJPEG 스트림 위에 폴리곤을 그리고, 오디오는 로컬 파일 선택 시 자동 업로드/적용 |
| `gpio_controls.py` | GPIO 재시작 버튼 — 라즈베리파이 재부팅이 아니라 `visionguide-device` 서비스만 재시작 |
| `rois_example.json` | ROI 설정 파일 예시 |
| `runs/white_cane_v1-2/weights/` | 학습된 가중치 (`best.pt`, `best_int8.tflite`) |

### 미구현 (계획)

- 관리자 대시보드 백엔드 (`visionguide-backend/` — FastAPI)
- 관리자 대시보드 프론트엔드 (`visionguide-frontend/` — React)
- GPIO 릴레이 트리거 (`gpiozero`)
- 설정 폴링 (`config_syncer.py`)
- 이벤트 로거 / 서버 전송 (`event_logger.py`)
- 헬스 워치독 (`watchdog.py`)

---

## 핵심 파일 관계

`camera_live_pi.py` (Pi 메인) ←→ `audio_trigger.py` + `simulator/roi_manager.py`  
`simulator/app.py` (PC 시뮬레이터) ←→ `audio_trigger.py` + `simulator/roi_manager.py` + `simulator/trigger_dispatcher.py`

새 기능을 추가할 때: `simulator/roi_manager.py`는 Pi와 시뮬레이터가 공유하므로 변경 시 양쪽 동작을 확인하세요.

---

## 데이터셋

- 위치: `datasets/images/` (JPG), `datasets/labels/` (YOLO format txt)
- 라벨 형식: `<class_id> <cx> <cy> <w> <h>` (정규화 0~1), class 0 = 흰 지팡이
- `*.Zone.Identifier` 파일은 Windows에서 복사된 부산물이며 무시하면 됩니다 (`.gitignore`에 등록됨)

---

## 디바이스 개발 명령어

```bash
# PC 의존성 (Python 3.10, Anaconda 가상환경)
conda env create -f environment.yml   # 최초 1회
conda activate visionguide
# 또는 기존 환경에 직접 설치:
# pip install -r requirements.txt

# PC 시뮬레이터 실행
cd simulator && streamlit run app.py

# Pi 전용 카메라 뷰어 (ROI + 오디오 없음)
python camera_live_pi.py --source 0 --headless

# Pi 전용 카메라 뷰어 (ROI + MP3 음성 안내)
python camera_live_pi.py --roi-config rois.json --headless

# YOLOv8 학습 (PC/GPU 환경)
yolo train data=data.yaml model=yolov8n.pt epochs=100 imgsz=640

# PT → TFLite INT8 변환
yolo export model=best.pt format=tflite int8=True

# 개별 모듈 단위 테스트
python -m pytest tests/ -v

# systemd 데몬 등록 (PC에서: make install-service). 등록 후 Pi에서:
sudo systemctl status visionguide-device
sudo journalctl -u visionguide-device -f
```

---

## Pi 배포 자동화 (Makefile)

`Makefile`로 PC → Pi 파일 전송과 의존성 설치를 자동화합니다.

**필요**: Git Bash (rsync + ssh 포함) 또는 WSL.
Windows에서 `make` 미설치 시: `scoop install make` 또는 `choco install make`.

```bash
# Pi Python 3.10 환경 일회성 설치 (최초 1회 — pyenv 이용)
make setup-pi-python310

# 전체 배포 (파일 전송 + 의존성 설치)
make deploy

# 코드만 변경된 경우 — 파일만 빠르게 재전송
make sync

# Pi에서 headless 스트리밍 시작 (수동 실행/테스트용)
make run-headless   # 브라우저: http://raspberrypi.local:8080/stream.mjpg

# systemd 등록 — 부팅 시 카메라 앱 + ROI 에디터 완전 자동/headless 구동 (최초 1회)
make install-service

# Pi 연결 및 환경 확인
make ping
```

**임베디드 headless 운영 흐름**: `make install-service` 이후로는 Pi IP 접속이 최초 ROI/오디오 설정(또는 재설정) 시에만 필요합니다.
탐지·음성 안내(`visionguide-device.service`)는 네트워크 연결 여부와 무관하게 기기 단독으로 부팅 시 자동 시작되며,
`roi_editor`(포트 5000, `visionguide-roi-editor.service`)에서 저장한 `rois.json` 변경은 최대 2초 내 재시작 없이 자동 반영됩니다
(`camera_live_pi.py`가 mtime을 폴링). ROI 에디터를 계속 켜두고 싶지 않으면
`ssh <user>@<pi> sudo systemctl disable --now visionguide-roi-editor` 로 끌 수 있습니다.

**IP 주소 지정** (Pi IP가 동적으로 바뀌는 경우):

```bash
# mDNS 호스트명이 작동할 때 (기본값, 대부분의 경우)
make deploy                          # PI=raspberrypi.local 기본값 사용

# IP를 직접 지정할 때
make deploy PI=192.168.0.89
make sync   PI=192.168.0.89

# 현재 Pi IP 확인 (라우터 DHCP 테이블 또는 Pi에서 실행)
#   Pi에서: hostname -I
#   PC에서: arp -a | findstr raspberry  (Windows)
```

**배포 대상 파일** — `Makefile` 상단 `DEPLOY_PY` 변수로 관리:

| 변수 | 파일 | 설명 |
|------|------|------|
| `DEPLOY_PY` | `camera_live_pi.py`, `detect.py`, `edgetpu_infer.py`, `audio_trigger.py`, `gpio_controls.py`, `fan_controller.py` | Pi에 배포할 Python 소스 |
| `DEPLOY_MODEL` | `best_int8.tflite` | TFLite INT8 추론 모델 |

`roi_editor/` 디렉토리는 별도로 `make sync-roi-editor` (deploy에 포함됨)로 전송됩니다.

새 Python 파일을 Pi에 배포해야 할 때는 `Makefile`의 `DEPLOY_PY`에 추가하세요.

---

## 물리 버튼 / LED (GPIO)

| 기능 | GPIO(BCM) | 물리 핀 | 배선 | 구현 위치 |
|------|-----------|---------|------|-----------|
| 전원(종료) 버튼 | GPIO4 | 7번 (GND: 9번) | 버튼 양단을 GPIO4–GND에 연결 | 커널 기능, 코드 없음 |
| Wi-Fi 전환 버튼 | GPIO17 | 11번 (GND: 9번) | 버튼 양단을 GPIO17–GND, 내부 풀업 사용(외부 저항 불필요) | `gpio_controls.py` |
| Wi-Fi 모드 상태 LED | GPIO24 | 18번 (GND: 아무 GND 핀) | GPIO24 → 저항(220~330Ω) → LED → GND | `gpio_controls.py` |
| Wi-Fi 전환 부저 | GPIO25 | 22번 (GND: 아무 GND 핀) | GPIO25 → 부저(+), 부저(-) → GND (액티브 부저 가정) | `gpio_controls.py` |
| 동작 확인 LED | GPIO27 | 13번 (GND: 아무 GND 핀) | GPIO27 → 저항(220~330Ω) → LED → GND | `camera_live_pi.py --status-led 27` |
| 냉각팬 | GPIO22 | 15번 | GPIO22 → 트랜지스터/MOSFET 베이스·게이트(1kΩ 저항) → 팬(+: 5V, 플라이백 다이오드 필수) | `fan_controller.py` |

**전원 버튼**은 라즈베리파이 OS 공식 기능이라 코드가 필요 없습니다. `/boot/config.txt`(Bookworm 이후는 `/boot/firmware/config.txt`)에
`dtoverlay=gpio-shutdown,gpio_pin=4` 한 줄을 추가하고 재부팅하면 됩니다 — 기본 GPIO(GPIO3, 물리 5번 핀)는 이
Pi에서 **PoE 어댑터가 물리 핀 1~6번을 점유**하고 있어 쓸 수 없으므로, `gpio_pin=4` 파라미터로 GPIO4(물리 7번 핀)를
대신 사용하도록 지정했다. **부팅 설정 파일을 건드리는 작업이라 원격에서 잘못 적용하면 복구가 번거로울 수 있으므로
Makefile로 자동화하지 않고 수동으로 적용하는 것을 권장합니다.** 짧게 누르면 안전 종료되고, 완전히 꺼진 상태에서
다시 누르면 재부팅됩니다.

**Wi-Fi 전환 버튼**은 이 Pi의 무선 칩/드라이버가 진짜 동시(AP+STA) 모드를 지원하지 않는 것으로 실측 확인되어
(홈 Wi-Fi `204_WIFI`와 자체 핫스팟 `VisionGuide-AP`가 wlan0 하나를 두고 경합, 항상 한쪽만 활성화됨) 만든
기능입니다. 버튼을 누르면 `gpio_controls.py`가 로컬에서 `nmcli connection up`으로 두 프로파일을 번갈아
전환합니다 — SSH 등 원격에서 같은 작업을 하면 전환 도중 그 연결 자체가 끊길 위험이 있지만, 이 방식은 Pi
로컬에서 D-Bus로 NetworkManager를 직접 호출하므로 그런 위험이 없습니다. 상태 LED(GPIO24)가 켜지면
핫스팟, 꺼지면 홈 Wi-Fi 모드이며, 부저(GPIO25)가 전환 시 1회(홈 Wi-Fi)/2회(핫스팟)/3회(실패)로 소리를 냅니다.
이전에 있던 "SW 재시작 버튼"(5초 홀드로 `visionguide-device` 재시작) 기능은 이 버튼에서 제거되었습니다 —
물리 버튼으로 SW를 재시작할 방법이 다시 필요하면 별도 GPIO 핀에 추가해야 합니다.

**냉각팬**은 신호선 없는 순수 2선(+/-) DC 모터(에듀이노 스마트홈 키트 팬, 정격 12V이나 5V 구동 확인됨)라
GPIO에 직접 연결할 수 없어 트랜지스터/MOSFET 스위치로 on/off만 한다. 온도 로직 없이 부팅과 함께 켜져서 상시
가동되며, `systemctl stop`(종료 시 자동 호출)에서 SIGTERM을 받아 팬을 끄고 종료한다 — 그래서 전원 버튼으로
종료해도 팬이 같이 꺼진다. 팬을 5V/GND에 직결하면 보드 대기전력 때문에 종료해도 안 꺼지므로 반드시 이 GPIO
스위칭 방식을 거쳐야 한다.

**동작 확인 LED**는 `camera_live_pi.py`의 탐지 루프가 프레임을 처리할 때마다 토글되는 하트비트입니다. 정상 동작 중엔
빠르게 깜빡이고, 루프가 멈추면(예: 추론 행/크래시) LED도 같이 멈추므로 모니터 없이도 "탐지 SW가 살아있는지"를
눈으로 확인할 수 있습니다.

`make install-service` 한 번으로 세 systemd 유닛(`visionguide-device`, `visionguide-roi-editor`, `visionguide-controls`)이
모두 설치/활성화됩니다.

---

## Pi 호환성 코딩 지침

Pi에서 실행될 코드를 작성하거나 수정할 때 반드시 지켜야 할 규칙입니다.

### 파일 쌍 유지

| PC 버전 | Pi 버전 | 관계 |
|---------|---------|------|
| `camera_live.py` | `camera_live_pi.py` | 동일 기능, 추론 백엔드·카메라 소스만 다름 |
| `detect.py` | — | Pi에서는 TFLite가 우선; `detect.py`는 PyTorch fallback 전용 |

`camera_live.py`에 새 기능(ROI 오버레이, 통계 표시 등)을 추가하면 `camera_live_pi.py`에도 반영하세요.

### Pi 코드 경로에서 금지

- `torch` / `torchvision` / `ultralytics` 직접 import → TFLite 백엔드 우선, PyTorch는 마지막 fallback
- `cv2.imshow` 단독 사용 → `--headless` MJPEG 경로도 항상 함께 지원
- `cuda` 하드코딩 → `device` 파라미터로 추상화

### 모델 가중치 변경 시 체크리스트

1. `yolo export model=best.pt format=tflite int8=True` 로 TFLite 재생성
2. `runs/white_cane_v1-2/weights/best_int8.tflite` 교체
3. `camera_live_pi.py`의 `_postprocess` 출력 형상 확인 (`[1,5,8400]` vs `[1,8400,5]`)
4. `make sync` 로 Pi에 재배포

### 의존성 추가 시

- `requirements.txt` (PC용) 와 `requirements-pi.txt` (Pi용) 모두 업데이트
- Pi에 설치하지 않는 패키지: `torch`, `torchvision`, `ultralytics` (무거움)
- Pi 전용 패키지: `tflite-runtime`, `picamera2` (apt), `gpiozero`, `RPi.GPIO`

---

## 대시보드 백엔드 개발 명령어

```bash
cd visionguide-backend

# 의존성 설치
pip install -e ".[dev]"          # pyproject.toml 기준
# 또는
pip install fastapi uvicorn sqlalchemy pydantic-settings passlib[bcrypt] \
            python-jose httpx loguru shapely

# 개발 서버 실행
uvicorn app.main:app --reload --port 8000

# DB 초기화 (관리자 계정 시드)
python -m app.db.init_db

# 테스트 실행
pytest tests/ -v
pytest tests/test_auth.py -v      # 특정 파일만

# Docker Compose
docker-compose up --build
```

---

## 대시보드 프론트엔드 개발 명령어

```bash
cd visionguide-frontend

npm install
npm run dev          # Vite 개발 서버
npm run build        # 프로덕션 빌드
npm run lint         # ESLint
npm run typecheck    # tsc --noEmit
```

---

## 시스템 아키텍처 핵심 흐름

```
Pi Camera → YOLOv8n(TFLite INT8) → SORT 추적 → ROI Point-in-Polygon
  → 디바운싱(0.5s) + 쿨다운(10s) → GPIO 릴레이 / TTS 재생
  → POST /api/events/ingest (API Key 인증) → FastAPI → SQLite + WebSocket 푸시
  → React 대시보드 (실시간 이벤트 + 통계 + MJPEG 영상)
```

**설정 동기화**: 디바이스가 60초마다 `GET /api/devices/me/config` (ETag 비교)를 폴링하여 ROI·음성 매핑·쿨다운을 핫리로드합니다.

**영상 전송**: 디바이스 `:8080/stream.mjpg` → FastAPI MJPEG 프록시 → 브라우저 `<img>` 태그 (동시 5명 제한).

---

## 인증 구조

- **관리자**: JWT Bearer (24h 유효, jti 블랙리스트 로그아웃)
- **디바이스**: `X-API-Key` 헤더 (sha256 해시를 DB에 저장, 등록 시 1회만 평문 노출)

---

## 디바이스 핵심 모듈

| 파일 | 역할 | 상태 |
|------|------|------|
| `camera_live_pi.py` | Pi Camera/OpenCV 추상화 + 추론 + 추적 + MJPEG 송출 | ✅ 구현됨 |
| `audio_trigger.py` | `StandaloneDispatcher` (디바운싱/쿨다운) + `AudioPlayer` (MP3) | ✅ 구현됨 |
| `simulator/roi_manager.py` | Shapely 기반 ROI Point-in-Polygon 판별 | ✅ 구현됨 |
| `simulator/trigger_dispatcher.py` | Streamlit 전용 디바운싱/쿨다운 (시뮬레이터용) | ✅ 구현됨 |
| `preprocess.py` | Letterbox 리사이즈 + CLAHE 야간 보정 | 미구현 (예정) |
| `priority_policy.py` | 다중 ROI 동시 점유 시 heapq 우선순위 | 미구현 (예정) |
| `config_syncer.py` | 60초 폴링, atomic config 교체, 핫리로드 | 미구현 (예정) |
| `event_logger.py` | 로컬 SQLite 버퍼 → 비동기 서버 전송 | 미구현 (예정) |
| `watchdog.py` | psutil CPU/온도/디스크, 픽셀 분산으로 렌즈 오염 탐지 | 미구현 (예정) |

---

## AI 모델 KPI

| 지표 | 목표값 |
|------|--------|
| mAP@0.5 (낮) | ≥ 0.85 |
| mAP@0.5 (야간) | ≥ 0.75 |
| FPR | ≤ 5% |
| TFLite INT8 FPS (라파) | ≥ 10 |
| 모델 크기 | ≤ 10 MB |
| mAP 손실 (양자화) | ≤ 5% |

---

## DB 스키마 요약

SQLite (`data/visionguide.db`). 주요 테이블:

- `users` — 단일 관리자, bcrypt 해싱, 로그인 실패 카운터 + 잠금
- `devices` — 시리얼 UNIQUE, `api_key_hash`, `config_etag`, `last_seen`
- `rois` — `polygon` JSON (정규화 0~1 좌표), `priority`, `is_active`
- `announcement_mappings` — ROI당 1행, `audio_url` 또는 `text` 중 하나 필수
- `detection_events` — 타입: `DETECTION / ANNOUNCEMENT / OFFLINE`, 90일 초과 조회 불가
- `hourly_stats` — UNIQUE(device_id, roi_id, hour), 통계 집계용 사전 집계 테이블

---

## 중요 설계 결정 사항

- ROI 폴리곤 좌표는 항상 **정규화 0~1** 범위로 저장/전송합니다. 렌더링 시 캔버스 크기에 맞게 스케일링하세요.
- 폴리곤 유효성 최종 검증은 **백엔드 Shapely**에서 수행합니다 (프론트에서는 점 3개 미만만 차단).
- 디바이스 이벤트 ingest는 **분당 600건** 초과 시 429 응답합니다.
- 통계 API 조회 범위: hourly ≤ 90일, daily ≤ 1년, summary ≤ 31일.
- MJPEG 프록시는 **동시 5명** 초과 시 신규 연결을 거부합니다.
