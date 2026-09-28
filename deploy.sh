#!/usr/bin/env bash
# =============================================================================
# VisionGuide Pi 자동 배포 스크립트
#
# 사용법 (Git Bash):
#   ./deploy.sh 192.168.0.89              # IP만 입력
#   ./deploy.sh 192.168.0.89 mypassword   # 비밀번호도 지정
#   ./deploy.sh                           # 대화식으로 IP 입력
#
# 하는 일:
#   1. SSH 연결 확인
#   2. device + roi_editor 파일 배포 (make sync sync-roi-editor)
#   3. sudoers 자동 설치 (없는 경우 — 이후 make restart가 비밀번호 없이 됨)
#   4. amixer 볼륨 100% 설정
#   5. 서비스 재시작 및 상태 확인
# =============================================================================

set -euo pipefail

PI="${1:-}"
PI_PASS="${2:-12345678}"
PI_USER="${PI_USER:-ailab}"

# ── 대화식 IP 입력 ────────────────────────────────────────────────────────────
if [[ -z "$PI" ]]; then
    read -rp "Pi IP 주소를 입력하세요: " PI
fi
[[ -z "$PI" ]] && { echo "오류: IP가 없습니다."; exit 1; }

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

SSH="ssh ${PI_USER}@${PI}"
SCP="scp"

bar() { printf '\n\e[36m══ %s\e[0m\n' "$*"; }
ok()  { printf '  \e[32m✓\e[0m %s\n' "$*"; }
err() { printf '  \e[31m✗\e[0m %s\n' "$*"; }
info(){ printf '  \e[90m→\e[0m %s\n' "$*"; }

bar "VisionGuide 배포 → ${PI}"

# ── 1. 연결 확인 ──────────────────────────────────────────────────────────────
bar "1/5  SSH 연결 확인"
if $SSH -o ConnectTimeout=8 -o BatchMode=yes "echo OK" >/dev/null 2>&1; then
    ok "키 인증으로 연결됨"
else
    # sshpass가 없으면 비밀번호 인증 우회 불가 — 연결 실패 안내
    if command -v sshpass >/dev/null 2>&1; then
        export SSHPASS="$PI_PASS"
        SSH="sshpass -e ssh ${PI_USER}@${PI}"
        SCP="sshpass -e scp"
        if $SSH -o ConnectTimeout=8 "echo OK" >/dev/null 2>&1; then
            ok "비밀번호 인증으로 연결됨 (sshpass)"
        else
            err "$PI 에 연결할 수 없습니다. IP와 계정을 확인하세요."
            exit 1
        fi
    else
        err "키 인증 실패. SSH 키를 등록하거나 sshpass를 설치하세요."
        info "키 등록: ssh-copy-id ${PI_USER}@${PI}"
        info "sshpass 설치 (Ubuntu/WSL): sudo apt-get install sshpass"
        exit 1
    fi
fi

# ── 2. 파일 배포 ──────────────────────────────────────────────────────────────
bar "2/5  파일 배포"
make sync sync-roi-editor PI="$PI" PI_USER="$PI_USER"
ok "device + roi_editor 전송 완료"

# ── 3. sudoers 자동 설치 ─────────────────────────────────────────────────────
bar "3/5  sudoers 확인"
if $SSH "test -f /etc/sudoers.d/visionguide-systemctl" 2>/dev/null; then
    ok "sudoers 이미 설치됨 — 건너뜀"
else
    info "visionguide-systemctl sudoers 설치 중..."
    SUDOERS_TMP="/tmp/vg_systemctl_$$.sudoers"
    $SCP "deploy/visionguide-systemctl.sudoers" "${PI_USER}@${PI}:${SUDOERS_TMP}" 2>/dev/null || \
        { err "sudoers 업로드 실패"; exit 1; }
    $SSH "sed -i \"s|__USER__|${PI_USER}|g\" ${SUDOERS_TMP} && \
          echo '${PI_PASS}' | sudo -S install -m 440 ${SUDOERS_TMP} /etc/sudoers.d/visionguide-systemctl && \
          echo '${PI_PASS}' | sudo -S visudo -cf /etc/sudoers.d/visionguide-systemctl && \
          rm -f ${SUDOERS_TMP}" 2>/dev/null && \
        ok "sudoers 설치 완료 — 이후 make restart 비밀번호 불필요" || \
        { err "sudoers 설치 실패 (비밀번호 오류?)"; }
fi

# ── 4. 볼륨 + 서비스 재시작 ──────────────────────────────────────────────────
bar "4/5  볼륨 100% + 서비스 재시작"
$SSH "amixer -c 2 sset 'PCM' 100% 2>/dev/null || true" && ok "볼륨 100%" || true
$SSH "sudo systemctl restart visionguide-device visionguide-roi-editor 2>/dev/null || \
      (echo '${PI_PASS}' | sudo -S systemctl restart visionguide-device 2>/dev/null && \
       echo '${PI_PASS}' | sudo -S systemctl restart visionguide-roi-editor 2>/dev/null)"
sleep 4

# ── 5. 결과 확인 ──────────────────────────────────────────────────────────────
bar "5/5  결과 확인"
STATUS=$($SSH "systemctl is-active visionguide-device visionguide-roi-editor" 2>/dev/null | tr '\n' ' ')
ok "서비스 상태: ${STATUS}"

LOG=$($SSH "journalctl -u visionguide-device -n 3 --no-pager -o cat" 2>/dev/null || true)
[[ -n "$LOG" ]] && info "$LOG"

printf '\n\e[32m══ 배포 완료 ══ http://%s:5000  스트림: http://%s:8080/stream.mjpg\e[0m\n\n' "$PI" "$PI"
