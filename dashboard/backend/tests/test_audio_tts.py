import wave
import shutil
import uuid
from pathlib import Path

from app.routers import audio as audio_router


def _fake_tts(path, request, powershell):
    with wave.open(path, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\x00\x00" * 16_000)


def test_tts_generates_wav_and_registers_audio(client, admin_user, monkeypatch):
    token = client.post("/api/auth/login", json={
        "username": "admin", "password": "test_password",
    }).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    audio_dir = Path.cwd() / f".pytest-audio-tts-{uuid.uuid4().hex}"
    audio_dir.mkdir()
    monkeypatch.setattr(audio_router.settings, "audio_dir", str(audio_dir))
    monkeypatch.setattr(audio_router, "_powershell_path", lambda: "powershell.exe")
    monkeypatch.setattr(audio_router, "_generate_windows_tts", _fake_tts)
    try:
        response = client.post(
            "/api/audio/tts",
            json={"text": "안내 방송입니다.", "voice": "Microsoft Heami", "rate": 2},
            headers=headers,
        )

        assert response.status_code == 201, response.text
        item = response.json()["data"]
        assert item["filename"].endswith(".wav")
        assert item["size_bytes"] > 0
        assert (audio_dir / item["filename"]).exists()

        listed = client.get("/api/audio", headers=headers)
        assert listed.status_code == 200
        assert any(item["filename"] == entry["filename"] for entry in listed.json()["data"])
    finally:
        shutil.rmtree(audio_dir, ignore_errors=True)


def test_tts_is_unavailable_without_windows_powershell(client, admin_user, monkeypatch):
    token = client.post("/api/auth/login", json={
        "username": "admin", "password": "test_password",
    }).json()["access_token"]
    monkeypatch.setattr(audio_router, "_powershell_path", lambda: None)

    response = client.post(
        "/api/audio/tts",
        json={"text": "안내 방송입니다."},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 503
