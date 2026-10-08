"""POST /api/bootstrap/enroll — Pi가 토큰 없이 스스로 등록을 요청한다.

핵심 계약 두 가지를 테스트가 지킨다.
1. **서버가 직접 그 Pi를 읽어 판정한다.** 요청 본문의 자기 신고는 쓰지 않는다.
2. **다른 서버 소속 기기는 건드리지 않는다.** `pending`이면 Pi로 신원을 보내지 않는다 — Pi의
   `POST /api/identity`는 키가 틀려도 덮어쓰므로, 한 번 보내면 곧 인수(탈취)다.
"""

import asyncio
import hashlib
import hmac
import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.config import settings
from app.models.device import Device
from app.routers import bootstrap
from app.services import enrollment
from app.services.pending_enrollments import REJECT_TTL_SEC, pending_store

IP = "10.0.0.7"
PI = f"http://{IP}:5000"
OURS = "http://192.168.0.105:8001"          # fixed_server 픽스처의 서버 주소
OTHER = "http://192.168.0.32:8000"          # 다른 서버
KEY = "vg_ourkey"


@pytest.fixture
def auth_headers(client: TestClient, admin_user):
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test_password"}
                        ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", OURS)
    monkeypatch.setattr(settings, "auto_enroll", True)
    monkeypatch.setattr(bootstrap, "peer_ipv4", lambda request: IP)
    pending_store.clear()
    enrollment.reset_throttle()
    bootstrap._tokens.clear()
    yield
    pending_store.clear()
    enrollment.reset_throttle()


def _proof(request, key: str) -> httpx.Response:
    nonce = request.url.params["nonce"]
    mac = hmac.new(key.encode(), nonce.encode(), hashlib.sha256).hexdigest()
    return httpx.Response(200, json={"proof": mac, "device_id": "x"})


def _pi(*, registered=False, device_id=None, server_url="", product="VisionGuide", verify=200):
    """Pi 한 대를 흉내 낸다. `verify`: /api/identity/verify의 상태 코드."""
    respx.get(f"{PI}/api/version").mock(return_value=httpx.Response(200, json={
        "version": "x", "product": product, "registered": registered, "device_id": device_id}))
    ident = ({"registered": True, "device_id": device_id, "server_url": server_url, "name": "n",
              "location": "", "registered_at": "2026-01-01T00:00:00Z"} if registered else {"registered": False})
    respx.get(f"{PI}/api/identity").mock(return_value=httpx.Response(200, json=ident))
    proof_route = respx.get(f"{PI}/api/identity/proof")
    if verify == 200:        # 우리 키를 아는 기기 — 서버가 보낸 nonce로 올바른 HMAC을 돌려준다
        proof_route.mock(side_effect=lambda req: _proof(req, KEY))
    elif verify == 401:      # 다른 키를 가진 기기(다른 서버 소속·가짜) — HMAC이 맞지 않는다
        proof_route.mock(side_effect=lambda req: _proof(req, "vg_somebody_elses_key"))
    else:                    # 404=구버전(proof 없음), 403=신원 없음 …
        proof_route.mock(return_value=httpx.Response(verify, json={"detail": "x"}))
    post = respx.post(f"{PI}/api/identity").mock(return_value=httpx.Response(200, json={"ok": True, "usable": True}))
    respx.get(f"{PI}/api/cameras").mock(return_value=httpx.Response(200, json={"cameras": []}))
    return post


def _enroll(client, **body):
    return client.post("/api/bootstrap/enroll", json={"hostname": "raspberrypi", **body})


def _owned_row(db_session, device_id="pi-10-0-0-7", ip=IP, key=KEY):
    row = Device(id=device_id, name="n", ip=ip, api_key_hash=hashlib.sha256(key.encode()).hexdigest(),
                 control_key=key)
    db_session.add(row)
    db_session.commit()
    return row


# --- 신원 없음 → 즉시 등록 ----------------------------------------------------------

