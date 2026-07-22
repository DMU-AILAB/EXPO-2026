"""roi_editor/server.py 단위 테스트 — ROI/카메라 CRUD, Shapely 유효성 검증, 카메라별 분리.

fastapi/httpx는 roi_editor 전용 의존성(PC 기본 requirements에는 없음, `make deps-roi-editor`로
별도 설치)이라 없으면 이 파일 전체를 건너뛴다.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "roi_editor"))

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import server as srv

VALID_POLY = [[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5]]
BOWTIE_POLY = [[0, 0], [1, 1], [1, 0], [0, 1]]  # 자체교차 — Shapely 기준 무효


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(srv, "rois_path", tmp_path / "rois.json")
    monkeypatch.setattr(srv, "audio_dir", tmp_path / "audio")
    monkeypatch.setattr(srv, "traffic_db_path", tmp_path / "foot_traffic.db")
    monkeypatch.setattr(srv, "camera_config_path", tmp_path / "camera_config.json")
    return TestClient(srv.app)


def test_get_rois_empty_by_default(client):
    res = client.get("/api/rois")
    assert res.status_code == 200
    assert res.json() == {"rois": []}


def test_post_rois_valid_polygon_succeeds(client):
    res = client.post("/api/rois", json={
        "rois": [{"name": "a", "points": VALID_POLY, "priority": 1, "announcement_text": "t"}],
        "conf": 0.5,
    })
    assert res.status_code == 200
    assert res.json() == {"ok": True, "count": 1}


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
    """model_variant를 안 보내면 CameraProfile 기본값(v2_640)이 그대로 저장돼야 한다."""
    res = client.post("/api/cameras", json={"cameras": [
        {"id": "cam0", "port": 8080, "inference_backend": "tflite"},
    ]})
    assert res.status_code == 200
    body = client.get("/api/cameras").json()
    assert body["cameras"][0]["model_variant"] == "v2_640"


def test_get_model_variants_lists_known_keys(client):
    res = client.get("/api/model-variants")
    assert res.status_code == 200
    keys = {v["key"] for v in res.json()["variants"]}
    assert keys == {"v2_640", "v3_320", "v4_320"}


def test_get_device_status_returns_expected_keys(client):
    res = client.get("/api/device/status")
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"uptime_seconds", "cpu_temp_c", "load_avg", "mem_used_mb", "mem_total_mb"}


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
