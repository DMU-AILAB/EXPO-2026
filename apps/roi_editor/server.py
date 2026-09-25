#!/usr/bin/env python3
"""Pi ROI Web Editor — FastAPI 서버

포트 5000에서 실행. rois.json CRUD + 정적 파일 서빙.
같은 Wi-Fi의 PC/스마트폰 브라우저에서 http://<Pi-IP>:5000 으로 접속.

실행:
    python roi_editor/server.py
    python roi_editor/server.py --rois /home/ailab/visionguide/rois.json --port 5000
"""
import argparse
import hmac
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
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
import static_mask as _static_mask  # noqa: E402
from camera_config import (  # noqa: E402
    MODEL_VARIANTS, CAPTURE_PRESETS, CameraProfile,
    _DEFAULT_MODEL_VARIANT, _DEFAULT_REQUIRE_PERSON, _DEFAULT_ROI_CROP_INFERENCE,
    load_camera_config, save_camera_config, validate_camera_config,
)
from yolo_postprocess import CLASS_NAMES  # noqa: E402
from event_logger import EventSender, HeartbeatSender, pending_count  # noqa: E402
from device_metrics import read_metrics  # noqa: E402
from device_status import read_status  # noqa: E402
from device_identity import (  # noqa: E402
    APP_VERSION, DeviceIdentity, clear_identity, default_path, load_identity,
    save_identity,
)

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
identity_path: Path = default_path(_ROOT)
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


def _all_traffic_dbs() -> list[Path]:
    """등록된 모든 카메라의 traffic_db + 기본 경로.

    **카메라마다 db 파일이 다를 수 있다**(`CameraProfile.traffic_db`). 기본 경로 하나만
    보면 다른 카메라의 이벤트 outbox·런타임 지표가 통째로 보이지 않는다 — 그 상태에서는
    이벤트가 쌓이기만 하고 서버로 영영 올라가지 않는다.
    """
    paths: dict[str, Path] = {str(traffic_db_path): traffic_db_path}
    for profile in load_camera_config(camera_config_path):
        value = Path(profile.traffic_db)
        resolved = value if value.is_absolute() else (rois_path.parent / value).resolve()
        paths.setdefault(str(resolved), resolved)
    return list(paths.values())


def _static_mask_path(camera: str | None = None) -> Path:
    """`static_mask.json` 경로 — **`rois.json`과 같은 자리**에 둔다.

    탐지 프로세스가 rois.json과 같은 mtime 폴링으로 함께 읽으므로 경로가 어긋나면
    조용히 반영되지 않는다. 카메라별 roi_config가 있으면 그 디렉터리를 따른다.
    """
    if camera is not None:
        return _resolve_camera_path(camera, "roi_config", rois_path).parent / "static_mask.json"
    return rois_path.parent / "static_mask.json"


def _calib_thumb_dir(camera: str | None = None) -> Path:
    """수집 썸네일 디렉터리 — camera_live_pi가 쓰는 자리와 같아야 한다."""
    return rois_path.parent / "recordings" / (camera or "legacy") / "calib"


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
    """Pi 상태(가동시간/CPU온도/부하/메모리).

    `/proc`·`/sys` 읽기는 `device/device_status.py`로 옮겼다 — 하트비트
    (`event_logger.HeartbeatSender`)가 같은 값을 쓰면서 두 곳에 같은 코드가 생길
    상황이었다.

    **응답 스키마를 늘리지 말 것.** 백엔드의 기기 탐색(명세 §12)이 "이 200 응답의
    바디 스키마"로 VisionGuide 기기 여부를 판별한다. 카메라별 런타임 지표는
    `/api/metrics`로 따로 낸다.
    """
    return read_status()


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
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@app.get("/api/stats/timeseries")
async def get_stats_timeseries(camera: str | None = None, period: str = "today",
                               date: str | None = None):
    """유동인구 시계열 — period=today면 시간대별(0~23시), 7d/30d면 일별 합계.

    ROI별 집계는 현재 DB 스키마(카메라 단위 시간별 합계만 기록)로는 낼 수 없어 대상 외.

    `date`(YYYY-MM-DD)는 **시간대별 조회에만** 쓴다 — 서버가 꺼져 있던 구간을 나중에
    메우기 위한 것이다. 이 값이 없으면 수집기가 볼 수 있는 시간별 데이터는 '오늘'뿐이라,
    중단된 시간대는 영영 0으로 남는다. `read_hourly_breakdown()`이 이미 date를 받으므로
    여기서는 넘겨주기만 하면 된다.
    """
    if period != "today" and period not in _STATS_PERIOD_DAYS:
        raise HTTPException(status_code=400, detail=f"unknown period: {period}")
    if date is not None and not _DATE_RE.match(date):
        raise HTTPException(status_code=400, detail="date는 YYYY-MM-DD 형식이어야 합니다")

    path = _resolve_camera_path(camera, "traffic_db", traffic_db_path)
    if period == "today":
        return {"granularity": "hour", "points": read_hourly_breakdown(path, date),
                "date": date}
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


