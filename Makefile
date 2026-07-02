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

PI      ?= 192.168.0.89
USER    ?= ailab
DEST     = $(USER)@$(PI):~/visionguide

# Pi Python 경로: pyenv 3.10 우선, 없으면 시스템 python3
# pyenv 설치 후 make deploy PI_PYTHON=~/.pyenv/versions/3.10.14/bin/python 으로 덮어쓰기 가능
PI_PYTHON ?= python3
PI_PIP    ?= pip3

# Pi에 배포할 Python 소스 — 새 파일 추가 시 여기에 추가
DEPLOY_PY = \
	camera_live_pi.py \
	detect.py \
	edgetpu_infer.py

# Pi에 배포할 모델 파일
DEPLOY_MODEL = runs/white_cane_v1-2/weights/best_int8.tflite

.PHONY: deploy sync sync-roi-editor deps deps-roi-editor \
        install-edgetpu-py39 setup-pi-python310 \
        run-headless run run-roi-editor ping help

help:
	@echo "VisionGuide Pi 배포 도구"
	@echo ""
	@echo "  make deploy          [PI=<ip>]  전체 배포 (카메라 앱 + ROI 에디터)"
	@echo "  make sync            [PI=<ip>]  카메라 앱 파일만 재전송"
	@echo "  make sync-roi-editor [PI=<ip>]  ROI 에디터 파일만 재전송"
	@echo "  make deps            [PI=<ip>]  카메라 앱 의존성 설치"
	@echo "  make deps-roi-editor [PI=<ip>]  ROI 에디터 의존성 설치 (fastapi, uvicorn)"
	@echo "  make run-headless    [PI=<ip>]  Pi에서 MJPEG 스트리밍 시작 (포트 8080)"
	@echo "  make run-roi-editor  [PI=<ip>]  Pi에서 ROI 웹 에디터 시작 (포트 5000)"
	@echo "  make run             [PI=<ip>]  Pi에서 디스플레이 모드 실행"
	@echo "  make ping            [PI=<ip>]  Pi 연결 및 환경 확인"
	@echo ""
	@echo "  현재 기본값: PI=$(PI)  USER=$(USER)  PI_PYTHON=$(PI_PYTHON)"
	@echo ""
	@echo "  pyenv Python 3.10 사용 시:"
	@echo "    make deploy PI_PYTHON=~/.pyenv/versions/3.10.14/bin/python PI_PIP=~/.pyenv/versions/3.10.14/bin/pip"

## 전체 배포 (카메라 앱 + ROI 에디터 파일 전송 + 의존성 설치)
deploy: sync sync-roi-editor deps deps-roi-editor
	@echo "[완료] $(PI) 전체 배포 완료"

## Pi로 카메라 앱 파일만 전송
sync:
	@echo "[SYNC] $(DEST) 으로 카메라 앱 파일 전송..."
	ssh $(USER)@$(PI) "mkdir -p ~/visionguide/runs/white_cane_v1-2/weights"
	rsync -avz --progress $(DEPLOY_PY) $(DEST)/
	rsync -avz --progress $(DEPLOY_MODEL) $(DEST)/runs/white_cane_v1-2/weights/

## Pi로 ROI 에디터 파일만 전송
sync-roi-editor:
	@echo "[SYNC] ROI 에디터 파일 전송..."
	ssh $(USER)@$(PI) "mkdir -p ~/visionguide/roi_editor/static"
	rsync -avz --progress roi_editor/ $(DEST)/roi_editor/
	rsync -avz --progress simulator/roi_manager.py $(DEST)/simulator/
	ssh $(USER)@$(PI) "mkdir -p ~/visionguide/simulator && touch ~/visionguide/simulator/__init__.py"

## Pi에 카메라 앱 의존성 설치
deps:
	@echo "[DEPS] 카메라 앱 의존성 설치..."
	ssh $(USER)@$(PI) "sudo apt-get install -y python3-picamera2 fonts-nanum || true"
	ssh $(USER)@$(PI) "$(PI_PIP) install --break-system-packages -q ai-edge-litert opencv-python-headless numpy shapely pillow"

## Pi에 ROI 에디터 의존성 설치 (fastapi + uvicorn)
deps-roi-editor:
	@echo "[DEPS] ROI 에디터 의존성 설치..."
	ssh $(USER)@$(PI) "$(PI_PIP) install --break-system-packages -q 'fastapi>=0.100.0' 'uvicorn[standard]>=0.20.0'"

## Python 3.9 EdgeTPU 전용 패키지 설치 (Python 3.9 빌드 후 실행)
install-edgetpu-py39:
	@echo "[PY39] Python 3.9 EdgeTPU 의존성 설치..."
	ssh $(USER)@$(PI) "~/.python39/bin/pip3 install -q \
		'https://github.com/google-coral/pycoral/releases/download/v2.0.0/tflite_runtime-2.5.0.post1-cp39-cp39-linux_aarch64.whl' \
		'numpy<2' \
		opencv-python-headless"
	@echo "[완료] Python 3.9 EdgeTPU 의존성 설치 완료"

## Pi Python 3.10 환경 일회성 설치 (pyenv 이용)
setup-pi-python310:
	@echo "[SETUP] Pi에 Python 3.10 설치 (pyenv)..."
	ssh $(USER)@$(PI) "curl https://pyenv.run | bash || true"
	ssh $(USER)@$(PI) "grep -q 'pyenv init' ~/.bashrc || echo 'export PYENV_ROOT=\"\$$HOME/.pyenv\"\nexport PATH=\"\$$PYENV_ROOT/bin:\$$PATH\"\neval \"\$$(pyenv init -)\"' >> ~/.bashrc"
	ssh $(USER)@$(PI) "~/.pyenv/bin/pyenv install -s 3.10.14 && ~/.pyenv/bin/pyenv global 3.10.14"
	@echo "[완료] Python 3.10 설치 완료. 확인: make ping"

## Pi에서 headless MJPEG 스트리밍 시작 (모니터 없는 경우)
run-headless:
	@echo "[RUN] 스트리밍 주소: http://$(PI):8080/stream.mjpg"
	ssh -t $(USER)@$(PI) "cd ~/visionguide && $(PI_PYTHON) camera_live_pi.py --headless --port 8080"

## Pi에서 ROI 웹 에디터 실행 (브라우저에서 http://PI:5000 접속)
run-roi-editor:
	@echo "[ROI Editor] 브라우저에서 http://$(PI):5000 으로 접속하세요"
	@echo "[ROI Editor] camera_live_pi.py 를 먼저 실행해야 스트림이 표시됩니다 (make run-headless)"
	ssh -t $(USER)@$(PI) "cd ~/visionguide && $(PI_PYTHON) roi_editor/server.py --rois ~/visionguide/rois.json"

## Pi에서 디스플레이 모드 실행 (모니터 연결된 경우)
run:
	ssh -t $(USER)@$(PI) "cd ~/visionguide && $(PI_PYTHON) camera_live_pi.py"

## Pi 연결 및 배포 환경 확인
ping:
	ssh $(USER)@$(PI) "$(PI_PYTHON) --version && ls ~/visionguide/ 2>/dev/null || echo '(아직 배포 전)'"
