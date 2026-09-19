"""event_logger 단위 테스트 — outbox 적재와 비동기 전송.

핵심 계약 둘: **탐지 루프에서 호출해도 절대 던지지 않는다**, 그리고 **서버가 없어도
이벤트를 잃지 않는다**.
"""
import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from device_identity import DeviceIdentity, save_identity
from event_logger import EventSender, pending_count, queue_event


def _q(db, n=1, **kw):
    for i in range(n):
        queue_event(db, f"2026-09-19T12:00:{i:02d}Z", kw.get("cam", "cam0"),
                    "white_cane", kw.get("roi", "복도"), kw.get("conf", 0.87))


# --------------------------------------------------------------------- #
# 적재
# --------------------------------------------------------------------- #

def test_queue_and_count(tmp_path):
    db = tmp_path / "t.db"
    _q(db, 3)
    assert pending_count(db) == 3


def test_queue_never_raises_on_bad_path(tmp_path):
    """탐지 루프에서 호출된다 — sqlite 오류로 안내가 멈추면 안 된다."""
    queue_event(tmp_path / "없는디렉터리" / "x.db", "t", "c", "white_cane", "r")


def test_pending_count_on_missing_db(tmp_path):
    assert pending_count(tmp_path / "없음.db") == 0


def test_outbox_is_capped(tmp_path, monkeypatch):
    """서버가 오래 꺼져 있어도 db가 무한히 커지면 안 된다."""
    import event_logger
    monkeypatch.setattr(event_logger, "_MAX_ROWS", 5)
    _q(tmp_path / "t.db", 12)
    assert pending_count(tmp_path / "t.db") == 5


# --------------------------------------------------------------------- #
# 전송 — 진짜 HTTP 서버를 세워 확인한다
# --------------------------------------------------------------------- #

class _Handler(BaseHTTPRequestHandler):
    received: list = []
    status = 201

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        _Handler.received.append((self.headers.get("X-API-Key"), self.path, body))
        self.send_response(_Handler.status)
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *a):        # 테스트 출력을 더럽히지 않는다
        pass


@pytest.fixture
def server():
    _Handler.received = []
    _Handler.status = 201
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _identity(tmp_path, url):
    p = tmp_path / "device_identity.json"
    save_identity(p, DeviceIdentity(device_id="cam-hall-01", api_key="K1",
                                    server_url=url))
    return p


def _drain(sender, db, tries=60):
    for _ in range(tries):
        if pending_count(db) == 0:
            return True
        threading.Event().wait(0.1)
    return False


def test_events_reach_the_server_with_api_key(tmp_path, server):
    httpd, url = server
    db = tmp_path / "t.db"
    _q(db, 2)
    s = EventSender(db, _identity(tmp_path, url), interval=0.05)
    s.start()
    assert _drain(s, db), "outbox가 비워지지 않았다"
    s.stop()

    keys = {k for k, _p, _b in _Handler.received}
    paths = {p for _k, p, _b in _Handler.received}
    assert keys == {"K1"}
    assert paths == {"/api/events/ingest"}
    body = _Handler.received[0][2]
    assert body["camera_id"] == "cam0"
    assert body["class_name"] == "white_cane"
    assert body["event_type"] == "ANNOUNCEMENT"
    assert body["confidence"] == pytest.approx(0.87)


def test_nothing_is_sent_without_identity(tmp_path, server):
    """등록 전에는 쌓기만 한다 — 이벤트를 잃지 않고, 등록되면 밀린 것이 올라간다."""
    db = tmp_path / "t.db"
    _q(db, 2)
    s = EventSender(db, tmp_path / "없는신원.json", interval=0.05)
    s.start()
    threading.Event().wait(0.4)
    s.stop()
    assert pending_count(db) == 2
    assert _Handler.received == []


def test_events_survive_a_dead_server_and_go_later(tmp_path, server):
    httpd, url = server
    db = tmp_path / "t.db"
    _q(db, 1)
    ident = _identity(tmp_path, "http://127.0.0.1:1")     # 아무도 없는 포트
    s = EventSender(db, ident, interval=0.05, timeout=0.3)
    s.start()
    threading.Event().wait(0.6)
    assert pending_count(db) == 1, "서버가 없으면 보관해야 한다"

    save_identity(ident, DeviceIdentity(device_id="d", api_key="K1", server_url=url))
    assert _drain(s, db), "서버가 살아난 뒤에도 보내지 않았다"
    s.stop()


def test_4xx_is_dropped_instead_of_blocking_the_queue(tmp_path, server):
    """스키마 불일치 같은 4xx는 다시 보내도 같은 답이 온다 — 붙들면 뒤가 막힌다."""
    httpd, url = server
    _Handler.status = 400
    db = tmp_path / "t.db"
    _q(db, 1)
    s = EventSender(db, _identity(tmp_path, url), interval=0.05)
    s.start()
    assert _drain(s, db), "4xx 이벤트가 큐를 막았다"
    s.stop()
