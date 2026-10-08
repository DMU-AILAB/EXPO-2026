"""UDP 발견 응답기 — **실제 UDP 소켓**으로 왕복한다.

모의 소켓만으로는 "Pi가 보낸 바이트를 서버가 알아듣는가"를 알 수 없다. 두 파일(`device/server_discovery.py`와
`app/services/discovery_responder.py`)이 각자 프로토콜 상수를 들고 있어서, 한쪽만 고치면 서로 조용히
못 알아듣는다 — 그래서 여기서 **진짜 `discover()`를 진짜 응답기에 붙여** 본다(루프백).
"""

import asyncio
import json
import socket

import pytest

import server_discovery as pi_side            # device/ — backend/tests/conftest.py가 경로에 넣는다
from app.config import settings
from app.services import discovery_responder as responder
from app.services.discovery_responder import REQUEST_PREFIX, build_response, start_responder, stop_responder


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setattr(settings, "auto_enroll", True)
    monkeypatch.setattr(responder, "public_url_for", lambda ip: f"http://192.168.0.105:8000")


def _query(port: int, payload: bytes = REQUEST_PREFIX, timeout: float = 1.0):
    """진짜 UDP 클라이언트 한 번. 응답이 없으면 None."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        s.sendto(payload, ("127.0.0.1", port))
        try:
            data, _ = s.recvfrom(4096)
        except socket.timeout:
            return None
    return json.loads(data.decode("utf-8"))


def _with_responder(scenario, **start_kw):
    """응답기를 루프백 임시 포트에 띄우고 `scenario(port)`(블로킹)를 스레드에서 돌린다."""
    async def main():
        kw = {"host": "127.0.0.1", "port": 0, "lan_check": lambda ip: ip, **start_kw}
        transport = await start_responder(**kw)
        assert transport is not None
        try:
            port = transport.get_extra_info("sockname")[1]
            return await asyncio.get_running_loop().run_in_executor(None, scenario, port)
        finally:
            stop_responder(transport)
    return asyncio.run(main())


# --- 프로토콜 계약 ------------------------------------------------------------------

def test_프로토콜_상수가_Pi_쪽과_같다():
    assert responder.REQUEST_PREFIX == pi_side.REQUEST_PREFIX
    assert responder.SERVICE == pi_side.SERVICE
    assert responder.PROTOCOL_VERSION == pi_side.PROTOCOL_VERSION
    assert settings.discovery_port == pi_side.DISCOVERY_PORT


def test_url_key_규칙이_Pi_쪽과_같다():
    from app.services.enrollment import url_key
    for url in ("http://192.168.0.5:8000", "http://192.168.0.5:8000/", "HTTP://PC.LOCAL", "https://pc.local:443/",
                "192.168.0.5:8000", "", "http://", "http://h:bad"):
        assert url_key(url) == pi_side.url_key(url), url


def test_응답은_Pi가_받아들이는_형식이다():
    parsed = pi_side.parse_response(build_response("192.168.0.50", name="pc"))
    assert parsed == {"url": "http://192.168.0.105:8000", "name": "pc", "v": 1}


# --- 응답기 동작 (진짜 UDP) -----------------------------------------------------------

def test_정상_요청에_서버_주소로_답한다():
    reply = _with_responder(lambda port: _query(port))
    assert reply["service"] == "visionguide" and reply["v"] == 1
    assert reply["url"] == "http://192.168.0.105:8000" and reply["name"]


def test_부가_정보가_붙은_요청도_답한다():
    payload = REQUEST_PREFIX + json.dumps({"v": 1, "hostname": "pi"}).encode()
    assert _with_responder(lambda port: _query(port, payload))["service"] == "visionguide"


def test_진짜_discover가_진짜_응답기를_찾는다():
    """Pi의 discover()가 서버의 응답기를 실제 UDP로 찾는다 — 두 구현이 서로를 알아듣는 유일한 증거."""
    def scenario(port):
        # 로컬 IP를 127.0.0.1로 주면 지향 브로드캐스트가 127.0.0.255가 되어 루프백으로 닿는다.
        return pi_side.discover(timeout=1.0, tries=2, port=port, local_ip_fn=lambda: "127.0.0.1", hostname="pi-test")
    # 운영 응답기처럼 0.0.0.0에 바인딩한다 — 특정 주소에만 바인딩한 소켓은 브로드캐스트 목적지 패킷을 못 받는다.
    found = _with_responder(scenario, host="0.0.0.0")
    assert [s["url"] for s in found] == ["http://192.168.0.105:8000"]
    assert found[0]["name"]


def test_요청자_IP별로_url을_계산한다(monkeypatch):
    monkeypatch.setattr(responder, "public_url_for", lambda ip: f"http://srv-toward-{ip}:8000")
    reply = _with_responder(lambda port: _query(port))
    assert reply["url"] == "http://srv-toward-127.0.0.1:8000"


@pytest.mark.parametrize("payload", [b"", b"HELLO", b"visionguide?", b"VISIONGUIDE", b"X" * 20,
                                     REQUEST_PREFIX + b"x" * 2000])
def test_형식이_다르거나_큰_요청은_무시한다(payload):
    assert _with_responder(lambda port: _query(port, payload, timeout=0.5)) is None


def test_사설_LAN이_아닌_출발지는_무시한다():
    assert _with_responder(lambda port: _query(port, timeout=0.5), lan_check=lambda ip: None) is None


def test_AUTO_ENROLL이_꺼져_있으면_침묵한다(monkeypatch):
    monkeypatch.setattr(settings, "auto_enroll", False)
    assert _with_responder(lambda port: _query(port, timeout=0.5)) is None


def test_보낼_주소를_정할_수_없으면_침묵한다(monkeypatch):
    monkeypatch.setattr(responder, "public_url_for", lambda ip: "")
    assert _with_responder(lambda port: _query(port, timeout=0.5)) is None


def test_한_요청에_한_번만_답한다():
    def scenario(port):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.5)
            s.sendto(REQUEST_PREFIX, ("127.0.0.1", port))
            s.recvfrom(4096)
            try:
                s.recvfrom(4096)
            except socket.timeout:
                return True
        return False
    assert _with_responder(scenario) is True


def test_포트가_이미_쓰이면_경고만_하고_None을_돌려준다(caplog):
    blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    blocker.bind(("127.0.0.1", 0))
    port = blocker.getsockname()[1]

    async def main():
        return await start_responder(port=port, host="127.0.0.1")

    try:
        with caplog.at_level("WARNING"):
            assert asyncio.run(main()) is None              # 예외 없이 — 서버는 계속 뜬다
    finally:
        blocker.close()
    assert any("발견 응답기를 시작하지 못했습니다" in r.message for r in caplog.records)


def test_stop_responder는_None도_받는다():
    stop_responder(None)


def test_기본_포트는_설정을_따른다(monkeypatch):
    monkeypatch.setattr(settings, "discovery_port", 0)

    async def main():
        t = await start_responder(host="127.0.0.1", lan_check=lambda ip: ip)
        try:
            return t.get_extra_info("sockname")[1]
        finally:
            stop_responder(t)
    assert asyncio.run(main()) > 0


# --- 앱 수명주기 --------------------------------------------------------------------

def test_앱_기동은_응답기_포트가_점유돼_있어도_성공한다(monkeypatch):
    """발견은 편의 기능이다 — 포트 충돌이 서버 기동을 막으면 안 된다(같은 PC에 서버 둘이 흔하다)."""
    from fastapi.testclient import TestClient
    from app.main import app

    blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    blocker.bind(("0.0.0.0", 0))
    monkeypatch.setattr(settings, "discovery_port", blocker.getsockname()[1])
    try:
        with TestClient(app) as client:                     # lifespan 실행
            assert client.get("/api/health").status_code == 200
    finally:
        blocker.close()


def test_AUTO_ENROLL이_꺼져_있으면_응답기를_띄우지_않는다(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app

    started = []

    async def fake_start(*a, **k):
        started.append(1)
        return None

    monkeypatch.setattr("app.main.start_responder", fake_start)
    monkeypatch.setattr(settings, "auto_enroll", False)
    with TestClient(app):
        pass
    assert started == []


def test_같은_출발지의_연속_요청은_속도를_제한한다():
    """12바이트 요청에 ~150바이트로 답하므로 출발지를 속인 요청이 반사 증폭이 되지 않게 한다."""
    def scenario(port):
        return [_query(port, timeout=0.4) for _ in range(3)]
    first, second, third = _with_responder(scenario, min_interval=30.0)
    assert first is not None and second is None and third is None


def test_간격이_지나면_다시_답한다():
    def scenario(port):
        return [_query(port, timeout=0.5) for _ in range(3)]
    assert all(r is not None for r in _with_responder(scenario, min_interval=0.0))


def test_출발지가_다르면_각자_답한다():
    from app.services.discovery_responder import _Responder
    now = [0.0]
    r = _Responder(lambda ip: ip, min_interval=1.0, clock=lambda: now[0])
    assert r._rate_limited("10.0.0.1") is False
    assert r._rate_limited("10.0.0.2") is False         # 다른 출발지는 영향이 없다
    assert r._rate_limited("10.0.0.1") is True
    now[0] += 1.5
    assert r._rate_limited("10.0.0.1") is False
