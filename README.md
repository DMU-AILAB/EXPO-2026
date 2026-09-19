# EXPO-2026 — VisionGuide

컴퓨터 비전 기반 시각장애인 보조 공학용 자동 음성 안내 시스템 (DMU 캡스톤 프로젝트 2026)

---

## 프로젝트 개요

흰 지팡이를 든 시각장애인이 카메라 시야에 진입하면, YOLOv8n 모델이 실시간으로 지팡이를 탐지하고 설정된 ROI(관심 영역)를 기준으로 해당 위치를 판별한 뒤 MP3 음성 안내를 자동 재생합니다.

```
Pi Camera → YOLOv8n (TFLite INT8) → SORT 추적 → ROI Point-in-Polygon
  → 디바운싱 (0.5s) + 쿨다운 (10s) → MP3 재생 / GPIO 릴레이
  → POST /api/events/ingest → FastAPI → SQLite + WebSocket
  → React 대시보드 (실시간 이벤트 + 통계 + MJPEG 영상)
```

---

## 구현 현황

### 완료

| 파일 | 설명 |
|------|------|
| `device/camera_live_pi.py` | Pi 전용 추론 뷰어 — Coral EdgeTPU / TFLite INT8 / PyTorch 자동 선택 |
| `device/detect.py` | `WhiteCaneDetector` 클래스 (PyTorch fallback) |
| `device/edgetpu_infer.py` | Coral Edge TPU 서브프로세스 워커 (Python 3.9) |
| `device/audio_trigger.py` | `StandaloneDispatcher` (디바운스/쿨다운) + `AudioPlayer` (큐 기반 순차 재생) |
| `apps/roi_editor/` | Pi 로컬 ROI 웹 에디터 (포트 5000) — **기기에서 실제로 보이는 화면** |
| `apps/simulator/app.py` | Streamlit PC 시뮬레이터 (ROI 편집 + 실시간 탐지 + 오디오 트리거) |
| `apps/simulator/roi_manager.py` | ROI 폴리곤 관리 + Point-in-Polygon 판별 — **Pi와 공유** |
| `apps/simulator/trigger_dispatcher.py` | Streamlit 기반 디바운스/쿨다운 (시뮬레이터 전용) |
| `tools/dev/camera_live.py` | PC용 추론 뷰어 (PyTorch) |
| `tools/eval/eval_video_recall.py` | 실영상 탐지/트리거 벤치 — **모델 채택 1차 기준** |
| `tools/data/resplit_dataset.py` | 누수 없는 그룹 단위 재분할 |
| `configs/examples/rois_example.json` | ROI 설정 예시 (입구, 횡단보도) |

### 미구현 (계획)

- `visionguide-backend/` — FastAPI 관리자 대시보드 백엔드
- `dashboard/frontend/` — React + TypeScript 대시보드 프론트엔드 (**Pi 배포 경로 없음**)
- GPIO 릴레이 트리거 (`gpiozero`)
- 설정 폴링 (`config_syncer.py`)
- 이벤트 로거 (`event_logger.py`)
- 헬스 워치독 (`watchdog.py`)

---

## 빠른 시작

### PC 시뮬레이터 (권장 — Pi 없이 테스트)

```bash
pip install streamlit ultralytics opencv-python shapely streamlit-drawable-canvas pygame
cd simulator
streamlit run app.py
```

1. 사이드바에서 영상 파일 업로드 또는 웹캠 선택
2. **편집 모드** 켜기 → 프레임 위에 ROI 폴리곤 그리기
3. ROI 이름, 안내 텍스트, MP3 경로 입력 → **추가**
4. **▶ 시작** — 지팡이가 ROI 진입 시 토스트 알림 + MP3 재생

### Pi 프로덕션 (음성 안내 포함)

```bash
# 1. mpg123 설치 (오디오 재생기)
sudo apt install -y mpg123 python3-shapely

# 2. Python 의존성
pip install -r requirements-pi.txt

# 3. ROI 설정 파일 편집
cp rois_example.json rois.json
# rois.json 에서 ROI 폴리곤 좌표와 audio_file 경로 수정

# 4. 실행 (헤드리스 모드)
python device/camera_live_pi.py --roi-config rois.json --headless --port 8080
```

브라우저에서 `http://<Pi IP>:8080/stream.mjpg` 로 MJPEG 스트리밍 확인.

### ROI 설정 없이 실행 (탐지·추적만)

```bash
python device/camera_live_pi.py --source 0 --conf 0.35
```

---

## ROI 설정 파일 형식 (`rois.json`)

