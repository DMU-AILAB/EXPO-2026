# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## 프로젝트 개요

**VisionGuide** — 컴퓨터 비전 기반 시각장애인 보조 공학용 자동 음성 안내 시스템 (EXPO-2026 캡스톤 프로젝트)

두 개의 독립된 서브시스템으로 구성됩니다.

1. **현장 디바이스 (Edge)** — Raspberry Pi 4에서 YOLOv8n으로 흰 지팡이를 탐지하고, ROI 위치 판별 후 GPIO 릴레이 또는 TTS로 음성 안내를 트리거
2. **관리자 대시보드** — FastAPI 백엔드 + React/TypeScript 프론트엔드로 ROI 설정, 음성 매핑, 실시간 모니터링, 통계 제공

---

## 현재 저장소 상태

- **AI 모델**: YOLOv8n 학습 완료. 최적 가중치 → `runs/white_cane_v1-2/weights/best.pt` (6.3MB)
- **소스 코드**: 아직 없음 (디바이스 파이프라인, 백엔드, 프론트엔드 모두 미구현)
- **기타**: `datasets/` (학습 데이터), `docs/` (기획 문서), `runs/` (학습 결과)

---

## 데이터셋

- `datasets/data.yaml` — 학습 설정 파일 (nc=1, names=['white_cane'])
- `datasets/train/images/`, `datasets/val/images/`, `datasets/test/images/` — 분할된 이미지
- `datasets/labels/` — YOLO format txt: `<class_id> <cx> <cy> <w> <h>` (정규화 0~1), class 0 = 흰 지팡이
- `*.Zone.Identifier` 파일은 Windows 복사 부산물 (`.gitignore`에 등록됨)

---

## 학습 완료 모델 (white_cane_v1-2)

| 항목 | 값 |
|------|-----|
| 기반 모델 | YOLOv8n (pretrained) |
| 학습 환경 | RTX 3060 12GB, Python 3.13, torch 2.6.0+cu124, Ultralytics 8.4.54 |
| epochs / batch / imgsz | 100 / 32 / 640 |
| 최적 mAP@0.5 (best.pt) | **0.994** |
| 최적 mAP@0.5-95 (best.pt) | 0.761 |
| 모델 크기 | 6.3 MB |
| 학습 시간 | 1.763 시간 |
| 학습 결과 | `runs/white_cane_v1-2/results.csv`, 곡선 PNG들 |

실제 학습에 사용된 명령어:

```bash
yolo train data=/home/ubuntu/expo/datasets/data.yaml model=yolov8n.pt \
     epochs=100 batch=32 imgsz=640 device=0 workers=8 \
     project=/home/ubuntu/expo/runs name=white_cane_v1-2
```

---

## PC 시뮬레이터 (simulator/)

라즈베리 파이 없이 전체 디바이스 파이프라인을 PC에서 테스트하는 Streamlit 앱.

```bash
# 의존성 설치 (anaconda base 환경 — ultralytics/streamlit이 설치된 곳)
/home/ubuntu/anaconda3/bin/pip install shapely streamlit-drawable-canvas

# 실행 (프로젝트 루트에서)
/home/ubuntu/anaconda3/bin/streamlit run simulator/app.py
```

기능: 웹캠/영상 파일 입력 → YOLOv8 탐지 → ROI 폴리곤 그리기 → 디바운싱(0.5s)/쿨다운(10s) → Mock 음성 안내

---

## 디바이스 개발 명령어

```bash
# Python 환경 (Python 3.11 권장; 학습 환경은 Python 3.13 + CUDA 12.4)
pip install ultralytics opencv-python-headless picamera2 httpx pydantic \
            filterpy scipy albumentations pyaudio gpiozero psutil

# PT → TFLite INT8 변환 (라즈베리 파이 배포용)
yolo export model=runs/white_cane_v1-2/weights/best.pt format=tflite int8=True

# 파이프라인 실행 (라즈베리 파이)
python main_pipeline.py

# 개별 모듈 단위 테스트
python -m pytest tests/ -v

# systemd 데몬 등록 후 실행
sudo systemctl start visionguide-device
sudo journalctl -u visionguide-device -f
```

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

## 디바이스 핵심 모듈 (구현 예정 파일명)

| 파일 | 역할 |
|------|------|
| `camera_source.py` | Pi Camera / RTSP 추상화 (끊김 시 5초 재연결) |
| `preprocess.py` | Letterbox 리사이즈 + CLAHE 야간 보정 |
| `roi_manager.py` | `cv2.pointPolygonTest`로 BBox 중심 ROI 판별 |
| `tracker.py` | SORT (Kalman + Hungarian), `max_age=30` |
| `trigger_dispatcher.py` | 디바운싱 0.5s, 쿨다운 10s, asyncio 이벤트 큐 |
| `priority_policy.py` | 다중 ROI 동시 점유 시 heapq 우선순위 |
| `config_syncer.py` | 60초 폴링, atomic config 교체, 핫리로드 |
| `event_logger.py` | 로컬 SQLite 버퍼 → 비동기 서버 전송 |
| `mjpeg_server.py` | aiohttp/Flask MJPEG 송출 `:8080` |
| `watchdog.py` | psutil CPU/온도/디스크, 픽셀 분산으로 렌즈 오염 탐지 |

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
