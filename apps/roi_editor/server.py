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
import time
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse, HTMLResponse, RedirectResponse, StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from shapely.geometry import Polygon

# roi_editor/server.py는 서브디렉토리에서 실행되는 스크립트라 sys.path[0]이 그
# 디렉토리가 된다 — 배포 런타임 모듈(foot_traffic_counter.py/camera_config.py 등)을
# import하려면 그 위치를 sys.path에 직접 넣어줘야 한다.
#
# 그 위치가 두 가지다. **Pi에서는 ~/visionguide/ 에 모든 .py가 평면으로 놓여**
# parent.parent가 곧 그 디렉토리지만, **PC 개발 트리에서는 apps/roi_editor/ 라서
# parent.parent가 apps/ 이고 런타임 모듈은 device/ 에 있다.** 둘 다 넣어 둔다.
_UP = Path(__file__).parent.parent          # Pi: ~/visionguide, PC: <repo>/apps
sys.path.insert(0, str(_UP))
_ROOT = _UP.parent if (_UP.parent / "device").is_dir() else _UP
if (_ROOT / "device").is_dir():             # PC 개발 트리에만 존재한다
    sys.path.insert(0, str(_ROOT / "device"))
from foot_traffic_counter import (  # noqa: E402
    read_daily_totals, read_hourly_breakdown, read_range_daily_totals,
)
from detection_events import read_recent_events  # noqa: E402
from fp_hotspots import clear_hotspots, read_hotspots  # noqa: E402
from camera_config import (  # noqa: E402
    MODEL_VARIANTS, CAPTURE_PRESETS, CameraProfile,
    _DEFAULT_MODEL_VARIANT, _DEFAULT_REQUIRE_PERSON, _DEFAULT_ROI_CROP_INFERENCE,
    load_camera_config, save_camera_config, validate_camera_config,
)
from yolo_postprocess import CLASS_NAMES  # noqa: E402

# ---------------------------------------------------------------------------
# Paths (overridden by CLI args at startup)
# ---------------------------------------------------------------------------
_DEFAULT_ROIS = _ROOT / "rois.json"
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


_STATS_PERIOD_DAYS = {"7d": 7, "30d": 30}


@app.get("/api/stats/timeseries")
async def get_stats_timeseries(camera: str | None = None, period: str = "today"):
    """유동인구 시계열 — period=today면 시간대별(0~23시), 7d/30d면 일별 합계.

    ROI별 집계는 현재 DB 스키마(카메라 단위 시간별 합계만 기록)로는 낼 수 없어 대상 외.
    """
    if period != "today" and period not in _STATS_PERIOD_DAYS:
        raise HTTPException(status_code=400, detail=f"unknown period: {period}")

    path = _resolve_camera_path(camera, "traffic_db", traffic_db_path)
    if period == "today":
        return {"granularity": "hour", "points": read_hourly_breakdown(path)}
    days = _STATS_PERIOD_DAYS[period]
    return {"granularity": "day", "points": read_range_daily_totals(path, days)}


@app.get("/api/events")
async def get_events(camera: str | None = None, limit: int = 8):
    """모니터링 탭의 "최근 감지 이벤트" 표 — ROI 트리거(오디오 안내가 실제로 나간
    순간)마다 camera_live_pi.py가 기록한 이벤트를 최신순으로 반환한다."""
    path = _resolve_camera_path(camera, "traffic_db", traffic_db_path)
    return {"events": read_recent_events(path, limit=limit)}


@app.get("/api/fp-hotspots")
async def get_fp_hotspots(camera: str | None = None, min_count: int = 30, limit: int = 5):
    """오탐지 다발 지점 — 정지 억제로 걸러낸(= 배경 지형지물이 거의 확실한) 지팡이
    탐지 위치를 camera_live_pi.py가 누적한 것. ROI 편집 탭이 이걸 읽어 "제외구역으로
    추가" 제안 배너를 띄운다.

    자동으로 제외구역을 만들지 않고 사람 확인을 거치는 이유: 지팡이 사용자가 늘 같은
    지점에서 멈춰 서면 그 위치도 핫스팟으로 잡힐 수 있기 때문이다.
    """
    path = _resolve_camera_path(camera, "traffic_db", traffic_db_path)
    return {"hotspots": read_hotspots(path, min_count=min_count, limit=limit)}


