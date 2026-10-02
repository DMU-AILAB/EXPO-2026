"""하트비트가 온 주소로 기기 IP를 따라간다 — 새 Wi-Fi/DHCP로 Pi의 IP가 바뀌어도 서버→Pi 호출이 이어진다."""
import hashlib
from types import SimpleNamespace

import pytest

from app.models.device import Device
from app.routers import devices as devices_router
from app.services.device_address import adopt_peer_ip, peer_ipv4

RAW_KEY = "hb-key"


def _req(host):
    return SimpleNamespace(client=SimpleNamespace(host=host) if host is not None else None)


@pytest.mark.parametrize("host", [
    "192.168.0.77", "10.1.2.3", "172.16.5.9", "172.31.255.1", "169.254.10.20", "::ffff:192.168.0.77",
])
def test_peer_ipv4_accepts_lan_addresses(host):
    assert peer_ipv4(_req(host)) == host.removeprefix("::ffff:")


@pytest.mark.parametrize("host", [
    "127.0.0.1", "::1", "0.0.0.0", "224.0.0.1",          # 루프백·미지정·멀티캐스트
    "8.8.8.8", "203.0.113.5", "172.32.0.1",              # 공인 주소, 172.16/12 밖
    "fe80::1", "2001:db8::1",                            # IPv4로 볼 수 없는 IPv6
    "testclient", "", None,                              # 주소가 아님/없음
])
def test_peer_ipv4_rejects_everything_else(host):
    assert peer_ipv4(_req(host)) is None


def _device(db, dev_id="d1", ip="192.168.0.10"):
    d = Device(id=dev_id, name=dev_id, ip=ip, api_key_hash="x", config_etag="e")
    db.add(d)
    db.commit()
    return d


def test_adopt_updates_ip_when_it_changed(db_session):
    d = _device(db_session)
    assert adopt_peer_ip(db_session, d, "192.168.1.55") is True
    db_session.expire_all()
    assert db_session.get(Device, "d1").ip == "192.168.1.55"


def test_adopt_is_a_noop_for_same_or_missing_address(db_session):
    d = _device(db_session)
    assert adopt_peer_ip(db_session, d, "192.168.0.10") is False
    assert adopt_peer_ip(db_session, d, None) is False
    assert d.ip == "192.168.0.10"


def test_adopt_refuses_an_address_another_device_already_uses(db_session):
    _device(db_session, "old", "192.168.0.50")        # 낡은 항목이 아직 이 주소를 들고 있다
    new = _device(db_session, "new", "192.168.0.11")
    assert adopt_peer_ip(db_session, new, "192.168.0.50") is False
    db_session.expire_all()
    assert db_session.get(Device, "new").ip == "192.168.0.11"
    assert db_session.get(Device, "old").ip == "192.168.0.50"


def _heartbeat_device(db):
    d = Device(id="hb1", name="hb1", ip="192.168.0.10", config_etag="e",
               api_key_hash=hashlib.sha256(RAW_KEY.encode()).hexdigest())
    db.add(d)
    db.commit()
    return d


def test_heartbeat_follows_the_source_address(client, db_session, monkeypatch):
    _heartbeat_device(db_session)
    monkeypatch.setattr(devices_router, "peer_ipv4", lambda request: "192.168.1.77")
    res = client.patch("/api/devices/me/heartbeat", json={"status": "online"},
                       headers={"X-API-Key": RAW_KEY})
    assert res.status_code == 200
    db_session.expire_all()
    assert db_session.get(Device, "hb1").ip == "192.168.1.77"


def test_heartbeat_keeps_ip_when_source_is_not_a_lan_address(client, db_session):
    # TestClient의 출발지는 "testclient"라 신뢰할 수 있는 IPv4가 아니다 → 기존 IP 유지.
    _heartbeat_device(db_session)
    res = client.patch("/api/devices/me/heartbeat", json={"status": "online"},
                       headers={"X-API-Key": RAW_KEY})
    assert res.status_code == 200
    db_session.expire_all()
    assert db_session.get(Device, "hb1").ip == "192.168.0.10"


def test_heartbeat_with_bad_key_does_not_touch_any_ip(client, db_session, monkeypatch):
    _heartbeat_device(db_session)
    monkeypatch.setattr(devices_router, "peer_ipv4", lambda request: "192.168.1.77")
    res = client.patch("/api/devices/me/heartbeat", json={"status": "online"},
                       headers={"X-API-Key": "wrong"})
    assert res.status_code == 401
    db_session.expire_all()
    assert db_session.get(Device, "hb1").ip == "192.168.0.10"