@respx.mock
def test_신원이_없으면_즉시_등록한다(client, db_session):
    post = _pi(registered=False)
    res = _enroll(client)
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["status"] == "enrolled" and data["device_id"] == "pi-10-0-0-7" and data["provisioned"] is True

    row = db_session.query(Device).filter(Device.id == "pi-10-0-0-7").one()
    assert row.ip == IP and row.name == "raspberrypi" and row.control_key
    sent = json.loads(post.calls.last.request.content)
    assert sent["server_url"] == OURS and sent["api_key"] == row.control_key
    assert "x-device-key" not in post.calls.last.request.headers           # 새 기기는 기존 키가 없다
    assert "api_key" not in res.text                                       # 키는 응답에 싣지 않는다


@respx.mock
def test_같은_IP의_기존_행은_재사용하고_중복을_만들지_않는다(client, db_session):
    # 다른 id로 수동 등록해 둔 기기가 신원을 잃은 경우
    _owned_row(db_session, device_id="lobby-pi", key="vg_old")
    _pi(registered=False)
    data = _enroll(client).json()["data"]
    assert data["status"] == "enrolled" and data["device_id"] == "lobby-pi"
    assert db_session.query(Device).filter(Device.ip == IP).count() == 1


# --- 우리 소속 → 주소만 갱신 --------------------------------------------------------

@respx.mock
def test_우리_소속이면_키를_바꾸지_않고_주소만_갱신한다(client, db_session):
    row = _owned_row(db_session)
    hash_before = row.api_key_hash
    post = _pi(registered=True, device_id="pi-10-0-0-7", server_url="http://192.168.0.50:8001")   # 옛 서버 주소

    data = _enroll(client).json()["data"]

    assert data["status"] == "known" and data["address"]["changed"] is True
    sent = json.loads(post.calls.last.request.content)
    assert sent["server_url"] == OURS and sent["api_key"] == KEY                   # 같은 키로 주소만 바뀐다
    assert post.calls.last.request.headers["x-device-key"] == KEY                  # 키가 맞으니 인수가 아니다
    db_session.expire_all()
    assert db_session.query(Device).filter(Device.id == "pi-10-0-0-7").one().api_key_hash == hash_before


@respx.mock
def test_우리_소속이고_주소가_이미_맞으면_아무것도_보내지_않는다(client, db_session):
    _owned_row(db_session)
    post = _pi(registered=True, device_id="pi-10-0-0-7", server_url=OURS)
    assert _enroll(client).json()["data"]["status"] == "known"
    assert post.call_count == 0


@respx.mock
def test_우리_소속_기기의_IP가_바뀌면_행의_IP를_따라간다(client, db_session):
    _owned_row(db_session, ip="10.0.0.99")                 # 옛 IP
    _pi(registered=True, device_id="pi-10-0-0-7", server_url=OURS)
    assert _enroll(client).json()["data"]["status"] == "known"
    db_session.expire_all()
    assert db_session.query(Device).filter(Device.id == "pi-10-0-0-7").one().ip == IP


# --- 다른 서버 소속 → 승인 대기, 기기는 건드리지 않는다 ------------------------------

@respx.mock
def test_다른_서버_소속은_pending이고_기기에_아무것도_보내지_않는다(client, db_session):
    post = _pi(registered=True, device_id="pi-other", server_url=OTHER)
    data = _enroll(client).json()["data"]

    assert data == {"status": "pending", "device_id": "pi-other", "current_server_url": OTHER}
    assert post.call_count == 0                                            # ★ Pi 무변경
    assert db_session.query(Device).count() == 0                           # 행도 만들지 않는다
    [entry] = pending_store.list()
    assert entry.ip == IP and entry.current_server_url == OTHER


@respx.mock
def test_행이_있어도_키가_안_맞으면_pending이고_기기를_건드리지_않는다(client, db_session):
    # 같은 id를 우리도 발급했었지만 지금은 다른 서버가 키를 새로 심은 경우 — 핑퐁 탈취가 되면 안 된다
    _owned_row(db_session)
    post = _pi(registered=True, device_id="pi-10-0-0-7", server_url=OTHER, verify=401)
    assert _enroll(client).json()["data"]["status"] == "pending"
    assert post.call_count == 0


