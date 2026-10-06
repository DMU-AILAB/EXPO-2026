"""하트비트 출발 주소로 `device.ip`를 따라가는지.

등록 때 적은 IP가 고정이면 DHCP 재할당·Wi-Fi 변경 뒤 대시보드의 모든 중계가
옛 주소로 나가 503이 된다 — 기기는 하트비트로 살아 있다고 알리는데도.
"""

import hashlib

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models.device import Device

RAW_KEY = "vg_hb_key"


@pytest.fixture
def device(db_session):
    d = Device(id="pi-1", name="pi", ip="192.168.0.89",
               api_key_hash=hashlib.sha256(RAW_KEY.encode()).hexdigest())
    db_session.add(d)
    db_session.commit()
    return d


def _client_from(db_session, host: str) -> TestClient:
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app, client=(host, 40000))


def _beat(c: TestClient):
    return c.patch("/api/devices/me/heartbeat", json={"status": "online"},
                   headers={"X-API-Key": RAW_KEY})


def test_heartbeat_from_new_address_updates_ip(db_session, device):
    c = _client_from(db_session, "192.168.0.114")
    try:
        assert _beat(c).status_code == 200
    finally:
        app.dependency_overrides.clear()
    db_session.refresh(device)
    assert device.ip == "192.168.0.114"


def test_heartbeat_from_same_address_keeps_ip(db_session, device):
    c = _client_from(db_session, "192.168.0.89")
    try:
        assert _beat(c).status_code == 200
    finally:
        app.dependency_overrides.clear()
    db_session.refresh(device)
    assert device.ip == "192.168.0.89"


def test_non_ip_source_is_ignored(client, db_session, device):
    # 기본 TestClient의 출발 주소는 "testclient" — 주소가 아니면 건드리지 않는다.
    assert _beat(client).status_code == 200
    db_session.refresh(device)
    assert device.ip == "192.168.0.89"


def test_bad_key_does_not_touch_ip(db_session, device):
    c = _client_from(db_session, "10.0.0.5")
    try:
        res = c.patch("/api/devices/me/heartbeat", json={"status": "online"},
                      headers={"X-API-Key": "wrong"})
    finally:
        app.dependency_overrides.clear()
    assert res.status_code == 401
    db_session.refresh(device)
    assert device.ip == "192.168.0.89"