@app.delete("/api/fp-hotspots")
async def delete_fp_hotspots(camera: str | None = None):
    """핫스팟 누적 초기화 — 제외구역을 만들었거나 카메라 위치/회전을 바꾼 뒤 호출."""
    path = _resolve_camera_path(camera, "traffic_db", traffic_db_path)
    clear_hotspots(path)
    return {"ok": True}


@app.get("/api/audio/file")
async def get_audio_file(path: str):
    """ROI에 연결된 오디오 파일을 브라우저에서 미리듣기(테스트 재생)할 수 있도록 서빙.

    `path`는 camera_live_pi.py가 재생에 쓰는 것과 동일한 절대경로(POST /api/audio/upload가
    반환한 값)이며, audio_dir 하위인지 확인해 그 밖의 임의 경로 접근은 차단한다.
    """
    resolved = Path(path).resolve()
    if audio_dir.resolve() not in resolved.parents:
        raise HTTPException(status_code=400, detail="audio_dir 밖의 경로는 서빙할 수 없습니다")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다")
    return FileResponse(resolved)


class RoisPayload(BaseModel):
    rois: list
    conf: float | dict[str, float] | None = None
    cooldown: float | None = None   # 초 단위, None이면 기존값 유지
    debounce: float | None = None   # 초 단위, None이면 기존값 유지


@app.post("/api/rois")
async def post_rois(payload: RoisPayload, camera: str | None = None):
    path = _resolve_camera_path(camera, "roi_config", rois_path)
    errors = _validate_rois(payload.rois) + _validate_conf(payload.conf)
    if errors:
        raise HTTPException(status_code=400, detail=errors)
    existing = _load(path)
    data: dict = {"rois": payload.rois}
    conf = payload.conf if payload.conf is not None else existing.get("conf")
    if conf is not None:
        data["conf"] = conf
    cooldown = payload.cooldown if payload.cooldown is not None else existing.get("cooldown", 10.0)
    debounce = payload.debounce if payload.debounce is not None else existing.get("debounce", 0.5)
    data["cooldown"] = max(0.0, cooldown)
    data["debounce"] = max(0.0, debounce)
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
    model_variant: str = _DEFAULT_MODEL_VARIANT
    capture_preset: str = "auto"
    # 아래 두 필드는 CameraProfile의 기본값을 그대로 따라간다 — 여기에 값을 다시
    # 적으면 두 곳이 어긋날 수 있다. pydantic은 선언되지 않은 필드를 model_dump()에서
    # 버리므로, 이 목록에서 빠진 필드는 UI에서 아무리 바꿔도 저장되지 않고 조용히
    # 기본값으로 되돌아간다(require_person_for_trigger가 실제로 그 상태였다).
    require_person_for_trigger: bool = _DEFAULT_REQUIRE_PERSON
    roi_crop_inference: bool = _DEFAULT_ROI_CROP_INFERENCE


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
    return {"variants": [{"key": k, **v} for k, v in MODEL_VARIANTS.items()],
            "default": _DEFAULT_MODEL_VARIANT}


@app.get("/api/capture-presets")
async def get_capture_presets():
    """캡처 해상도 프리셋 목록 — SD급 프리셋 + 'auto'(종횡비 자동 매칭)."""
    return {"presets": [{"key": k, **v} for k, v in CAPTURE_PRESETS.items()]}


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
# Network / Wi-Fi onboarding API
# ---------------------------------------------------------------------------
import network_manager as _nm  # noqa: E402  (roi_editor/ 디렉토리가 sys.path[0])


class _WifiConnectPayload(BaseModel):
    ssid: str
    password: str = ""


