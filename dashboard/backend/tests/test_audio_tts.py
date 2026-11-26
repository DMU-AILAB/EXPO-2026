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


def test_tts_is_unavailable_without_powershell_or_edge_tts(client, admin_user, monkeypatch):
    token = client.post("/api/auth/login", json={
        "username": "admin", "password": "test_password",
    }).json()["access_token"]
    monkeypatch.setattr(audio_router, "_powershell_path", lambda: None)
    monkeypatch.setattr(audio_router, "_edge_tts_available", lambda: False)

    response = client.post(
        "/api/audio/tts",
        json={"text": "안내 방송입니다."},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 503


def _fake_edge_tts(path, request):
    # 실제 mp3 헤더가 아니어도 길이 계산을 우회하도록 아래 테스트가 _audio_duration도 대체한다.
    Path(path).write_bytes(b"ID3" + b"\x00" * 64)


def test_tts_uses_edge_tts_when_not_windows(client, admin_user, monkeypatch):
    token = client.post("/api/auth/login", json={
        "username": "admin", "password": "test_password",
    }).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    audio_dir = Path.cwd() / f".pytest-audio-edge-{uuid.uuid4().hex}"
    audio_dir.mkdir()
    monkeypatch.setattr(audio_router.settings, "audio_dir", str(audio_dir))
    monkeypatch.setattr(audio_router, "_powershell_path", lambda: None)
    monkeypatch.setattr(audio_router, "_edge_tts_available", lambda: True)
    monkeypatch.setattr(audio_router, "_generate_edge_tts", _fake_edge_tts)
    monkeypatch.setattr(audio_router, "_audio_duration", lambda path, suffix: 2.0)
    try:
        response = client.post(
            "/api/audio/tts",
            json={"text": "AILAB 부스 앞입니다", "voice": "Microsoft Heami - Korean", "rate": 1},
            headers=headers,
        )
        assert response.status_code == 201, response.text
        item = response.json()["data"]
        assert item["filename"].endswith(".mp3")
        assert (audio_dir / item["filename"]).exists()
    finally:
        shutil.rmtree(audio_dir, ignore_errors=True)


def test_edge_voice_name_ignores_browser_voices():
    assert audio_router._edge_voice_name("Microsoft Heami - Korean (Korea)") == audio_router.DEFAULT_EDGE_VOICE
    assert audio_router._edge_voice_name(None) == audio_router.DEFAULT_EDGE_VOICE
    assert audio_router._edge_voice_name("ko-KR-InJoonNeural") == "ko-KR-InJoonNeural"
