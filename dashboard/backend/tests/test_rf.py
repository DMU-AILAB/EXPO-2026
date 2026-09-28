"""RF 리모컨 음성 목록 중계 — Pi에 **어떤 경로 목록을 PUT하는지**를 단언한다."""

import json

import httpx
import pytest
import respx

from app.config import settings
from app.models.audio import AudioDeployment
from app.models.device import Device

PI = "http://192.168.1.101:5000"


@pytest.fixture
def device(db_session):
    d = Device(id="dev-rf-01", name="정문", ip="192.168.1.101",
               location="정문", api_key_hash="x", config_etag="etag-1")
    db_session.add(d)
    db_session.commit()
    return d


@pytest.fixture
def auth(client, admin_user):
    token = client.post("/api/auth/login", json={
        "username": "admin", "password": "test_password"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@respx.mock
def test_get_rf_returns_config_and_pi_audio(client, auth, device):
    respx.get(f"{PI}/api/rf/config").mock(return_value=httpx.Response(
        200, json={"config": {"enabled": True}, "audio_files": ["/home/ailab/visionguide/audio/a.mp3"]}))
    respx.get(f"{PI}/api/audio/list").mock(return_value=httpx.Response(
        200, json={"files": [{"name": "a.mp3", "path": "/home/ailab/visionguide/audio/a.mp3", "size": 3}]}))

    body = client.get(f"/api/devices/{device.id}/rf", headers=auth).json()
    assert body["data"]["audio_files"] == ["/home/ailab/visionguide/audio/a.mp3"]
    assert body["data"]["pi_audio"][0]["name"] == "a.mp3"


@respx.mock
def test_put_rf_audio_uploads_library_files_and_keeps_order(client, auth, device, tmp_path, monkeypatch,
                                                             db_session):
    monkeypatch.setattr(settings, "audio_dir", str(tmp_path))
    (tmp_path / "guide.mp3").write_bytes(b"mp3")
    existing = "/home/ailab/visionguide/audio/3__.mp3"
    uploaded = "/home/ailab/visionguide/audio/guide.mp3"

    respx.get(f"{PI}/api/audio/list").mock(return_value=httpx.Response(
        200, json={"files": [{"name": "3__.mp3", "path": existing, "size": 1}]}))
    upload = respx.post(f"{PI}/api/audio/upload").mock(return_value=httpx.Response(
        200, json={"ok": True, "path": uploaded}))
    put = respx.put(f"{PI}/api/rf/audio").mock(return_value=httpx.Response(
        200, json={"ok": True, "audio_files": [uploaded, existing]}))

    res = client.put(f"/api/devices/{device.id}/rf/audio", headers=auth, json={"items": [
        {"source": "library", "filename": "guide.mp3"},
        {"source": "pi", "path": existing},
    ]})
    assert res.status_code == 200
    assert upload.call_count == 1
    assert json.loads(put.calls[0].request.content) == {"audio_files": [uploaded, existing]}
    assert db_session.query(AudioDeployment).filter_by(device_id=device.id).one().pi_path == uploaded


@respx.mock
def test_put_rf_audio_reuploads_when_cached_file_is_gone(client, auth, device, tmp_path, monkeypatch,
                                                          db_session):
    monkeypatch.setattr(settings, "audio_dir", str(tmp_path))
    (tmp_path / "guide.mp3").write_bytes(b"mp3")
    db_session.add(AudioDeployment(device_id=device.id, filename="guide.mp3",
                                   pi_path="/home/ailab/visionguide/audio/old.mp3"))
    db_session.commit()
    fresh = "/home/ailab/visionguide/audio/guide.mp3"

    respx.get(f"{PI}/api/audio/list").mock(return_value=httpx.Response(200, json={"files": []}))
    upload = respx.post(f"{PI}/api/audio/upload").mock(return_value=httpx.Response(
        200, json={"ok": True, "path": fresh}))
    put = respx.put(f"{PI}/api/rf/audio").mock(return_value=httpx.Response(
        200, json={"ok": True, "audio_files": [fresh]}))

    res = client.put(f"/api/devices/{device.id}/rf/audio", headers=auth,
                     json={"items": [{"source": "library", "filename": "guide.mp3"}]})
    assert res.status_code == 200
    assert upload.call_count == 1
    assert json.loads(put.calls[0].request.content) == {"audio_files": [fresh]}


@respx.mock
def test_put_rf_audio_missing_library_file_is_404(client, auth, device, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "audio_dir", str(tmp_path))
    respx.get(f"{PI}/api/audio/list").mock(return_value=httpx.Response(200, json={"files": []}))
    put = respx.put(f"{PI}/api/rf/audio")
    res = client.put(f"/api/devices/{device.id}/rf/audio", headers=auth,
                     json={"items": [{"source": "library", "filename": "nope.mp3"}]})
    assert res.status_code == 404
    assert put.call_count == 0