@respx.mock
def test_소속을_증명할_수_없는_구버전_기기는_안전하게_pending(client, db_session):
    _owned_row(db_session)
    post = _pi(registered=True, device_id="pi-10-0-0-7", server_url=OURS, verify=404)   # verify 없는 버전
    assert _enroll(client).json()["data"]["status"] == "pending"
    assert post.call_count == 0


@respx.mock
def test_소속_확인은_키를_보내지_않는_proof로만_한다(client, db_session):
    """신원을 다시 보내 보는 방식은 Pi가 덮어쓰므로 쓰지 않는다."""
    _owned_row(db_session)
    post = _pi(registered=True, device_id="pi-10-0-0-7", server_url=OTHER, verify=401)
    _enroll(client)
    assert post.call_count == 0
    verbs = [(c.request.method, c.request.url.path) for c in respx.calls]
    assert ("GET", "/api/identity/proof") in verbs
    assert ("POST", "/api/identity") not in verbs


@respx.mock
def test_우리_행이_없지만_기기가_이미_우리_서버를_보고_있으면_재등록한다(client, db_session):
    # 서버 DB가 날아간 경우 — 다른 서버의 기기가 아니므로 누구의 것도 빼앗지 않는다
    _pi(registered=True, device_id="pi-10-0-0-7", server_url=OURS + "/")
    data = _enroll(client).json()["data"]
    assert data["status"] == "enrolled"
    assert db_session.query(Device).filter(Device.id == "pi-10-0-0-7").one().control_key


@respx.mock
def test_본문의_자기_신고는_판정에_쓰지_않는다(client, db_session):
    post = _pi(registered=True, device_id="pi-other", server_url=OTHER)
    # 거짓으로 "신원 없음/우리 소속"이라고 주장해도 서버가 직접 읽은 값으로 판정한다
    res = _enroll(client, registered=False, device_id="pi-10-0-0-7", server_url=OURS, status="known")
    assert res.json()["data"]["status"] == "pending"
    assert post.call_count == 0


@respx.mock
def test_pending이_다시_요청하면_last_seen만_갱신한다(client):
    _pi(registered=True, device_id="pi-other", server_url=OTHER)
    _enroll(client)
    first = pending_store.get(IP).last_seen
    enrollment.reset_throttle()
    pending_store._clock = lambda: first + 120            # 2분 뒤
    try:
        _enroll(client)
    finally:
        pending_store._clock = __import__("time").monotonic
    assert len(pending_store.list()) == 1


# --- 승인 / 거절 ------------------------------------------------------------------

@respx.mock
def test_승인하면_기존_인수_경로로_신원을_심는다(client, auth_headers, db_session):
    post = _pi(registered=True, device_id="pi-other", server_url=OTHER)
    _enroll(client)
    assert post.call_count == 0

    res = client.post(f"/api/bootstrap/pending/{IP}/approve", headers=auth_headers)

    assert res.status_code == 200 and res.json()["data"]["provisioned"] is True
    assert post.call_count == 1
    sent = json.loads(post.calls.last.request.content)
    assert sent["server_url"] == OURS and sent["device_id"] == "pi-10-0-0-7"
    assert "x-device-key" not in post.calls.last.request.headers           # 키 없는 덮어쓰기 = 인수(Pi가 로그+부저)
    assert pending_store.list() == []
    assert db_session.query(Device).filter(Device.id == "pi-10-0-0-7").one().control_key


@respx.mock
def test_승인_뒤에는_우리_소속이다(client, auth_headers, db_session):
    _pi(registered=True, device_id="pi-other", server_url=OTHER)
    _enroll(client)
    client.post(f"/api/bootstrap/pending/{IP}/approve", headers=auth_headers)
    # 이제 Pi는 **승인 때 서버가 새로 발급한 키**를 가진다 — 그 키로 증명한다
    issued = db_session.query(Device).filter(Device.id == "pi-10-0-0-7").one().control_key
    _pi(registered=True, device_id="pi-10-0-0-7", server_url=OURS)
    respx.get(f"{PI}/api/identity/proof").mock(side_effect=lambda req: _proof(req, issued))
    enrollment.reset_throttle()
    assert _enroll(client).json()["data"]["status"] == "known"


