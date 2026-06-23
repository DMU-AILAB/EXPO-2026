# =============================================================================
# VisionGuide — Pi 배포 자동화
# 필요: rsync + ssh  (Git Bash 또는 WSL)
#       Windows에서 make 미설치 시: scoop install make  /  choco install make
#
# 사용법:
#   make deploy PI=192.168.0.10        전체 배포 (파일 전송 + 의존성 설치)
#   make sync   PI=192.168.0.10        파일만 재전송 (코드 변경 후 빠른 업데이트)
#   make deps   PI=192.168.0.10        의존성만 설치
#   make run-headless PI=192.168.0.10  Pi에서 MJPEG 스트리밍 시작
#   make run    PI=192.168.0.10        Pi에서 디스플레이 모드 실행
#   make ping   PI=192.168.0.10        Pi 연결 및 환경 확인
#
# PI, USER 기본값을 아래에서 수정해두면 make deploy 만으로 실행 가능
# =============================================================================

PI   ?= raspberrypi.local
USER ?= pi
DEST  = $(USER)@$(PI):~/visionguide

# Pi에 배포할 Python 소스 — 새 파일 추가 시 여기에 추가
DEPLOY_PY = \
	camera_live_pi.py \
	detect.py

# Pi에 배포할 모델 파일
DEPLOY_MODEL = runs/white_cane_v1-2/weights/best_int8.tflite

.PHONY: deploy sync deps run-headless run ping help

help:
	@echo "VisionGuide Pi 배포 도구"
	@echo ""
	@echo "  make deploy [PI=<ip>] [USER=<user>]   파일 전송 + 의존성 설치 (전체 배포)"
	@echo "  make sync   [PI=<ip>]                 파일만 재전송 (코드 변경 후)"
	@echo "  make deps   [PI=<ip>]                 의존성만 설치"
	@echo "  make run-headless [PI=<ip>]           Pi에서 MJPEG 스트리밍 시작"
	@echo "  make run    [PI=<ip>]                 Pi에서 디스플레이 모드 실행"
	@echo "  make ping   [PI=<ip>]                 Pi 연결 및 환경 확인"
	@echo ""
	@echo "  현재 기본값: PI=$(PI)  USER=$(USER)"

## 전체 배포 (파일 전송 + 의존성 설치)
deploy: sync deps
	@echo "[완료] $(PI) 배포 완료"

## Pi로 파일만 전송
sync:
	@echo "[SYNC] $(DEST) 으로 파일 전송..."
	ssh $(USER)@$(PI) "mkdir -p ~/visionguide/runs/white_cane_v1-2/weights"
	rsync -avz --progress $(DEPLOY_PY) $(DEST)/
	rsync -avz --progress $(DEPLOY_MODEL) $(DEST)/runs/white_cane_v1-2/weights/

## Pi에 Python 의존성 설치
deps:
	@echo "[DEPS] Pi 의존성 설치..."
	ssh $(USER)@$(PI) "sudo apt-get install -y python3-picamera2 || true"
	ssh $(USER)@$(PI) "pip install -q tflite-runtime opencv-python-headless numpy"

## Pi에서 headless MJPEG 스트리밍 시작 (모니터 없는 경우)
run-headless:
	@echo "[RUN] 스트리밍 주소: http://$(PI):8080/stream.mjpg"
	ssh -t $(USER)@$(PI) "cd ~/visionguide && python camera_live_pi.py --headless --port 8080"

## Pi에서 디스플레이 모드 실행 (모니터 연결된 경우)
run:
	ssh -t $(USER)@$(PI) "cd ~/visionguide && python camera_live_pi.py"

## Pi 연결 및 배포 환경 확인
ping:
	ssh $(USER)@$(PI) "python3 --version && ls ~/visionguide/ 2>/dev/null || echo '(아직 배포 전)'"
