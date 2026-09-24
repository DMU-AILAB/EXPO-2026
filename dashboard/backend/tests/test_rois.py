"""ROI 중계 — Pi로 나가는 바디의 **키 이름과 누락 여부**를 단언한다.

여기서 틀리면 전부 조용히 실패한다.

- `polygon` 대신 `points`로 보내지 않으면 → Pi의 ROI 로더가 `KeyError`를 통째로
  삼켜서 **그 파일의 ROI 전부가** 사라진다 (`apps/simulator/roi_manager.py:100-110`).
- `conf`·`cooldown`·`debounce`를 빠뜨리면 → 현장에서 맞춰둔 임계값이 날아간다.
- `audio_file`을 파일명 그대로 보내면 → 재생에 실패한다(= 안내가 안 나간다).
"""

import json

import httpx
import pytest
import respx

from app.models.camera import Camera
from app.models.device import Device
from app.models.roi import Roi
from app.schemas.roi import RoiCreate

PI = "http://192.168.1.101:5000"
SQUARE = [[0.1, 0.2], [0.5, 0.2], [0.5, 0.8], [0.1, 0.8]]


@pytest.fixture
def device(db_session):
    d = Device(id="cam-entrance-01", name="정문", ip="192.168.1.101",
               api_key_hash="x", config_etag="etag-1")
    db_session.add(d)
    db_session.add(Camera(id="dev-cam0", device_id=d.id, port=8080,
                          conf_white_cane=0.6, conf_person=0.45,
                          cooldown=8.0, debounce=0.5))
    db_session.commit()
    return d