# ---------------------------------------------------------------------------
# 구조물 마스크 — 새벽 캘리브레이션 결과를 제안하고, 운영자가 고른 것만 적용한다
# ---------------------------------------------------------------------------
@app.get("/api/static-mask/candidates")
async def get_static_mask_candidates(camera: str | None = None):
    """수집된 구조물 후보 목록.

    `fp_hotspots`와 같은 **제안-확인** 구조다. 자동으로 적용하지 않는 이유도 같다 —
    캘리브레이션 중 청소·보수 인력이 지나가면 그 자리가 구조물로 굳어 **사각지대**가
    된다. 운영자가 썸네일·탐지횟수·이동량을 보고 판단한다.

    응답의 `applied`는 현재 `static_mask.json`에 켜져 있는지다(IoU로 대조).
    """
    db = _resolve_camera_path(camera, "traffic_db", traffic_db_path)
    rows = _static_mask.read_candidates(db)
    active = _static_mask.load_mask_file(_static_mask_path(camera))
    for r in rows:
        r["applied"] = any(
            b["cls"] == r["cls"] and _static_mask.iou(b["bbox"], r["bbox"]) >= 0.9
            for b in active.boxes)
        # 지팡이는 적용해도 위험이 거의 없지만(트리거는 지팡이 트랙만 순회) 사람은
        # 그 자리의 진짜 사람을 가릴 수 있다 — UI가 기본 선택을 다르게 하도록 알린다.
        r["recommend"] = (r["cls"] == 0)
    return {"candidates": rows}


@app.post("/api/static-mask/apply")
async def apply_static_mask(payload: dict, camera: str | None = None):
    """선택한 후보만 `static_mask.json`에 쓴다 — 탐지 프로세스가 mtime 폴링으로 반영한다.

    `{"ids": [1, 3, 7]}` 형식. 빈 목록이면 마스크를 모두 끈다.
    """
    ids = {int(i) for i in (payload or {}).get("ids", [])}
    db = _resolve_camera_path(camera, "traffic_db", traffic_db_path)
    rows = [r for r in _static_mask.read_candidates(db) if r["id"] in ids]
    path = _static_mask_path(camera)
    _static_mask.save_mask_file(
        path,
        [{"cls": r["cls"], "bbox": r["bbox"]} for r in rows],
        meta={"applied_at": time.time(), "count": len(rows),
              "rotation": rows[0]["rotation"] if rows else None,
              "preset": rows[0]["preset"] if rows else None})
    return {"ok": True, "applied": len(rows), "path": str(path)}


@app.delete("/api/static-mask")
async def delete_static_mask(camera: str | None = None):
    """후보와 적용 상태를 모두 초기화 — 재캘리브레이션 전이나 카메라를 옮긴 뒤."""
    db = _resolve_camera_path(camera, "traffic_db", traffic_db_path)
    _static_mask.clear_candidates(db)
    _static_mask.clear_mask_hits(db)
    _static_mask.save_mask_file(_static_mask_path(camera), [])
    return {"ok": True}


