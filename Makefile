# =============================================================================
# VisionGuide — Pi 배포 자동화
# 필요: rsync + ssh  (Git Bash 또는 WSL)
#       Windows에서 make 미설치 시: scoop install make  /  choco install make
#
# 사용법:
#   make deploy PI=192.168.0.10        전체 배포 (파일 전송 + 의존성 설치)
#   make deploy PI="10.0.0.1 10.0.0.2"  여러 대에 차례로 배포
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
# 소스는 device/ 아래에 있지만 **Pi에는 ~/visionguide/ 에 평면으로 전개된다** —
# 기기의 디렉터리 구조와 systemd 유닛 경로는 이 재배치 전후로 달라지지 않는다.
DEPLOY_PY = \
	device/camera_live_pi.py \
	device/detect.py \
	device/edgetpu_infer.py \
	device/audio_trigger.py \
	device/announcement_router.py \
	device/kics_protocol.py \
	device/si4432_radio.py \
	device/rf_audio_trigger.py \
	device/gpio_controls.py \
	device/fan_controller.py \
	device/yolo_postprocess.py \
	device/simple_tracker.py \
	device/cane_person_assoc.py \
	device/pedestrian_entity.py \
	device/gate_chain.py \
	device/replay_engine.py \
	device/device_identity.py \
	device/event_logger.py \
	device/device_status.py \
	device/device_metrics.py \
	device/foot_traffic_counter.py \
	device/camera_config.py \
	device/detection_events.py \
	device/fp_hotspots.py

# Pi에 배포할 모델 파일 — 카메라 프로필의 model_variant로 선택되는 각 모델 디렉터리.
# 새 모델을 추가하려면 camera_config.py의 MODEL_VARIANTS와 함께 이 목록에도 추가할 것.
# (best.pt는 PyTorch fallback용이라 Pi엔 torch/ultralytics 자체를 설치하지 않으므로 배포 대상 아님)
DEPLOY_MODEL_DIRS = \
	runs/white_cane_v2/weights \
	runs/white_cane_v3_320/weights \
	runs/white_cane_v4_320/weights \
	runs/white_cane_v5b_ft320/weights \
	runs/white_cane_v6_ft320/weights \
	runs/white_cane_v10_nolkc/weights \
	runs/white_cane_v11_v26n/weights

.PHONY: deploy sync sync-roi-editor deps deps-roi-editor check-time setup-ntp \
        install-edgetpu-py39 setup-pi-python310 install-service \
        run-headless run run-roi-editor ping help

help:
	@echo "VisionGuide Pi 배포 도구"
	@echo ""
	@echo "  make deploy          [PI=<ip>]  전체 배포 (카메라 앱 + ROI 에디터)"
	@echo "  make sync            [PI=<ip>]  카메라 앱 파일만 재전송"
	@echo "  make sync-roi-editor [PI=<ip>]  ROI 에디터 파일만 재전송"
	@echo "  make deps            [PI=<ip>]  카메라 앱 의존성 설치"
	@echo "  make deps-roi-editor [PI=<ip>]  ROI 에디터 의존성 설치 (fastapi, uvicorn, python-multipart)"
	@echo "  make run-headless    [PI=<ip>]  Pi에서 MJPEG 스트리밍 시작 (포트 8080, 수동 실행/테스트용)"
	@echo "  make run-roi-editor  [PI=<ip>]  Pi에서 ROI 웹 에디터 시작 (포트 5000, 수동 실행/테스트용)"
	@echo "  make run             [PI=<ip>]  Pi에서 디스플레이 모드 실행"
	@echo "  make install-service [PI=<ip>]  systemd 등록 — 부팅 시 완전 자동/headless 구동"
	@echo "  make ping            [PI=<ip>]  Pi 연결 및 환경 확인"
	@echo "  make check-time      [PI=<ip>]  시계 동기(NTP) 상태 확인"
	@echo "  make setup-ntp       [PI=<ip>]  시계 동기 활성화"
	@echo ""
	@echo "  ★ 기기가 여러 대면 주소를 공백으로 나열하면 한 대씩 차례로 실행합니다:"
	@echo "      make deploy PI=\"192.168.0.101 192.168.0.102 192.168.0.103\""
	@echo "    한 대가 실패해도 나머지는 계속하고, 마지막에 실패한 기기를 모아 보여줍니다."
	@echo ""
	@echo "  현재 기본값: PI=$(PI)  USER=$(USER)  PI_PYTHON=$(PI_PYTHON)"
	@echo ""
	@echo "  pyenv Python 3.10 사용 시:"
	@echo "    make deploy PI_PYTHON=~/.pyenv/versions/3.10.14/bin/python PI_PIP=~/.pyenv/versions/3.10.14/bin/pip"

