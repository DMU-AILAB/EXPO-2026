"""서버 주소 자동 계산과 기기 서버 주소 갱신.

핵심은 **키를 바꾸지 않는다**는 점이다 — `/provision`은 키를 새로 발급하지만 주소 갱신은
저장된 `control_key`로 같은 신원을 다시 보낸다. 키가 바뀌면 서버의 `api_key_hash`와 기기의
키가 갈라져 하트비트가 401이 된다.
"""

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.config import settings
from app.models.device import Device
from app.services import address_sync, server_address


@pytest.fixture
def auth_headers(client: TestClient, admin_user):
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test_password"}
                        ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    monkeypatch.setattr(server_address, "_seen_port", None)
    address_sync._retry.clear()


def test_explicit_url_is_used_as_is(monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", "http://pc.example:9000/")
    assert server_address.public_url_for("10.0.0.5") == "http://pc.example:9000"


def test_auto_uses_ip_toward_device_and_seen_port(monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", "auto")
    monkeypatch.setattr(server_address, "local_ip_toward", lambda ip: "192.168.7.9")
    monkeypatch.setattr(settings, "port", 8000)
    assert server_address.public_url_for("192.168.7.50") == "http://192.168.7.9:8000"
    server_address.remember_port(8001)                 # 실제로 본 포트가 .env의 PORT보다 우선
    assert server_address.public_url_for("192.168.7.50") == "http://192.168.7.9:8001"


def test_auto_without_route_is_empty(monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", "auto")
    monkeypatch.setattr(server_address, "local_ip_toward", lambda ip: None)
    assert server_address.public_url_for("10.0.0.5") == ""


def test_local_ip_toward_loopback_is_real_ip():
    # 실제 소켓: 루프백으로 향하면 127.0.0.1 — 계산 자체가 예외 없이 동작한다.
    assert server_address.local_ip_toward("127.0.0.1") == "127.0.0.1"


def _register(client, headers, device_id="d1", ip="10.0.0.1", pi_identity=None):
    pi = f"http://{ip}:5000"
    respx.post(f"{pi}/api/identity").mock(return_value=httpx.Response(200, json={"ok": True}))
    respx.get(f"{pi}/api/cameras").mock(return_value=httpx.Response(200, json={"cameras": []}))
    res = client.post("/api/devices", json={"id": device_id, "name": device_id, "ip": ip,
                                            "location": "x"}, headers=headers)
    assert res.status_code == 201
    respx.reset()
    return pi, res.json()["data"]["api_key"]


@respx.mock
def test_refresh_pushes_same_key_when_url_differs(client, auth_headers, db_session, monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", "http://new-server:8001")
    pi, key = _register(client, auth_headers)
    stored = db_session.query(Device).filter(Device.id == "d1").one()
    hash_before = stored.api_key_hash
    respx.get(f"{pi}/api/identity").mock(return_value=httpx.Response(200, json={
        "registered": True, "device_id": "d1", "server_url": "http://old-server:8000",
        "name": "d1", "location": "x", "registered_at": "2026-10-07T00:00:00Z"}))
    push = respx.post(f"{pi}/api/identity").mock(return_value=httpx.Response(200, json={"ok": True}))

    res = client.post("/api/devices/d1/refresh-address", headers=auth_headers).json()["data"]

    assert res["ok"] and res["changed"] and res["previous"] == "http://old-server:8000"
    sent = push.calls.last.request
    assert sent.headers["x-device-key"] == key                  # 현재 키로 인증
    body = sent.read().decode()
    assert key in body and "http://new-server:8001" in body     # 같은 키를 다시 심는다
    assert "2026-10-07T00:00:00Z" in body                        # 등록 시각 보존
    db_session.expire_all()
    assert db_session.query(Device).filter(Device.id == "d1").one().api_key_hash == hash_before


@respx.mock
def test_refresh_is_noop_when_url_matches(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", "http://same:8001")
    pi, _ = _register(client, auth_headers)
    respx.get(f"{pi}/api/identity").mock(return_value=httpx.Response(200, json={
        "registered": True, "device_id": "d1", "server_url": "http://same:8001/"}))
    push = respx.post(f"{pi}/api/identity")

    res = client.post("/api/devices/d1/refresh-address", headers=auth_headers).json()["data"]

    assert res["ok"] and not res["changed"]
    assert not push.called


@respx.mock
def test_refresh_skips_unprovisioned_and_foreign_devices(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", "http://same:8001")
    # 신원 주입에 실패한 기기 — 키가 없으니 건드리지 않는다
    pi = "http://10.0.0.2:5000"
    respx.post(f"{pi}/api/identity").mock(return_value=httpx.Response(401, json={"detail": "x"}))
    respx.get(f"{pi}/api/cameras").mock(return_value=httpx.Response(200, json={"cameras": []}))
    client.post("/api/devices", json={"id": "np", "name": "np", "ip": "10.0.0.2", "location": "x"},
                headers=auth_headers)
    res = client.post("/api/devices/np/refresh-address", headers=auth_headers).json()["data"]
    assert res["skipped"] and not res["ok"]

    # 같은 IP를 다른 기기가 쓰게 된 경우 — 우리 키를 엉뚱한 기기에 심으면 안 된다
    pi2, _ = _register(client, auth_headers, device_id="d2", ip="10.0.0.3")
    respx.get(f"{pi2}/api/identity").mock(return_value=httpx.Response(200, json={
        "registered": True, "device_id": "someone-else", "server_url": "http://x"}))
    push = respx.post(f"{pi2}/api/identity")
    res = client.post("/api/devices/d2/refresh-address", headers=auth_headers).json()["data"]
    assert res["skipped"] and "someone-else" in res["error"]
    assert not push.called


@respx.mock
def test_bulk_refresh_continues_after_failure(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", "http://new:8001")
    ok, _ = _register(client, auth_headers, device_id="ok", ip="10.0.0.4")
    bad, _ = _register(client, auth_headers, device_id="bad", ip="10.0.0.5")
    respx.get(f"{ok}/api/identity").mock(return_value=httpx.Response(200, json={
        "registered": True, "device_id": "ok", "server_url": "http://old:8000"}))
    respx.post(f"{ok}/api/identity").mock(return_value=httpx.Response(200, json={"ok": True}))
    respx.get(f"{bad}/api/identity").mock(side_effect=httpx.ConnectError("down"))

    res = client.post("/api/devices/refresh-address", json={}, headers=auth_headers).json()["data"]
    by_id = {r["device_id"]: r for r in res["results"]}
    assert by_id["ok"]["ok"] and by_id["ok"]["changed"]
    assert not by_id["bad"]["ok"] and "연결" in by_id["bad"]["error"]


@pytest.mark.asyncio
async def test_reconcile_skips_healthy_and_backs_off(monkeypatch):
    calls = []

    class Dev:
        id = "d1"
        control_key = "k"

    class Q:
        def filter(self, *a): return self
        def all(self): return [Dev()]

    class DB:
        def query(self, *a): return Q()
        def close(self): pass

    async def fake_refresh(db, device):
        calls.append(device.id)
        return {"ok": False, "changed": False, "error": "down"}

    state = {"hb": None}

    async def fake_status(device_id): return state["hb"]

    monkeypatch.setattr(address_sync, "SessionLocal", lambda: DB())
    monkeypatch.setattr(address_sync, "refresh_device_address", fake_refresh)
    monkeypatch.setattr(address_sync, "get_buffered_status", fake_status)
    monkeypatch.setattr(address_sync, "is_stale", lambda t: False)

    state["hb"] = {"updated_at": "now"}                 # 하트비트가 정상이면 건드리지 않는다
    await address_sync.reconcile_addresses(now=1000.0)
    assert calls == []

    state["hb"] = None                                  # 끊기면 점검한다
    await address_sync.reconcile_addresses(now=1000.0)
    assert calls == ["d1"]
    await address_sync.reconcile_addresses(now=1100.0)  # 실패 직후엔 백오프 중이라 건너뛴다
    assert calls == ["d1"]
    await address_sync.reconcile_addresses(now=1000.0 + address_sync._BASE_RETRY_SEC + 1)
    assert calls == ["d1", "d1"]