@app.get("/api/static-mask/hits")
async def get_static_mask_hits(camera: str | None = None):
    """마스크가 걸러낸 트랙 수(클래스별).

    **사각지대를 발견하는 유일한 수단이다** — 안내가 조용해진 것이 오탐이 줄어서인지
    사람을 못 봐서인지는 이 값으로만 구분된다. 사람 클래스 적중이 늘고 있다면
    그 마스크를 꺼야 한다.
    """
    db = _resolve_camera_path(camera, "traffic_db", traffic_db_path)
    return {"hits": _static_mask.read_mask_hits(db)}


@app.get("/api/static-mask/thumb")
async def get_static_mask_thumb(name: str, camera: str | None = None):
    """후보 썸네일. 경로 탈출을 막으려고 **파일명만** 받는다(오디오 서빙과 같은 원칙)."""
    if "/" in name or "\\" in name or not name.endswith(".jpg"):
        raise HTTPException(status_code=400, detail="잘못된 파일명")
    f = (_calib_thumb_dir(camera) / name).resolve()
    if not f.is_file():
        raise HTTPException(status_code=404, detail="썸네일 없음")
    return FileResponse(f)


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
# 기기 신원 / 버전 — 서버의 기기 탐색·등록(백엔드 명세 §12)이 쓴다.
#
# 명세는 "서버가 Pi를 찾는 것"까지만 정의하고 **발급한 api_key를 Pi에 넣는 경로는
# 비어 있다.** 여기서 그 수신구를 연다. 서버가 Pi에 쓰는 패턴 자체는 이미 있다 —
# 명세 §13.0이 카메라/ROI 설정을 `POST /api/cameras`로 밀어 넣도록 정의한다.
# ---------------------------------------------------------------------------

class IdentityIn(BaseModel):
    device_id: str
    api_key: str
    server_url: str = ""
    name: str = ""
    location: str = ""
    registered_at: str = ""


@app.get("/api/version")
def get_version():
    """기기 탐색이 `version` 필드를 채우는 소스.

    명세 §12에 "이 필드를 채울 소스가 현재 Pi에 없다"고 적힌 공백이다.
    **등록 전에도 인증 없이 응답해야 한다** — 등록 자체가 이 응답을 보고 이뤄진다.
    """
    ident = load_identity(identity_path)
    return {
        "version": APP_VERSION,
        "product": "VisionGuide",
        "registered": ident is not None,
        "device_id": ident.device_id if ident else None,
    }


# ---------------------------------------------------------------------------
# 기기 제어 — 대시보드의 "서비스 재시작" · "재부팅" 버튼
#
# 이 두 라우트가 없어서 백엔드의 제어 기능이 통째로 동작하지 않았다(없는 경로를
# 부르고 예외를 삼켜 항상 202를 돌려주고 있었다).
#
# **인증을 붙인다.** 포트 5000의 나머지 라우트는 무인증이지만, 그건 같은 망에서
# 설정을 바꾸는 것까지고 재부팅은 서비스 자체를 끊는다. 서버가 등록 때 심어둔
# api_key를 헤더로 받아 대조한다 — 신원이 없는 기기는 아직 아무에게도 속하지 않았
# 으므로 제어를 거부한다(현장 설치 중 오작동 방지).
#
# 재시작은 반드시 systemctl로 한다. CLAUDE.md에 적힌 대로 앱이 SIGTERM에 정상
# 종료(exit 0)하므로 pkill로는 Restart=on-failure가 걸리지 않아 되살아나지 않는다.
# ---------------------------------------------------------------------------

def _require_device_key(request: Request) -> None:
    """서버가 심어둔 api_key와 대조한다."""
    ident = load_identity(identity_path)
    if ident is None or not ident.api_key:
        raise HTTPException(403, "기기가 아직 서버에 등록되지 않아 원격 제어를 받지 않습니다")
    presented = request.headers.get("x-device-key", "")
    # 길이가 달라도 같은 시간이 걸리도록 비교한다.
    if not hmac.compare_digest(presented, ident.api_key):
        raise HTTPException(401, "device key가 일치하지 않습니다")


def _run_privileged(cmd: list[str], what: str) -> None:
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=10)
    except FileNotFoundError as exc:
        raise HTTPException(500, f"{what} 실패: 명령을 찾을 수 없습니다 ({cmd[0]})") from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(504, f"{what} 실패: 명령이 응답하지 않습니다") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or b"").decode("utf-8", "replace").strip()
        # sudoers가 안 깔린 기기에서 비밀번호를 기다리다 실패하는 경우가 흔하다.
        raise HTTPException(500, f"{what} 실패: {stderr or exc}") from exc