```json
{
  "debounce": 0.5,
  "cooldown": 10.0,
  "rois": [
    {
      "name": "입구",
      "points": [[0.1, 0.2], [0.5, 0.2], [0.5, 0.8], [0.1, 0.8]],
      "priority": 1,
      "announcement_text": "입구 앞입니다",
      "audio_file": "audio/entrance.mp3"
    }
  ]
}
```

- `points`: 정규화 좌표 (0~1). 프레임 해상도에 독립적
- `debounce`: 트리거 발사 전 연속 점유 시간(초)
- `cooldown`: 트리거 후 재발사 금지 시간(초)
- `audio_file`: MP3 경로 (상대 경로는 실행 위치 기준)

---

## 추론 백엔드 자동 선택

`device/camera_live_pi.py` 실행 시 아래 순서로 사용 가능한 최선의 백엔드를 자동 선택합니다.

| 우선순위 | 백엔드 | 모델 파일 | 비고 |
|----------|--------|-----------|------|
| 1 | Google Coral Edge TPU | `best_int8_edgetpu.tflite` | `edgetpu_compiler`로 컴파일 필요 |
| 2 | TFLite INT8 CPU | `best_int8.tflite` | Pi 기본 동작 |
| 3 | PyTorch / ultralytics | `best.pt` | fallback |

모델 파일 위치: `runs/white_cane_v1-2/weights/`

---

## 개발 명령어

```bash
# YOLOv8 학습 (PC/GPU)
yolo train data=data.yaml model=yolov8n.pt epochs=100 imgsz=640

# PT → TFLite INT8 변환
yolo export model=best.pt format=tflite int8=True

# Pi 배포 (Makefile)
make deploy PI=192.168.0.89     # 전체 배포
make sync   PI=192.168.0.89     # 코드만 재전송

# 테스트
python -m pytest tests/ -v
```

자세한 Pi 배포 방법은 [`docs/pi-deployment-guide.md`](docs/pi-deployment-guide.md)를 참고하세요.

---

## 레포지토리 구조

```
expo/
├── device/                    # Pi에서 실행되는 런타임 20개 (Makefile의 DEPLOY_PY와 일치)
│   ├── camera_live_pi.py      #   메인 — 카메라·추론·트래킹·ROI·MJPEG
│   ├── yolo_postprocess.py    #   전처리(letterbox)·후처리 공유
│   ├── detect.py              #   WhiteCaneDetector (PyTorch fallback)
│   ├── edgetpu_infer.py       #   Coral EdgeTPU 서브프로세스 워커
│   └── …                      #   트래킹·오디오·GPIO·RF 등
├── tools/                     # PC 전용 스크립트
│   ├── data/                  #   데이터 준비 (resplit_dataset, prepare_*, fetch_*)
│   ├── eval/                  #   평가 (eval_video_recall, eval_background_fp)
│   └── dev/                   #   개발 보조 (camera_live, discover, seed_dummy_traffic)
├── apps/                      # 사람이 띄워 쓰는 앱
│   ├── roi_editor/            #   Pi 웹 UI (:5000) — 실제 운영 화면
│   ├── simulator/             #   Streamlit PC 시뮬레이터
│   └── label_tool/            #   라벨 보완 툴
├── dashboard/                 # 미구현 React 대시보드 + 디자인 자료
├── configs/                   # 학습 설정 yaml
├── deploy/                    # systemd 유닛 · sudoers · 배포 스크립트
│   └── examples/              #   *_example.json (rois·camera_config·rf)
├── weights/                   # COCO 사전학습 .pt (gitignore)
├── datasets/                  # 흰 지팡이 학습 이미지 + YOLO 라벨
├── runs/                      # 학습 결과 (가중치 파일)
├── tests/                     # pytest + conftest.py
├── docs/                      # 기획·평가 문서
├── requirements.txt           # PC 의존성
├── requirements-pi.txt        # Pi 의존성
└── Makefile                   # Pi 배포 자동화
```

> **Pi는 평면 배치다.** `make sync`가 `device/*.py`를 기기의 `~/visionguide/`에
> 평평하게 풀어놓으므로, 기기에는 `device/` 디렉터리가 없다. 자세한 규칙은
> `CLAUDE.md`의 "디렉터리 구조" 절 참고.

---

## 데이터셋 출처

> **White cane Computer Vision Model**
> Created by **noDongjaeTeam**
> Source: [Roboflow Universe](https://universe.roboflow.com/nodongjaeteam-g1pbo/white-cane-hyjat)
> License: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)

원본 데이터셋은 수정 없이 학습·평가 용도로 사용되었습니다.
