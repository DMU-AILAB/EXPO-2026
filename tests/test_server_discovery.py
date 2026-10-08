"""device/server_discovery.py — UDP 브로드캐스트로 서버 찾기.

소켓과 시계를 주입해 결정적으로 돌린다. 실제 UDP 왕복은 서버 쪽 테스트
(`dashboard/backend/tests/test_discovery.py`)가 **진짜 응답기**와 맞대어 본다.
"""

import json
import socket

import pytest

import server_discovery as sd

SERVER_A = {"v": 1, "service": "visionguide", "url": "http://192.168.0.105:8000", "name": "pc-a"}
SERVER_B = {"v": 1, "service": "visionguide", "url": "http://192.168.0.32:8000", "name": "pc-b"}


def _bytes(obj) -> bytes:
    return json.dumps(obj).encode("utf-8")


# --- parse_response -------------------------------------------------------------

def test_정상_응답을_읽는다():
    assert sd.parse_response(_bytes(SERVER_A)) == {"url": "http://192.168.0.105:8000", "name": "pc-a", "v": 1}


def test_끝_슬래시는_정리한다():
    assert sd.parse_response(_bytes({**SERVER_A, "url": "http://10.0.0.5:8000/"}))["url"] == "http://10.0.0.5:8000"


@pytest.mark.parametrize("data", [
    b"", b"not json", b"\xff\xfe", b"[]", b"null", b'"str"',
    _bytes({**SERVER_A, "service": "other"}),                       # 다른 서비스
    _bytes({k: v for k, v in SERVER_A.items() if k != "service"}),
    _bytes({**SERVER_A, "url": ""}),
    _bytes({**SERVER_A, "url": None}),
    _bytes({**SERVER_A, "url": 123}),
    _bytes({**SERVER_A, "url": "ftp://192.168.0.5:21"}),            # http(s)만
    _bytes({**SERVER_A, "url": "192.168.0.5:8000"}),                # 스킴 없음
    _bytes({**SERVER_A, "url": "http://"}),
    _bytes({**SERVER_A, "url": "http://host with space:8000"}),
    _bytes({**SERVER_A, "url": "http://192.168.0.5:99999"}),        # 포트 범위 초과
    _bytes({**SERVER_A, "url": "http://192.168.0.5:abc"}),
    _bytes({**SERVER_A, "url": "http://" + "a" * 300 + ":8000"}),   # 너무 김
    b"x" * 5000,
])
def test_잘못된_응답은_버린다(data):
    assert sd.parse_response(data) is None


def test_name이_문자열이_아니면_빈_값():
    assert sd.parse_response(_bytes({**SERVER_A, "name": 5}))["name"] == ""


# --- url_key · broadcast_targets ------------------------------------------------------

@pytest.mark.parametrize("a,b", [
    ("http://192.168.0.5:8000", "http://192.168.0.5:8000/"),
    ("http://192.168.0.5:8000", "HTTP://192.168.0.5:8000"),
    ("http://pc.local:80", "http://pc.local"),
    ("https://pc.local", "https://pc.local:443/"),
    ("192.168.0.5:8000", "http://192.168.0.5:8000"),
])
def test_url_key는_표기_차이를_무시한다(a, b):
    assert sd.url_key(a) == sd.url_key(b) != ""


@pytest.mark.parametrize("a,b", [
    ("http://192.168.0.5:8000", "http://192.168.0.5:8001"),
    ("http://192.168.0.5:8000", "http://192.168.0.6:8000"),
])
def test_url_key는_호스트와_포트가_다르면_다르다(a, b):
    assert sd.url_key(a) != sd.url_key(b)


@pytest.mark.parametrize("bad", [None, "", "http://", "http://host:notaport"])
def test_url_key_빈_값_안전(bad):
    assert sd.url_key(bad) == ""


def test_브로드캐스트는_전역과_로컬_24를_함께_쓴다():
    assert sd.broadcast_targets("192.168.0.89") == ["255.255.255.255", "192.168.0.255"]


def test_로컬_주소를_모르면_전역만():
    assert sd.broadcast_targets(None) == ["255.255.255.255"]
    assert sd.broadcast_targets("garbage") == ["255.255.255.255"]


# --- discover (가짜 소켓) -----------------------------------------------------------

class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class FakeSocket:
    """한 번의 시도(라운드) = 첫 `sendto`에서 시작해 `recvfrom`의 타임아웃에서 끝난다.

    `rounds[i]`는 i번째 라운드 동안 받을 데이터 목록이다. 타임아웃이 나면 `settimeout`한 만큼
    시계가 흐른다(그래서 `discover`의 마감 계산이 실제처럼 돈다).
    """

    def __init__(self, clock, rounds, *, fail_sends=(), bind_error=False):
        self.clock = clock
        self.rounds = [list(r) for r in rounds]
        self.fail_sends = set(fail_sends)
        self.bind_error = bind_error
        self.sent: list[tuple[bytes, tuple]] = []
        self.opts: list[tuple] = []
        self.closed = False
        self._round = -1
        self._inbox: list[bytes] = []
        self._in_round = False
        self._timeout = 0.0

    def setsockopt(self, *args):
        self.opts.append(args)

    def bind(self, addr):
        if self.bind_error:
            raise OSError("bind")

    def sendto(self, data, addr):
        if addr[0] in self.fail_sends:
            raise OSError("unreachable")
        if not self._in_round:
            self._round += 1
            self._inbox = list(self.rounds[self._round]) if self._round < len(self.rounds) else []
            self._in_round = True
        self.sent.append((data, addr))

    def settimeout(self, t):
        self._timeout = t

    def recvfrom(self, n):
        if self._inbox:
            return self._inbox.pop(0), ("192.168.0.105", 48555)
        self.clock.t += self._timeout                # 기다린 만큼 시간이 흐른다
        self._in_round = False                       # 라운드 끝 — 다음 sendto가 새 라운드를 연다
        raise socket.timeout()

    def close(self):
        self.closed = True


