"""대시보드 백엔드가 부르는 Pi 경로가 **실제로 Pi에 있는지** 대조한다.

`dashboard/backend/app/services/pi_client.py` 머리말의 사고 — 구현이
`PATCH /api/cameras/{id}`를 불렀는데 Pi에는 없었다 — 는 양쪽 테스트가 각자
통과하는 상태에서 났다. 백엔드 테스트는 respx로 Pi를 흉내 내므로 **흉내 낸
경로가 실재하는지**는 아무도 확인하지 않는다. 여기서 소스의 호출과 Pi의 라우트
표를 맞대어 본다.

- roi_editor(포트 5000): FastAPI `app.routes`
- 카메라 MJPEG 포트(녹화·수집): `camera_live_pi._route_recording`/`_route_calibrate`
"""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BACKEND_APP = ROOT / "dashboard" / "backend" / "app"

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

# 카메라 포트에 있는 경로 접두사 — 나머지는 전부 roi_editor다.
_CAMERA_PORT_PREFIXES = ("/recording/", "/calibrate/")

_PATTERNS = [
    # PiClient._request("POST", "/api/...")
    (re.compile(r'_request\(\s*"(GET|POST|PUT|DELETE|PATCH)",\s*f?"(/[^"]+)"'), None),
    (re.compile(r'_get_json\(\s*f?"(/[^"]+)"'), "GET"),
    (re.compile(r'stream_file\(\s*f?"(/[^"]+)"'), "GET"),
    (re.compile(r'post_control\(\s*f?"(/[^"]+)"'), "POST"),
]


def _calls() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for py in BACKEND_APP.rglob("*.py"):
        src = py.read_text(encoding="utf-8")
        for pat, fixed_method in _PATTERNS:
            for m in pat.finditer(src):
                if fixed_method is None:
                    found.add((m.group(1), m.group(2)))
                else:
                    found.add((fixed_method, m.group(1)))
    return found


def _concrete(path: str) -> str:
    """f-string 자리표시자를 실제처럼 보이는 값으로 바꾼다."""
    return re.sub(r"\{[^}]+\}", "clip_20260101_000000.mp4", path)


def test_backend_calls_are_found():
    # 패턴이 깨져 아무것도 못 찾으면 아래 대조가 공허하게 통과한다.
    calls = _calls()
    assert len(calls) >= 30, sorted(calls)
    assert ("POST", "/recording/start") in calls
    assert ("GET", "/api/network/scan") in calls


def test_roi_editor_has_every_called_route():
    # test_roi_editor_server.py와 같은 방식 — server.py가 같은 폴더의
    # network_manager를 최상위로 import하므로 roi_editor/ 자체를 경로에 넣는다.
    sys.path.insert(0, str(ROOT / "apps" / "roi_editor"))
    import server as srv

    table: set[tuple[str, str]] = set()
    for r in srv.app.routes:
        for method in getattr(r, "methods", None) or ():
            table.add((method, getattr(r, "path", "")))

    missing = sorted(
        (m, p) for m, p in _calls()
        if not p.startswith(_CAMERA_PORT_PREFIXES) and (m, p) not in table
    )
    assert not missing, f"Pi roi_editor에 없는 경로를 부른다: {missing}"


def test_camera_port_has_every_called_route():
    from camera_live_pi import _route_calibrate, _route_recording

    missing = []
    for method, path in _calls():
        if not path.startswith(_CAMERA_PORT_PREFIXES):
            continue
        p = _concrete(path)
        if _route_recording(method, p) is None and _route_calibrate(method, p) is None:
            missing.append((method, path))
    assert not missing, f"카메라 포트에 없는 경로를 부른다: {sorted(missing)}"
