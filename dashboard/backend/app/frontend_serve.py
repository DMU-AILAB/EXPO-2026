"""프런트(`dist`)를 백엔드가 같은 포트로 내보낸다 — Node·Vite·CORS 없이 서버 한 프로세스로 끝낸다.

`dist/index.html`이 있을 때만 켠다. 없으면(개발 모드: `vite dev` + `uvicorn --reload`) 아무것도
건드리지 않아 `/`가 예전 상태 JSON 그대로다.

SPA 폴백(`BrowserRouter`)이 **API·WebSocket·문서 경로의 404를 가리면 안 된다** — 오타 난 `/api/...`가
index.html(200)로 돌아오면 클라이언트가 성공으로 오해한다. 그래서 그 접두사는 JSON 404를 유지한다.
라우터가 전부 등록된 **뒤에** 호출해야 한다(폴백이 `/{path}`를 먹기 때문).
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

# 폴백하지 않을 최상위 경로 — 접두사가 아니라 **첫 세그먼트가 정확히 일치**할 때만 제외한다.
_API_SEGMENTS = {"api", "ws"}
_API_EXACT = {"docs", "redoc", "openapi.json"}


def default_dist() -> Path:
    # dashboard/backend/app/frontend_serve.py → parents[2] = dashboard/
    return Path(__file__).resolve().parents[2] / "frontend" / "dist"


def mount_frontend(app: FastAPI, dist: Path | str | None = None) -> bool:
    """`dist`를 서빙하도록 앱에 붙인다. 붙였으면 True."""
    root = Path(dist) if dist else default_dist()
    index = root / "index.html"
    if not index.is_file():
        return False
    root = root.resolve()

    assets = root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="frontend-assets")

    def _not_found() -> HTTPException:
        return HTTPException(status_code=404, detail="Not Found")

    @app.get("/", include_in_schema=False)
    async def _spa_root():
        return FileResponse(index)

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa_fallback(full_path: str):
        first = full_path.split("/", 1)[0]
        if first in _API_SEGMENTS or full_path in _API_EXACT:
            raise _not_found()
        # 루트 직하 정적 파일(favicon 등)은 그대로, 그 외는 SPA가 라우팅한다.
        candidate = (root / full_path).resolve()
        if candidate.is_file() and root in candidate.parents:      # 경로 탈출 차단
            return FileResponse(candidate)
        return FileResponse(index)

    logger.info("프런트 정적 서빙을 켭니다: %s", root)
    return True