def _run(rounds, *, tries=3, timeout=2.0, local_ip="192.168.0.89", **kw):
    clock = Clock()
    made = []

    def factory(*args, **kwargs):
        made.append(FakeSocket(clock, rounds, **kw))
        return made[0]

    found = sd.discover(timeout=timeout, tries=tries, sock_factory=factory,
                        local_ip_fn=lambda: local_ip, clock=clock, hostname="pi-test")
    return found, made[0], clock


def test_서버_둘이_답하면_둘_다_돌려준다():
    found, sock, _ = _run([[_bytes(SERVER_A), _bytes(SERVER_B)]])
    assert [s["url"] for s in found] == ["http://192.168.0.105:8000", "http://192.168.0.32:8000"]
    assert sock.closed


def test_응답이_없으면_빈_목록이고_tries번_시도한다():
    found, sock, clock = _run([[], [], []], tries=3, timeout=2.0)
    assert found == []
    assert len(sock.sent) == 3 * 2                                   # 3번 × (전역 + 로컬 24)
    assert clock.t == pytest.approx(6.0)                             # 시도마다 timeout만큼 기다린다


def test_응답이_모이면_다음_시도를_하지_않는다():
    found, sock, _ = _run([[_bytes(SERVER_A)], [_bytes(SERVER_B)]], tries=3)
    assert [s["url"] for s in found] == ["http://192.168.0.105:8000"]
    assert len(sock.sent) == 2                                       # 한 번 시도(두 방향)로 끝


def test_첫_시도가_유실되면_재시도로_찾는다():
    found, sock, _ = _run([[], [_bytes(SERVER_A)]], tries=3)
    assert [s["url"] for s in found] == ["http://192.168.0.105:8000"]
    assert len(sock.sent) == 4


def test_같은_서버의_중복_응답은_하나로():
    found, _, _ = _run([[_bytes(SERVER_A), _bytes({**SERVER_A, "url": "http://192.168.0.105:8000/"})]])
    assert len(found) == 1


def test_깨진_응답은_건너뛰고_정상_응답은_받는다():
    found, _, _ = _run([[b"garbage", _bytes({**SERVER_A, "service": "x"}), _bytes(SERVER_B)]])
    assert [s["url"] for s in found] == ["http://192.168.0.32:8000"]


def test_요청_형식과_송신_대상():
    _, sock, _ = _run([[_bytes(SERVER_A)]])
    payloads = {d for d, _ in sock.sent}
    targets = [a for _, a in sock.sent]
    assert targets == [("255.255.255.255", 48555), ("192.168.0.255", 48555)]
    [payload] = payloads
    assert payload.startswith(b"VISIONGUIDE?")
    assert json.loads(payload[len(b"VISIONGUIDE?"):]) == {"v": 1, "hostname": "pi-test"}
    assert (socket.SOL_SOCKET, socket.SO_BROADCAST, 1) in sock.opts


def test_한쪽_방향_전송이_실패해도_다른_쪽으로_계속한다():
    found, sock, _ = _run([[_bytes(SERVER_A)]], fail_sends={"255.255.255.255"})
    assert len(found) == 1
    assert [a[0] for _, a in sock.sent] == ["192.168.0.255"]


def test_모든_전송이_실패하면_빈_목록():
    found, _, _ = _run([[]], fail_sends={"255.255.255.255", "192.168.0.255"}, tries=2)
    assert found == []


def test_바인딩에_실패하면_빈_목록이고_소켓을_닫는다():
    found, sock, _ = _run([[]], bind_error=True)
    assert found == [] and sock.closed


def test_소켓을_만들_수_없으면_빈_목록():
    def boom(*a, **k):
        raise OSError("no socket")
    assert sd.discover(sock_factory=boom, local_ip_fn=lambda: None) == []


def test_tries가_0이어도_한_번은_시도한다():
    found, sock, _ = _run([[_bytes(SERVER_A)]], tries=0)
    assert len(found) == 1


# --- 프로토콜 상수 ---------------------------------------------------------------

def test_프로토콜_상수():
    assert sd.DISCOVERY_PORT == 48555
    assert sd.REQUEST_PREFIX == b"VISIONGUIDE?"
    assert sd.SERVICE == "visionguide" and sd.PROTOCOL_VERSION == 1


def test_표준_라이브러리만_쓴다():
    import ast
    import sys
    from pathlib import Path
    tree = ast.parse((Path(sd.__file__)).read_text(encoding="utf-8"))
    mods = {n.names[0].name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)} | \
           {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    assert mods <= stdlib, mods - stdlib
