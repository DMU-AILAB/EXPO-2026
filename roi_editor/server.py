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
import re
import sys
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# roi_editor/server.py는 roi_editor/ 서브디렉토리에서 실행되는 스크립트라
# sys.path[0]이 그 디렉토리가 된다 — 저장소 루트의 foot_traffic_counter.py를
# import하려면 루트를 sys.path에 직접 넣어줘야 한다.
sys.path.insert(0, str(Path(__file__).parent.parent))
from foot_traffic_counter import read_daily_totals  # noqa: E402

# ---------------------------------------------------------------------------
# Paths (overridden by CLI args at startup)
# ---------------------------------------------------------------------------
_DEFAULT_ROIS = Path(__file__).parent.parent / "rois.json"
_DEFAULT_AUDIO_DIR = Path(__file__).parent.parent / "audio"
_DEFAULT_TRAFFIC_DB = Path(__file__).parent.parent / "foot_traffic.db"
rois_path: Path = _DEFAULT_ROIS
audio_dir: Path = _DEFAULT_AUDIO_DIR
traffic_db_path: Path = _DEFAULT_TRAFFIC_DB
STATIC_DIR = Path(__file__).parent / "static"
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="VisionGuide ROI Editor", docs_url=None, redoc_url=None, openapi_url=None)
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


def _safe_filename(name: str) -> str:
    """업로드 파일명에서 경로 조작 문자를 제거하고 영숫자/./_/- 만 남긴다."""
    name = Path(name).name
    name = _SAFE_NAME_RE.sub("_", name)
    return name or "audio.mp3"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/rois")
async def get_rois():
    return _load()


@app.get("/api/stats")
async def get_stats():
    return read_daily_totals(traffic_db_path)


class RoisPayload(BaseModel):
    rois: list
    conf: float | None = None


@app.post("/api/rois")
async def post_rois(payload: RoisPayload):
    data = {"rois": payload.rois}
    conf = payload.conf if payload.conf is not None else _load().get("conf")
    if conf is not None:
        data["conf"] = conf
    _save(data)
    return {"ok": True, "count": len(payload.rois)}


@app.post("/api/audio/upload")
async def upload_audio(file: UploadFile = File(...)):
    """PC/폰에서 고른 오디오 파일을 Pi의 audio_dir에 저장하고 절대경로를 반환.

    반환된 경로를 그대로 ROI의 audio_file에 넣으면 camera_live_pi.py가
    같은 기기에서 바로 재생할 수 있다 (별도 다운로드/스트리밍 없음).
    """
    audio_dir.mkdir(parents=True, exist_ok=True)
    filename = _safe_filename(file.filename or "audio.mp3")
    dest = audio_dir / filename
    stem, suffix = dest.stem, dest.suffix
    n = 1
    while dest.exists():
        dest = audio_dir / f"{stem}_{n}{suffix}"
        n += 1
    content = await file.read()
    dest.write_bytes(content)
    return {"ok": True, "path": str(dest.resolve())}


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
    parser.add_argument("--audio-dir", default=str(_DEFAULT_AUDIO_DIR), help="업로드된 오디오 저장 경로")
    parser.add_argument("--traffic-db", default=str(_DEFAULT_TRAFFIC_DB),
                         help="유동인구 집계 sqlite 경로 (camera_live_pi.py --traffic-db와 동일해야 함)")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    rois_path = Path(args.rois).resolve()
    audio_dir = Path(args.audio_dir).resolve()
    traffic_db_path = Path(args.traffic_db).resolve()
    print(f"[ROI Editor] rois.json: {rois_path}")
    print(f"[ROI Editor] audio_dir: {audio_dir}")
    print(f"[ROI Editor] traffic_db: {traffic_db_path}")
    print(f"[ROI Editor] 브라우저: http://<Pi-IP>:{args.port}")

    uvicorn.run(
        app, host=args.host, port=args.port,
        log_level="warning", access_log=False, workers=1,
    )