@app.get("/api/network/status")
async def get_network_status():
    """현재 wlan0 연결 상태(mode/ssid/ip/hostname)."""
    return _nm.get_status()


@app.get("/api/network/scan")
async def scan_wifi():
    """주변 Wi-Fi 네트워크 목록 (신호 강도 내림차순)."""
    return {"networks": _nm.scan_networks()}


@app.post("/api/network/connect")
async def connect_to_wifi(payload: _WifiConnectPayload):
    """Wi-Fi 연결 시작. 응답 즉시 반환(3초 딜레이 후 AP 종료)."""
    if _nm.is_connect_in_progress():
        raise HTTPException(status_code=409, detail="연결 시도 중입니다. 잠시 후 다시 시도하세요.")
    if not payload.ssid.strip():
        raise HTTPException(status_code=400, detail="SSID를 입력하세요.")
    _nm.connect_wifi(payload.ssid.strip(), payload.password, delay_seconds=_nm.CONNECT_DELAY_S)
    return {
        "ok": True,
        "message": f"연결 시도 중. {_nm.CONNECT_DELAY_S}초 후 '{payload.ssid}' 네트워크로 전환됩니다.",
        "delay_seconds": _nm.CONNECT_DELAY_S,
    }


@app.get("/api/network/connect-result")
async def get_connect_result():
    """connect_wifi() 결과 폴링용. in_progress=True면 아직 연결 중."""
    return {
        "in_progress": _nm.is_connect_in_progress(),
        "result": _nm.get_connect_result(),
    }


@app.post("/api/network/ap")
async def switch_to_ap():
    """AP 모드로 전환."""
    try:
        _nm.switch_to_ap()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "message": "AP 모드로 전환됐습니다.", "ip": _nm.AP_IP}


# ---------------------------------------------------------------------------
# Captive Portal — iOS / Android / Windows 자동 팝업 트리거
# ---------------------------------------------------------------------------
# iptables가 AP 클라이언트의 포트 80 요청을 5000으로 리다이렉트할 때
# OS별 캡티브 포털 감지 URL이 여기로 들어오면 대시보드로 302 응답.
# ---------------------------------------------------------------------------
# 검증 재생 — 저장된 영상을 배포와 같은 경로로 돌려 화면에서 확인한다.
#
# 게이트 로직은 `device/replay_engine.py`가 `gate_chain.GateChain`으로 돌린다.
# 여기서는 세션 하나를 만들고 MJPEG로 내보내는 것만 한다.
# ---------------------------------------------------------------------------

# 영상을 찾을 디렉터리. PC 개발 트리에는 datasets/videos/ 가 있고, 기기에는 보통
# ~/visionguide/videos/ 에 사람이 직접 올려둔다.
def _video_dirs() -> list[Path]:
    return [d for d in (_ROOT / "datasets" / "videos", _ROOT / "videos",
                        Path(__file__).parent.parent / "videos") if d.is_dir()]


_replay = {"session": None}


class ReplayStart(BaseModel):
    video: str
    conf: float = 0.55
    model_variant: str = _DEFAULT_MODEL_VARIANT
    require_person: bool = _DEFAULT_REQUIRE_PERSON
    speed: float = 1.0
    loop: bool = False
    debug_gates: bool = True


def _resolve_video(name: str) -> Path:
    """이름으로 영상을 찾는다. 경로 탈출(../)을 막으려고 파일명만 받는다."""
    safe = Path(name).name
    for d in _video_dirs():
        cand = d / safe
        if cand.is_file():
            return cand
    raise HTTPException(404, f"영상을 찾을 수 없습니다: {safe}")


@app.get("/api/replay/videos")
def replay_videos():
    out = []
    for d in _video_dirs():
        for f in sorted(d.glob("*.mp4")) + sorted(d.glob("*.avi")):
            out.append({"name": f.name, "size_mb": round(f.stat().st_size / 1e6, 1),
                        "dir": str(d)})
    # 같은 이름이 여러 디렉터리에 있으면 먼저 찾은 것만 남긴다(_resolve_video와 동일 순서).
    seen, uniq = set(), []
    for v in out:
        if v["name"] in seen:
            continue
        seen.add(v["name"])
        uniq.append(v)
    return {"videos": uniq}


