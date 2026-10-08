"""roi_editor/server.py 단위 테스트 — ROI/카메라 CRUD, Shapely 유효성 검증, 카메라별 분리.

fastapi/httpx는 roi_editor 전용 의존성(PC 기본 requirements에는 없음, `make deps-roi-editor`로
별도 설치)이라 없으면 이 파일 전체를 건너뛴다.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "roi_editor"))

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import camera_config
import server as srv

VALID_POLY = [[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5]]
BOWTIE_POLY = [[0, 0], [1, 1], [1, 0], [0, 1]]  # 자체교차 — Shapely 기준 무효


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(srv, "rois_path", tmp_path / "rois.json")
    monkeypatch.setattr(srv, "audio_dir", tmp_path / "audio")
    monkeypatch.setattr(srv, "traffic_db_path", tmp_path / "foot_traffic.db")
    monkeypatch.setattr(srv, "camera_config_path", tmp_path / "camera_config.json")
    monkeypatch.setattr(srv, "identity_path", tmp_path / "device_identity.json")
    monkeypatch.setattr(srv, "rf_config_path", tmp_path / "rf_config.json")
    monkeypatch.setattr(srv, "audio_settings_path", tmp_path / "audio_settings.json")
    return TestClient(srv.app)


def test_get_rois_empty_by_default(client):
    res = client.get("/api/rois")
    assert res.status_code == 200
    assert res.json() == {"rois": []}


def test_pairing_page_is_available_in_api_only_mode(client, monkeypatch):
    monkeypatch.setattr(srv, "api_only", True)

    root = client.get("/", follow_redirects=False)
    assert root.status_code == 307
    assert root.headers["location"] == "/pairing"

    page = client.get("/pairing")
    assert page.status_code == 200
    assert "VisionGuide Wi-Fi" in page.text


def test_captive_portal_redirects_to_pairing_page(client):
    response = client.get("/generate_204", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "http://192.168.4.1:5000/pairing"


def test_post_rois_valid_polygon_succeeds(client):
    res = client.post("/api/rois", json={
        "rois": [{"name": "a", "points": VALID_POLY, "priority": 1, "announcement_text": "t"}],
        "conf": 0.5,
    })
    assert res.status_code == 200
    assert res.json() == {"ok": True, "count": 1}


def test_post_rois_accepts_per_class_conf_dict(client):
    res = client.post("/api/rois", json={
        "rois": [{"name": "a", "points": VALID_POLY, "priority": 1, "announcement_text": "t"}],
        "conf": {"white_cane": 0.6, "person": 0.4},
    })
    assert res.status_code == 200
    assert client.get("/api/rois").json()["conf"] == {"white_cane": 0.6, "person": 0.4}


def test_post_rois_rejects_per_class_conf_missing_a_class(client):
    res = client.post("/api/rois", json={
        "rois": [{"name": "a", "points": VALID_POLY, "priority": 1, "announcement_text": "t"}],
        "conf": {"white_cane": 0.6},
    })
    assert res.status_code == 400


def test_post_rois_rejects_per_class_conf_out_of_range(client):
    res = client.post("/api/rois", json={
        "rois": [{"name": "a", "points": VALID_POLY, "priority": 1, "announcement_text": "t"}],
        "conf": {"white_cane": 0.6, "person": 1.5},
    })
    assert res.status_code == 400


def test_post_rois_rejects_scalar_conf_out_of_range(client):
    res = client.post("/api/rois", json={
        "rois": [{"name": "a", "points": VALID_POLY, "priority": 1, "announcement_text": "t"}],
        "conf": 1.5,
    })
    assert res.status_code == 400


def test_post_rois_rejects_invalid_zone_type(client):
    res = client.post("/api/rois", json={
        "rois": [{"name": "b", "points": VALID_POLY, "priority": 1, "announcement_text": "t", "zone_type": "bogus"}],
    })
    assert res.status_code == 400


def test_post_rois_rejects_self_intersecting_polygon(client):
    res = client.post("/api/rois", json={
        "rois": [{"name": "c", "points": BOWTIE_POLY, "priority": 1, "announcement_text": "t"}],
    })
    assert res.status_code == 400


def test_delete_roi_not_found_returns_404(client):
    res = client.delete("/api/rois/nonexistent")
    assert res.status_code == 404


def test_cameras_empty_by_default(client):
    res = client.get("/api/cameras")
    assert res.status_code == 200
    assert res.json() == {"cameras": []}


def test_scan_cameras_fails_gracefully_without_picamera2(client):
    """picamera2는 Pi 전용 패키지라 PC 테스트 환경엔 없다 — 에러가 나도 500이 아니라
    빈 목록 + error 필드로 정상 응답해야 한다(roi_editor는 PC에서도 개발/테스트되므로)."""
    res = client.get("/api/cameras/scan")
    assert res.status_code == 200
    body = res.json()
    assert body["cameras"] == []
    assert "error" in body


def test_post_cameras_valid_config_succeeds(client):
    res = client.post("/api/cameras", json={"cameras": [
        {"id": "cam0", "port": 8080, "inference_backend": "edgetpu"},
        {"id": "cam1", "port": 8081, "inference_backend": "tflite"},
    ]})
    assert res.status_code == 200
    assert res.json() == {"ok": True, "count": 2}


def test_post_cameras_rejects_duplicate_edgetpu(client):
    res = client.post("/api/cameras", json={"cameras": [
        {"id": "cam0", "port": 8080, "inference_backend": "edgetpu"},
        {"id": "cam1", "port": 8081, "inference_backend": "edgetpu"},
    ]})
    assert res.status_code == 400


def test_post_cameras_defaults_model_variant(client):
    """model_variant를 안 보내면 CameraProfile 기본값이 그대로 저장돼야 한다.

    기대값을 문자열로 박아두면 기본 모델을 바꿀 때마다 테스트가 깨지므로
    camera_config의 상수를 그대로 참조한다 — 검증하려는 것은 "어떤 모델인가"가
    아니라 "pydantic 페이로드 기본값이 CameraProfile과 어긋나지 않는가"다.
    """
    res = client.post("/api/cameras", json={"cameras": [
        {"id": "cam0", "port": 8080, "inference_backend": "tflite"},
    ]})
    assert res.status_code == 200
    body = client.get("/api/cameras").json()
    assert body["cameras"][0]["model_variant"] == camera_config._DEFAULT_MODEL_VARIANT


def test_get_model_variants_lists_known_keys(client):
    res = client.get("/api/model-variants")
    assert res.status_code == 200
    keys = {v["key"] for v in res.json()["variants"]}
    assert keys == set(camera_config.MODEL_VARIANTS)


def test_model_variants는_가중치_존재를_available로_알린다(client, tmp_path, monkeypatch):
    """목록은 정적 표라 없는 모델도 나온다 — 대시보드의 모델 일괄 변경이 이 값을 보고 건너뛴다."""
    root = tmp_path / "root"
    present = camera_config.MODEL_VARIANTS["v10_320"]["weights_dir"]
    (root / present).mkdir(parents=True)
    (root / present / "best_int8.tflite").write_bytes(b"x")
    # 디렉터리만 있고 파일이 없는 경우도 '없음'이다
    (root / camera_config.MODEL_VARIANTS["v15_320"]["weights_dir"]).mkdir(parents=True)
    monkeypatch.setattr(srv, "_ROOT", root)

    by_key = {v["key"]: v for v in client.get("/api/model-variants").json()["variants"]}
    assert by_key["v10_320"]["available"] is True
    assert by_key["v15_320"]["available"] is False
    assert by_key["v4_320"]["available"] is False
    assert all(isinstance(v["available"], bool) for v in by_key.values())
    assert by_key["v10_320"]["weights_dir"] == present                 # 기존 필드는 그대로


def test_get_device_status_returns_expected_keys(client):
    res = client.get("/api/device/status")
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"uptime_seconds", "cpu_temp_c", "load_avg", "mem_used_mb", "mem_total_mb"}


def test_identity_bootstrap_does_not_require_pairing_token(client):
    payload = {
        "device_id": "pi-01",
        "api_key": "vg_secret",
        "server_url": "http://pc:8000",
    }

    registered = client.post("/api/identity", json=payload)
    assert registered.status_code == 200
    assert registered.json()["usable"] is True

    # 기존 키 없이도 덮어쓸 수 있다(인수) — 서버를 바꾸거나 DB를 잃은 기기를 다시 등록한다.
    overwritten = client.post("/api/identity", json={**payload, "device_id": "pi-02"})
    assert overwritten.status_code == 200
    assert client.get("/api/identity").json()["device_id"] == "pi-02"


def test_identity_takeover_leaves_signal_and_log(client, tmp_path, capsys):
    base = {"device_id": "pi-01", "api_key": "old-key", "server_url": "http://pc:8000"}
    assert client.post("/api/identity", json=base).status_code == 200
    # 최초 등록은 인수가 아니다
    assert not (tmp_path / "takeover.flag").exists()

    # 올바른 키로 다시 심는 것도 인수가 아니다
    ok = client.post("/api/identity", json={**base, "api_key": "k2"},
                     headers={"X-Device-Key": "old-key"})
    assert ok.status_code == 200
    assert not (tmp_path / "takeover.flag").exists()

    # 키 없이 덮어쓰면 인수 — 신호 파일과 로그가 남는다
    taken = client.post("/api/identity", json={**base, "device_id": "pi-02", "api_key": "k3"})
    assert taken.status_code == 200
    assert (tmp_path / "takeover.flag").exists()
    assert "기기 신원 인수" in capsys.readouterr().out

    # 틀린 키도 인수로 취급한다
    (tmp_path / "takeover.flag").unlink()
    wrong = client.post("/api/identity", json={**base, "api_key": "k4"},
                        headers={"X-Device-Key": "nope"})
    assert wrong.status_code == 200
    assert (tmp_path / "takeover.flag").exists()


def _proof(key: str, nonce: str) -> str:
    import hashlib
    import hmac as _hmac
    return _hmac.new(key.encode(), nonce.encode(), hashlib.sha256).hexdigest()


def test_identity_proof는_키를_보내지_않고_안다는_것을_증명한다(client):
    """서버가 다른 서버 소속 기기를 빼앗지 않고, 가짜 기기에 비밀을 건네지도 않고 소속을 확인하는 수단."""
    client.post("/api/identity", json={"device_id": "pi-01", "api_key": "key-1", "server_url": "http://pc:8000"})
    res = client.get("/api/identity/proof", params={"nonce": "n" * 16})
    assert res.status_code == 200
    assert res.json()["proof"] == _proof("key-1", "n" * 16) and res.json()["device_id"] == "pi-01"
    assert "key-1" not in res.text                                           # 키는 나가지 않는다


def test_identity_proof는_nonce마다_다르다(client):
    client.post("/api/identity", json={"device_id": "pi-01", "api_key": "key-1", "server_url": "http://pc:8000"})
    a = client.get("/api/identity/proof", params={"nonce": "a" * 16}).json()["proof"]
    b = client.get("/api/identity/proof", params={"nonce": "b" * 16}).json()["proof"]
    assert a != b                                                            # 응답을 재사용할 수 없다


def test_identity_proof는_다른_키로는_위조할_수_없다(client):
    client.post("/api/identity", json={"device_id": "pi-01", "api_key": "key-1", "server_url": "http://pc:8000"})
    proof = client.get("/api/identity/proof", params={"nonce": "n" * 16}).json()["proof"]
    assert proof != _proof("wrong-key", "n" * 16)


def test_identity_proof는_신원이_없으면_403이다(client):
    assert client.get("/api/identity/proof", params={"nonce": "n" * 16}).status_code == 403


@pytest.mark.parametrize("nonce", ["short", "x" * 129])
def test_identity_proof는_nonce_길이를_검증한다(client, nonce):
    client.post("/api/identity", json={"device_id": "pi-01", "api_key": "key-1", "server_url": "http://pc:8000"})
    assert client.get("/api/identity/proof", params={"nonce": nonce}).status_code == 400


def test_identity_proof는_아무것도_바꾸지_않는다(client, tmp_path):
    client.post("/api/identity", json={"device_id": "pi-01", "api_key": "key-1", "server_url": "http://pc:8000"})
    ident_file = next(tmp_path.glob("device_identity.json"))
    raw = ident_file.read_bytes()
    client.get("/api/identity/proof", params={"nonce": "n" * 16})
    assert ident_file.read_bytes() == raw and not (tmp_path / "takeover.flag").exists()


def test_틀린_키로_신원을_보내면_덮어쓰지만_proof는_그렇지_않다(client):
    """이 비대칭이 proof를 따로 둔 이유다 — POST /api/identity로 소속을 '시험'하면 곧 인수가 된다."""
    client.post("/api/identity", json={"device_id": "pi-01", "api_key": "key-1", "server_url": "http://pc:8000"})
    client.get("/api/identity/proof", params={"nonce": "n" * 16})
    assert client.get("/api/identity").json()["device_id"] == "pi-01"        # proof는 건드리지 않았다
    client.post("/api/identity", json={"device_id": "pi-02", "api_key": "k2", "server_url": "http://x:1"},
                headers={"X-Device-Key": "wrong"})
    assert client.get("/api/identity").json()["device_id"] == "pi-02"        # POST는 틀린 키로도 덮어쓴다


def test_identity_delete_requires_current_device_key(client, tmp_path):
    initial = {
        "device_id": "pi-01",
        "api_key": "old-key",
        "server_url": "http://pc:8000",
    }
    assert client.post("/api/identity", json=initial).status_code == 200

    replacement = {**initial, "device_id": "pi-02", "api_key": "new-key"}
    response = client.post("/api/identity", json=replacement,
                           headers={"X-Device-Key": "old-key"})
    assert response.status_code == 200
    assert client.delete("/api/identity").status_code == 401
    assert client.delete("/api/identity", headers={"X-Device-Key": "new-key"}).json()["removed"] is True


def test_camera_scoped_rois_are_isolated(client):
    # inference_backend를 다르게 지정 — 둘 다 기본값(auto)이면 auto가 Coral을 먼저
    # 시도하므로 두 카메라가 동시 활성화될 때 검증 단계에서 거부된다.
    client.post("/api/cameras", json={"cameras": [
        {"id": "cam0", "port": 8080, "roi_config": "rois_cam0.json", "inference_backend": "auto"},
        {"id": "cam1", "port": 8081, "roi_config": "rois_cam1.json", "inference_backend": "tflite"},
    ]})
    client.post("/api/rois", params={"camera": "cam0"}, json={
        "rois": [{"name": "only-cam0", "points": VALID_POLY, "priority": 1, "announcement_text": "t"}],
    })

    cam0 = client.get("/api/rois", params={"camera": "cam0"}).json()
    cam1 = client.get("/api/rois", params={"camera": "cam1"}).json()
    assert len(cam0["rois"]) == 1 and cam0["rois"][0]["name"] == "only-cam0"
    assert len(cam1["rois"]) == 0


def test_unknown_camera_returns_404(client):
    res = client.get("/api/rois", params={"camera": "nonexistent"})
    assert res.status_code == 404


def test_stats_timeseries_today_returns_24_hourly_points(client):
    res = client.get("/api/stats/timeseries", params={"period": "today"})
    assert res.status_code == 200
    body = res.json()
    assert body["granularity"] == "hour"
    assert len(body["points"]) == 24
    assert [p["hour"] for p in body["points"]] == list(range(24))


def test_stats_timeseries_7d_returns_7_daily_points(client):
    res = client.get("/api/stats/timeseries", params={"period": "7d"})
    assert res.status_code == 200
    body = res.json()
    assert body["granularity"] == "day"
    assert len(body["points"]) == 7


def test_stats_timeseries_30d_returns_30_daily_points(client):
    res = client.get("/api/stats/timeseries", params={"period": "30d"})
    assert res.status_code == 200
    body = res.json()
    assert body["granularity"] == "day"
    assert len(body["points"]) == 30


def test_stats_timeseries_rejects_unknown_period(client):
    res = client.get("/api/stats/timeseries", params={"period": "bogus"})
    assert res.status_code == 400


def test_get_events_empty_by_default(client):
    res = client.get("/api/events")
    assert res.status_code == 200
    assert res.json() == {"events": []}


def test_get_audio_file_serves_file_inside_audio_dir(client, tmp_path):
    (tmp_path / "audio").mkdir(exist_ok=True)
    f = tmp_path / "audio" / "hello.mp3"
    f.write_bytes(b"fake-mp3-bytes")
    res = client.get("/api/audio/file", params={"path": str(f)})
    assert res.status_code == 200
    assert res.content == b"fake-mp3-bytes"


def test_get_audio_file_rejects_path_outside_audio_dir(client, tmp_path):
    outside = tmp_path / "outside.mp3"
    outside.write_bytes(b"nope")
    res = client.get("/api/audio/file", params={"path": str(outside)})
    assert res.status_code == 400


def test_get_audio_file_404_for_missing_file(client, tmp_path):
    missing = tmp_path / "audio" / "missing.mp3"
    res = client.get("/api/audio/file", params={"path": str(missing)})
    assert res.status_code == 404


def test_fp_hotspots_empty_when_nothing_logged(client):
    res = client.get("/api/fp-hotspots")
    assert res.status_code == 200
    assert res.json() == {"hotspots": []}


def test_fp_hotspots_returns_logged_spots_and_clear_resets(client, tmp_path):
    from fp_hotspots import log_suppressed

    db = tmp_path / "foot_traffic.db"
    for _ in range(6):
        log_suppressed(db, 0.40, 0.50, 0.45, 0.62)

    spots = client.get("/api/fp-hotspots?min_count=5").json()["hotspots"]
    assert len(spots) == 1
    assert spots[0]["count"] == 6
    assert spots[0]["bbox"] == pytest.approx([0.40, 0.50, 0.45, 0.62])

    # min_count 미달이면 제안하지 않는다
    assert client.get("/api/fp-hotspots?min_count=10").json()["hotspots"] == []

    assert client.delete("/api/fp-hotspots").status_code == 200
    assert client.get("/api/fp-hotspots?min_count=1").json()["hotspots"] == []


def test_audio_list_returns_only_audio_files(client, tmp_path):
    audio = tmp_path / "audio"
    audio.mkdir()
    (audio / "b.wav").write_bytes(b"x")
    (audio / "a.mp3").write_bytes(b"yy")
    (audio / "notes.txt").write_text("skip")
    files = client.get("/api/audio/list").json()["files"]
    assert [f["name"] for f in files] == ["a.mp3", "b.wav"]
    assert files[0]["size"] == 2
    assert Path(files[0]["path"]).is_absolute()


def test_rf_config_defaults_to_empty_when_missing(client):
    body = client.get("/api/rf/config").json()
    assert body == {"config": {}, "audio_files": []}


def test_put_rf_audio_saves_ordered_list_and_keeps_other_keys(client, tmp_path):
    import json

    audio = tmp_path / "audio"
    audio.mkdir()
    first, second = audio / "1.mp3", audio / "2.mp3"
    first.write_bytes(b"x")
    second.write_bytes(b"x")
    (tmp_path / "rf_config.json").write_text(
        json.dumps({"enabled": True, "frequency_mhz": 356.635}), encoding="utf-8")

    r = client.put("/api/rf/audio", json={"audio_files": [str(second), str(first)]})
    assert r.status_code == 200
    saved = json.loads((tmp_path / "rf_config.json").read_text(encoding="utf-8"))
    assert saved["enabled"] is True and saved["frequency_mhz"] == 356.635
    assert saved["audio_files"] == [str(second.resolve()), str(first.resolve())]
    assert client.get("/api/rf/config").json()["audio_files"] == saved["audio_files"]


def test_put_rf_audio_rejects_outside_and_missing_paths(client, tmp_path):
    (tmp_path / "audio").mkdir()
    outside = tmp_path / "evil.mp3"
    outside.write_bytes(b"x")
    assert client.put("/api/rf/audio", json={"audio_files": [str(outside)]}).status_code == 400
    missing = tmp_path / "audio" / "nope.mp3"
    assert client.put("/api/rf/audio", json={"audio_files": [str(missing)]}).status_code == 404
    assert not (tmp_path / "rf_config.json").exists()


def test_put_rf_group_saves_priority_and_keeps_other_keys(client, tmp_path):
    import json

    (tmp_path / "rf_config.json").write_text(
        json.dumps({"enabled": True, "audio_files": ["/x.mp3"]}), encoding="utf-8")
    r = client.put("/api/rf/group", json={"group_enabled": True, "group_priority": 2})
    assert r.status_code == 200
    saved = json.loads((tmp_path / "rf_config.json").read_text(encoding="utf-8"))
    assert saved == {"enabled": True, "audio_files": ["/x.mp3"],
                     "group_enabled": True, "group_priority": 2}
    assert client.put("/api/rf/group", json={"group_enabled": True, "group_priority": -1}).status_code == 422


def test_put_rf_detection_saves_threshold_and_keeps_other_keys(client, tmp_path):
    import json

    (tmp_path / "rf_config.json").write_text(
        json.dumps({"enabled": True, "rssi_threshold": 110, "group_priority": 2}), encoding="utf-8")
    r = client.put("/api/rf/detection", json={"rssi_threshold": 80})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "rssi_threshold": 80}
    saved = json.loads((tmp_path / "rf_config.json").read_text(encoding="utf-8"))
    assert saved == {"enabled": True, "rssi_threshold": 80, "group_priority": 2}


def test_put_rf_detection_rejects_out_of_range(client, tmp_path):
    # RSSI 레지스터는 0~255이고 rf_audio_trigger.py는 0 < threshold < 256만 받는다.
    for bad in (0, -1, 256, 1000):
        assert client.put("/api/rf/detection", json={"rssi_threshold": bad}).status_code == 422
    assert client.put("/api/rf/detection", json={"rssi_threshold": "abc"}).status_code == 422
    assert not (tmp_path / "rf_config.json").exists()


# ---------------------------------------------------------------------------
# 푸시 업데이트 (/api/update) — 코드를 받아 적용하는 경로라 키·배치·검증을 고정한다.
# ---------------------------------------------------------------------------

@pytest.fixture
def update_env(client, tmp_path, monkeypatch):
    dest = tmp_path / "visionguide"
    dest.mkdir()
    (dest / "detect.py").write_text("OLD = 1\n")
    monkeypatch.setattr(srv, "update_dest", dest)
    restarts = []
    monkeypatch.setattr(srv, "_schedule_update_restart", lambda: restarts.append(1))
    # 스모크는 별도 테스트(test_self_update)가 맡는다 — 여기서는 서버를 또 띄우지 않는다.
    real = srv.self_update.apply_bundle
    monkeypatch.setattr(srv.self_update, "apply_bundle",
                        lambda data, d, include_models=False: real(data, d, include_models, smoke=False))
    client.post("/api/identity", json={"device_id": "pi-01", "api_key": "key-1",
                                       "server_url": "http://pc:8000"})
    return client, dest, restarts


def _upload(client, bundle, key="key-1", **params):
    headers = {"X-Device-Key": key} if key is not None else {}
    return client.post("/api/update", params=params, headers=headers,
                       files={"file": ("bundle.tar.gz", bundle, "application/gzip")})


def test_update_requires_device_key(update_env):
    from tests.test_self_update import make_bundle
    client, dest, restarts = update_env
    bundle = make_bundle({"detect.py": "NEW = 2\n"})
    assert _upload(client, bundle, key=None).status_code == 401
    assert _upload(client, bundle, key="wrong").status_code == 401
    assert (dest / "detect.py").read_text() == "OLD = 1\n"
    assert restarts == []


def test_update_applies_and_schedules_restart(update_env):
    from tests.test_self_update import make_bundle
    client, dest, restarts = update_env
    res = _upload(client, make_bundle({"detect.py": "NEW = 2\n"}, bundle_id="v9"))
    assert res.status_code == 200
    body = res.json()
    assert body["restarting"] is True and body["bundle_id"] == "v9"
    assert (dest / "detect.py").read_text() == "NEW = 2\n"
    assert restarts == [1]
    assert client.get("/api/update/status").json() == {"bundle_id": "v9", "has_backup": True}


def test_update_rejects_bad_bundle_without_restart(update_env):
    from tests.test_self_update import make_bundle
    client, dest, restarts = update_env
    res = _upload(client, make_bundle({"detect.py": "def (:\n"}))
    assert res.status_code == 422
    assert (dest / "detect.py").read_text() == "OLD = 1\n"
    assert restarts == []


def test_update_refuses_dev_tree(update_env):
    from tests.test_self_update import make_bundle
    client, dest, restarts = update_env
    (dest / "device").mkdir()                    # 저장소처럼 보이는 배치
    res = _upload(client, make_bundle({"detect.py": "NEW = 2\n"}))
    assert res.status_code == 409
    assert (dest / "detect.py").read_text() == "OLD = 1\n"


def test_update_rollback_requires_key_and_restores(update_env):
    from tests.test_self_update import make_bundle
    client, dest, restarts = update_env
    _upload(client, make_bundle({"detect.py": "NEW = 2\n"}))
    assert client.post("/api/update/rollback").status_code == 401
    res = client.post("/api/update/rollback", headers={"X-Device-Key": "key-1"})
    assert res.status_code == 200
    assert (dest / "detect.py").read_text() == "OLD = 1\n"
    assert restarts == [1, 1]
    assert client.post("/api/update/rollback", headers={"X-Device-Key": "key-1"}).status_code == 409


def test_diagnose_endpoint_reports_server_power_and_clock(client, monkeypatch):
    import diagnose
    monkeypatch.setattr(diagnose, "check_server", lambda url: {"url": url, "dns": {"ok": True}})
    monkeypatch.setattr(diagnose, "read_throttled", lambda: {"raw": "0x0"})
    monkeypatch.setattr(diagnose, "ntp_synchronized", lambda: True)

    # 신원이 없으면 서버 점검은 건너뛴다
    body = client.get("/api/diagnose").json()
    assert body["server"] is None and body["power"] == {"raw": "0x0"} and body["ntp_synchronized"] is True

    client.post("/api/identity", json={"device_id": "pi-01", "api_key": "k", "server_url": "http://pc:8001"})
    assert client.get("/api/diagnose").json()["server"]["url"] == "http://pc:8001"
    assert "api_key" not in client.get("/api/diagnose").text           # 키는 나가지 않는다


def test_audio_settings_default_is_audible_and_put_persists(client, tmp_path):
    assert client.get("/api/audio/settings").json() == {"muted": False}
    res = client.put("/api/audio/settings", json={"muted": True})
    assert res.status_code == 200 and res.json()["muted"] is True
    assert (tmp_path / "audio_settings.json").read_text().count("true") == 1
    assert client.get("/api/audio/settings").json() == {"muted": True}
    assert client.put("/api/audio/settings", json={"muted": "maybe"}).status_code == 422
