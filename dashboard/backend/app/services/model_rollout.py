"""model_rollout.py — 기존 기기들의 카메라 모델(`model_variant`)을 한 번에 올린다.

신규 설치의 기본 모델이 바뀌어도 **이미 깔린 기기의 `camera_config.json`은 그대로**라서(예: `v4_320`), 기존
기기를 올리려면 따로 바꿔야 한다. 새 Pi API는 만들지 않고 이미 있는 경로를 재사용한다:
`GET /api/cameras` → `merge_camera_profiles`(모르는 필드 보존) → 같은 규칙으로 선검증 → `POST /api/cameras`(전체 치환).
Pi는 `camera_config.json`의 mtime을 0.5초 단위로 감시해 **바뀐 카메라의 파이프라인만** 다시 띄운다(재시작 불필요).

지키는 것
1. **가중치가 없으면 바꾸지 않는다.** 파일이 없는 모델로 바꾸면 파이프라인이 시작하자마자 죽고 재시작을 반복해
   탐지가 멈춘다. Pi의 `/api/model-variants`가 `available`(가중치 존재)을 알려 주고, 이 값이 없는 구버전 기기는
   **증명할 수 없으므로 건너뛴다**. `push_models`면 코드 번들(기본 모델 포함)을 먼저 올린 뒤 다시 확인한다.
2. **Coral(edgetpu) 카메라는 건드리지 않는다** — 모델 형식·백엔드가 다르다.
3. **바꾼 뒤 FPS를 확인하고 미달이면 되돌린다.** 정지 이미지 지표가 좋아도 기기에서 느리면 배포할 수 없다
   (KPI: TFLite INT8 FPS ≥ 10). 되돌릴 때는 **그 카메라만** 이전 값으로 돌리고 나머지는 유지한다.
4. **기기별 결과를 따로** 돌려준다. 한 대가 실패해도 나머지는 계속한다.
5. 서버 캐시(`Camera.model_variant`)는 **Pi가 받아들인 뒤에만** 갱신한다.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Awaitable, Callable, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models.camera import Camera
from ..models.device import Device
from .pi_client import PiClient
from .pi_sync import merge_camera_profiles, validate_profiles_locally

logger = logging.getLogger(__name__)

__all__ = ["rollout_device", "MIN_FPS", "VERIFY_SEC", "POLL_SEC", "SETTLE_SEC"]

MIN_FPS = 10.0          # KPI: 라즈베리파이 TFLite INT8 FPS ≥ 10
VERIFY_SEC = 45.0       # 바꾼 뒤 FPS가 안정될 때까지 기다리는 최대 시간
POLL_SEC = 3.0
# 바꾼 직후에는 **이전 모델이 보고한 지표**가 아직 신선해 보인다(파이프라인이 다시 뜨는 데 몇 초 걸린다).
# 곧바로 재면 새 모델이 느려도 통과로 오판하므로, 새 파이프라인이 지표를 덮어쓸 시간을 먼저 준다.
SETTLE_SEC = 8.0


def _detail(exc: HTTPException) -> str:
    d = exc.detail
    return d.get("message", str(d)) if isinstance(d, dict) else str(d)


def _result(device: Device, **kw) -> dict:
    return {"device_id": device.id, "ok": True, "skipped": False, "changed": [], "skipped_cameras": [],
            "models_pushed": False, "error": None, "reason": None, **kw}


async def _availability(client: PiClient, to: str) -> tuple[Optional[bool], str, str]:
    """`(available, reason, default_variant)` — `available`이 None이면 증명할 수 없다(구버전·알 수 없음)."""
    data = await client.get_model_variants()
    default = data.get("default") or ""
    for v in data.get("variants", []):
        if v.get("key") == to:
            if "available" not in v:
                return None, "구버전 기기라 가중치가 있는지 확인할 수 없습니다 — 먼저 코드를 업데이트하세요", default
            if not v["available"]:
                return False, f"이 기기에 {to} 가중치(best_int8.tflite)가 없습니다", default
            return True, "", default
    return None, f"기기가 모르는 모델입니다: {to}", default


async def _push_models(db: Session, device: Device) -> None:
    """코드 번들(기본 모델 포함)을 올린다 — 푸시 업데이트와 같은 경로다(키·재시작·복귀 대기 포함)."""
    from ..routers.devices import _bundle_or_503, _update_one      # 순환 import를 피한다
    bundle = _bundle_or_503("default")
    await _update_one(db, device, bundle, True)


async def rollout_device(
    db: Session, device: Device, *, to: str, from_variants: Optional[list[str]] = None,
    push_models: bool = False, verify: bool = True, dry_run: bool = False, min_fps: Optional[float] = None,
    verify_sec: Optional[float] = None, poll_sec: Optional[float] = None, settle_sec: Optional[float] = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict:
    """기기 한 대의 카메라를 `to`로 바꾼다. 결과는 항상 dict(예외를 던지지 않는다)."""
    # 기본값은 **호출 시점에** 읽는다 — 기본 인자로 붙잡으면 import 시점의 값에 묶여 설정·테스트가 바꿔도
    # 반영되지 않는다(실제로 테스트가 8초·45초를 진짜로 기다렸다).
    min_fps = MIN_FPS if min_fps is None else min_fps
    verify_sec = VERIFY_SEC if verify_sec is None else verify_sec
    poll_sec = POLL_SEC if poll_sec is None else poll_sec
    settle_sec = SETTLE_SEC if settle_sec is None else settle_sec
    client = PiClient(device.ip)
    out = _result(device)

    try:
        available, reason, default = await _availability(client, to)
        if available is False and push_models:
            if dry_run:
                out["needs_push"] = True                  # 실제 실행이면 코드 번들(기본 모델 포함)을 먼저 올린다
            else:
                await _push_models(db, device)
                out["models_pushed"] = True
                available, reason, default = await _availability(client, to)
        if available is not True and not out.get("needs_push"):
            out.update(ok=False, skipped=True, reason=reason)
            return out

        profiles = await client.get_cameras()
        targets: list[str] = []
        previous: dict[str, str] = {}
        for p in profiles:
            cid = p.get("id")
            current = p.get("model_variant") or default
            if not p.get("enabled", True):
                continue
            if current == to:
                continue
            if from_variants is not None and current not in from_variants:
                continue
            if (p.get("inference_backend") or "auto") == "edgetpu":
                out["skipped_cameras"].append({"camera_id": cid, "reason": "Coral(edgetpu) 카메라는 건드리지 않습니다"})
                continue
            targets.append(cid)
            previous[cid] = current
        if not targets:
            return out                                   # 바꿀 것이 없다 — 이미 목표이거나 대상이 아니다

        if dry_run:
            out["dry_run"] = True
            out["changed"] = [{"camera_id": c, "previous": previous[c], "current": to, "fps": None,
                               "rolled_back": False} for c in targets]
            return out

        merged = [dict(p) for p in profiles]
        for p in merged:
            if p.get("id") in targets:
                p["model_variant"] = to
        errors = validate_profiles_locally(merged)
        if errors:
            out.update(ok=False, error="기기가 거부할 설정입니다: " + "; ".join(map(str, errors)))
            return out

        await client.put_cameras(merged)
        _update_cache(db, device, {cid: to for cid in targets})
        out["changed"] = [{"camera_id": c, "previous": previous[c], "current": to, "fps": None, "rolled_back": False}
                          for c in targets]

        if verify:
            await sleep(settle_sec)
            await _verify_and_rollback(db, device, client, out, previous, to, min_fps, verify_sec, poll_sec,
                                       sleep, clock)
        return out
    except HTTPException as exc:
        logger.warning("모델 변경 실패 (%s): %s", device.id, _detail(exc))
        out.update(ok=False, error=_detail(exc))
        return out


def _update_cache(db: Session, device: Device, variants: dict[str, str]) -> None:
    for cam in db.query(Camera).filter(Camera.device_id == device.id).all():
        if cam.id in variants:
            cam.model_variant = variants[cam.id]
    device.config_etag = str(uuid.uuid4())
    db.commit()


async def _measure(client: PiClient, targets: list[str]) -> dict[str, Optional[float]]:
    try:
        cams = {c.get("camera_id"): c for c in (await client.get_metrics()).get("cameras", [])}
    except HTTPException:
        return {t: None for t in targets}
    result: dict[str, Optional[float]] = {}
    for t in targets:
        c = cams.get(t)
        ok = c is not None and c.get("streaming") and not c.get("stale") and c.get("fps") is not None
        result[t] = float(c["fps"]) if ok else None
    return result


async def _verify_and_rollback(db, device, client, out, previous, to, min_fps, verify_sec, poll_sec, sleep, clock):
    targets = [c["camera_id"] for c in out["changed"]]
    deadline = clock() + verify_sec
    fps: dict[str, Optional[float]] = {t: None for t in targets}
    while True:
        fps = await _measure(client, targets)
        if all(v is not None and v >= min_fps for v in fps.values()):
            break
        if clock() >= deadline:
            break
        await sleep(poll_sec)

    for c in out["changed"]:
        c["fps"] = fps.get(c["camera_id"])
    bad = [t for t in targets if fps.get(t) is None or fps[t] < min_fps]
    if not bad:
        return

    # 미달 — 그 카메라만 이전 값으로 되돌린다(나머지는 유지). 최신 목록을 다시 읽어 그 위에 얹는다.
    bad_list = ", ".join(bad)
    measured = ", ".join("%s=%s" % (t, "측정 불가" if fps[t] is None else "%.1f" % fps[t]) for t in bad)
    try:
        profiles = await client.get_cameras()
        merged = [dict(p) for p in profiles]
        for p in merged:
            if p.get("id") in bad:
                p["model_variant"] = previous[p["id"]]
        await client.put_cameras(merged)
        _update_cache(db, device, {t: previous[t] for t in bad})
        for c in out["changed"]:
            if c["camera_id"] in bad:
                c["rolled_back"] = True
        out["ok"] = False
        out["error"] = "FPS가 %g 미만이라 %s를 이전 모델로 되돌렸습니다 (측정: %s)" % (min_fps, bad_list, measured)
    except HTTPException as exc:
        was = ", ".join("%s=%s" % (t, previous[t]) for t in bad)
        out["ok"] = False
        out["error"] = ("FPS 미달(%s, 측정: %s)이지만 이전 모델로 되돌리지 못했습니다: %s — 직접 확인하세요 (이전 값: %s)"
                        % (bad_list, measured, _detail(exc), was))
