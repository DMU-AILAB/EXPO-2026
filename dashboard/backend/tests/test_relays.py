"""녹화·검증 재생·오탐 관리·네트워크 중계 — Pi에 **무엇을 어디로 보내는지**를 단언한다.

`pi_client.py` 머리말의 사고(Pi에 없는 경로를 부름)가 다시 나지 않도록, 경로·포트·
쿼리·바디를 respx로 고정한다. 녹화·수집은 roi_editor(5000)가 아니라 **카메라 포트**다.
"""

import json

import httpx
import pytest
import respx

from app.models.camera import Camera
from app.models.device import Device

PI = "http://192.168.1.101:5000"
CAM = "http://192.168.1.101:8081"   # 카메라 MJPEG 포트 — 5000이 아님을 확인하려고 기본값과 다르게


@pytest.fixture
def device(db_session):
    d = Device(id="dev-1", name="정문", ip="192.168.1.101", api_key_hash="x", config_etag="e")
    db_session.add(d)
    db_session.add(Camera(id="cam0", device_id="dev-1", port=8081))
    db_session.commit()
    return d


@pytest.fixture
def auth(client, admin_user):
    token = client.post("/api/auth/login", json={
        "username": "admin", "password": "test_password"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def token(auth):
    return auth["Authorization"].split(" ", 1)[1]


BASE = "/api/devices/dev-1"


# ------------------------------------------------------------------ 녹화

@respx.mock
def test_recording_start_goes_to_camera_port_with_raw(client, auth, device):
    route = respx.post(f"{CAM}/recording/start").mock(return_value=httpx.Response(
        200, json={"ok": True, "clip_id": "clip_20261004_120000"}))
    res = client.post(f"{BASE}/cameras/cam0/recording/start?raw=true", headers=auth)
    assert res.status_code == 200
    assert res.json()["data"]["clip_id"] == "clip_20261004_120000"
    assert route.calls.last.request.url.params["raw"] == "1"


@respx.mock
def test_recording_start_without_raw_sends_no_raw_param(client, auth, device):
    route = respx.post(f"{CAM}/recording/start").mock(return_value=httpx.Response(200, json={"ok": True}))
    client.post(f"{BASE}/cameras/cam0/recording/start", headers=auth)
    assert "raw" not in route.calls.last.request.url.params


@respx.mock
def test_recording_pi_conflict_keeps_message(client, auth, device):
    # 카메라 포트의 오류 본문은 {"ok": false, "error": "<문장>"} — 문장이 메시지로 살아야 한다.
    respx.post(f"{CAM}/recording/stop").mock(return_value=httpx.Response(
        409, json={"ok": False, "error": "녹화 중이 아닙니다"}))
    res = client.post(f"{BASE}/cameras/cam0/recording/stop", headers=auth)
    assert res.status_code == 409
    assert res.json()["error"] == "CONFLICT"
    assert res.json()["message"] == "녹화 중이 아닙니다"


@respx.mock
def test_recording_list(client, auth, device):
    respx.get(f"{CAM}/recording/list").mock(return_value=httpx.Response(
        200, json={"clips": [{"id": "clip_20261004_120000", "has_thumb": True}]}))
    body = client.get(f"{BASE}/cameras/cam0/recording/clips", headers=auth).json()
    assert body["total"] == 1


@respx.mock
def test_recording_clip_streams_with_query_token(client, token, device):
    respx.get(f"{CAM}/recording/clips/clip_20261004_120000.mp4").mock(return_value=httpx.Response(
        200, content=b"MP4DATA", headers={"Content-Type": "video/mp4"}))
    res = client.get(f"{BASE}/cameras/cam0/recording/clips/clip_20261004_120000.mp4?token={token}")
    assert res.status_code == 200
    assert res.content == b"MP4DATA"
    assert res.headers["content-type"] == "video/mp4"
    assert res.headers["content-disposition"].startswith("inline")


@respx.mock
def test_recording_clip_download_is_attachment(client, token, device):
    respx.get(f"{CAM}/recording/clips/clip_20261004_120000.mp4").mock(return_value=httpx.Response(
        200, content=b"MP4DATA", headers={"Content-Type": "video/mp4"}))
    res = client.get(f"{BASE}/cameras/cam0/recording/clips/clip_20261004_120000.mp4"
                     f"?token={token}&download=true")
    assert res.headers["content-disposition"].startswith("attachment")


@pytest.mark.parametrize("name", ["../rois.json", "clip_1.mp4", "clip_20261004_120000.json",
                                  "x.mp4"])
def test_recording_clip_rejects_bad_names(client, token, device, name):
    res = client.get(f"{BASE}/cameras/cam0/recording/clips/{name}?token={token}")
    assert res.status_code in (400, 404)


def test_recording_clip_requires_auth(client, device):
    res = client.get(f"{BASE}/cameras/cam0/recording/clips/clip_20261004_120000.mp4")
    assert res.status_code == 401


def test_recording_unknown_camera_is_404(client, auth, device):
    assert client.get(f"{BASE}/cameras/nope/recording", headers=auth).status_code == 404


# ------------------------------------------------------------------ 검증 재생

@respx.mock
def test_replay_start_forwards_only_given_fields(client, auth, device):
    route = respx.post(f"{PI}/api/replay/start").mock(return_value=httpx.Response(
        200, json={"ok": True, "video": "test1.mp4", "rois": 2}))
    res = client.post(f"{BASE}/replay/start", headers=auth,
                      json={"video": "test1.mp4", "conf": 0.4})
    assert res.status_code == 200
    # 보내지 않은 값은 Pi 기본값을 따르도록 빠져 있어야 한다.
    assert json.loads(route.calls.last.request.content) == {"video": "test1.mp4", "conf": 0.4}


@respx.mock
def test_replay_pause_toggle_sends_null(client, auth, device):
    route = respx.post(f"{PI}/api/replay/pause").mock(return_value=httpx.Response(
        200, json={"paused": True}))
    client.post(f"{BASE}/replay/pause", headers=auth, json={})
    assert json.loads(route.calls.last.request.content) == {"paused": None}


@respx.mock
def test_replay_no_session_conflict(client, auth, device):
    respx.post(f"{PI}/api/replay/step").mock(return_value=httpx.Response(
        409, json={"detail": "재생 중인 세션이 없습니다"}))
    res = client.post(f"{BASE}/replay/step", headers=auth)
    assert res.status_code == 409
    assert res.json()["message"] == "재생 중인 세션이 없습니다"


@respx.mock
def test_replay_videos(client, auth, device):
    respx.get(f"{PI}/api/replay/videos").mock(return_value=httpx.Response(
        200, json={"videos": [{"name": "test1.mp4", "size_mb": 3.1}]}))
    assert client.get(f"{BASE}/replay/videos", headers=auth).json()["total"] == 1


# ------------------------------------------------------------------ 오탐 관리

@respx.mock
def test_mask_apply_sends_ids_and_camera(client, auth, device):
    route = respx.post(f"{PI}/api/static-mask/apply").mock(return_value=httpx.Response(
        200, json={"ok": True, "applied": 2}))
    res = client.put(f"{BASE}/cameras/cam0/static-mask", headers=auth, json={"ids": [1, 3]})
    assert res.status_code == 200
    req = route.calls.last.request
    assert req.url.params["camera"] == "cam0"
    assert json.loads(req.content) == {"ids": [1, 3]}


@respx.mock
def test_mask_candidates_pass_through(client, auth, device):
    respx.get(f"{PI}/api/static-mask/candidates").mock(return_value=httpx.Response(
        200, json={"candidates": [{"id": 1, "cls": 0, "recommend": True, "applied": False}]}))
    body = client.get(f"{BASE}/cameras/cam0/static-mask", headers=auth).json()
    assert body["data"][0]["recommend"] is True


def test_mask_thumb_rejects_path(client, token, device):
    res = client.get(f"{BASE}/cameras/cam0/static-mask/thumb?name=../x.jpg&token={token}")
    assert res.status_code == 400


@respx.mock
def test_calibration_start_goes_to_camera_port(client, auth, device):
    route = respx.post(f"{CAM}/calibrate/start").mock(return_value=httpx.Response(
        200, json={"ok": True, "seconds": 600}))
    client.post(f"{BASE}/cameras/cam0/calibration/start", headers=auth, json={"seconds": 600})
    assert route.calls.last.request.url.params["seconds"] == "600.0"


def test_calibration_seconds_bounds(client, auth, device):
    res = client.post(f"{BASE}/cameras/cam0/calibration/start", headers=auth, json={"seconds": 5})
    assert res.status_code in (400, 422)


@respx.mock
def test_fp_hotspots_query(client, auth, device):
    route = respx.get(f"{PI}/api/fp-hotspots").mock(return_value=httpx.Response(
        200, json={"hotspots": [{"cx": 0.5, "cy": 0.5, "count": 40}]}))
    body = client.get(f"{BASE}/cameras/cam0/fp-hotspots?min_count=10", headers=auth).json()
    assert body["total"] == 1
    params = route.calls.last.request.url.params
    assert params["camera"] == "cam0" and params["min_count"] == "10"


# ------------------------------------------------------------------ 네트워크

@respx.mock
def test_network_connect_forwards(client, auth, device):
    route = respx.post(f"{PI}/api/network/connect").mock(return_value=httpx.Response(
        200, json={"ok": True, "delay_seconds": 3}))
    res = client.post(f"{BASE}/network/connect", headers=auth,
                      json={"ssid": " HomeNet ", "password": "pw123456"})
    assert res.status_code == 202
    assert json.loads(route.calls.last.request.content) == {"ssid": "HomeNet", "password": "pw123456"}


def test_network_connect_rejects_long_password(client, auth, device):
    res = client.post(f"{BASE}/network/connect", headers=auth,
                      json={"ssid": "x", "password": "p" * 64})
    assert res.status_code in (400, 422)


def test_network_ap_switch_is_not_exposed(client, auth, device):
    # 원격 AP 전환은 기기를 망에서 잃게 하므로 일부러 없다.
    assert client.post(f"{BASE}/network/ap", headers=auth).status_code in (404, 405)


@respx.mock
def test_device_unreachable_is_503(client, auth, device):
    respx.get(f"{PI}/api/network/status").mock(side_effect=httpx.ConnectError("down"))
    res = client.get(f"{BASE}/network", headers=auth)
    assert res.status_code == 503


# ------------------------------------------------------------------ 탐색 기본 대역

@pytest.mark.parametrize("url,expected", [
    ("http://192.168.0.2:8001", "192.168.0.0/24"),
    ("http://10.1.2.3:8000", "10.1.2.0/24"),
    ("http://localhost:8000", None),
    ("http://127.0.0.1:8000", None),
    ("https://vg.example.com", None),
])
def test_suggest_subnet(url, expected):
    from app.routers.scan import suggest_subnet
    assert suggest_subnet(url) == expected


def test_suggest_route_is_not_shadowed_by_scan_id(client, auth):
    res = client.get("/api/scan/suggest", headers=auth)
    assert res.status_code == 200
    assert "subnet" in res.json()["data"]


# ------------------------------------------------------------------ 수동 확인(등록 여부)

@respx.mock
def test_verify_recognizes_registered_device_at_new_ip(client, auth, device):
    # 블루투스로 Wi-Fi를 바꿔 IP가 달라졌지만 하트비트가 아직 안 온 경우 — device_id로 알아본다.
    new = "http://192.168.0.77:5000"
    respx.get(f"{new}/api/version").mock(return_value=httpx.Response(
        200, json={"version": "1.0.0", "product": "VisionGuide", "registered": True, "device_id": "dev-1"}))
    respx.get(f"{new}/api/cameras").mock(return_value=httpx.Response(200, json={"cameras": []}))
    body = client.post("/api/scan/verify", headers=auth, json={"ip": "192.168.0.77"}).json()["data"]
    assert body["reachable"] and body["already_registered"] is True


@respx.mock
def test_verify_unknown_device_is_not_registered(client, auth, device):
    new = "http://192.168.0.78:5000"
    respx.get(f"{new}/api/version").mock(return_value=httpx.Response(
        200, json={"version": "1.0.0", "product": "VisionGuide", "registered": False, "device_id": None}))
    respx.get(f"{new}/api/cameras").mock(return_value=httpx.Response(200, json={"cameras": []}))
    body = client.post("/api/scan/verify", headers=auth, json={"ip": "192.168.0.78"}).json()["data"]
    assert body["already_registered"] is False
