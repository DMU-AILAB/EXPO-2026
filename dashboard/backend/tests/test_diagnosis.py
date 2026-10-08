"""연결 진단 — 이번 세션에서 실제로 겪은 원인마다 올바른 항목이 걸리는지.

방화벽(기기→서버만 막힘), 구버전 코드, 서버 주소 불일치, cam1 미연결, 신원 미주입.
"""

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.config import settings
from app.models.camera import Camera
from app.services import address_sync, server_address


@pytest.fixture
def auth_headers(client: TestClient, admin_user):
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test_password"}
                        ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def fixed_server(monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", "http://server:8001")
    monkeypatch.setattr(server_address, "_seen_port", None)
    address_sync._retry.clear()


PI = "http://10.0.0.1:5000"
GOOD_SERVER = {"url": "http://server:8001", "dns": {"ok": True}, "tcp": {"ok": True},
               "http": {"ok": True, "ms": 12, "detail": "HTTP 200"}}


def _register(client, headers, db_session, cameras=("cam0",)):
    respx.post(f"{PI}/api/identity").mock(return_value=httpx.Response(200, json={"ok": True}))
    respx.get(f"{PI}/api/cameras").mock(return_value=httpx.Response(200, json={"cameras": []}))
    assert client.post("/api/devices", json={"id": "d1", "name": "d1", "ip": "10.0.0.1",
                                             "location": "x"}, headers=headers).status_code == 201
    for cid in cameras:
        db_session.add(Camera(id=cid, device_id="d1", port=8080 + len(cid), is_active=True))
    db_session.commit()
    respx.reset()


def _healthy_pi():
    respx.get(f"{PI}/api/version").mock(return_value=httpx.Response(
        200, json={"version": "1.0.0", "product": "VisionGuide", "registered": True, "device_id": "d1"}))
    respx.get(f"{PI}/api/update/status").mock(return_value=httpx.Response(
        200, json={"bundle_id": "b-1", "has_backup": False}))
    respx.get(f"{PI}/api/identity").mock(return_value=httpx.Response(
        200, json={"registered": True, "device_id": "d1", "server_url": "http://server:8001"}))
    respx.get(f"{PI}/api/outbox").mock(return_value=httpx.Response(
        200, json={"heartbeat": {"last_error": None, "last_sent_at": 1.0}, "events": {}}))
    respx.get(f"{PI}/api/diagnose").mock(return_value=httpx.Response(200, json={
        "server": GOOD_SERVER, "ntp_synchronized": True,
        "power": {"raw": "0x0", "now": {"undervoltage": False, "throttled": False},
                  "ever": {"undervoltage": False, "throttled": False}}}))
    respx.get(f"{PI}/api/metrics").mock(return_value=httpx.Response(200, json={"cameras": [
        {"camera_id": "cam0", "streaming": True, "stale": False}]}))
    respx.get(f"{PI}/api/device/status").mock(return_value=httpx.Response(200, json={
        "uptime_seconds": 1, "cpu_temp_c": 50.0, "load_avg": [0, 0, 0], "mem_used_mb": 1, "mem_total_mb": 2}))


def _diag(client, headers):
    res = client.get("/api/devices/d1/diagnose", headers=headers)
    assert res.status_code == 200
    data = res.json()["data"]
    return data, {c["key"]: c for c in data["checks"]}


@respx.mock
def test_healthy_device_has_only_link_failing_before_first_heartbeat(client, auth_headers, db_session):
    _register(client, auth_headers, db_session)
    _healthy_pi()
    data, c = _diag(client, auth_headers)
    for key in ("reach", "firmware", "identity", "provisioned", "server_from_device",
                "cameras", "power", "temp", "clock"):
        assert c[key]["status"] == "ok", (key, c[key])
    assert c["link"]["status"] == "fail"               # 아직 하트비트를 받은 적이 없다
    assert data["overall"] == "fail"


@respx.mock
def test_unreachable_device(client, auth_headers, db_session):
    _register(client, auth_headers, db_session)
    for path in ("version", "update/status", "identity", "outbox", "diagnose", "metrics", "device/status"):
        respx.get(f"{PI}/api/{path}").mock(side_effect=httpx.ConnectError("down"))
    _, c = _diag(client, auth_headers)
    assert c["reach"]["status"] == "fail" and "같은 Wi-Fi" in c["reach"]["fix"]
    for key in ("firmware", "identity", "server_from_device", "cameras", "power", "temp", "clock"):
        assert c[key]["status"] == "unknown"


@respx.mock
def test_stale_server_address_offers_refresh(client, auth_headers, db_session):
    _register(client, auth_headers, db_session)
    _healthy_pi()
    respx.get(f"{PI}/api/identity").mock(return_value=httpx.Response(
        200, json={"registered": True, "device_id": "d1", "server_url": "http://192.168.0.2:8001"}))
    _, c = _diag(client, auth_headers)
    assert c["identity"]["status"] == "fail" and c["identity"]["action"] == "refresh_address"
    assert "192.168.0.2" in c["identity"]["detail"] and "http://server:8001" in c["identity"]["detail"]


@respx.mock
def test_firewall_symptom_is_tcp_step_from_device(client, auth_headers, db_session):
    """이름은 풀리지만 TCP가 막힘 — Windows/Hyper-V 방화벽이었다."""
    _register(client, auth_headers, db_session)
    _healthy_pi()
    blocked = {**GOOD_SERVER, "tcp": {"ok": False, "detail": "192.168.0.105:8001에 연결하지 못했습니다: timed out"},
               "http": None}
    respx.get(f"{PI}/api/diagnose").mock(return_value=httpx.Response(200, json={
        "server": blocked, "ntp_synchronized": True, "power": None}))
    _, c = _diag(client, auth_headers)
    assert c["server_from_device"]["status"] == "fail"
    assert "방화벽" in c["server_from_device"]["fix"]
    assert c["power"]["status"] == "unknown"


@respx.mock
def test_dns_failure_points_at_local_names(client, auth_headers, db_session):
    _register(client, auth_headers, db_session)
    _healthy_pi()
    respx.get(f"{PI}/api/diagnose").mock(return_value=httpx.Response(200, json={
        "server": {"url": "http://B9.local:8001", "dns": {"ok": False, "detail": "이름을 해석하지 못했습니다"},
                   "tcp": None, "http": None}, "ntp_synchronized": True, "power": None}))
    _, c = _diag(client, auth_headers)
    assert c["server_from_device"]["status"] == "fail" and ".local" in c["server_from_device"]["fix"]


@respx.mock
def test_old_firmware_is_flagged_not_failed(client, auth_headers, db_session):
    _register(client, auth_headers, db_session)
    _healthy_pi()
    respx.get(f"{PI}/api/update/status").mock(return_value=httpx.Response(404, json={"detail": "Not Found"}))
    respx.get(f"{PI}/api/diagnose").mock(return_value=httpx.Response(404, json={"detail": "Not Found"}))
    _, c = _diag(client, auth_headers)
    assert c["firmware"]["status"] == "warn" and "구버전" in c["firmware"]["detail"]
    assert c["server_from_device"]["status"] == "unknown" and c["server_from_device"]["action"] == "update"
    assert c["power"]["status"] == "unknown"


@respx.mock
def test_missing_second_camera_is_the_attention_cause(client, auth_headers, db_session):
    """실제 '주의'의 원인: 설정은 cam0, cam1인데 cam1이 연결돼 있지 않았다."""
    _register(client, auth_headers, db_session, cameras=("cam0", "cam1"))
    _healthy_pi()
    respx.get(f"{PI}/api/metrics").mock(return_value=httpx.Response(200, json={"cameras": [
        {"camera_id": "cam0", "streaming": True, "stale": False},
        {"camera_id": "cam1", "streaming": False, "stale": True}]}))
    _, c = _diag(client, auth_headers)
    assert c["cameras"]["status"] == "warn" and "cam1" in c["cameras"]["detail"]
    assert "cam0" not in c["cameras"]["detail"]
    assert "비활성화" in c["cameras"]["fix"]


@respx.mock
def test_power_distinguishes_now_from_past(client, auth_headers, db_session):
    _register(client, auth_headers, db_session)
    _healthy_pi()

    def power(now, ever):
        respx.get(f"{PI}/api/diagnose").mock(return_value=httpx.Response(200, json={
            "server": GOOD_SERVER, "ntp_synchronized": True, "power": {
                "raw": "0x?", "now": {"undervoltage": now, "throttled": False},
                "ever": {"undervoltage": ever, "throttled": False}}}))
        return _diag(client, auth_headers)[1]["power"]["status"]

    assert power(False, False) == "ok"
    assert power(False, True) == "warn"                # 과거에만 — 지금은 정상
    assert power(True, True) == "fail"


@respx.mock
def test_unprovisioned_device_offers_provision(client, auth_headers, db_session):
    respx.post(f"{PI}/api/identity").mock(return_value=httpx.Response(401, json={"detail": "x"}))
    respx.get(f"{PI}/api/cameras").mock(return_value=httpx.Response(200, json={"cameras": []}))
    client.post("/api/devices", json={"id": "d1", "name": "d1", "ip": "10.0.0.1", "location": "x"},
                headers=auth_headers)
    respx.reset()
    _healthy_pi()
    _, c = _diag(client, auth_headers)
    assert c["provisioned"]["status"] == "fail" and c["provisioned"]["action"] == "provision"


@respx.mock
def test_temperature_thresholds(client, auth_headers, db_session):
    _register(client, auth_headers, db_session)
    _healthy_pi()
    for temp, want in ((59.9, "ok"), (65.0, "warn"), (82.0, "fail")):
        respx.get(f"{PI}/api/device/status").mock(return_value=httpx.Response(200, json={
            "uptime_seconds": 1, "cpu_temp_c": temp, "load_avg": [0, 0, 0], "mem_used_mb": 1, "mem_total_mb": 2}))
        assert _diag(client, auth_headers)[1]["temp"]["status"] == want


def test_diagnose_unknown_device_is_404(client, auth_headers):
    assert client.get("/api/devices/ghost/diagnose", headers=auth_headers).status_code == 404
