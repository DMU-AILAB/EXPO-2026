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
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from shapely.geometry import Polygon

# roi_editor/server.py는 roi_editor/ 서브디렉토리에서 실행되는 스크립트라
# sys.path[0]이 그 디렉토리가 된다 — 저장소 루트의 foot_traffic_counter.py/camera_config.py를
# import하려면 루트를 sys.path에 직접 넣어줘야 한다.
sys.path.insert(0, str(Path(__file__).parent.parent))
from foot_traffic_counter import read_daily_totals  # noqa: E402
from camera_config import (  # noqa: E402
    MODEL_VARIANTS, CameraProfile, load_camera_config, save_camera_config, validate_camera_config,
)
from yolo_postprocess import CLASS_NAMES  # noqa: E402

# ---------------------------------------------------------------------------
# Paths (overridden by CLI args at startup)
# ---------------------------------------------------------------------------
_DEFAULT_ROIS = Path(__file__).parent.parent / "rois.json"
_DEFAULT_AUDIO_DIR = Path(__file__).parent.parent / "audio"
_DEFAULT_TRAFFIC_DB = Path(__file__).parent.parent / "foot_traffic.db"
_DEFAULT_CAMERA_CONFIG = Path(__file__).parent.parent / "camera_config.json"
rois_path: Path = _DEFAULT_ROIS
audio_dir: Path = _DEFAULT_AUDIO_DIR
traffic_db_path: Path = _DEFAULT_TRAFFIC_DB
camera_config_path: Path = _DEFAULT_CAMERA_CONFIG
STATIC_DIR = Path(__file__).parent / "static"
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_VALID_ZONE_TYPES = {"trigger", "exclude"}

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="VisionGuide ROI Editor", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _load(path: Path | None = None) -> dict:
    path = path or rois_path
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"rois": []}


def _save(data: dict, path: Path | None = None) -> None:
    """atomic write — camera_live_pi.py가 동시에 읽어도 안전."""
    path = path or rois_path
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
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


def _resolve_camera_path(camera: str | None, field: str, default: Path) -> Path:
    """?camera=<id> 쿼리가 있으면 해당 카메라 프로필의 roi_config/traffic_db 경로를,
    없으면 기존 단일-카메라 기본 경로를 반환한다 (완전 하위호환).
    """
    if camera is None:
        return default
    profiles = load_camera_config(camera_config_path)
    for p in profiles:
        if p.id == camera:
            value = getattr(p, field)
            value_path = Path(value)
            return value_path if value_path.is_absolute() else (rois_path.parent / value_path).resolve()
    raise HTTPException(status_code=404, detail=f"camera '{camera}' not found")


def _read_device_status() -> dict:
    """Pi 상태(가동시간/CPU온도/부하/메모리)를 표준 라이브러리 /proc, /sys 파일만으로 읽는다.
    psutil 등 신규 의존성을 추가하지 않기 위해서다 (Pi에는 무거운 패키지를 최소화하는 방침).
    Pi가 아닌 환경(개발 PC 등)에서 실행되면 해당 항목만 조용히 null로 빠진다."""
    status: dict = {"uptime_seconds": None, "cpu_temp_c": None, "load_avg": None,
                     "mem_used_mb": None, "mem_total_mb": None}

    try:
        with open("/proc/uptime") as f:
            status["uptime_seconds"] = float(f.read().split()[0])
    except OSError:
        pass

    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            status["cpu_temp_c"] = round(int(f.read().strip()) / 1000.0, 1)
    except (OSError, ValueError):
        pass

    try:
        status["load_avg"] = list(os.getloadavg())
    except (OSError, AttributeError):
        pass

    try:
        meminfo = {}
        with open("/proc/meminfo") as f:
            for line in f:
                key, _, rest = line.partition(":")
                meminfo[key] = int(rest.strip().split()[0])  # kB
        if "MemTotal" in meminfo and "MemAvailable" in meminfo:
            status["mem_total_mb"] = round(meminfo["MemTotal"] / 1024, 1)
            status["mem_used_mb"] = round((meminfo["MemTotal"] - meminfo["MemAvailable"]) / 1024, 1)
    except OSError:
        pass

    return status


def _validate_rois(rois: list) -> list[str]:
    """폴리곤 유효성(Shapely) + zone_type 값을 검사해 에러 메시지 목록을 반환."""
    errors: list[str] = []
    for r in rois:
        name = r.get("name", "?")
        points = r.get("points", [])
        if len(points) >= 3 and not Polygon(points).is_valid:
            errors.append(f"'{name}': 폴리곤이 자체교차하는 등 유효하지 않습니다")
        zone_type = r.get("zone_type", "trigger")
        if zone_type not in _VALID_ZONE_TYPES:
            errors.append(f"'{name}': zone_type 값이 잘못됨 ({zone_type})")
    return errors


def _validate_conf(conf: float | dict | None) -> list[str]:
    """conf는 스칼라(모든 클래스 동일) 또는 {"white_cane": .., "person": ..} 클래스별
    딕셔너리일 수 있다 — 둘 다 0~1 범위인지 검사. 지금까지는 conf에 대한 검증이 전혀
    없었다(소수점이 밀려 55가 들어가도 그대로 저장됨) — 클래스별 필드를 새로 추가하는
    김에 같이 채워 넣는다."""
    if conf is None:
        return []

    def _in_range(name: str, v) -> list[str]:
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not (0.0 <= v <= 1.0):
            return [f"{name}: 0~1 사이 값이어야 함 ({v!r})"]
        return []

    if isinstance(conf, dict):
        errors: list[str] = []
        for name in CLASS_NAMES:
            if name not in conf:
                errors.append(f"conf: '{name}' 임계값이 없습니다")
            else:
                errors.extend(_in_range(f"conf.{name}", conf[name]))
        extra = sorted(set(conf) - set(CLASS_NAMES))
        if extra:
            errors.append(f"conf: 알 수 없는 클래스 {extra}")
        return errors

    return _in_range("conf", conf)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/rois")
