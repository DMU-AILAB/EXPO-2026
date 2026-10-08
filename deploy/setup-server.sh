#!/usr/bin/env bash
# VisionGuide 대시보드 서버 설치 — Linux/macOS (보조).
#
# 주 경로는 Windows 네이티브(setup-server.bat / deploy/setup-server.ps1)다. 이 스크립트는 최소한만 한다:
#   venv · 의존성 · .env · DB · (Node가 있으면) 프런트 빌드 · 실행 방법 안내
# 방화벽·자동 시작은 건드리지 않는다 — 그건 OS마다 달라서 사람이 정한다.
#
# 판단 로직(.env 채우기·관리자 비밀번호)은 deploy/server_setup.py에 있다. 멱등이다.
#
#   bash deploy/setup-server.sh [--port 8000] [--reset-env] [--rebuild] [--start] [--dry-run]

set -Eeuo pipefail

PORT=8000; RESET_ENV=0; REBUILD=0; START=0; DRY_RUN=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="${2:?--port 값이 필요합니다}"; shift 2 ;;
    --reset-env) RESET_ENV=1; shift ;;
    --rebuild) REBUILD=1; shift ;;
    --start) START=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "알 수 없는 옵션: $1" >&2; exit 2 ;;
  esac
done
[[ "$PORT" =~ ^[0-9]+$ ]] || { echo "--port 는 숫자여야 합니다" >&2; exit 2; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/dashboard/backend"
FRONTEND="$ROOT/dashboard/frontend"
SETUP_PY="$ROOT/deploy/server_setup.py"

step() { printf '\n[*] %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '[!] %s\n' "$*" >&2; }
act() {   # act <설명> <명령...> — 변경하는 일은 전부 여기를 거친다
  local desc="$1"; shift
  if [[ $DRY_RUN -eq 1 ]]; then info "[dry-run] $desc"; else info "$desc"; "$@"; fi
}

# WSL에서는 Pi가 서버에 닿지 못하는 일이 많다(NAT/미러 모드, Hyper-V 방화벽) — 이 프로젝트에서 실제로 겪었다.
if grep -qiE 'microsoft|wsl' /proc/version 2>/dev/null; then
  warn "WSL에서 실행 중입니다 — 기기(Pi)가 이 서버에 닿지 않을 수 있습니다."
  warn "서버는 Windows 네이티브로 돌리는 것을 권장합니다: setup-server.bat (개발·테스트 용도라면 계속하세요)"
fi

step "Python 확인"
PYTHON=""
for cand in python3.11 python3.12 python3.10 python3; do
  command -v "$cand" >/dev/null 2>&1 && PYTHON="$(command -v "$cand")" && break
done
[[ -n "$PYTHON" ]] || { echo "[ERROR] Python 3.10 이상이 필요합니다" >&2; exit 1; }
"$PYTHON" "$SETUP_PY" check --port "$PORT" >/dev/null || { echo "[ERROR] Python 3.10 이상이 필요합니다 ($("$PYTHON" --version 2>&1))" >&2; exit 1; }
info "Python: $PYTHON ($("$PYTHON" --version 2>&1))"

step "백엔드 환경 (venv · 의존성)"
VENV="$BACKEND/.venv"
[[ -x "$VENV/bin/python" ]] || act "venv 생성: $VENV" "$PYTHON" -m venv "$VENV"
VENV_PY="$VENV/bin/python"
act "pip install -r requirements.txt" "$VENV_PY" -m pip install --disable-pip-version-check -q -r "$BACKEND/requirements.txt"

step ".env · 관리자 계정 · DB"
ENV_ARGS=(env --backend "$BACKEND" --port "$PORT"); [[ $RESET_ENV -eq 1 ]] && ENV_ARGS+=(--reset)
ADMIN_PW=""; PW_FILE=""
if [[ $DRY_RUN -eq 1 ]]; then
  info "[dry-run] python server_setup.py ${ENV_ARGS[*]}"
else
  RESULT="$("$VENV_PY" "$SETUP_PY" "${ENV_ARGS[@]}")"
  ADMIN_PW="$(printf '%s' "$RESULT" | "$VENV_PY" -c 'import json,sys; print(json.load(sys.stdin).get("admin_password") or "")')"
  PW_FILE="$(printf '%s' "$RESULT" | "$VENV_PY" -c 'import json,sys; print(json.load(sys.stdin).get("password_file") or "")')"
  info ".env: $(printf '%s' "$RESULT" | "$VENV_PY" -c 'import json,sys; print(json.load(sys.stdin)["status"])')"
fi
act "python -m app.db.init_db" bash -c "cd '$BACKEND' && '$VENV_PY' -m app.db.init_db"

step "프런트 빌드"
if [[ $REBUILD -eq 0 && -f "$FRONTEND/dist/index.html" ]]; then
  info "dist가 이미 있습니다 (--rebuild로 다시 빌드)"
elif command -v npm >/dev/null 2>&1; then
  act "npm ci && npm run build" bash -c "cd '$FRONTEND' && npm ci && npm run build"
else
  warn "npm이 없어 프런트를 빌드하지 못했습니다 — Node.js LTS를 설치한 뒤 다시 실행하세요 (없어도 API는 동작합니다)"
fi

LAN="$("$PYTHON" "$SETUP_PY" lan-ip | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin).get("lan_ip") or "localhost")')"
printf '\n====================================================\n VisionGuide 대시보드 서버 준비 완료\n====================================================\n'
printf '  접속 주소 : http://%s:%s\n  관리자 ID : admin\n' "$LAN" "$PORT"
if [[ -n "$ADMIN_PW" ]]; then printf '  비밀번호  : %s\n              (저장 위치: %s)\n' "$ADMIN_PW" "$PW_FILE"
elif [[ $DRY_RUN -eq 1 ]]; then printf '  비밀번호  : (새로 만들면 여기에 표시됩니다)\n'
else printf '  비밀번호  : 기존 값을 유지했습니다 (%s/data/admin-password.txt)\n' "$BACKEND"; fi
printf '\n  실행      : cd %s && .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port %s\n' "$BACKEND" "$PORT"
printf '              (--reload/--workers 는 쓰지 마세요 — 하트비트 버퍼·스케줄러가 한 프로세스 안에 있습니다)\n'
printf '  방화벽    : TCP %s, UDP 48555 를 열어 두세요 (기기가 서버에 닿고 서버를 찾는 데 필요합니다)\n' "$PORT"
[[ $DRY_RUN -eq 1 ]] && printf '\n(dry-run — 아무것도 바꾸지 않았습니다)\n'

if [[ $START -eq 1 && $DRY_RUN -eq 0 ]]; then
  step "서버 시작 (Ctrl+C로 종료)"
  cd "$BACKEND" && exec "$VENV_PY" -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
fi