@pytest.fixture
def auth(client, admin_user):
    token = client.post("/api/auth/login", json={
        "username": "admin", "password": "test_password"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _sent_body(route):
    return json.loads(route.calls[0].request.content)


# --------------------------------------------------------------------------
# 폴리곤 검증
# --------------------------------------------------------------------------

def test_polygon_validation():
    valid = RoiCreate(camera_id="dev-cam0", name="Test ROI", polygon=SQUARE)
    assert valid.name == "Test ROI"

    with pytest.raises(ValueError):
        RoiCreate(camera_id="dev-cam0", name="x", polygon=[[0.1, 0.1], [0.9, 0.1]])

    # 나비넥타이 — 자기교차
    with pytest.raises(ValueError):
        RoiCreate(camera_id="dev-cam0", name="x",
                  polygon=[[0.0, 0.0], [1.0, 1.0], [1.0, 0.0], [0.0, 1.0]])


def test_invalid_polygon_returns_400_not_422(client, auth, device):
    """명세 §16은 폴리곤 오류를 400 INVALID_POLYGON으로 규정한다."""
    res = client.post("/api/devices/cam-entrance-01/rois",
                      json={"camera_id": "dev-cam0", "name": "x",
                            "polygon": [[0.1, 0.1], [0.9, 0.1]]},
                      headers={**auth, "If-Match": "etag-1"})
    assert res.status_code == 400
    assert res.json()["error"] == "INVALID_POLYGON"


# --------------------------------------------------------------------------
# Pi로 나가는 바디
# --------------------------------------------------------------------------

@respx.mock
def test_create_roi_sends_points_not_polygon(client, auth, device):
    post = respx.post(f"{PI}/api/rois").mock(return_value=httpx.Response(200, json={"ok": True}))

    res = client.post("/api/devices/cam-entrance-01/rois",
                      json={"camera_id": "dev-cam0", "name": "정문 진입 구역",
                            "priority": 1, "announcement_text": "정문입니다",
                            "polygon": SQUARE},
                      headers={**auth, "If-Match": "etag-1"})

    assert res.status_code == 201, res.text
    body = _sent_body(post)
    roi = body["rois"][0]
    assert roi["points"] == SQUARE
    assert "polygon" not in roi
    # ROI 로더가 필수로 읽는 네 키 — 하나라도 없으면 ROI 전체가 사라진다.
    for key in ("name", "points", "priority", "announcement_text"):
        assert key in roi, f"'{key}'가 빠지면 Pi가 ROI를 통째로 버린다"


@respx.mock
def test_create_roi_uses_the_camera_query_param(client, auth, device):
    """ROI 파일은 카메라마다 분리된다 — `?camera=<id>`가 없으면 기본 파일에 쓴다."""
    post = respx.post(f"{PI}/api/rois").mock(return_value=httpx.Response(200, json={"ok": True}))

    client.post("/api/devices/cam-entrance-01/rois",
                json={"camera_id": "dev-cam0", "name": "A", "polygon": SQUARE},
                headers={**auth, "If-Match": "etag-1"})

    assert post.calls[0].request.url.params["camera"] == "dev-cam0"


@respx.mock
def test_detection_params_are_preserved_on_every_write(client, auth, device):
    """ROI만 쓰고 conf·cooldown·debounce를 빠뜨리면 기존 값이 사라진다 (명세 §5)."""
    post = respx.post(f"{PI}/api/rois").mock(return_value=httpx.Response(200, json={"ok": True}))

    client.post("/api/devices/cam-entrance-01/rois",
                json={"camera_id": "dev-cam0", "name": "A", "polygon": SQUARE},
                headers={**auth, "If-Match": "etag-1"})

    body = _sent_body(post)
    assert body["conf"] == {"white_cane": 0.6, "person": 0.45}
    assert body["cooldown"] == 8.0
    assert body["debounce"] == 0.5


@respx.mock
def test_conf_has_every_class_pi_requires(client, auth, device):
    """Pi의 `_validate_conf()`는 CLASS_NAMES 전 클래스를 요구한다 — 하나라도 빠지면 400."""
    from app.services.pi_sync import CLASS_NAMES

    post = respx.post(f"{PI}/api/rois").mock(return_value=httpx.Response(200, json={"ok": True}))
    client.post("/api/devices/cam-entrance-01/rois",
                json={"camera_id": "dev-cam0", "name": "A", "polygon": SQUARE},
                headers={**auth, "If-Match": "etag-1"})

    assert set(_sent_body(post)["conf"]) == set(CLASS_NAMES)


@respx.mock
def test_inactive_rois_are_excluded(client, auth, db_session, device):
    """Pi의 rois.json에는 비활성 개념이 없다 — 내려보내면 곧바로 활성 ROI가 된다."""
    db_session.add(Roi(device_id=device.id, camera_id="dev-cam0", name="꺼둔 구역",
                       polygon=json.dumps(SQUARE), priority=0,
                       announcement_text="", is_active=False))
    db_session.commit()

    post = respx.post(f"{PI}/api/rois").mock(return_value=httpx.Response(200, json={"ok": True}))
    client.post("/api/devices/cam-entrance-01/rois",
                json={"camera_id": "dev-cam0", "name": "켜둔 구역", "polygon": SQUARE},
                headers={**auth, "If-Match": "etag-1"})

    names = [r["name"] for r in _sent_body(post)["rois"]]
    assert names == ["켜둔 구역"]


@respx.mock
def test_color_is_not_sent_to_pi(client, auth, device):
    """`color`는 서버 DB 전용이다 — Pi의 ROI 로더는 자기 팔레트를 쓴다."""
    post = respx.post(f"{PI}/api/rois").mock(return_value=httpx.Response(200, json={"ok": True}))
    client.post("/api/devices/cam-entrance-01/rois",
                json={"camera_id": "dev-cam0", "name": "A", "polygon": SQUARE,
                      "color": "#FF0000"},
                headers={**auth, "If-Match": "etag-1"})

    assert "color" not in _sent_body(post)["rois"][0]


@respx.mock
def test_delete_roi_rewrites_the_whole_list(client, auth, db_session, device):
    """Pi의 `DELETE /api/rois/{name}`을 따로 부르지 않고 전체 치환으로 반영한다."""
    keep = Roi(device_id=device.id, camera_id="dev-cam0", name="남길 구역",
               polygon=json.dumps(SQUARE), priority=1, announcement_text="")
    drop = Roi(device_id=device.id, camera_id="dev-cam0", name="지울 구역",
               polygon=json.dumps(SQUARE), priority=2, announcement_text="")
    db_session.add_all([keep, drop])
    db_session.commit()

    post = respx.post(f"{PI}/api/rois").mock(return_value=httpx.Response(200, json={"ok": True}))
    res = client.delete(f"/api/devices/cam-entrance-01/rois/{drop.id}",
                        headers={**auth, "If-Match": "etag-1"})

    assert res.status_code == 204
    assert [r["name"] for r in _sent_body(post)["rois"]] == ["남길 구역"]


# --------------------------------------------------------------------------
# 오프라인 동작
# --------------------------------------------------------------------------

@respx.mock
def test_offline_device_rolls_back_and_returns_503(client, auth, db_session, device):
    """큐에 쌓아 나중에 적용하지 않는다 — 그러면 서버가 사실상 원본이 된다 (명세 §13.0)."""
    respx.post(f"{PI}/api/rois").mock(side_effect=httpx.ConnectError("offline"))

    res = client.post("/api/devices/cam-entrance-01/rois",
                      json={"camera_id": "dev-cam0", "name": "A", "polygon": SQUARE},
                      headers={**auth, "If-Match": "etag-1"})

    assert res.status_code == 503
    assert res.json()["error"] == "DEVICE_OFFLINE"
    # 캐시에도 남으면 안 된다 — 화면의 값과 기기의 실제 동작이 갈라진다.
    assert db_session.query(Roi).filter(Roi.name == "A").first() is None


def test_create_roi_requires_if_match(client, auth, device):
    res = client.post("/api/devices/cam-entrance-01/rois",
                      json={"camera_id": "dev-cam0", "name": "A", "polygon": SQUARE},
                      headers=auth)
    assert res.status_code == 400
