#!/usr/bin/env python3
"""Pi ROI Web Editor — FastAPI 서버

포트 5000에서 실행. rois.json CRUD + 정적 파일 서빙.
같은 Wi-Fi의 PC/스마트폰 브라우저에서 http://<Pi-IP>:5000 으로 접속.

실행:
    python roi_editor/server.py
    python roi_editor/server.py --rois /home/ailab/visionguide/rois.json --port 5000
"""
import argparse
import json
import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Paths (overridden by CLI args at startup)
# ---------------------------------------------------------------------------
_DEFAULT_ROIS = Path(__file__).parent.parent / "rois.json"
rois_path: Path = _DEFAULT_ROIS
STATIC_DIR = Path(__file__).parent / "static"

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="VisionGuide ROI Editor", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _load() -> dict:
    try:
        return json.loads(rois_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"rois": []}


def _save(data: dict) -> None:
    """atomic write — camera_live_pi.py가 동시에 읽어도 안전."""
    rois_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=rois_path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, rois_path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/rois")
async def get_rois():
    return _load()


class RoisPayload(BaseModel):
    rois: list


@app.post("/api/rois")
async def post_rois(payload: RoisPayload):
    _save({"rois": payload.rois})
    return {"ok": True, "count": len(payload.rois)}


@app.delete("/api/rois/{name}")
async def delete_roi(name: str):
    data = _load()
    before = len(data["rois"])
    data["rois"] = [r for r in data["rois"] if r["name"] != name]
    if len(data["rois"]) == before:
        raise HTTPException(status_code=404, detail=f"ROI '{name}' not found")
    _save(data)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description="VisionGuide ROI Web Editor")
    parser.add_argument("--rois", default=str(_DEFAULT_ROIS), help="rois.json 경로")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    rois_path = Path(args.rois).resolve()
    print(f"[ROI Editor] rois.json: {rois_path}")
    print(f"[ROI Editor] 브라우저: http://<Pi-IP>:{args.port}")

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
