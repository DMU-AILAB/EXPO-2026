#!/usr/bin/env bash
# VisionGuide — Pi 부트스트랩 설치.
#
# Pi에서 한 줄로 코드 설치 + systemd 유닛 + sudoers + 의존성 + (선택) 서버 등록을 한다.
# SSH·make·PC 쪽 준비가 필요 없다. 새 SD카드와 구버전 기기(푸시 업데이트를 받지 못하는)를 모두 대상으로 한다.
#
# 대시보드 "기기 추가 → 새 기기 설치"가 토큰이 든 명령을 만들어 준다:
#   bash <(curl -fsSL "http://<서버>/api/bootstrap/install.sh?token=<토큰>")
#
# 먼저 무엇을 할지만 보려면 --dry-run (아무것도 바꾸지 않고, sudo 비밀번호도 묻지 않는다).
#
# 단일 출처: 유닛·sudoers 목록은 서버가 `Makefile` install-service의 scp 목록으로 만든 에셋에서
# 오고, 아래 패키지 목록은 Makefile의 deps / deps-roi-editor와 같아야 한다
# (tests/test_installer_matches_makefile.py가 대조한다).
#
# 모든 단계는 멱등이다 — 여러 번 실행해도 안전하고, rois.json·camera_config.json·
# device_identity.json 같은 런타임 파일은 건드리지 않는다.

set -Eeuo pipefail

SERVER_URL="${SERVER_URL:-__SERVER_URL__}"
TOKEN="${TOKEN:-}"
INSTALL_USER="${INSTALL_USER:-$(id -un)}"
PYTHON_BIN="${PYTHON_BIN:-}"
DRY_RUN=0
UNITS_ONLY=0
NO_REGISTER=0
MODELS="default"        # none | default | all — 서버 번들 안의 모델 범위
AP_PASSWORD="${AP_PASSWORD:-visionguide}"
# ★ 서버 번들의 기본 모델 = dashboard/backend/app/services/bundle_builder.py BOOTSTRAP_MODELS가 가리키는
# camera_config.MODEL_VARIANTS의 weights_dir. 이미 있으면 다시 받지 않는 데 쓴다
# (tests/test_installer_matches_makefile.py가 대조한다).
BOOTSTRAP_MODEL_DIRS=(runs/white_cane_v15_vid_s2/weights runs/white_cane_v10_nolkc/weights)
# ★ deploy/auto_ap.sh의 AP_IP와 같아야 한다(같은 테스트가 대조). 프로필 *이름*은 코드가 전환에 쓰는 이름이다.
AP_CONNECTION="VisionGuide-AP"
AP_IP="192.168.4.1"

# ★ Makefile의 deps(apt) + install-service(iptables). 바꾸면 Makefile도 같이 바꿀 것.
APT_PACKAGES=(python3-picamera2 fonts-nanum mpg123 uhubctl iptables)
# ★ Makefile의 deps(pip) + deps-roi-editor.
PIP_PACKAGES=(ai-edge-litert spidev opencv-python-headless numpy shapely pillow gpiozero lgpio dbus-next bleak "fastapi>=0.100.0" "uvicorn[standard]>=0.20.0" python-multipart)

usage() {
  cat <<'EOF'
사용법: install.sh [옵션]

  --server URL        VisionGuide 서버 주소 (기본: 이 스크립트를 내려준 서버)
  --token TOKEN       설치 토큰 — 코드 내려받기와 서버 등록에 쓴다
  --user NAME         서비스를 돌릴 사용자 (기본: 지금 사용자)
  --python PATH       파이썬 경로 (기본: pyenv 3.10 → python3)
  --models MODE       받을 모델: default(현행 v15 + 예비 v10, 약 6MB — 기본) | all(전부, 수십 MB) | none
                      기본 모델이 이미 있으면 default는 다시 받지 않는다
  --include-models    --models all 과 같다
  --ap-password PW    핫스팟 프로필(VisionGuide-AP)을 새로 만들 때의 비밀번호 (8자 이상, 기본: visionguide)
                      프로필이 이미 있으면 건드리지 않는다
  --units-only        코드는 건드리지 않고 유닛·sudoers만 다시 설치한다
  --no-register       서버 등록을 건너뛴다 (이후 대시보드 기기 추가에서 등록)
  --dry-run           실제 변경 없이 단계와 명령만 출력한다
  -h, --help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --server) SERVER_URL="${2:?--server 값이 필요합니다}"; shift 2 ;;
    --token) TOKEN="${2:?--token 값이 필요합니다}"; shift 2 ;;
    --user) INSTALL_USER="${2:?--user 값이 필요합니다}"; shift 2 ;;
    --python) PYTHON_BIN="${2:?--python 값이 필요합니다}"; shift 2 ;;
    --models) MODELS="${2:?--models 값이 필요합니다}"; shift 2 ;;
    --include-models) MODELS="all"; shift ;;
    --ap-password) AP_PASSWORD="${2:?--ap-password 값이 필요합니다}"; shift 2 ;;
    --units-only) UNITS_ONLY=1; shift ;;
    --no-register) NO_REGISTER=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "알 수 없는 옵션: $1" >&2; usage >&2; exit 2 ;;
  esac