@app.post("/api/service/restart")
def post_service_restart(request: Request):
    """탐지 서비스만 재시작한다 — 라즈베리파이 재부팅이 아니다."""
    _require_device_key(request)
    _run_privileged(["sudo", "-n", "/usr/bin/systemctl", "restart", "visionguide-device"],
                    "서비스 재시작")
    print("[INFO] 원격 요청으로 visionguide-device 재시작")
    return {"ok": True, "service": "visionguide-device"}


@app.post("/api/system/reboot")
def post_system_reboot(request: Request, confirm: bool = False):
    """기기를 재부팅한다.

    `confirm=true`를 요구하는 이유: 이 Pi는 PoE 어댑터가 GPIO3를 점유해 **버튼으로
    다시 켤 수 없다**. 재부팅이 실패해 꺼진 채로 남으면 현장에 가야 한다.
    """
    _require_device_key(request)
    if not confirm:
        raise HTTPException(400, "재부팅은 confirm=true가 필요합니다")
    # 응답을 먼저 돌려주고 끊기도록 짧게 지연시킨다 — 즉시 죽으면 호출자는 연결
    # 리셋만 보고 성공인지 실패인지 구분할 수 없다.
    threading.Timer(
        1.0,
        lambda: subprocess.run(["sudo", "-n", "/usr/sbin/reboot"], capture_output=True),
    ).start()
    print("[INFO] 원격 요청으로 재부팅 예약(1초 후)")
    return {"ok": True, "rebooting_in_sec": 1}


@app.get("/api/metrics")
def get_metrics():
    """카메라별 런타임 지표(추론 시간·프레임 시간·스트리밍 여부).

    탐지 프로세스가 sqlite로 넘겨준 값이다 — 보고가 끊기면 `stale`로 표시된다.
    """
    cameras = []
    seen: set[str] = set()
    for path in _all_traffic_dbs():
        for row in read_metrics(path):
            if row.get("camera_id") in seen:
                continue
            seen.add(row.get("camera_id"))
            cameras.append(row)
    return {"cameras": cameras}


@app.get("/api/outbox")
def get_outbox():
    """서버 연동 상태 — 이벤트 전송과 하트비트가 살아있는지 한눈에 본다."""
    ev, hb = _sender["thread"], _sender["heartbeat"]
    return {
        "events": {
            "pending": sum(pending_count(p) for p in _all_traffic_dbs()),
            "sent_total": ev.sent_total if ev else 0,
            "last_error": ev.last_error if ev else None,
            "running": bool(ev and ev.is_alive()),
        },
        "heartbeat": {
            "sent_total": hb.sent_total if hb else 0,
            "last_sent_at": hb.last_sent_at if hb else None,
            "last_error": hb.last_error if hb else None,
            "running": bool(hb and hb.is_alive()),
        },
    }


@app.get("/api/heartbeat/preview")
def heartbeat_preview():
    """지금 보낼 하트비트 본문 — 서버 없이도 필드가 채워지는지 확인하는 용도."""
    hb = _sender["heartbeat"]
    if hb is None:
        raise HTTPException(503, "하트비트 스레드가 없습니다")
    return hb.build_payload()


@app.get("/api/identity")
def get_identity():
    """현재 신원. **api_key는 돌려주지 않는다** — 한 번 심으면 읽어갈 이유가 없고
    포트 5000은 같은 네트워크에 열려 있다."""
    ident = load_identity(identity_path)
    if ident is None:
        return {"registered": False}
    return {"registered": True, "device_id": ident.device_id,
            "server_url": ident.server_url, "name": ident.name,
            "location": ident.location, "registered_at": ident.registered_at}