@app.post("/api/replay/start")
def replay_start(req: ReplayStart):
    from replay_engine import ReplaySession

    path = _resolve_video(req.video)
    variant = MODEL_VARIANTS.get(req.model_variant)
    if variant is None:
        raise HTTPException(400, f"알 수 없는 모델: {req.model_variant}")

    old = _replay["session"]
    if old is not None:
        old.stop()

    # ROI는 지금 편집 중인 파일을 그대로 쓴다 — 그려 놓고 바로 "이 영상이면 안내가
    # 나갔을까"를 확인하는 것이 이 기능의 목적이다.
    roi_manager = None
    try:
        from roi_manager import ROIManager           # Pi 평면 배치
    except ImportError:
        try:
            from simulator.roi_manager import ROIManager
        except ImportError:
            ROIManager = None
    if ROIManager is not None and rois_path.exists():
        try:
            roi_manager = ROIManager.load(str(rois_path))
        except Exception as exc:                      # noqa: BLE001
            print(f"[WARN] ROI 로드 실패 — ROI 없이 재생합니다: {exc}")

    weights_dir = str(_ROOT / variant["weights_dir"])
    session = ReplaySession(
        path, weights_dir, conf=req.conf,
        input_size=variant.get("input_size", 320), roi_manager=roi_manager,
        require_person=req.require_person, speed=req.speed, loop=req.loop,
        debug_gates=req.debug_gates,
    )
    session.start()
    _replay["session"] = session
    return {"ok": True, "video": path.name, "weights_dir": weights_dir,
            "rois": len(roi_manager.rois) if roi_manager else 0}


@app.post("/api/replay/stop")
def replay_stop():
    s = _replay["session"]
    if s is not None:
        s.stop()
        _replay["session"] = None
    return {"ok": True}


@app.get("/api/replay/status")
def replay_status():
    s = _replay["session"]
    return s.status() if s is not None else {"running": False, "done": False}


@app.get("/api/replay/stream.mjpg")
def replay_stream():
    s = _replay["session"]
    if s is None:
        raise HTTPException(409, "재생 중인 세션이 없습니다")

    def gen():
        last = None
        while True:
            frame = s.latest_jpeg
            if frame is None or frame is last:
                if s.status()["done"] and frame is None:
                    break
                time.sleep(0.02)
                continue
            last = frame
            yield (b"--frame\r\nContent-Type: image/jpeg\r\n"
                   b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n"
                   + frame + b"\r\n")

    return StreamingResponse(
        gen(), media_type="multipart/x-mixed-replace; boundary=frame")


_CAPTIVE_REDIRECT = f"http://{_nm.AP_IP}:5000"


def _captive():
    return RedirectResponse(_CAPTIVE_REDIRECT, status_code=302)


@app.get("/hotspot-detect.html")       # iOS / macOS
@app.get("/library/test/success.html") # iOS 구버전
@app.get("/generate_204")              # Android / Chrome
@app.get("/gen_204")                   # Android 구버전
@app.get("/ncsi.txt")                  # Windows NCSI
@app.get("/connecttest.txt")           # Windows 최신
@app.get("/canonical.html")            # Ubuntu
async def captive_portal(request: Request):
    return _captive()


@app.get("/{full_path:path}")
async def captive_catch_all(full_path: str, request: Request):
    """iptables 리다이렉트로 들어온 포트 80 요청 처리.
    Host 헤더가 192.168.4.1:5000이 아니면 캡티브 포털로 판단한다.
    """
    host = request.headers.get("host", "")
    if "192.168.4.1" not in host and "5000" not in host:
        return _captive()
    raise HTTPException(status_code=404)


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
