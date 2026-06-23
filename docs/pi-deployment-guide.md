# Raspberry Pi 배포 가이드

**프로젝트:** VisionGuide — 흰 지팡이 탐지 엣지 디바이스
**대상 보드:** Raspberry Pi 4 Model B
**OS 권장:** Raspberry Pi OS 64-bit Bookworm (Python 3.11 내장)
**최종 수정:** 2026-06-23

---

## 목차

1. [구조 개요](#1-구조-개요)
2. [사전 준비](#2-사전-준비)
3. [Pi 초기 설정](#3-pi-초기-설정)
4. [SSH 키 등록 (최초 1회)](#4-ssh-키-등록-최초-1회)
5. [첫 배포](#5-첫-배포)
6. [일상적인 재배포](#6-일상적인-재배포)
7. [Pi에서 실행](#7-pi에서-실행)
8. [배포 도구 레퍼런스](#8-배포-도구-레퍼런스)
9. [트러블슈팅](#9-트러블슈팅)

---

## 1. 구조 개요

### PC → Pi 배포 파일

| 파일 | 설명 |
|------|------|
| `camera_live_pi.py` | Pi 전용 추론 뷰어 (TFLite / Coral / PyTorch 자동 선택) |
| `detect.py` | PyTorch fallback용 탐지 모듈 |
| `runs/white_cane_v1-2/weights/best_int8.tflite` | TFLite INT8 양자화 모델 |

### 추론 백엔드 우선순위 (자동 선택)

```
1순위  Google Coral Edge TPU   best_int8_edgetpu.tflite  (Coral 연결 시)
2순위  TFLite INT8 CPU         best_int8.tflite          (기본 동작)
3순위  PyTorch / ultralytics   best.pt                   (fallback)
```

대부분의 경우 **2순위 TFLite INT8**로 동작합니다.

---

## 2. 사전 준비

### PC (Windows)

| 항목 | 확인 방법 |
|------|-----------|
| Windows 10 21H2 이상 (OpenSSH 내장) | `ssh -V` |
| PowerShell 5.1 이상 | `$PSVersionTable.PSVersion` |
| Git for Windows | `git --version` |
| (선택) GNU Make — Makefile 사용 시 | `scoop install make` 또는 `choco install make` |

### Raspberry Pi

| 항목 | 비고 |
|------|------|
| Raspberry Pi OS 64-bit Bookworm | [다운로드](https://www.raspberrypi.com/software/) |
| Python 3.11 | Bookworm 기본 내장 |
| 인터넷 연결 (의존성 설치용) | 와이파이 또는 이더넷 |

---

## 3. Pi 초기 설정

### 3-1. SD 카드 굽기

[Raspberry Pi Imager](https://www.raspberrypi.com/software/)를 사용할 경우, **OS 커스터마이즈** 단계에서 아래를 설정하면 모니터·키보드 없이도 바로 SSH 접속 가능합니다.

```
호스트명:  raspberrypi
사용자명:  pi
SSH:       Enable
Wi-Fi:     SSID / 비밀번호 입력
```

### 3-2. SSH 활성화 (이미 Pi를 사용 중인 경우)

Pi에 모니터 + 키보드를 연결하여 실행합니다.

```bash
sudo systemctl enable ssh
sudo systemctl start ssh
sudo systemctl status ssh   # Active: active (running) 확인
```

또는 `raspi-config` 메뉴로 설정합니다.

```bash
sudo raspi-config
# Interface Options → SSH → Enable
```

### 3-3. Pi IP 확인

```bash
# Pi 터미널에서
hostname -I        # 예: 192.168.0.89

# PC에서 (mDNS가 작동하는 경우)
ping raspberrypi.local
```

> **IP가 자주 바뀌는 경우** — 공유기 설정에서 Pi의 MAC 주소에 고정 IP를 할당하거나, 항상 `raspberrypi.local`(mDNS)로 접근하면 IP 변경에 영향을 받지 않습니다.

---

## 4. SSH 키 등록 (최초 1회)

비밀번호 없이 자동 배포하려면 SSH 키를 Pi에 등록해야 합니다.  
**이 단계에서만 Pi 비밀번호를 입력하며, 이후 배포 시에는 불필요합니다.**

### PowerShell 사용 (권장)

```powershell
# 프로젝트 폴더에서 실행
.\deploy.ps1 setup-ssh -PI 192.168.0.89
```

실행 흐름:

1. `~/.ssh/id_ed25519` 키가 없으면 자동 생성 (passphrase 없으려면 Enter 두 번)
2. Pi에 비밀번호 입력
3. 공개키가 Pi의 `~/.ssh/authorized_keys`에 등록됨

### 수동 등록 (참고)

```powershell
# SSH 키 생성
ssh-keygen -t ed25519 -C "visionguide"

# 공개키를 Pi에 복사 (Pi 비밀번호 입력)
Get-Content "$env:USERPROFILE\.ssh\id_ed25519.pub" | `
  ssh pi@192.168.0.89 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```

### 등록 확인

```powershell
.\deploy.ps1 ping -PI 192.168.0.89
```

비밀번호 프롬프트 없이 아래와 같이 출력되면 성공입니다.

```
[*] Pi 연결 확인: pi@192.168.0.89
Python 3.11.x
(아직 배포 전)
```

---

## 5. 첫 배포

SSH 키 등록 완료 후 전체 배포를 실행합니다. 파일 전송과 Pi 의존성 설치가 자동으로 진행됩니다.

### PowerShell

```powershell
.\deploy.ps1 deploy -PI 192.168.0.89
```

### Makefile (Git Bash / WSL)

```bash
make deploy PI=192.168.0.89
```

실행 과정:

```
[*] Pi 디렉토리 생성...
[*] Python 파일 전송...
[OK] camera_live_pi.py
[OK] detect.py
[*] 모델 파일 전송 (best_int8.tflite)...
[OK] best_int8.tflite
[*] Pi 의존성 설치 중...
[OK] 의존성 설치 완료
[완료] 192.168.0.89 배포 완료
```

### 설치되는 Pi 패키지

| 패키지 | 설치 방법 | 용도 |
|--------|-----------|------|
| `python3-picamera2` | apt | Pi Camera Module 드라이버 |
| `tflite-runtime` | pip | TFLite INT8 추론 엔진 |
| `opencv-python-headless` | pip | 영상 처리 (GUI 없는 버전) |
| `numpy` | pip | 수치 연산 |

---

## 6. 일상적인 재배포

코드를 수정한 뒤 Pi에 반영할 때는 `sync`만 실행하면 됩니다. 의존성은 이미 설치되어 있으므로 재설치하지 않아도 됩니다.

```powershell
# 코드 변경 후
.\deploy.ps1 sync -PI 192.168.0.89

# Makefile
make sync PI=192.168.0.89
```

### 새 Python 파일을 Pi에 추가 배포하는 경우

`Makefile`과 `deploy.ps1` 양쪽 모두에서 배포 파일 목록을 관리합니다.

**Makefile** — 상단 `DEPLOY_PY` 변수에 추가:

```makefile
DEPLOY_PY = \
    camera_live_pi.py \
    detect.py \
    새파일.py       # ← 여기에 추가
```

**deploy.ps1** — `$PyFiles` 배열에 추가:

```powershell
$PyFiles = @(
    "camera_live_pi.py",
    "detect.py",
    "새파일.py"     # ← 여기에 추가
)
```

---

## 7. Pi에서 실행

### 헤드리스 모드 (모니터 없는 경우, 권장)

PC에서 SSH로 Pi 실행을 원격 제어합니다.

```powershell
.\deploy.ps1 run-headless -PI 192.168.0.89

# Makefile
make run-headless PI=192.168.0.89
```

실행 후 PC 브라우저에서 스트리밍 확인:

```
http://192.168.0.89:8080/stream.mjpg
```

### 디스플레이 모드 (Pi에 모니터 연결된 경우)

```powershell
.\deploy.ps1 run -PI 192.168.0.89

# Makefile
make run PI=192.168.0.89
```

### Pi에서 직접 실행

SSH 접속 후 수동 실행합니다.

```bash
ssh pi@192.168.0.89
cd ~/visionguide

# 헤드리스
python camera_live_pi.py --headless --port 8080

# 디스플레이
python camera_live_pi.py

# 신뢰도 임계값 조정
python camera_live_pi.py --headless --conf 0.35
```

---

## 8. 배포 도구 레퍼런스

### deploy.ps1 (Windows PowerShell)

```
사용법: .\deploy.ps1 <action> [-PI <ip>] [-User <user>]

action:
  setup-ssh     SSH 키 생성 + Pi 등록 (최초 1회)
  deploy        파일 전송 + 의존성 설치
  sync          파일만 재전송
  deps          의존성만 설치
  run-headless  Pi에서 MJPEG 스트리밍 시작
  run           Pi에서 디스플레이 모드 실행
  ping          Pi 연결 및 환경 확인

기본값: PI=raspberrypi.local  User=pi

실행 권한 오류 시:
  Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

### Makefile (Git Bash / WSL)

```
사용법: make <target> [PI=<ip>] [USER=<user>]

target:
  deploy        파일 전송 + 의존성 설치
  sync          파일만 재전송
  deps          의존성만 설치
  run-headless  Pi에서 MJPEG 스트리밍 시작
  run           Pi에서 디스플레이 모드 실행
  ping          Pi 연결 확인

기본값: PI=raspberrypi.local  USER=pi

make 미설치 시:
  scoop install make  또는  choco install make
```

---

## 9. 트러블슈팅

### SSH 연결 실패

| 증상 | 원인 | 해결 |
|------|------|------|
| `Connection refused` | Pi에서 SSH 비활성화 | `sudo systemctl start ssh` |
| `Connection timed out` | IP 주소 오류 또는 네트워크 문제 | Pi에서 `hostname -I`로 IP 재확인 |
| `Host key verification failed` | Pi를 재설치했거나 IP가 재사용됨 | `ssh-keygen -R 192.168.0.89` 후 재시도 |
| 비밀번호 계속 물어봄 | SSH 키 등록 안 됨 | `.\deploy.ps1 setup-ssh` 재실행 |

### 한글 깨짐 (PowerShell)

```powershell
# PowerShell 세션에서 한 번 실행
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
```

`deploy.ps1` 상단에 이미 포함되어 있으나, 콘솔 설정에 따라 추가로 필요할 수 있습니다.

### TFLite 모델을 찾지 못하는 경우

```
[WARN] TFLite 모델 없음: runs/white_cane_v1-2/weights/best_int8.tflite
```

`best_int8.tflite`가 Pi에 없는 것입니다. 파일만 재전송합니다.

```powershell
.\deploy.ps1 sync -PI 192.168.0.89
```

PC에도 파일이 없다면 모델을 먼저 변환합니다.

```bash
yolo export model=runs/white_cane_v1-2/weights/best.pt format=tflite int8=True
```

### FPS가 너무 낮은 경우

Pi 4에서 TFLite INT8 기준 목표 FPS는 ≥ 10입니다.

```bash
# 입력 해상도 낮추기 (camera_live_pi.py 내 _INPUT_SIZE 조정)
_INPUT_SIZE = 320   # 기본값 640 → 320으로 변경 후 재배포

# 또는 카메라 해상도 낮추기
python camera_live_pi.py --headless  # _Picamera2Source의 size 파라미터 조정
```

### picamera2 초기화 실패

```
[WARN] picamera2 초기화 실패: ...
[WARN] OpenCV VideoCapture로 대체합니다.
```

Pi Camera Module이 연결되지 않았거나 활성화되지 않은 경우입니다.

```bash
# Pi에서 카메라 인터페이스 활성화
sudo raspi-config
# Interface Options → Camera → Enable → 재부팅
```

USB 웹캠을 사용하는 경우에는 이 경고를 무시해도 됩니다. OpenCV로 자동 전환됩니다.

---

## 부록. Pi 원격 디렉토리 구조

배포 완료 후 Pi의 `~/visionguide/` 디렉토리 구조입니다.

```
~/visionguide/
├── camera_live_pi.py
├── detect.py
└── runs/
    └── white_cane_v1-2/
        └── weights/
            ├── best_int8.tflite          ← 기본 추론 모델
            └── best_int8_edgetpu.tflite  ← Coral 사용 시 (별도 컴파일 필요)
```

### Coral Edge TPU 사용 (USB Accelerator)

> **중요:** `edgetpu_compiler`는 **x86-64 전용** 바이너리입니다. Raspberry Pi(aarch64)에서는
> 설치/실행되지 않습니다. **모델 컴파일은 PC(WSL/Linux)에서**, **Pi에는 런타임만** 설치합니다.

| 단계 | 명령 | 실행 위치 |
|------|------|-----------|
| 1. Pi 런타임 설치 | `.\deploy.ps1 coral-setup -PI <ip>` | PC → Pi (sudo 비밀번호 입력) |
| 2. 모델 컴파일 | `.\deploy.ps1 coral-compile` | PC의 WSL (x86-64) |
| 3. 컴파일 모델 전송 | `.\deploy.ps1 sync -PI <ip>` | PC → Pi |
| 4. 실행 (자동 선택) | `.\deploy.ps1 run-headless -PI <ip>` | Pi |

- **USB 연결:** Coral USB Accelerator를 Pi의 **USB 3.0 포트(파란색)** 에 꽂습니다.
  연결되면 드라이버 없이도 `lsusb`에 `1a6e:089a`(초기) 또는 `18d1:9302`(추론 후)로 표시됩니다.
- **WSL 미설치 시:** 관리자 PowerShell에서 `wsl --install -d Ubuntu` 실행 후 재부팅.
- 컴파일된 `best_int8_edgetpu.tflite`가 Pi에 있으면 `camera_live_pi.py`가 자동으로
  Edge TPU 백엔드를 1순위로 선택합니다.