@app.post("/api/identity")
def post_identity(req: IdentityIn):
    """서버가 등록 시 발급한 신원을 심는다.

    **덮어쓰기를 허용한다** — 기기를 다른 서버로 옮기거나 키를 교체하는 정상 흐름이
    있고, 거부하면 사람이 파일을 직접 지워야 한다.
    """
    ident = DeviceIdentity(
        device_id=req.device_id.strip(), api_key=req.api_key.strip(),
        server_url=req.server_url.rstrip("/"), name=req.name,
        location=req.location, registered_at=req.registered_at,
    )
    if not ident.device_id or not ident.api_key:
        raise HTTPException(400, "device_id와 api_key는 비워둘 수 없습니다")
    try:
        save_identity(identity_path, ident)
    except OSError as exc:
        raise HTTPException(500, f"신원 저장 실패: {exc}") from exc
    print(f"[INFO] 기기 신원 등록: {ident.device_id} -> {ident.server_url or '(서버 미지정)'}")
    return {"ok": True, "device_id": ident.device_id, "usable": ident.is_usable()}


@app.delete("/api/identity")
def delete_identity():
    """등록 해제. 기기를 회수하거나 다른 현장으로 옮길 때 쓴다."""
    return {"ok": True, "removed": clear_identity(identity_path)}


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
_sender: dict = {"thread": None, "heartbeat": None}


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


class ReplayPause(BaseModel):
    # None이면 토글. UI 버튼 하나로 정지/재개를 오가는 게 자연스럽다.
    paused: bool | None = None


@app.post("/api/replay/pause")
def replay_pause(req: ReplayPause):
    s = _replay["session"]
    if s is None:
        raise HTTPException(409, "재생 중인 세션이 없습니다")
    if req.paused is None:
        return {"paused": s.toggle_pause()}
    s.set_paused(req.paused)
    return {"paused": req.paused}


@app.post("/api/replay/step")
def replay_step():
    """일시정지 상태에서 한 프레임만 진행 — 멈춰 놓고 들여다보기 위한 것."""
    s = _replay["session"]
    if s is None:
        raise HTTPException(409, "재생 중인 세션이 없습니다")
    s.step_once()
    return {"ok": True}


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
    parser.add_argument("--identity", default=None,
                         help="기기 신원 파일 경로 (기본: rois.json과 같은 디렉터리의 device_identity.json)")
    parser.add_argument("--camera-config", default=str(_DEFAULT_CAMERA_CONFIG),
                         help="다중 카메라 프로필 JSON 경로 (camera_live_pi.py --camera-config와 동일해야 함)")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    rois_path = Path(args.rois).resolve()
    audio_dir = Path(args.audio_dir).resolve()
    traffic_db_path = Path(args.traffic_db).resolve()
    camera_config_path = Path(args.camera_config).resolve()
    identity_path = (Path(args.identity).resolve() if args.identity
                     else default_path(rois_path.parent))
    print(f"[ROI Editor] rois.json: {rois_path}")
    print(f"[ROI Editor] audio_dir: {audio_dir}")
    print(f"[ROI Editor] traffic_db: {traffic_db_path}")
    print(f"[ROI Editor] camera_config: {camera_config_path}")
    # 이벤트 전송 스레드. 신원이 없어도 띄운다 — 등록되는 순간 밀린 것이 함께
    # 올라가야 하고, 그때 프로세스를 재시작하게 만들면 안 된다.
    _sender["thread"] = EventSender(_all_traffic_dbs, identity_path)
    _sender["thread"].start()
    # 하트비트는 이벤트와 목적이 다르다 — 아무 일이 없어도 나가야 서버가 "조용한
    # 것"과 "죽은 것"을 구분한다(기기가 여러 대로 흩어지면 특히).
    _sender["heartbeat"] = HeartbeatSender(
        _all_traffic_dbs, identity_path,
        camera_ids=[p.id for p in load_camera_config(camera_config_path)])
    _sender["heartbeat"].start()

    _ident = load_identity(identity_path)
    print(f"[ROI Editor] 기기 신원: "
          f"{_ident.device_id + ' (' + (_ident.server_url or '서버 미지정') + ')' if _ident else '미등록'}")
    print(f"[ROI Editor] 브라우저: http://<Pi-IP>:{args.port}")

    uvicorn.run(
        app, host=args.host, port=args.port,
        log_level="warning", access_log=False, workers=1,
    )