def test_대기_중이_아니면_승인은_404(client, auth_headers):
    res = client.post(f"/api/bootstrap/pending/{IP}/approve", headers=auth_headers)
    assert res.status_code == 404 and res.json()["error"] == "PENDING_NOT_FOUND"


@respx.mock
def test_승인_실패하면_대기에_남는다(client, auth_headers):
    _pi(registered=True, device_id="pi-other", server_url=OTHER)
    _enroll(client)
    respx.post(f"{PI}/api/identity").mock(return_value=httpx.Response(503, json={"detail": "starting"}))
    res = client.post(f"/api/bootstrap/pending/{IP}/approve", headers=auth_headers)
    assert res.json()["data"]["provisioned"] is False
    assert pending_store.get(IP) is not None                # 다시 시도할 수 있게


@respx.mock
def test_거절하면_1시간_동안_기기를_조회하지도_않는다(client, auth_headers):
    _pi(registered=True, device_id="pi-other", server_url=OTHER)
    _enroll(client)
    res = client.post(f"/api/bootstrap/pending/{IP}/reject", headers=auth_headers)
    assert res.json()["data"]["was_pending"] is True and pending_store.list() == []

    enrollment.reset_throttle()
    calls_before = sum(r.call_count for r in respx.routes)
    assert _enroll(client).json()["data"] == {"status": "rejected"}
    assert sum(r.call_count for r in respx.routes) == calls_before          # Pi를 두드리지 않는다

    base = pending_store._clock()
    pending_store._clock = lambda: base + REJECT_TTL_SEC + 1
    try:
        enrollment.reset_throttle()
        assert _enroll(client).json()["data"]["status"] == "pending"        # 억제가 풀리면 다시 올라온다
    finally:
        pending_store._clock = __import__("time").monotonic


@respx.mock
def test_pending_목록은_관리자만_본다(client, auth_headers):
    _pi(registered=True, device_id="pi-other", server_url=OTHER)
    _enroll(client)
    assert client.get("/api/bootstrap/pending").status_code in (401, 403)
    body = client.get("/api/bootstrap/pending", headers=auth_headers).json()
    assert body["total"] == 1 and body["data"][0]["current_server_url"] == OTHER
    assert body["data"][0]["ip"] == IP


def test_승인_거절은_인증이_필요하다(client):
    assert client.post(f"/api/bootstrap/pending/{IP}/approve").status_code in (401, 403)
    assert client.post(f"/api/bootstrap/pending/{IP}/reject").status_code in (401, 403)


def test_사설_LAN이_아닌_주소는_승인_거절_대상이_아니다(client, auth_headers):
    for action in ("approve", "reject"):
        res = client.post(f"/api/bootstrap/pending/8.8.8.8/{action}", headers=auth_headers)
        assert res.status_code == 400 and res.json()["error"] == "INVALID_ADDRESS"


# --- 거부 조건 ---------------------------------------------------------------------

def test_AUTO_ENROLL이_꺼져_있으면_403(client, monkeypatch):
    monkeypatch.setattr(settings, "auto_enroll", False)
    res = _enroll(client)
    assert res.status_code == 403 and res.json()["error"] == "ENROLL_DISABLED"


def test_사설_LAN이_아닌_요청은_400(client, monkeypatch):
    monkeypatch.setattr(bootstrap, "peer_ipv4", lambda request: None)
    res = _enroll(client)
    assert res.status_code == 400 and res.json()["error"] == "UNTRUSTED_ADDRESS"


@respx.mock
def test_VisionGuide가_아닌_기기는_400(client, db_session):
    _pi(product="SomethingElse")
    res = _enroll(client)
    assert res.status_code == 400 and res.json()["error"] == "NOT_VISIONGUIDE"
    assert db_session.query(Device).count() == 0 and pending_store.list() == []


