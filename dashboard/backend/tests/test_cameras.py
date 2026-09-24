"""카메라 중계 — **Pi를 어떤 URL·바디로 부르는지**를 단언한다.

이 파일은 원래 본문이 전부 `pass`였다(assert 0개). 그 상태에서 구현은 존재하지도
않는 `PATCH /api/cameras/{id}`를 부르고 있었는데 테스트 15건이 모두 통과했다.
그래서 여기서는 응답 코드가 아니라 **바깥으로 나간 요청**을 본다.
"""

import json

import httpx
import pytest
import respx

from app.models.camera import Camera
from app.models.device import Device

PI = "http://192.168.1.101:5000"


@pytest.fixture
def device(db_session):
    d = Device(id="cam-entrance-01", name="정문", ip="192.168.1.101",
               location="정문 입구", api_key_hash="x", config_etag="etag-1")
    db_session.add(d)
    db_session.add(Camera(id="dev-cam0", device_id=d.id, port=8080,
                          capture_preset="auto", model_variant="v10_320",
                          rotation=0, require_person=True, is_active=True,
                          conf_white_cane=0.55, conf_person=0.55,
                          cooldown=10.0, debounce=0.5))
    db_session.commit()
    return d


@pytest.fixture
def auth(client, admin_user):
    token = client.post("/api/auth/login", json={
        "username": "admin", "password": "test_password"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _pi_profile(**overrides):
    """Pi의 `CameraProfilePayload` 전체 15필드 (apps/roi_editor/server.py:298-317)."""
    profile = {
        "id": "dev-cam0", "enabled": True, "label": "정문", "backend": "auto",
        "source": "0", "rotation": 0, "inference_backend": "edgetpu",
        "roi_config": "rois.cam0.json", "port": 8080, "traffic_db": "ft0.db",
        "swap_rb": False, "model_variant": "v10_320", "capture_preset": "auto",
        "require_person_for_trigger": True, "roi_crop_inference": False,
    }
    profile.update(overrides)
    return profile


@respx.mock
def test_update_camera_calls_the_route_that_actually_exists(client, auth, device):
    """Pi에는 `POST /api/cameras`(목록 전체)만 있다 — PATCH나 하위 경로는 없다."""
    respx.get(f"{PI}/api/capture-presets").mock(return_value=httpx.Response(
        200, json={"presets": [{"key": "auto"}, {"key": "640x480"}]}))
    respx.get(f"{PI}/api/cameras").mock(return_value=httpx.Response(
        200, json={"cameras": [_pi_profile()]}))
    post = respx.post(f"{PI}/api/cameras").mock(return_value=httpx.Response(200, json={"ok": True}))

    res = client.patch("/api/devices/cam-entrance-01/cameras/dev-cam0",
                       json={"capture_preset": "640x480"},
                       headers={**auth, "If-Match": "etag-1"})

    assert res.status_code == 200, res.text
    assert post.called
    sent = json.loads(post.calls[0].request.content)
    assert list(sent.keys()) == ["cameras"], "Pi는 {'cameras': [...]} 형태만 받는다"


@respx.mock
def test_update_camera_preserves_fields_the_backend_does_not_know(client, auth, device):
    """백엔드 스키마에 없는 필드가 기본값으로 되돌아가면 안 된다 (명세 §13.2).

    전체 치환이라 빠뜨린 필드는 Pi에서 코드 기본값이 된다 — `inference_backend`가
    'edgetpu'에서 'auto'로 돌아가면 Coral을 쓰던 카메라가 조용히 느려진다.
    """
    respx.get(f"{PI}/api/capture-presets").mock(return_value=httpx.Response(
        200, json={"presets": [{"key": "auto"}, {"key": "640x480"}]}))
    respx.get(f"{PI}/api/cameras").mock(return_value=httpx.Response(
        200, json={"cameras": [_pi_profile()]}))
    post = respx.post(f"{PI}/api/cameras").mock(return_value=httpx.Response(200, json={"ok": True}))

    client.patch("/api/devices/cam-entrance-01/cameras/dev-cam0",
                 json={"capture_preset": "640x480"},
                 headers={**auth, "If-Match": "etag-1"})

    sent = json.loads(post.calls[0].request.content)["cameras"][0]
    assert sent["inference_backend"] == "edgetpu"
    assert sent["roi_config"] == "rois.cam0.json"
    assert sent["traffic_db"] == "ft0.db"
    assert sent["label"] == "정문"
    assert len(sent) == 15, f"프로필 필드가 누락됐다: {sorted(sent)}"


@respx.mock
def test_require_person_is_renamed_for_pi(client, auth, device):
    """서버 `require_person` ↔ Pi `require_person_for_trigger`.

    이름이 틀리면 pydantic이 조용히 버려서 사람 동반 게이트가 기본값으로 돌아간다.
    """
    respx.get(f"{PI}/api/cameras").mock(return_value=httpx.Response(
        200, json={"cameras": [_pi_profile()]}))
    post = respx.post(f"{PI}/api/cameras").mock(return_value=httpx.Response(200, json={"ok": True}))

    client.patch("/api/devices/cam-entrance-01/cameras/dev-cam0",
                 json={"require_person": False},
                 headers={**auth, "If-Match": "etag-1"})

    sent = json.loads(post.calls[0].request.content)["cameras"][0]
    assert sent["require_person_for_trigger"] is False
    assert "require_person" not in sent


@respx.mock
def test_fps_is_not_sent_to_pi(client, auth, device):
    """Pi의 CameraProfile에 fps가 없다 — 서버는 값만 보관한다 (명세 §4)."""
    respx.get(f"{PI}/api/cameras").mock(return_value=httpx.Response(
        200, json={"cameras": [_pi_profile()]}))
    post = respx.post(f"{PI}/api/cameras").mock(return_value=httpx.Response(200, json={"ok": True}))

    res = client.patch("/api/devices/cam-entrance-01/cameras/dev-cam0",
                       json={"fps": 15}, headers={**auth, "If-Match": "etag-1"})

    assert res.status_code == 200
    assert "fps" not in json.loads(post.calls[0].request.content)["cameras"][0]


@respx.mock
def test_update_camera_rejects_invalid_rotation_before_calling_pi(client, auth, device):
    """Pi는 유효성 오류 시 갱신 **전체**를 무시한다 — 먼저 걸러야 한다.

    `rotation`은 pydantic이 범위를 보지 않으므로(그냥 int다) 실제로 여기까지 온다.
    선검증은 규칙을 복제하지 않고 `device/camera_config.validate_camera_config()`를
    그대로 쓴다.
    """
    respx.get(f"{PI}/api/cameras").mock(return_value=httpx.Response(
        200, json={"cameras": [_pi_profile()]}))
    post = respx.post(f"{PI}/api/cameras").mock(return_value=httpx.Response(200, json={"ok": True}))

    res = client.patch("/api/devices/cam-entrance-01/cameras/dev-cam0",
                       json={"rotation": 45}, headers={**auth, "If-Match": "etag-1"})

    assert res.status_code == 400
    assert res.json()["error"] == "VALIDATION_ERROR"
    assert not post.called, "검증에 걸렸는데 Pi를 불렀다"


@respx.mock
def test_offline_device_returns_503(client, auth, device):
    respx.get(f"{PI}/api/cameras").mock(side_effect=httpx.ConnectError("offline"))

    res = client.patch("/api/devices/cam-entrance-01/cameras/dev-cam0",
                       json={"rotation": 90}, headers={**auth, "If-Match": "etag-1"})

    assert res.status_code == 503
    assert res.json()["ok"] is False


@respx.mock
def test_timeout_returns_504(client, auth, device):
    respx.get(f"{PI}/api/cameras").mock(side_effect=httpx.ReadTimeout("slow"))

    res = client.patch("/api/devices/cam-entrance-01/cameras/dev-cam0",
                       json={"rotation": 90}, headers={**auth, "If-Match": "etag-1"})

    assert res.status_code == 504


def test_update_camera_requires_if_match(client, auth, device):
    res = client.patch("/api/devices/cam-entrance-01/cameras/dev-cam0",
                       json={"rotation": 90}, headers=auth)
    assert res.status_code == 400


def test_update_camera_rejects_stale_etag(client, auth, device):
    res = client.patch("/api/devices/cam-entrance-01/cameras/dev-cam0",
                       json={"rotation": 90},
                       headers={**auth, "If-Match": "etag-stale"})
    assert res.status_code == 412


def test_invalid_camera_id_is_rejected(client, auth, device):
    """id는 URL 경로와 파일명(`rois.<id>.json`)에 그대로 들어간다."""
    res = client.patch("/api/devices/cam-entrance-01/cameras/bad%20id",
                       json={"rotation": 90}, headers={**auth, "If-Match": "etag-1"})
    assert res.status_code == 400
    assert res.json()["error"] == "INVALID_CAMERA_ID"


@respx.mock
def test_get_cameras_refreshes_cache_from_pi(client, auth, db_session, device):
    """조회는 Pi 스냅샷으로 캐시를 갱신한 뒤 반환한다 (명세 §13.0)."""
    respx.get(f"{PI}/api/cameras").mock(return_value=httpx.Response(
        200, json={"cameras": [_pi_profile(capture_preset="640x480", rotation=270)]}))

    res = client.get("/api/devices/cam-entrance-01/cameras", headers=auth)

    assert res.status_code == 200
    body = res.json()
    assert body["stale"] is False
    assert body["data"][0]["capture_preset"] == "640x480"
    assert body["data"][0]["rotation"] == 270


@respx.mock
def test_get_cameras_marks_stale_when_device_is_offline(client, auth, device):
    """기기가 꺼져 있으면 캐시를 주되 낡았다고 알린다."""
    respx.get(f"{PI}/api/cameras").mock(side_effect=httpx.ConnectError("offline"))

    res = client.get("/api/devices/cam-entrance-01/cameras", headers=auth)

    assert res.status_code == 200
    assert res.json()["stale"] is True
