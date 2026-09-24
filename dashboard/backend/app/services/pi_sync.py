"""서버 표현 ↔ Pi 표현 변환.

명세 §13.2가 "Pi가 그대로 소비하는 값이므로 서버 DB 표현과 다르다"고 적어둔 자리다.
어긋나는 지점이 네 군데이고, 전부 **조용히** 실패한다.

| 서버 | Pi | 어기면 |
|---|---|---|
| `polygon` | **`points`** | ROI 로더가 그 ROI를 판정에서 뺀다 |
| `require_person` | **`require_person_for_trigger`** | 사람 동반 게이트가 기본값으로 되돌아간다 |
| 파일명 `entrance.mp3` | **Pi 로컬 절대경로** | 재생에 실패한다 |
| `is_active: false` | (개념 없음) | 내려보내면 곧바로 활성 ROI가 된다 |

**쓰기는 항상 전체 치환이다.** Pi의 저장 API는 배열을 통째로 받으므로, 읽어서 병합하지
않으면 백엔드가 모르는 필드가 조용히 코드 기본값으로 되돌아간다.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "PI_CAMERA_FIELD_MAP",
    "merge_camera_profiles",
    "build_roi_payload",
    "pi_roi_to_server",
    "validate_profiles_locally",
    "CLASS_NAMES",
]

# ---------------------------------------------------------------------------
# Pi 런타임 모듈 import
#
# 검증 규칙을 백엔드에 복제하지 않는다 — 규칙이 두 곳에 있으면 한쪽만 고쳐진다.
# 백엔드는 저장소 트리 안(dashboard/backend/)에서 돌므로 device/ 가 닿는다.
# 닿지 않는 배치(컨테이너 등)에서는 선검증을 건너뛰고 Pi의 판정에 맡긴다.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (_REPO_ROOT, _REPO_ROOT / "device"):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.append(str(_p))

try:
    from camera_config import validate_camera_config, CameraProfile  # type: ignore
    _HAVE_PI_MODULES = True
except ImportError:                                   # pragma: no cover - 배치 의존
    validate_camera_config = None                     # type: ignore
    CameraProfile = None                              # type: ignore
    _HAVE_PI_MODULES = False
    logger.warning("device/camera_config.py를 찾지 못해 카메라 설정 선검증을 건너뜁니다")

try:
    from yolo_postprocess import CLASS_NAMES          # type: ignore
except ImportError:                                   # pragma: no cover
    # Pi의 _validate_conf()가 이 이름들을 **전부** 요구한다. 하나라도 빠지면 400.
    CLASS_NAMES = ("white_cane", "person")


# 서버 필드 → Pi 필드. 여기 없는 서버 필드는 Pi로 보내지 않는다.
#
# `fps`가 빠져 있는 것은 의도다 — Pi의 CameraProfile에 대응 필드가 없어 보내도
# pydantic이 버린다. 명세 §4대로 서버가 값만 보관한다.
PI_CAMERA_FIELD_MAP = {
    "port": "port",
    "capture_preset": "capture_preset",
    "model_variant": "model_variant",
    "rotation": "rotation",
    "require_person": "require_person_for_trigger",
    "is_active": "enabled",
}

# ROI 로더가 **필수로 읽는** 키 (apps/simulator/roi_manager.py:100-110).
# 그 로더는 전체를 `except KeyError: pass`로 감싸고 있어서, 한 ROI에 키 하나가
# 없으면 **그 파일의 ROI 전부가** 조용히 사라진다. 절대 빠뜨리지 말 것.
_ROI_REQUIRED_KEYS = ("name", "points", "priority", "announcement_text")


def merge_camera_profiles(pi_profiles: list[dict], camera_id: str,
                          updates: dict[str, Any]) -> list[dict]:
    """Pi에서 읽어온 프로필 목록에 변경분만 얹어 **전체 목록**을 돌려준다.

    백엔드가 모르는 필드(`backend`·`source`·`inference_backend`·`roi_config`·
    `traffic_db`·`swap_rb`·`roi_crop_inference`·`label`)를 **원본 그대로 보존**하는 것이
    이 함수의 존재 이유다. 빠뜨리면 그 값들이 코드 기본값으로 되돌아간다.
    """
    merged: list[dict] = []
    found = False
    for profile in pi_profiles:
        profile = dict(profile)
        if profile.get("id") == camera_id:
            found = True
            for server_key, pi_key in PI_CAMERA_FIELD_MAP.items():
                if server_key in updates and updates[server_key] is not None:
                    profile[pi_key] = updates[server_key]
        merged.append(profile)

    if not found:
        raise KeyError(camera_id)
    return merged


def build_roi_payload(rois: Iterable[Any], *, conf_white_cane: float, conf_person: float,
                      cooldown: float, debounce: float,
                      audio_path_resolver=None) -> dict:
    """`POST /api/rois?camera=<id>`의 바디를 만든다.

    **`conf`·`cooldown`·`debounce`를 함께 보내야 한다** — Pi는 `rois` 배열을 통째로
    갈아끼우면서 이 값들도 같은 파일 최상위에 다시 쓴다. 빼먹으면 현장에서 맞춰둔
    임계값이 기본값으로 되돌아간다.

    `audio_path_resolver(filename) -> str|None`은 서버 파일명을 Pi 로컬 절대경로로
    바꾼다. None을 주면 이미 절대경로인 값만 통과시킨다.
    """
    items: list[dict] = []
    for roi in rois:
        # 비활성 ROI는 **목록에서 뺀다**. Pi의 rois.json에는 비활성 개념이 없어서
        # 그대로 내려보내면 곧바로 활성 ROI가 된다.
        if not _attr(roi, "is_active", True):
            continue

        points = _attr(roi, "polygon", None)
        if isinstance(points, str):
            points = json.loads(points)
        if not points:
            continue

        audio_file = _attr(roi, "audio_file", "") or ""
        if audio_file and not audio_file.startswith("/"):
            resolved = audio_path_resolver(audio_file) if audio_path_resolver else None
            # 절대경로로 바꾸지 못했으면 **빈 값으로 둔다.** 파일명을 그대로 내려보내면
            # 기기가 재생에 실패하는데, 그건 안내가 나가지 않는 것과 같다.
            audio_file = resolved or ""

        items.append({
            "name": _attr(roi, "name", ""),
            "points": points,
            "priority": int(_attr(roi, "priority", 0) or 0),
            "announcement_text": _attr(roi, "announcement_text", "") or "",
            "audio_file": audio_file,
            "zone_type": _attr(roi, "zone_type", "trigger") or "trigger",
            # `color`는 서버 DB 전용이다 — Pi의 ROI 로더는 자기 팔레트를 쓴다.
        })

    for item in items:
        missing = [k for k in _ROI_REQUIRED_KEYS if k not in item]
        if missing:                                    # pragma: no cover - 방어
            raise ValueError(f"ROI '{item.get('name')}'에 필수 키가 없습니다: {missing}")

    return {
        "rois": items,
        # Pi는 스칼라도 받지만 이 API는 클래스별 딕셔너리로 통일한다(명세 §5).
        # 키가 CLASS_NAMES와 정확히 일치해야 하며, 하나라도 빠지면 Pi가 400을 낸다.
        "conf": {"white_cane": conf_white_cane, "person": conf_person},
        "cooldown": cooldown,
        "debounce": debounce,
    }


def pi_roi_to_server(item: dict) -> dict:
    """Pi의 ROI 한 건을 서버 표현으로 — 캐시 갱신용(`points` → `polygon`)."""
    return {
        "name": item.get("name", ""),
        "polygon": item.get("points", []),
        "priority": int(item.get("priority", 0) or 0),
        "announcement_text": item.get("announcement_text", "") or "",
        "audio_file": item.get("audio_file", "") or "",
        "zone_type": item.get("zone_type", "trigger") or "trigger",
    }


def validate_profiles_locally(profiles: list[dict]) -> list[str]:
    """Pi에 보내기 전에 **Pi와 같은 규칙으로** 먼저 검사한다.

    규칙을 복제하지 않고 `device/camera_config.py`의 `validate_camera_config()`를
    그대로 쓴다 — 포트 중복, 포트 5000 예약, Coral 동글 1개, 열거값, id 중복이
    전부 거기 한 곳에 있다.

    선검증이 필요한 이유: Pi는 유효성 오류가 있으면 **갱신 전체를 무시**하고(일부만
    적용하지 않는다) 400을 낸다. 서버 캐시만 바뀌고 기기는 옛 설정으로 도는 상태를
    막으려면 여기서 먼저 걸러야 한다.
    """
    if not _HAVE_PI_MODULES:
        return []
    try:
        objs = [CameraProfile(**_profile_kwargs(p)) for p in profiles]
    except (TypeError, ValueError) as exc:
        return [f"카메라 프로필 형식이 잘못되었습니다: {exc}"]
    return list(validate_camera_config(objs))


def _profile_kwargs(profile: dict) -> dict:
    """`CameraProfile`이 아는 필드만 남긴다 — Pi가 나중에 필드를 늘려도 깨지지 않게."""
    import dataclasses

    known = {f.name for f in dataclasses.fields(CameraProfile)}
    return {k: v for k, v in profile.items() if k in known}


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    """ORM 객체와 dict를 같이 받는다 (라우터는 ORM, 테스트는 dict가 편하다)."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)