@respx.mock
def test_기기에_닿지_못하면_오류로_알린다(client):
    respx.get(f"{PI}/api/version").mock(side_effect=httpx.ConnectError("refused"))
    res = _enroll(client)
    assert res.status_code == 503


@respx.mock
def test_같은_IP의_연속_요청은_기기를_다시_두드리지_않는다(client):
    _pi(registered=True, device_id="pi-other", server_url=OTHER)
    first = _enroll(client).json()["data"]
    n = sum(r.call_count for r in respx.routes)
    again = _enroll(client).json()["data"]
    assert again["status"] == first["status"] and again.get("throttled") is True
    assert sum(r.call_count for r in respx.routes) == n                      # 무인증 엔드포인트가 증폭기가 되지 않게


# --- 토큰 없는 내려받기 (authorize) ---------------------------------------------------

@pytest.mark.parametrize("path", ["install.sh", "self_update.py", "bundle.tar.gz", "assets.tar.gz"])
def test_사설_LAN이고_자동등록이_켜져_있으면_토큰_없이_받는다(client, path):
    assert client.get(f"/api/bootstrap/{path}").status_code == 200


@pytest.mark.parametrize("path", ["install.sh", "self_update.py", "bundle.tar.gz", "assets.tar.gz"])
def test_자동등록이_꺼져_있으면_토큰이_필요하다(client, monkeypatch, path):
    monkeypatch.setattr(settings, "auto_enroll", False)
    assert client.get(f"/api/bootstrap/{path}").status_code == 401


@pytest.mark.parametrize("path", ["install.sh", "bundle.tar.gz"])
def test_사설_LAN이_아니면_토큰이_필요하다(client, monkeypatch, path):
    monkeypatch.setattr(bootstrap, "peer_ipv4", lambda request: None)
    assert client.get(f"/api/bootstrap/{path}").status_code == 401


def test_틀린_토큰은_LAN이어도_거부한다(client):
    # 만료된 명령을 붙여 넣은 것을 조용히 통과시키면 원인을 알 수 없다
    assert client.get("/api/bootstrap/install.sh?token=bogus").status_code == 401


def test_유효한_토큰은_그대로_동작한다(client, auth_headers):
    t = client.post("/api/bootstrap/token", headers=auth_headers).json()["data"]["token"]
    assert client.get(f"/api/bootstrap/install.sh?token={t}").status_code == 200


def test_등록은_여전히_토큰이_필요하다(client):
    # 토큰 없는 경로는 enroll뿐이다 — register는 1회용 토큰을 요구한다
    res = client.post("/api/bootstrap/register", json={"token": ""})
    assert res.status_code == 401


# --- 독립 리뷰가 지적한 결함의 재현 -------------------------------------------------------

def _all_requests_text():
    return "\n".join(f"{c.request.method} {c.request.url} {dict(c.request.headers)} {c.request.content!r}"
                     for c in respx.calls)


@respx.mock
def test_증명_전에는_어떤_요청에도_우리_키가_실리지_않는다(client, db_session):
    """가짜 Pi가 우리 기기의 device_id를 주장해도 control_key를 건네지 않는다(코드 푸시·재부팅이 가능한 키)."""
    _owned_row(db_session)
    _pi(registered=True, device_id="pi-10-0-0-7", server_url=OTHER, verify=401)      # 가짜: 증명 실패
    assert _enroll(client).json()["data"]["status"] == "pending"
    assert KEY not in _all_requests_text()
    assert "x-device-key" not in _all_requests_text().lower()


@respx.mock
def test_증명에_실패하면_행의_IP를_옮기지_않는다(client, db_session):
    """가짜 기기가 우리 기기의 id를 주장하는 것만으로 대시보드의 행이 자기 쪽을 가리키게 되면 안 된다."""
    _owned_row(db_session, ip="10.0.0.99")
    post = _pi(registered=True, device_id="pi-10-0-0-7", server_url=OTHER, verify=401)
    _enroll(client)
    db_session.expire_all()
    assert db_session.query(Device).filter(Device.id == "pi-10-0-0-7").one().ip == "10.0.0.99"
    assert post.call_count == 0


