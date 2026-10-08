"""device/server_join.py — 참가 에이전트.

지키는 계약
1. **자기 서버와 같으면 아무것도 하지 않는다. 다른 서버에만 요청한다.**
2. **Pi 스스로는 신원을 바꾸지 않는다** — 신원 파일을 읽기만 한다.
3. 같은 서버에는 결과별 간격으로만 다시 묻는다(승인 대기는 서버가 15분 무요청이면 만료하므로 5분마다).
4. 에이전트의 실패가 `roi_editor`를 멈추지 않는다.

시계·탐색·전송을 주입하므로 스레드도 네트워크도 쓰지 않는다.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import server_join as sj
from device_identity import DeviceIdentity, save_identity

OWN = "http://192.168.0.32:8000"
NEW = "http://192.168.0.105:8000"
THIRD = "http://192.168.0.200:8000"


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class Env:
    """에이전트 한 대와 그 주변(신원 파일·시계·탐색 결과·서버 응답)."""

    def __init__(self, tmp_path, *, identity_server=OWN, found=(NEW,), responses=None):
        self.path = tmp_path / "device_identity.json"
        if identity_server is not None:
            save_identity(self.path, DeviceIdentity(device_id="pi-x", api_key="vg_key", server_url=identity_server))
        self.clock = Clock()
        self.found = [{"url": u, "name": "pc"} for u in found]
        self.responses = dict(responses or {})
        self.posts: list[tuple[str, str]] = []
        self.logs: list[str] = []
        self.agent = sj.JoinAgent(
            self.path, discover_fn=lambda: list(self.found), post_fn=self._post,
            hostname_fn=lambda: "raspberrypi", clock=self.clock, log=self.logs.append, start_delay=0)

    def _post(self, url, hostname):
        self.posts.append((url, hostname))
        r = self.responses.get(url, {"status": "pending"})
        if isinstance(r, Exception):
            raise r
        return r(url) if callable(r) else r

    @property
    def urls(self):
        return [u for u, _ in self.posts]

    def text(self):
        return "\n".join(self.logs)


# --- 1. 자기 서버와 같으면 무동작, 다른 서버에만 요청 ---------------------------------------

def test_자기_서버만_응답하면_아무것도_하지_않는다(tmp_path):
    env = Env(tmp_path, identity_server=OWN, found=(OWN,))
    env.agent.step()
    assert env.posts == []


def test_표기가_달라도_같은_서버로_본다(tmp_path):
    env = Env(tmp_path, identity_server="HTTP://192.168.0.32:8000/", found=(OWN,))
    env.agent.step()
    assert env.posts == []


def test_다른_서버에만_요청한다(tmp_path):
    env = Env(tmp_path, found=(OWN, NEW), responses={NEW: {"status": "pending"}})
    env.agent.step()
    assert env.urls == [NEW]                                # 자기 서버(OWN)에는 묻지 않는다
    assert env.posts[0][1] == "raspberrypi"                 # 호스트명을 보낸다


def test_서버를_찾지_못하면_아무것도_하지_않는다(tmp_path):
    env = Env(tmp_path, found=())
    env.agent.step()
    assert env.posts == []


def test_포트만_달라도_다른_서버다(tmp_path):
    env = Env(tmp_path, identity_server="http://192.168.0.32:8000", found=("http://192.168.0.32:8001",))
    env.agent.step()
    assert env.urls == ["http://192.168.0.32:8001"]


# --- 2. 신원을 바꾸지 않는다 ---------------------------------------------------------------

@pytest.mark.parametrize("status", ["enrolled", "known", "pending", "rejected", "failed", "error"])
def test_어떤_응답에도_신원_파일을_바꾸지_않는다(tmp_path, status):
    env = Env(tmp_path, responses={NEW: {"status": status, "device_id": "pi-x"}})
    before = env.path.read_bytes()
    mtime = env.path.stat().st_mtime_ns
    env.agent.step()
    assert env.path.read_bytes() == before and env.path.stat().st_mtime_ns == mtime


def test_신원이_없을_때도_파일을_만들지_않는다(tmp_path):
    env = Env(tmp_path, identity_server=None, responses={NEW: {"status": "enrolled", "device_id": "pi-x"}})
    env.agent.step()
    assert not env.path.exists()          # 신원은 서버가 :5000/api/identity로 심는다


def test_모듈은_신원을_쓰는_함수를_가져오지_않는다():
    import ast
    from pathlib import Path
    tree = ast.parse(Path(sj.__file__).read_text(encoding="utf-8"))
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module == "device_identity"
                for a in n.names}
    assert imported == {"load_identity"}


# --- 신원이 없을 때: 등록될 때까지 차례로 ------------------------------------------------------

def test_신원이_없으면_첫_응답_서버에_등록하고_멈춘다(tmp_path):
    env = Env(tmp_path, identity_server=None, found=(NEW, THIRD),
              responses={NEW: {"status": "enrolled", "device_id": "pi-10"}})
    env.agent.step()
    assert env.urls == [NEW]
    assert "서버에 등록되었습니다" in env.text()


def test_신원이_없고_첫_서버가_pending이면_다음_서버를_시도한다(tmp_path):
    env = Env(tmp_path, identity_server=None, found=(NEW, THIRD),
              responses={NEW: {"status": "pending"}, THIRD: {"status": "enrolled", "device_id": "pi-1"}})
    env.agent.step()
    assert env.urls == [NEW, THIRD]


def test_신원이_있으면_모든_다른_서버에_알린다(tmp_path):
    env = Env(tmp_path, found=(OWN, NEW, THIRD))
    env.agent.step()
    assert env.urls == [NEW, THIRD]


# --- 3. 간격 -----------------------------------------------------------------------

@pytest.mark.parametrize("status,wait", [("known", 1800), ("enrolled", 1800), ("pending", 300),
                                          ("rejected", 3600), ("failed", 300), ("error", 300)])
def test_같은_서버는_상태별_간격으로만_다시_묻는다(tmp_path, status, wait):
    env = Env(tmp_path, responses={NEW: {"status": status}})
    env.agent.step()
    assert len(env.posts) == 1
    env.clock.t += wait - 1
    env.agent.step()
    assert len(env.posts) == 1                          # 아직 이르다
    env.clock.t += 2
    env.agent.step()
    assert len(env.posts) == 2


def test_승인_대기는_서버의_15분_만료보다_짧은_간격으로_다시_알린다(tmp_path):
    from_server = 15 * 60
    assert sj.PENDING_PERIOD_SEC < from_server


def test_pending이_있으면_신원이_있어도_5분_뒤에_다시_점검한다(tmp_path):
    env = Env(tmp_path, responses={NEW: {"status": "pending"}})
    assert env.agent.step() == sj.PENDING_PERIOD_SEC


def test_신원이_있고_할_일이_없으면_10분_뒤(tmp_path):
    env = Env(tmp_path, found=(OWN,))
    assert env.agent.step() == sj.IDENTITY_PERIOD_SEC == 600


def test_신원이_없고_서버가_없으면_30초에서_5분까지_백오프한다(tmp_path):
    env = Env(tmp_path, identity_server=None, found=())
    delays = [env.agent.step() for _ in range(7)]
    assert delays == [30, 60, 120, 240, 300, 300, 300]


def test_등록에_성공하면_백오프를_되돌린다(tmp_path):
    env = Env(tmp_path, identity_server=None, found=(), responses={NEW: {"status": "enrolled"}})
    for _ in range(4):
        env.agent.step()
    env.found = [{"url": NEW}]
    env.agent.step()
    assert env.agent._backoff == sj.NO_IDENTITY_MIN_SEC


# --- 로그 ----------------------------------------------------------------------------

def test_pending이면_승인_대기_중이라고_알리고_계속_동작한다(tmp_path):
    env = Env(tmp_path, responses={NEW: {"status": "pending"}})
    env.agent.step()
    assert "승인 대기 중" in env.text() and NEW in env.text()


def test_같은_메시지를_반복해서_남기지_않는다(tmp_path):
    env = Env(tmp_path, found=())
    for _ in range(5):
        env.agent.step()
    assert env.text().count("서버를 찾지 못했습니다") == 1


def test_여러_서버가_답하면_모두_기록한다(tmp_path):
    env = Env(tmp_path, found=(OWN, NEW, THIRD))
    env.agent.step()
    assert NEW in env.text() and THIRD in env.text() and OWN in env.text()


def test_요청_실패는_기록하고_다음에_다시_시도한다(tmp_path):
    env = Env(tmp_path, responses={NEW: {"status": "error", "error": "HTTP 403: 자동 등록이 꺼져 있습니다"}})
    env.agent.step()
    assert "등록 요청 실패" in env.text() and "자동 등록이 꺼져" in env.text()


# --- 4. 실패가 roi_editor를 멈추지 않는다 ----------------------------------------------------

def test_탐색이_예외를_던져도_step은_죽지_않는다(tmp_path):
    env = Env(tmp_path)
    env.agent._discover = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    env.agent.step()
    assert "서버 탐색 실패" in env.text()


def test_신원_파일이_깨져도_step은_죽지_않는다(tmp_path):
    env = Env(tmp_path, identity_server=None)
    env.path.write_text("{ not json", encoding="utf-8")
    env.agent.step()                                       # load_identity가 None을 주든 예외를 던지든 안전해야 한다


def test_run_루프는_예외가_나도_계속_돈다(tmp_path):
    env = Env(tmp_path)
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        env.agent.stop()                                   # 두 번째 점검에서 멈춘다
        return []

    env.agent._discover = flaky
    env.agent._stop_event.wait = lambda timeout=None: env.agent._stop_event.is_set()   # 대기 없이
    env.agent.run()
    assert len(calls) == 2


def test_step이_밖으로_던진_예외도_run이_삼킨다(tmp_path):
    env = Env(tmp_path)
    n = []

    def bad_step():
        n.append(1)
        if len(n) == 2:
            env.agent.stop()
        raise ValueError("x")

    env.agent.step = bad_step
    env.agent._stop_event.wait = lambda timeout=None: env.agent._stop_event.is_set()
    env.agent.run()
    assert len(n) == 2 and "점검 중 오류" in env.text()


def test_시작_지연_동안_stop하면_점검하지_않는다(tmp_path):
    env = Env(tmp_path)
    env.agent.start_delay = 3600
    env.agent.stop()
    env.agent.run()
    assert env.posts == []


def test_스레드로_시작하고_join할_수_있다(tmp_path):
    # Thread의 내부 메서드 `_stop`을 덮어쓰면 join이 TypeError로 깨진다
    env = Env(tmp_path, found=())
    env.agent.start()
    env.agent.stop()
    env.agent.join(timeout=5)
    assert not env.agent.is_alive()


# --- post_enroll (실제 HTTP) --------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    mode = "ok"
    seen: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        type(self).seen.append((self.path, json.loads(body), self.headers.get("Content-Type")))
        mode = type(self).mode
        if mode == "ok":
            self._send(200, {"data": {"status": "pending", "current_server_url": OWN}, "ok": True})
        elif mode == "403":
            self._send(403, {"error": "ENROLL_DISABLED", "message": "자동 등록이 꺼져 있습니다", "ok": False})
        elif mode == "garbage":
            self._send_raw(200, b"<html>captive portal</html>")
        elif mode == "noenvelope":
            self._send(200, {"hello": "world"})
        else:
            self._send_raw(500, b"oops")

    def _send(self, code, obj):
        self._send_raw(code, json.dumps(obj).encode("utf-8"))

    def _send_raw(self, code, data):
        self.send_response(code)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def http_server():
    _Handler.seen = []
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv, f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    srv.server_close()


def test_post_enroll_정상(http_server):
    _, url = http_server
    _Handler.mode = "ok"
    r = sj.post_enroll(url, "raspberrypi")
    assert r == {"status": "pending", "current_server_url": OWN}
    assert _Handler.seen == [("/api/bootstrap/enroll", {"hostname": "raspberrypi"}, "application/json")]


def test_post_enroll_끝_슬래시를_정리한다(http_server):
    _, url = http_server
    _Handler.mode = "ok"
    assert sj.post_enroll(url + "/", "h")["status"] == "pending"


def test_post_enroll_http_오류는_메시지를_담아_error로(http_server):
    _, url = http_server
    _Handler.mode = "403"
    r = sj.post_enroll(url, "h")
    assert r["status"] == "error" and "403" in r["error"] and "자동 등록이 꺼져" in r["error"]


@pytest.mark.parametrize("mode", ["garbage", "noenvelope", "500"])
def test_post_enroll_이상한_응답은_error(http_server, mode):
    _, url = http_server
    _Handler.mode = mode
    assert sj.post_enroll(url, "h")["status"] == "error"


def test_post_enroll_연결_실패는_error():
    r = sj.post_enroll("http://127.0.0.1:1", "h", timeout=2)
    assert r["status"] == "error" and r["error"]