done

case "$MODELS" in none|default|all) ;; *) echo "--models 는 none|default|all 중 하나여야 합니다: $MODELS" >&2; exit 2 ;; esac
# WPA2 비밀번호는 8~63자다 — 나중에 nmcli가 실패해 설치가 중간에 멈추지 않도록 지금 거른다.
if [[ ${#AP_PASSWORD} -lt 8 || ${#AP_PASSWORD} -gt 63 ]]; then
  echo "--ap-password 는 8~63자여야 합니다" >&2; exit 2
fi

SERVER_URL="${SERVER_URL%/}"
APP_DIR="${APP_DIR:-/home/${INSTALL_USER}/visionguide}"   # 환경변수로 바꿀 수 있다(테스트용)
CURRENT_STEP="시작"
STEP_NO=0

step() { STEP_NO=$((STEP_NO + 1)); CURRENT_STEP="$*"; printf '\n[%d] %s\n' "$STEP_NO" "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '    ! %s\n' "$*" >&2; }
die()  { printf '\n[실패] %s\n       (단계: %s)\n' "$*" "$CURRENT_STEP" >&2; exit 1; }
trap 'printf "\n[중단] 단계 \"%s\"에서 실패했습니다. 같은 명령을 다시 실행해도 안전합니다.\n" "$CURRENT_STEP" >&2' ERR

# 변경하는 명령은 전부 여기를 거친다 — --dry-run이 한 곳에서 막는다.
run() {
  if [[ $DRY_RUN -eq 1 ]]; then printf '    [dry-run] %s\n' "$*"; return 0; fi
  "$@"
}

# 서버가 내려줄 때만 주소가 치환된다. 저장소의 원본을 그대로 실행하면 자리표시자가 남아 있다.
# (아래 문자열은 일부러 둘로 쪼갰다 — 서버의 치환이 이 줄까지 바꾸면 주소가 지워진다.)
PLACEHOLDER="__SERVER""_URL__"
[[ "$SERVER_URL" == "$PLACEHOLDER" ]] && SERVER_URL=""

# --------------------------------------------------------------------------- 1. 사전 점검
step "사전 점검"
[[ "$(id -u)" -ne 0 ]] || die "root로 실행하지 마세요 — 서비스가 돌 일반 사용자로 실행하면 필요할 때만 sudo를 씁니다"
for cmd in curl tar; do command -v "$cmd" >/dev/null || die "$cmd 가 필요합니다 (sudo apt-get install $cmd)"; done
[[ "$(uname -m)" == "aarch64" || "$(uname -m)" == "armv7l" ]] || warn "라즈베리파이가 아닌 구조($(uname -m))입니다 — 계속합니다"
if [[ $UNITS_ONLY -eq 0 ]]; then
  [[ -n "$SERVER_URL" ]] || die "서버 주소가 없습니다 (--server URL)"
  [[ -n "$TOKEN" || $DRY_RUN -eq 1 ]] || die "설치 토큰이 없습니다 (--token) — 대시보드에서 설치 명령을 새로 만드세요"
fi
if [[ $DRY_RUN -eq 0 ]]; then
  info "sudo 비밀번호를 한 번 묻습니다 (이후 단계는 묻지 않습니다)"
  sudo -v || die "sudo 권한이 필요합니다"
else
  info "dry-run — sudo·네트워크·파일 변경을 하지 않습니다"
fi
info "사용자: ${INSTALL_USER}   설치 위치: ${APP_DIR}   서버: ${SERVER_URL:-(없음)}"

# --------------------------------------------------------------------------- 2. 파이썬
step "파이썬 선택"
if [[ -z "$PYTHON_BIN" ]]; then
  # Makefile의 PI_PYTHON 규칙: pyenv 3.10 → 시스템 python3. systemd에 절대경로를 넘긴다.
  for cand in "/home/${INSTALL_USER}"/.pyenv/versions/3.10*/bin/python; do
    [[ -x "$cand" ]] && PYTHON_BIN="$cand" && break
  done
  [[ -n "$PYTHON_BIN" ]] || PYTHON_BIN="$(command -v python3 || true)"
fi
[[ -n "$PYTHON_BIN" ]] || die "python3를 찾지 못했습니다"
PYTHON_BIN="$(readlink -f "$PYTHON_BIN" 2>/dev/null || echo "$PYTHON_BIN")"
info "파이썬: ${PYTHON_BIN} ($("$PYTHON_BIN" --version 2>&1 || echo '?'))"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

models_present() {   # 기본 모델 가중치가 모두 이미 있는가
  local d
  for d in "${BOOTSTRAP_MODEL_DIRS[@]}"; do [[ -f "${APP_DIR}/${d}/best_int8.tflite" ]] || return 1; done
}

# 실제로 받을 모델 범위. default는 이미 갖춰져 있으면 none으로 낮춘다(6MB를 매번 받지 않는다).
FETCH_MODELS="$MODELS"
if [[ "$MODELS" == "default" ]] && models_present; then FETCH_MODELS="none"; fi

fetch() {   # fetch <경로> <저장 파일> — 토큰은 쿼리로 보낸다
  local path="$1" out="$2" sep='?'
  [[ "$path" == *\?* ]] && sep='&'
  curl -fsS --retry 2 --connect-timeout 5 -o "$out" "${SERVER_URL}${path}${sep}token=${TOKEN}" \
    || die "${path} 를 내려받지 못했습니다 — 서버 주소·토큰(30분 유효)·방화벽을 확인하세요"
}

# --------------------------------------------------------------------------- 3. 에셋 내려받기
step "서버에서 설치 파일 받기"
if [[ $DRY_RUN -eq 1 ]]; then
  info "[dry-run] ${SERVER_URL}/api/bootstrap/{self_update.py,assets.tar.gz$([[ $UNITS_ONLY -eq 0 ]] && echo ',bundle.tar.gz')}"
  [[ $UNITS_ONLY -eq 1 ]] || info "[dry-run] 모델: --models ${MODELS}$([[ "$FETCH_MODELS" != "$MODELS" ]] && echo " (기본 모델이 이미 있어 받지 않음)")"
else
  fetch /api/bootstrap/self_update.py "$WORK/self_update.py"
  fetch /api/bootstrap/assets.tar.gz "$WORK/assets.tar.gz"
  [[ $UNITS_ONLY -eq 1 ]] || fetch "/api/bootstrap/bundle.tar.gz?models=${FETCH_MODELS}" "$WORK/bundle.tar.gz"
  mkdir -p "$WORK/assets" && tar -xzf "$WORK/assets.tar.gz" -C "$WORK/assets"
  info "받음: $(ls "$WORK" | tr '\n' ' ')"
fi

# --------------------------------------------------------------------------- 4. 코드 적용
if [[ $UNITS_ONLY -eq 0 ]]; then
  step "코드 적용 (경로 검증·sha256·문법 검사 후 백업하고 교체)"
  run mkdir -p "$APP_DIR"
  # 검증 로직을 셸로 다시 만들지 않고 푸시 업데이트와 같은 모듈을 쓴다.
  # 의존성이 아직 없어 roi_editor 스모크는 뒤(8단계)로 미룬다.
  if [[ $DRY_RUN -eq 1 ]]; then
    info "[dry-run] ${PYTHON_BIN} self_update.py apply bundle.tar.gz --dest ${APP_DIR} --no-smoke"
  else
    "$PYTHON_BIN" "$WORK/self_update.py" apply "$WORK/bundle.tar.gz" --dest "$APP_DIR" --no-smoke \
      $([[ "$FETCH_MODELS" != "none" ]] && echo --include-models) || die "코드 번들 적용에 실패했습니다 (기기의 파일은 그대로입니다)"
  fi
fi

# --------------------------------------------------------------------------- 5. 의존성
step "의존성 설치"
if [[ $UNITS_ONLY -eq 1 ]]; then
  info "--units-only — 건너뜁니다"
else
  run sudo apt-get install -y "${APT_PACKAGES[@]}" || warn "일부 apt 패키지를 설치하지 못했습니다 — 계속합니다 (Makefile deps도 || true)"
  # Debian Bookworm 이후의 pip는 --break-system-packages가 필요하고, 옛 pip는 그 옵션을 모른다.
  run "$PYTHON_BIN" -m pip install --break-system-packages -q "${PIP_PACKAGES[@]}" \
    || run "$PYTHON_BIN" -m pip install -q "${PIP_PACKAGES[@]}" \
    || die "pip 패키지를 설치하지 못했습니다"
fi

# --------------------------------------------------------------------------- 6. 유닛·sudoers
step "systemd 유닛과 sudoers 설치"
UNIT_NAMES=()
if [[ $DRY_RUN -eq 1 ]]; then
  info "[dry-run] 에셋의 *.service → /etc/systemd/system, *.sudoers → /etc/sudoers.d (visudo -cf 검증 후), auto_ap.sh → ${APP_DIR}/deploy"
else
  for f in "$WORK"/assets/*.service; do
    name="$(basename "$f" .service)"; UNIT_NAMES+=("$name")
    sed "s|__USER__|${INSTALL_USER}|g; s|__PI_PYTHON__|${PYTHON_BIN}|g" "$f" > "$WORK/$name.service"
    sudo install -m 644 -o root -g root "$WORK/$name.service" "/etc/systemd/system/$name.service"
    info "유닛: $name"
  done
  for f in "$WORK"/assets/*.sudoers; do
    name="$(basename "$f" .sudoers)"
    # visionguide-network.sudoers만 사용자명이 'ailab'으로 박혀 있다 — 다른 계정이면 바꾼다.
    sed "s|__USER__|${INSTALL_USER}|g; s|^ailab |${INSTALL_USER} |" "$f" > "$WORK/$name.sudoers"
    # ★ 설치 전에 검증한다. 깨진 sudoers를 먼저 넣으면 이후 sudo가 전부 막힌다.
    sudo visudo -cf "$WORK/$name.sudoers" >/dev/null || die "sudoers 문법 오류: $name (설치하지 않았습니다)"
    sudo install -m 440 -o root -g root "$WORK/$name.sudoers" "/etc/sudoers.d/$name"
    info "sudoers: $name"
  done
  mkdir -p "$APP_DIR/deploy"
  install -m 755 "$WORK/assets/auto_ap.sh" "$APP_DIR/deploy/auto_ap.sh"
fi

# --------------------------------------------------------------------------- 7. 핫스팟 프로필
# Wi-Fi가 없을 때 auto_ap.sh·Wi-Fi 버튼이 `nmcli connection up VisionGuide-AP`로 전환한다. 프로필이
# 없으면 그 폴백이 조용히 실패한다 — 저장소 어디에도 이 프로필을 만드는 코드가 없었다(손으로 만든 것).
# ★ 있으면 건드리지 않는다: 기존 기기는 SSID·IP가 다른 프로필(예: VisionGuide-Pi/192.168.50.1)을 쓴다.
step "핫스팟 프로필 (${AP_CONNECTION})"
AP_STATUS="만들지 못함(nmcli 없음)"
HAVE_NMCLI=0; command -v nmcli >/dev/null 2>&1 && HAVE_NMCLI=1
if [[ $HAVE_NMCLI -eq 0 && $DRY_RUN -eq 0 ]]; then
  warn "nmcli가 없습니다 — 핫스팟 프로필을 만들 수 없습니다 (NetworkManager 환경이 아닙니다)"
elif [[ $HAVE_NMCLI -eq 1 ]] && nmcli -t -f NAME connection show 2>/dev/null | grep -Fxq "$AP_CONNECTION"; then
  AP_STATUS="기존 프로필 유지"
  info "이미 있습니다 — 그대로 둡니다"
else
  AP_STATUS="새로 만듦 (SSID ${AP_CONNECTION}, ${AP_IP}, 비밀번호 ${AP_PASSWORD})"
  # autoconnect no: 평소에는 홈 Wi-Fi를 쓰고, 연결이 없을 때만 auto_ap.sh가 올린다.
  run sudo nmcli connection add type wifi ifname wlan0 con-name "$AP_CONNECTION" autoconnect no \
    ssid "$AP_CONNECTION" mode ap 802-11-wireless.band bg \
    ipv4.method shared ipv4.addresses "${AP_IP}/24" \
    wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$AP_PASSWORD" \
    || { warn "핫스팟 프로필 생성에 실패했습니다 — 계속합니다 (Wi-Fi가 없을 때 핫스팟 폴백이 동작하지 않습니다)"; AP_STATUS="생성 실패"; }
fi

# --------------------------------------------------------------------------- 8. 서비스 시작
step "서비스 시작 (탐지가 몇 초 끊깁니다)"
run sudo systemctl daemon-reload
run sudo usermod -aG bluetooth "$INSTALL_USER"
run sudo systemctl enable --now bluetooth || warn "bluetooth 서비스를 켜지 못했습니다 — BLE 페어링만 영향이 있습니다"
if [[ $DRY_RUN -eq 0 ]]; then
  run sudo systemctl enable --now "${UNIT_NAMES[@]}"
  # 이미 떠 있던 서비스는 enable --now로 재시작되지 않으므로 새 코드를 읽게 다시 시작한다.
  [[ $UNITS_ONLY -eq 1 ]] || run sudo systemctl restart visionguide-device visionguide-controls visionguide-roi-editor
else
  info "[dry-run] sudo systemctl enable --now <유닛들> && restart visionguide-{device,controls,roi-editor}"
fi

# --------------------------------------------------------------------------- 9. 검증
step "동작 확인"
if [[ $DRY_RUN -eq 1 ]]; then
  info "[dry-run] http://127.0.0.1:5000/api/version 응답과 서비스 상태를 확인합니다"
else
  ok=0
  for _ in $(seq 1 30); do
    if curl -fsS -m 2 http://127.0.0.1:5000/api/version >/dev/null 2>&1; then ok=1; break; fi
    sleep 1
  done
  [[ $ok -eq 1 ]] || die "roi_editor가 30초 안에 응답하지 않습니다 — journalctl -u visionguide-roi-editor -n 30"
  info "roi_editor 응답 확인"
  for u in visionguide-device visionguide-roi-editor visionguide-controls; do
    info "$u: $(systemctl is-active "$u" 2>/dev/null || true)"
  done
fi

# --------------------------------------------------------------------------- 10. 서버 등록
step "서버 등록"
if [[ $NO_REGISTER -eq 1 || $UNITS_ONLY -eq 1 || -z "$TOKEN" ]]; then
  info "건너뜁니다 — 대시보드 '기기 추가'에서 이 기기를 찾아 등록하세요"
elif [[ $DRY_RUN -eq 1 ]]; then
  info "[dry-run] POST ${SERVER_URL}/api/bootstrap/register  (서버가 이 기기에 신원을 심습니다)"
else
  body="$("$PYTHON_BIN" -c 'import json,sys; print(json.dumps({"token": sys.argv[1], "hostname": sys.argv[2]}))' "$TOKEN" "$(hostname)")"
  resp="$(curl -fsS -m 60 -X POST -H 'Content-Type: application/json' -d "$body" "${SERVER_URL}/api/bootstrap/register")" \
    || die "서버 등록에 실패했습니다 — 토큰이 만료됐거나(30분) 한 번 쓴 토큰일 수 있습니다. 대시보드에서 명령을 새로 만드세요"
  "$PYTHON_BIN" - "$resp" <<'PY'
import json, sys
d = json.loads(sys.argv[1]).get("data", {})
print(f"    등록: {d.get('device_id')}  신원 주입: {'성공' if d.get('provisioned') else '실패 — ' + str(d.get('provision_error'))}")
PY
fi

# --------------------------------------------------------------------------- 요약
printf '\n요약\n'
if [[ $DRY_RUN -eq 1 ]]; then
  info "모델: (dry-run — 변경 없음) --models ${MODELS}"
else
  have=0; total=${#BOOTSTRAP_MODEL_DIRS[@]}
  for d in "${BOOTSTRAP_MODEL_DIRS[@]}"; do [[ -f "${APP_DIR}/${d}/best_int8.tflite" ]] && have=$((have + 1)); done
  info "모델: 기본 ${total}개 중 ${have}개 설치됨"
  [[ $have -eq $total ]] || warn "기본 모델이 모두 있지 않습니다 — 모델이 없는 카메라는 탐지하지 못합니다 (--models default 로 다시 실행)"
fi
info "핫스팟 프로필: ${AP_STATUS}"
if [[ $DRY_RUN -eq 0 ]] && command -v rpicam-hello >/dev/null 2>&1; then
  cams="$(timeout 8 rpicam-hello --list-cameras 2>/dev/null | grep -cE '^[[:space:]]*[0-9]+ : ' || true)"
  info "카메라: ${cams:-0}대 감지"
  [[ "${cams:-0}" -gt 0 ]] || warn "카메라가 감지되지 않았습니다 — 케이블·연결을 확인하세요"
fi

printf '\n완료. 대시보드에서 이 기기가 "온라인"으로 바뀌는지 확인하세요.\n'
[[ $DRY_RUN -eq 1 ]] && printf '(dry-run — 아무것도 바꾸지 않았습니다)\n'
exit 0