@respx.mock
def test_증명이_끝난_뒤에야_키가_실린다(client, db_session):
    """증명(proof)은 키 없이, 그 뒤 주소 갱신에서만 키를 쓴다 — 키를 아는 기기에게만 간다."""
    _owned_row(db_session)
    _pi(registered=True, device_id="pi-10-0-0-7", server_url="http://192.168.0.50:8001", verify=200)
    _enroll(client)
    calls = list(respx.calls)
    proof_i = next(i for i, c in enumerate(calls) if c.request.url.path == "/api/identity/proof")
    post_i = next(i for i, c in enumerate(calls) if c.request.method == "POST" and c.request.url.path == "/api/identity")
    assert proof_i < post_i
    assert KEY not in "".join(f"{dict(c.request.headers)}{c.request.content!r}{c.request.url}" for c in calls[:post_i + 0])


@respx.mock
def test_proof는_nonce가_매번_다르다(client, db_session):
    _owned_row(db_session)
    _pi(registered=True, device_id="pi-10-0-0-7", server_url=OURS, verify=200)
    _enroll(client)
    enrollment.reset_throttle()
    _enroll(client)
    nonces = [c.request.url.params["nonce"] for c in respx.calls if c.request.url.path == "/api/identity/proof"]
    assert len(nonces) == 2 and nonces[0] != nonces[1] and all(len(n) >= 16 for n in nonces)


@respx.mock
def test_응답을_재사용하는_가짜는_통과하지_못한다(client, db_session):
    """고정된 proof를 돌려주는 기기(과거 응답을 녹음해 재생)는 새 nonce에 맞지 않는다."""
    _owned_row(db_session)
    post = _pi(registered=True, device_id="pi-10-0-0-7", server_url=OTHER)
    respx.get(f"{PI}/api/identity/proof").mock(
        return_value=httpx.Response(200, json={"proof": "0" * 64, "device_id": "x"}))
    assert _enroll(client).json()["data"]["status"] == "pending"
    assert post.call_count == 0


@respx.mock
def test_proof_응답이_이상하면_pending이다(client, db_session):
    _owned_row(db_session)
    for body in ([], {"proof": 5}, {"nope": 1}, "text"):
        enrollment.reset_throttle()
        pending_store.clear()
        post = _pi(registered=True, device_id="pi-10-0-0-7", server_url=OURS)
        respx.get(f"{PI}/api/identity/proof").mock(return_value=httpx.Response(200, json=body))
        assert _enroll(client).json()["data"]["status"] == "pending", body
        assert post.call_count == 0


@respx.mock
def test_동시_요청이_와도_행은_하나고_키는_한_번만_돈다(client, db_session):
    """install.sh의 등록과 JoinAgent의 첫 시도가 몇 초 안에 겹친다."""
    post = _pi(registered=False)

    async def both():
        return await asyncio.gather(enrollment.handle_enroll(db_session, IP, "a"),
                                    enrollment.handle_enroll(db_session, IP, "b"))

    r1, r2 = asyncio.run(both())
    assert {r1["status"], r2["status"]} == {"enrolled"}
    assert post.call_count == 1                                   # 두 번째는 직렬화된 뒤 직전 결과를 받는다
    assert db_session.query(Device).filter(Device.ip == IP).count() == 1


@respx.mock
def test_실패한_요청도_10초_동안_기억해_서버가_두드리지_않는다(client):
    route = respx.get(f"{PI}/api/version").mock(side_effect=httpx.ConnectError("refused"))
    assert _enroll(client).status_code == 503
    n = route.call_count
    assert _enroll(client).status_code == 503                      # 같은 오류를 돌려준다
    assert route.call_count == n                                   # 기기를 다시 두드리지 않았다