# ── 다중 호스트 ──────────────────────────────────────────────────────────────
# PI에 공백으로 구분된 주소를 여러 개 주면 **한 대씩 차례로** 실행한다.
#   make deploy PI="192.168.0.101 192.168.0.102 192.168.0.103"
#
# 기기가 여러 대로 흩어지면 같은 명령을 대수만큼 반복해야 하는데, 한 대라도 빠뜨리면
# 그 기기만 구버전으로 남아 조용히 다르게 동작한다. 아래 재귀 호출은 기존 레시피를
# 한 줄도 바꾸지 않고 그 반복을 없앤다.
#
# **병렬로 돌리지 않는 이유**: 한 대가 실패했을 때 출력이 뒤섞여 어디서 멈췄는지 알 수
# 없게 된다. 3대 기준 순차로도 몇 분이면 끝난다.
# **한 대가 실패해도 나머지는 계속한다** — 3대 중 1대만 꺼져 있을 때 나머지 2대 배포까지
# 막을 이유가 없다. 대신 마지막에 실패한 기기를 모아 보여주고 종료코드를 낸다.
MULTI_TARGETS = deploy sync sync-roi-editor deps deps-roi-editor \
                install-service ping check-time setup-ntp

ifneq ($(word 2,$(PI)),)

$(MULTI_TARGETS):
	@fail=""; ok=0; \
	for h in $(PI); do \
		echo ""; echo "──────────── [$$h] $@ ────────────"; \
		if $(MAKE) --no-print-directory $@ PI=$$h USER=$(USER) \
			PI_PYTHON=$(PI_PYTHON) PI_PIP=$(PI_PIP); then \
			ok=$$((ok+1)); \
		else \
			fail="$$fail $$h"; \
		fi; \
	done; \
	echo ""; \
	if [ -n "$$fail" ]; then \
		echo "[실패] $@ — 성공 $$ok대 / 실패:$$fail"; exit 1; \
	else \
		echo "[완료] $@ — $(words $(PI))대 전부 성공"; \
	fi

else

## 전체 배포 (카메라 앱 + ROI 에디터 파일 전송 + 의존성 설치)
deploy: sync sync-roi-editor deps deps-roi-editor
	@echo "[완료] $(PI) 전체 배포 완료"

## Pi로 카메라 앱 파일만 전송
sync:
	@echo "[SYNC] $(DEST) 으로 카메라 앱 파일 전송..."
	ssh $(USER)@$(PI) "$(foreach d,$(DEPLOY_MODEL_DIRS),mkdir -p ~/visionguide/$(d) &&) true"
	rsync -avz --progress $(DEPLOY_PY) $(DEST)/
	rsync -avz --progress configs/examples/rf_config_example.json $(DEST)/
	@# 모델 파일은 **파일별로** 따로 전송한다. 한 rsync에 두 파일을 함께 넘기면
	@# EdgeTPU 컴파일본이 없는 모델 디렉터리에서 rsync가 "No such file" 로 실패해
	@# 배포 전체가 중단된다 — EdgeTPU 컴파일은 현재 범위 밖이라 v2 외에는 없다.
	@# best_int8_edgetpu.tflite는 있으면 보내고 없으면 건너뛴다.
	for d in $(DEPLOY_MODEL_DIRS); do \
		rsync -avz --progress $$d/best_int8.tflite $(DEST)/$$d/; \
		if [ -f $$d/best_int8_edgetpu.tflite ]; then \
			rsync -avz --progress $$d/best_int8_edgetpu.tflite $(DEST)/$$d/; \
		else \
			echo "[SKIP] $$d/best_int8_edgetpu.tflite 없음 (EdgeTPU 미컴파일 — CPU TFLite 경로로 동작)"; \
		fi; \
	done

## Pi로 ROI 에디터 파일만 전송
## 주의: roi_editor/server.py가 foot_traffic_counter.py(sync 타겟으로 배포됨)를
## import하므로, 최초 배포는 이 타겟만 단독 실행하지 말고 반드시 make deploy로 함께 배포할 것.
sync-roi-editor:
	@echo "[SYNC] ROI 에디터 파일 전송..."
	ssh $(USER)@$(PI) "mkdir -p ~/visionguide/roi_editor/static"
	rsync -avz --progress apps/roi_editor/ $(DEST)/roi_editor/
	rsync -avz --progress apps/simulator/roi_manager.py $(DEST)/simulator/
	ssh $(USER)@$(PI) "mkdir -p ~/visionguide/simulator && touch ~/visionguide/simulator/__init__.py"

## Pi에 카메라 앱 의존성 설치
deps:
	@echo "[DEPS] 카메라 앱 의존성 설치..."
	ssh $(USER)@$(PI) "sudo apt-get install -y python3-picamera2 fonts-nanum mpg123 uhubctl || true"
	ssh $(USER)@$(PI) "$(PI_PIP) install --break-system-packages -q ai-edge-litert spidev opencv-python-headless numpy shapely pillow gpiozero lgpio"