async def get_rois(camera: str | None = None):
    path = _resolve_camera_path(camera, "roi_config", rois_path)
    return _load(path)


@app.get("/api/stats")
async def get_stats(camera: str | None = None):
    path = _resolve_camera_path(camera, "traffic_db", traffic_db_path)
    return read_daily_totals(path)


class RoisPayload(BaseModel):
    rois: list
    conf: float | dict[str, float] | None = None


@app.post("/api/rois")
async def post_rois(payload: RoisPayload, camera: str | None = None):
    path = _resolve_camera_path(camera, "roi_config", rois_path)
    errors = _validate_rois(payload.rois) + _validate_conf(payload.conf)
    if errors:
        raise HTTPException(status_code=400, detail=errors)
    data = {"rois": payload.rois}
    conf = payload.conf if payload.conf is not None else _load(path).get("conf")
    if conf is not None:
        data["conf"] = conf
    _save(data, path)
    return {"ok": True, "count": len(payload.rois)}


class CameraProfilePayload(BaseModel):
    id: str
    enabled: bool = True
    label: str = ""
    backend: str = "auto"
    source: str = "0"
    rotation: int = 0
    inference_backend: str = "auto"
    roi_config: str = "rois.json"
    port: int = 8080
    traffic_db: str = "foot_traffic.db"
    swap_rb: bool = False
    model_variant: str = "v2_640"


class CamerasPayload(BaseModel):
    cameras: list[CameraProfilePayload]


@app.get("/api/cameras")
async def get_cameras():
    profiles = load_camera_config(camera_config_path)
    return {"cameras": [asdict(p) for p in profiles]}


@app.get("/api/model-variants")
async def get_model_variants():
    """카메라 편집 UI가 드롭다운을 채울 때 쓰는 모델 목록 — camera_config.MODEL_VARIANTS가
    유일한 출처라서 UI에 라벨을 하드코딩해도 드리프트가 안 나지만, API로 노출해두면
    새 모델을 추가할 때 index.html을 건드릴 필요가 없다."""
    return {"variants": [{"key": k, **v} for k, v in MODEL_VARIANTS.items()]}


@app.get("/api/device/status")
async def get_device_status():
    return _read_device_status()


@app.get("/api/cameras/scan")
async def scan_cameras():
    """libcamera가 인식하는 카메라(CSI + UVC 지원 USB 캠 포함) 목록을 반환.

    Picamera2.global_camera_info()는 카메라를 열지 않고 목록만 조회하므로
    camera_live_pi.py가 이미 카메라를 점유한 상태에서 호출해도 안전하다.
    이미 camera_config.json에 등록된 source는 already_registered로 표시한다.
    """
    try:
        from picamera2 import Picamera2
        cameras = Picamera2.global_camera_info()
    except Exception as exc:
        return {"cameras": [], "error": str(exc)}

    registered_sources = {p.source for p in load_camera_config(camera_config_path)}
    return {
        "cameras": [
            {
                "num": cam["Num"],
                "model": cam.get("Model", "unknown"),
                "id": cam.get("Id", ""),
                "already_registered": str(cam["Num"]) in registered_sources,
            }
            for cam in cameras
        ]
    }


@app.post("/api/cameras")
async def post_cameras(payload: CamerasPayload):
    profiles = [CameraProfile(**c.model_dump()) for c in payload.cameras]
    errors = validate_camera_config(profiles)
    if errors:
        raise HTTPException(status_code=400, detail=errors)
    save_camera_config(camera_config_path, profiles)
    return {"ok": True, "count": len(profiles)}


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
async def delete_roi(name: str, camera: str | None = None):
    path = _resolve_camera_path(camera, "roi_config", rois_path)
    data = _load(path)
    before = len(data["rois"])
    data["rois"] = [r for r in data["rois"] if r["name"] != name]
    if len(data["rois"]) == before:
        raise HTTPException(status_code=404, detail=f"ROI '{name}' not found")
    _save(data, path)
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
    parser.add_argument("--camera-config", default=str(_DEFAULT_CAMERA_CONFIG),
                         help="다중 카메라 프로필 JSON 경로 (camera_live_pi.py --camera-config와 동일해야 함)")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    rois_path = Path(args.rois).resolve()
    audio_dir = Path(args.audio_dir).resolve()
    traffic_db_path = Path(args.traffic_db).resolve()
    camera_config_path = Path(args.camera_config).resolve()
    print(f"[ROI Editor] rois.json: {rois_path}")
    print(f"[ROI Editor] audio_dir: {audio_dir}")
    print(f"[ROI Editor] traffic_db: {traffic_db_path}")
    print(f"[ROI Editor] camera_config: {camera_config_path}")
    print(f"[ROI Editor] 브라우저: http://<Pi-IP>:{args.port}")

    uvicorn.run(
        app, host=args.host, port=args.port,
        log_level="warning", access_log=False, workers=1,
    )