@respx.mock
def test_이상한_JSON_응답은_500이_아니다(client):
    for ident in ([], "str", 5, {"registered": True, "device_id": 7, "server_url": ["x"]}):
        enrollment.reset_throttle()
        pending_store.clear()
        _pi(registered=False)
        respx.get(f"{PI}/api/identity").mock(return_value=httpx.Response(200, json=ident))
        res = _enroll(client)
        assert res.status_code in (200, 400), (ident, res.status_code, res.text)


@respx.mock
def test_기기가_준_문자열은_길이를_자른다(client):
    _pi(registered=True, device_id="D" * 5000, server_url="http://" + "h" * 5000)
    _enroll(client)
    [entry] = pending_store.list()
    assert len(entry.device_id) <= 200 and len(entry.current_server_url) <= 200


@respx.mock
def test_기존_행은_주입이_실패하면_키를_바꾸지_않는다(client, db_session):
    """그 IP가 다른 기기로 넘어갔거나 기기가 아직 부팅 중일 때 — 멀쩡한 기기의 키가 서버에서 거부되면 안 된다."""
    row = _owned_row(db_session, key=KEY)
    hash_before, ip_before = row.api_key_hash, row.ip
    respx.get(f"{PI}/api/version").mock(return_value=httpx.Response(200, json={
        "version": "x", "product": "VisionGuide", "registered": False, "device_id": None}))
    respx.get(f"{PI}/api/identity").mock(return_value=httpx.Response(200, json={"registered": False}))
    respx.post(f"{PI}/api/identity").mock(return_value=httpx.Response(503, json={"detail": "starting"}))
    respx.get(f"{PI}/api/cameras").mock(return_value=httpx.Response(200, json={"cameras": []}))

    data = _enroll(client).json()["data"]

    assert data["status"] == "failed"
    db_session.expire_all()
    after = db_session.query(Device).filter(Device.id == "pi-10-0-0-7").one()
    assert after.api_key_hash == hash_before and after.control_key == KEY and after.ip == ip_before


@respx.mock
def test_승인_시점에_기기가_바뀌었으면_거부한다(client, auth_headers, db_session):
    """대기 항목은 최대 15분 묵은 것 — 그 사이 같은 IP를 다른 기기가 받았을 수 있다."""
    post = _pi(registered=True, device_id="pi-other", server_url=OTHER)
    _enroll(client)
    assert pending_store.get(IP) is not None

    _pi(registered=True, device_id="some-other-pi", server_url="http://192.168.0.77:8000")   # 이제 다른 기기
    res = client.post(f"/api/bootstrap/pending/{IP}/approve", headers=auth_headers)

    assert res.status_code == 409 and res.json()["error"] == "PENDING_STALE"
    assert post.call_count == 0 and db_session.query(Device).count() == 0
    assert pending_store.get(IP) is None


@respx.mock
def test_승인_시점에_같은_기기면_통과한다(client, auth_headers):
    post = _pi(registered=True, device_id="pi-other", server_url=OTHER)
    _enroll(client)
    assert client.post(f"/api/bootstrap/pending/{IP}/approve", headers=auth_headers).status_code == 200
    assert post.call_count == 1


@respx.mock
def test_승인과_거절은_주소_표기_변형을_정규화한다(client, auth_headers):
    _pi(registered=True, device_id="pi-other", server_url=OTHER)
    _enroll(client)
    res = client.post(f"/api/bootstrap/pending/::ffff:{IP}/reject", headers=auth_headers)
    assert res.status_code == 200 and res.json()["data"]["was_pending"] is True     # 같은 항목을 가리킨다
    assert pending_store.list() == []


def test_기록하는_IP_수에는_상한이_있다():
    enrollment.reset_throttle()
    for i in range(enrollment._MAX_TRACKED + 50):
        enrollment._recent[f"10.1.{i // 250}.{i % 250}"] = (0.0, {"status": "x"})
        enrollment._locks[f"10.1.{i // 250}.{i % 250}"] = asyncio.Lock()
    enrollment._prune(1000.0)
    assert len(enrollment._recent) <= 1 and len(enrollment._locks) <= 1
