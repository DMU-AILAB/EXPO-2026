# Pi 배포 및 실행 가이드

> VisionGuide 현장 디바이스(Raspberry Pi 4) — 초기 설정부터 ROI 편집까지 전체 흐름

---

## 시스템 구성도

```
┌──────────────────────────────────────────────────────┐
│                  Raspberry Pi 4                      │
│                                                      │
│  ┌──────────────────────────────┐                   │
│  │  camera_live_pi.py           │──── 포트 8080 ────►│── MJPEG 스트림
│  │  · Pi Camera 추론 (TFLite)   │                   │
│  │  · ROI 판정 + 음성 트리거     │                   │
│  │  · rois.json 60초 핫리로드    │                   │
│  └──────────┬───────────────────┘                   │
│             │ rois.json (공유)                       │
│  ┌──────────┴───────────────────┐                   │
│  │  roi_editor/server.py        │──── 포트 5000 ────►│── ROI 웹 에디터
│  │  · rois.json CRUD API        │                   │
│  │  · HTML5 Canvas 에디터 서빙   │                   │
│  └──────────────────────────────┘                   │
└──────────────────────────────────────────────────────┘
         ▲                        ▲
         │ MJPEG 스트림 배경       │ ROI 편집
         └────────────────────────┘
         PC / 스마트폰 브라우저 (같은 Wi-Fi)
         http://192.168.0.89:5000
```

---

## 0단계 — 사전 준비

### PC (Windows)

| 항목 | 확인 |
|------|------|
| Git for Windows (ssh, rsync 포함) | `ssh -V` |
| GNU Make | `scoop install make` 또는 `choco install make` |
| Python 3.10 + Anaconda | `conda activate visionguide` |

### Raspberry Pi

