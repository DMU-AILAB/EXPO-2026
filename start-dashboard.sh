#!/usr/bin/env bash
# VisionGuide 대시보드 한 번에 실행 — Linux/WSL용 (Windows는 start-dashboard.bat).
#
#   ./start-dashboard.sh          백엔드(:8001) + 프론트엔드(:5173)를 함께 띄운다. Ctrl+C로 둘 다 종료.
#
# 백엔드는 `--workers`/`--reload`를 쓰지 않는다 — 하트비트 메모리 버퍼와 APScheduler가 한 프로세스 안에 있다.
# 의존성(pip/npm)·DB는 자동으로 설치하지 않는다: 없으면 안내만 하고 멈춘다(최초 설치는 deploy/setup-server.sh).

set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/dashboard/backend"
FRONTEND="$ROOT/dashboard/frontend"
# 8000이 이미 쓰이고 있어 8001. 바꾸면 dashboard/frontend/.env의 VITE_API_BASE도 같은 포트로 맞출 것.
BACKEND_PORT="${BACKEND_PORT:-8001}"

# 백엔드 파이썬: deploy/setup-server.sh가 만든 venv → 활성화된 conda/venv → PATH의 python3 순.
PY=""
for cand in "$BACKEND/.venv/bin/python" "$ROOT/.venv/bin/python" "${CONDA_PREFIX:-/nonexistent}/bin/python" "$(command -v python3 || true)"; do
  if [[ -x "$cand" ]] && "$cand" -c "import uvicorn, fastapi" 2>/dev/null; then PY="$cand"; break; fi
done
[[ -n "$PY" ]] || { echo "[ERROR] uvicorn/fastapi가 있는 파이썬을 찾지 못했습니다." >&2
  echo "        pip install -r dashboard/backend/requirements.txt  또는  bash deploy/setup-server.sh" >&2; exit 1; }
[[ -d "$FRONTEND/node_modules" ]] || { echo "[ERROR] 프론트엔드 의존성이 없습니다: cd dashboard/frontend && npm ci" >&2; exit 1; }
[[ -f "$BACKEND/data/visionguide.db" ]] || echo "[!] DB가 없습니다. 최초 1회: cd dashboard/backend && python -m app.db.init_db"

pids=()
cleanup() { trap - INT TERM EXIT; kill "${pids[@]}" 2>/dev/null || true; wait 2>/dev/null || true; }
trap cleanup INT TERM EXIT

echo "백엔드 시작: $PY"
( cd "$BACKEND" && exec "$PY" -m uvicorn app.main:app --host 0.0.0.0 --port "$BACKEND_PORT" --workers 1 ) & pids+=($!)
echo "프론트엔드 시작"
( cd "$FRONTEND" && exec npm run dev -- --host 0.0.0.0 ) & pids+=($!)

echo
echo "대시보드: http://localhost:5173   (백엔드 http://localhost:$BACKEND_PORT)   종료: Ctrl+C"
# WSL이면 Windows 기본 브라우저로 연다. 실패해도 무시.
( sleep 3; { command -v wslview >/dev/null && wslview http://localhost:5173; } \
  || { command -v xdg-open >/dev/null && xdg-open http://localhost:5173; } ) >/dev/null 2>&1 &

wait -n "${pids[@]}" || true
