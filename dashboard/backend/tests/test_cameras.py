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
from app.routers import cameras as cameras_router

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


@respx.mock
async def test_get_cameras_exposes_running_legacy_camera(client, auth, db_session, device,
                                                         monkeypatch):
    """A legacy Pi camera must appear even when camera_config.json is empty."""
    respx.get(f"{PI}/api/cameras").mock(return_value=httpx.Response(
        200, json={"cameras": []}))
    respx.get(f"{PI}/api/cameras/scan").mock(return_value=httpx.Response(
        200, json={"cameras": [{"num": 0, "model": "imx708_wide_noir"}]}))

    async def runtime_cameras(_device_id):
        return {"legacy": {"is_streaming": True, "configured": False}}

    monkeypatch.setattr(cameras_router, "get_buffered_cameras", runtime_cameras)

    res = client.get("/api/devices/cam-entrance-01/cameras", headers=auth)

    assert res.status_code == 200
    body = res.json()
    assert body["data"][0]["id"] == "legacy"
    assert body["data"][0]["is_streaming"] is True


# ---------------------------------------------------------------------------
# 얼굴 모자이크 — 기기 단위 토글 + 카메라 필드 + 기존 DB 마이그레이션
# ---------------------------------------------------------------------------

@respx.mock
def test_privacy_mask_toggle_changes_only_that_field_for_every_camera(client, auth, device,
                                                                      db_session):
    """목록의 토글은 기기의 **모든 카메라**에 `privacy_mask`만 바꿔 되돌려 쓴다."""
    respx.get(f"{PI}/api/cameras").mock(return_value=httpx.Response(200, json={"cameras": [
        _pi_profile(privacy_mask=True, privacy_mask_ratio=0.3),
        _pi_profile(id="dev-cam1", port=8081, roi_config="rois.cam1.json",
                    traffic_db="ft1.db", privacy_mask=True, privacy_mask_ratio=0.3,
                    inference_backend="tflite"),
    ]}))
    post = respx.post(f"{PI}/api/cameras").mock(return_value=httpx.Response(200, json={"ok": True}))

    res = client.put("/api/devices/cam-entrance-01/privacy-mask", json={"enabled": False},
                     headers=auth)

    assert res.status_code == 200, res.text
    assert res.json()["cameras"] == 2
    sent = json.loads(post.calls[0].request.content)["cameras"]
    assert [c["privacy_mask"] for c in sent] == [False, False]
    # 다른 필드(비율 포함)는 Pi 값 그대로 — 낡은 값으로 덮으면 안 된다
    assert all(c["privacy_mask_ratio"] == 0.3 for c in sent)
    assert sent[0]["label"] == "정문" and sent[1]["port"] == 8081

    cam = db_session.query(Camera).filter(Camera.id == "dev-cam0").first()
    db_session.refresh(cam)
    assert cam.privacy_mask is False


@respx.mock
def test_privacy_mask_toggle_does_not_need_if_match(client, auth, device):
    """목록 화면은 etag가 없다 — 읽고-병합-쓰기라 낡은 값으로 덮지 않으므로 요구하지 않는다."""
    respx.get(f"{PI}/api/cameras").mock(return_value=httpx.Response(
        200, json={"cameras": [_pi_profile()]}))
    respx.post(f"{PI}/api/cameras").mock(return_value=httpx.Response(200, json={"ok": True}))
    res = client.put("/api/devices/cam-entrance-01/privacy-mask", json={"enabled": True},
                     headers=auth)
    assert res.status_code == 200


@respx.mock
def test_privacy_mask_toggle_leaves_cache_alone_when_pi_rejects(client, auth, device,
                                                                db_session):
    respx.get(f"{PI}/api/cameras").mock(return_value=httpx.Response(
        200, json={"cameras": [_pi_profile()]}))
    respx.post(f"{PI}/api/cameras").mock(return_value=httpx.Response(500, json={"detail": "x"}))
    res = client.put("/api/devices/cam-entrance-01/privacy-mask", json={"enabled": False},
                     headers=auth)
    assert res.status_code >= 400
    cam = db_session.query(Camera).filter(Camera.id == "dev-cam0").first()
    db_session.refresh(cam)
    assert cam.privacy_mask is not False


@respx.mock
def test_camera_patch_sends_privacy_mask_to_pi(client, auth, device):
    respx.get(f"{PI}/api/cameras").mock(return_value=httpx.Response(
        200, json={"cameras": [_pi_profile()]}))
    post = respx.post(f"{PI}/api/cameras").mock(return_value=httpx.Response(200, json={"ok": True}))
    client.patch("/api/devices/cam-entrance-01/cameras/dev-cam0",
                 json={"privacy_mask": False}, headers={**auth, "If-Match": "etag-1"})
    sent = json.loads(post.calls[0].request.content)["cameras"][0]
    assert sent["privacy_mask"] is False


def test_device_list_exposes_privacy_mask_per_camera(client, auth, device):
    res = client.get("/api/devices", headers=auth)
    cams = res.json()["data"][0]["cameras"]
    assert cams and cams[0]["privacy_mask"] is True


def test_ensure_columns_adds_privacy_mask_to_an_old_database(tmp_path, monkeypatch):
    """★ 기존 운영 DB에는 컬럼이 없다 — 안 채우면 카메라 조회가 전부 실패한다."""
    from sqlalchemy import create_engine, text
    import app.database as database

    old = create_engine(f"sqlite:///{tmp_path/'old.db'}")
    with old.begin() as c:
        c.execute(text("CREATE TABLE cameras (id TEXT, device_id TEXT, port INTEGER)"))
        c.execute(text("INSERT INTO cameras VALUES ('c0','d0',8080)"))
    monkeypatch.setattr(database, "engine", old)

    database.ensure_columns()
    database.ensure_columns()          # 두 번 불러도 안전해야 한다

    with old.begin() as c:
        row = c.execute(text("SELECT privacy_mask FROM cameras")).fetchone()
    assert row[0] == 1                 # 기존 행은 켜짐(안전한 쪽)