| 항목 | 비고 |
|------|------|
| Raspberry Pi OS 64-bit Bookworm | [Raspberry Pi Imager](https://www.raspberrypi.com/software/) |
| SSH 활성화 | Imager 커스터마이즈 단계에서 설정 또는 `sudo raspi-config` |
| 고정 IP 또는 IP 확인 | Pi에서 `hostname -I` → **192.168.0.89** |

### SSH 키 등록 (최초 1회)

비밀번호 없이 자동 배포하기 위해 SSH 공개키를 Pi에 등록합니다.

```bash
# Git Bash에서
ssh-keygen -t ed25519 -C "visionguide"          # 키 생성 (이미 있으면 생략)
ssh-copy-id ailab@192.168.0.89                  # Pi에 공개키 등록
```

등록 확인:
```bash
make ping       # 비밀번호 없이 Python 버전과 파일 목록이 출력되면 성공
```

---

## 1단계 — Pi Python 3.10 환경 설치 (최초 1회)

Pi 기본 Python은 3.11(Bookworm)이므로 pyenv로 3.10을 별도 설치합니다.

```bash
make setup-pi-python310
```

완료 확인:
```bash
make ping
# → Python 3.10.14
```

---

## 2단계 — 전체 배포 (최초 1회)

코드·모델·의존성을 Pi에 한 번에 전송합니다.

```bash
make deploy
```

내부 동작 순서:

| 순서 | 타겟 | 내용 |
|------|------|------|
| 1 | `sync` | `camera_live_pi.py`, `detect.py`, `edgetpu_infer.py`, TFLite 모델 전송 |
| 2 | `sync-roi-editor` | `roi_editor/`, `simulator/roi_manager.py` 전송 |
| 3 | `deps` | `ai-edge-litert`, `opencv`, `shapely`, `pillow`, `fonts-nanum` 설치 |
| 4 | `deps-roi-editor` | `fastapi`, `uvicorn` 설치 |

완료 후 Pi 디렉토리 구조:

```
~/visionguide/
├── camera_live_pi.py
├── detect.py
├── edgetpu_infer.py
├── rois.json                      ← ROI 설정 파일 (없으면 빈 상태로 시작)
├── audio/                         ← MP3 파일 위치
│   └── (예: crosswalk.mp3)
├── roi_editor/
│   ├── server.py                  ← ROI 웹 에디터 서버
│   └── static/
│       └── index.html             ← 브라우저 에디터 UI
├── simulator/
│   ├── __init__.py
│   └── roi_manager.py             ← ROI 폴리곤 판별 모듈
└── runs/
    └── white_cane_v1-2/
        └── weights/
            └── best_int8.tflite
```

---

## 3단계 — 서비스 실행

두 프로세스를 **각각 별도 터미널**에서 실행합니다.
(SSH로 접속 중이라면 tmux 사용 권장 → [tmux 사용법](#tmux로-백그라운드-실행))

### 터미널 A — 카메라 추론 + MJPEG 스트리밍

```bash
make run-headless
```

- Pi Camera 캡처 → TFLite 추론 → ROI 판정 → 음성 트리거
- `rois.json` 변경 시 60초 이내 자동 핫리로드
- 스트림 확인: `http://192.168.0.89:8080/stream.mjpg`

### 터미널 B — ROI 웹 에디터

```bash
make run-roi-editor
```

> **카메라 앱을 먼저 실행한 상태여야** 에디터 배경에 스트림이 표시됩니다.

- 브라우저에서 `http://192.168.0.89:5000` 접속

---

## 4단계 — ROI 편집

1. PC 또는 스마트폰 브라우저에서 `http://192.168.0.89:5000` 접속
2. 카메라 피드가 배경으로 표시됨

### 조작 방법

| 동작 | 방법 |
|------|------|
| 꼭짓점 추가 | 캔버스 클릭 |
| 폴리곤 완성 | 더블클릭 |
| 그리기 취소 | `ESC` |
| ROI 삭제 | 우측 목록의 [삭제] 버튼 (즉시 저장) |
| 전체 삭제 | [전체 삭제] 버튼 |

### ROI 정보 입력

| 필드 | 필수 | 설명 | 예시 |
|------|------|------|------|
| 이름 | ✅ | ROI 식별자 (중복 불가) | `횡단보도` |
| 안내 텍스트 | ✅ | 음성 안내 내용 | `횡단보도 앞입니다` |
| 오디오 파일 경로 | - | Pi 내 MP3 경로 | `audio/crosswalk.mp3` |
| 우선순위 | - | 낮을수록 높은 우선순위 | `1` |

3. **[ROI 추가]** → **[💾 전체 저장]** 클릭
4. 카메라 앱이 60초 이내 자동 반영

---

## 코드 변경 후 재배포

| 변경 내용 | 명령어 |
|-----------|--------|
| 카메라 앱 코드만 수정 | `make sync` |
| ROI 에디터만 수정 | `make sync-roi-editor` |
| 모든 파일 재전송 | `make deploy` |
| 카메라 앱 의존성 변경 | `make deps` |
| ROI 에디터 의존성 변경 | `make deps-roi-editor` |

---

## 포트 정리

| 포트 | 서비스 | 접근 URL |
|------|--------|----------|
| `8080` | MJPEG 카메라 스트리밍 | `http://192.168.0.89:8080/stream.mjpg` |
| `5000` | ROI 웹 에디터 | `http://192.168.0.89:5000` |

> Pi IP가 바뀐 경우: `make deploy PI=<새IP>` 형식으로 덮어쓸 수 있습니다.

---

## tmux로 백그라운드 실행

SSH 접속이 끊겨도 프로세스를 유지하려면 tmux를 사용합니다.

```bash
ssh ailab@192.168.0.89

# 세션 시작
tmux new -s visionguide

# 창 1: 카메라 앱 실행
cd ~/visionguide
~/.pyenv/versions/3.10.14/bin/python camera_live_pi.py --headless --port 8080 --roi-config rois.json
```

```
Ctrl+B, C    새 창 열기
```

```bash
# 창 2: ROI 에디터 실행
cd ~/visionguide
~/.pyenv/versions/3.10.14/bin/python roi_editor/server.py
```

```
Ctrl+B, D    세션 분리 (SSH 끊어도 계속 실행)
```

재접속 후 복귀:
```bash
tmux attach -t visionguide
```

창 전환: `Ctrl+B, 0` (창 1) / `Ctrl+B, 1` (창 2)

---

## Makefile 전체 타겟 레퍼런스

```
make deploy             전체 배포 (파일 전송 + 의존성 설치)
make sync               카메라 앱 파일만 재전송
make sync-roi-editor    ROI 에디터 파일만 재전송
make deps               카메라 앱 의존성 설치
make deps-roi-editor    ROI 에디터 의존성 설치 (fastapi, uvicorn)
make run-headless       Pi에서 카메라 앱 + MJPEG 스트리밍 시작 (포트 8080)
make run-roi-editor     Pi에서 ROI 웹 에디터 시작 (포트 5000)
make run                Pi에서 디스플레이 모드 실행 (모니터 연결 시)
make setup-pi-python310 Pi에 Python 3.10 설치 (최초 1회)
make ping               Pi 연결 및 환경 확인
make help               전체 도움말 출력

기본값: PI=192.168.0.89  USER=ailab
IP 변경: make <target> PI=<새IP>
```

---

## 트러블슈팅

### SSH 연결 실패

| 증상 | 원인 | 해결 |
|------|------|------|
| `Connection refused` | Pi SSH 비활성 | `sudo systemctl start ssh` |
| `Connection timed out` | IP 오류 | Pi에서 `hostname -I` 재확인 |
| `Host key verification failed` | Pi 재설치로 키 변경 | `ssh-keygen -R 192.168.0.89` 후 재시도 |
| 비밀번호 계속 요구 | 공개키 미등록 | `ssh-copy-id ailab@192.168.0.89` 재실행 |

### ROI 에디터 스트림이 안 보임

- `make run-headless`가 먼저 실행 중인지 확인
- 브라우저에서 `http://192.168.0.89:8080/stream.mjpg` 직접 접근해서 스트림 확인

### rois.json 저장 후 카메라 앱에 반영 안 됨

- 카메라 앱의 핫리로드 주기는 **60초**입니다. 최대 60초 대기 후 확인
- 즉시 반영하려면 카메라 앱을 재시작하세요

### TFLite 모델을 찾지 못하는 경우

```bash
# 모델 파일만 재전송
make sync

# PC에도 없으면 변환
yolo export model=runs/white_cane_v1-2/weights/best.pt format=tflite int8=True
```

### MP3가 재생되지 않는 경우

```bash
# mpg123 설치 확인
which mpg123
sudo apt install -y mpg123

# 오디오 장치 볼륨 확인
amixer sset PCM 90%

# 수동 재생 테스트
mpg123 ~/visionguide/audio/crosswalk.mp3
```

### FPS가 낮은 경우 (목표 ≥ 10 FPS)

- `camera_live_pi.py` 내 `_INPUT_SIZE = 640` → `320`으로 낮추고 `make sync`
- 카메라 해상도 파라미터 조정 검토