## Pi에 ROI 에디터 의존성 설치 (fastapi + uvicorn + 오디오 업로드용 python-multipart)
deps-roi-editor:
	@echo "[DEPS] ROI 에디터 의존성 설치..."
	ssh $(USER)@$(PI) "$(PI_PIP) install --break-system-packages -q 'fastapi>=0.100.0' 'uvicorn[standard]>=0.20.0' python-multipart"

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

## systemd 등록 — 부팅 시 카메라 앱 + ROI 에디터 + GPIO 재시작버튼이 완전 자동/headless 로 구동됨
## 이후로는 최초 ROI/오디오 설정 시에만 http://<Pi-IP>:5000 접속이 필요하고,
## 탐지·음성 안내 자체는 네트워크 연결 없이 기기 단독으로 계속 동작한다.
install-service:
	@echo "[SERVICE] systemd 유닛 설치..."
	rsync -avz deploy/visionguide-device.service deploy/visionguide-roi-editor.service deploy/visionguide-controls.service deploy/visionguide-fan.service deploy/visionguide-auto-ap.service deploy/visionguide-uhubctl.sudoers deploy/auto_ap.sh $(USER)@$(PI):/tmp/
	ssh $(USER)@$(PI) "sed -i 's|__USER__|$(USER)|g; s|__PI_PYTHON__|$(PI_PYTHON)|g' /tmp/visionguide-device.service /tmp/visionguide-roi-editor.service /tmp/visionguide-controls.service /tmp/visionguide-fan.service /tmp/visionguide-auto-ap.service"
	ssh $(USER)@$(PI) "sudo mv /tmp/visionguide-device.service /tmp/visionguide-roi-editor.service /tmp/visionguide-controls.service /tmp/visionguide-fan.service /tmp/visionguide-auto-ap.service /etc/systemd/system/ && sudo chmod +x /tmp/auto_ap.sh && sudo mkdir -p /home/$(USER)/visionguide/deploy && sudo mv /tmp/auto_ap.sh /home/$(USER)/visionguide/deploy/"
	ssh $(USER)@$(PI) "sudo apt-get install -y uhubctl iptables && sudo install -m 440 /tmp/visionguide-uhubctl.sudoers /etc/sudoers.d/visionguide-uhubctl && sudo visudo -cf /etc/sudoers.d/visionguide-uhubctl && sudo systemctl daemon-reload && sudo systemctl enable --now visionguide-device visionguide-roi-editor visionguide-controls visionguide-fan visionguide-auto-ap"
	@echo "[완료] 재부팅해도 자동 시작됩니다."
	@echo "       확인: ssh $(USER)@$(PI) sudo systemctl status visionguide-device"
	@echo "       ROI 에디터를 끄고 싶으면: ssh $(USER)@$(PI) sudo systemctl disable --now visionguide-roi-editor"

## Pi 연결 및 배포 환경 확인
ping:
	ssh $(USER)@$(PI) "$(PI_PYTHON) --version && ls ~/visionguide/ 2>/dev/null || echo '(아직 배포 전)'"

## Pi 시계 동기 상태 확인
##
## 기기가 여러 대면 **시계가 맞아야 데이터가 맞는다.** 이벤트 타임스탬프는 Pi가 찍어
## 서버로 보내므로(event_logger.py), 기기마다 시계가 어긋나면 서버에서 순서가 뒤바뀌고
## 시간대별 통계가 엉킨다. 나중에 카메라 간 핸드오프를 넣으면 "몇 초 전에 저쪽에서
## 사라졌다"를 비교하게 되는데, 그때는 어긋남이 곧 오작동이 된다.
check-time:
	@pi_t=$$(ssh $(USER)@$(PI) "date -u +%s" 2>/dev/null); \
	if [ -z "$$pi_t" ]; then echo "[$(PI)] 접속 실패"; exit 1; fi; \
	local_t=$$(date -u +%s); drift=$$((pi_t - local_t)); \
	sync=$$(ssh $(USER)@$(PI) "timedatectl show -p NTPSynchronized --value" 2>/dev/null); \
	printf "[%s] NTP동기=%s  PC와의 차이=%+ds\n" "$(PI)" "$${sync:-?}" "$$drift"; \
	if [ "$$sync" != "yes" ]; then \
		echo "  → 동기 안 됨. make setup-ntp PI=$(PI) 로 켜세요"; \
	elif [ $$drift -gt 2 ] || [ $$drift -lt -2 ]; then \
		echo "  → 차이가 2초를 넘습니다 (PC 시계가 틀렸을 수도 있음)"; \
	fi

## Pi에 NTP 시각 동기 활성화 (Raspberry Pi OS는 systemd-timesyncd 내장)
setup-ntp:
	@echo "[NTP] $(PI) 시각 동기 활성화..."
	ssh $(USER)@$(PI) "sudo timedatectl set-ntp true && sudo systemctl enable --now systemd-timesyncd"
	@echo "  동기까지 수십 초 걸릴 수 있습니다. 확인: make check-time PI=$(PI)"

endif
