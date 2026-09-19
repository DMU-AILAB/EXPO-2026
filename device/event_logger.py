"""event_logger.py — 감지 이벤트를 로컬에 쌓아두고 서버로 비동기 전송한다.

백엔드 명세가 `POST /api/events/ingest`를 정의하면서 함께 적어둔 공백을 메운다.

> **현재 Pi에는 아웃바운드 HTTP 클라이언트가 없다** (`device/`에 `requests`/`httpx`가
> 없고 `requirements-pi.txt`에도 포함돼 있지 않다). 이 엔드포인트를 쓰려면 Pi 측에
> 전송 모듈과 의존성을 새로 추가해야 한다 — **서버만 구현해서는 동작하지 않는다.**

설계 원칙 3가지
---------------

1. **탐지 루프는 네트워크를 모른다.** `camera_live_pi.py`는 sqlite에 한 줄 쓰고 끝이다
   (`queue_event`). 전송은 별도 프로세스(`roi_editor`)의 백그라운드 스레드가 맡는다.
   안전 기능인 음성 안내가 서버 응답을 기다리는 일이 없어야 한다.
2. **서버가 없어도 정상 동작한다.** 보낼 수 없으면 outbox에 쌓아두고 나중에 보낸다.
   기기 단독 동작은 이 시스템의 기본 전제다(CLAUDE.md "임베디드 headless 운영 흐름").
3. **의존성을 늘리지 않는다.** `urllib.request`(표준 라이브러리)로 보낸다 —
   `requirements-pi.txt`에 `requests`를 추가하면 3대 전부에 설치·관리 부담이 생긴다.

outbox는 `detection_events`·`fp_hotspots`와 같은 db 파일에 별도 테이블로 둔다
(로컬 표시용 `detection_events` 테이블과 분리한 이유: 그쪽은 오래된 행을 자동
정리하는데, 서버가 오래 꺼져 있으면 **아직 못 보낸 이벤트가 정리에 쓸려나간다**).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Union

__all__ = ["queue_event", "pending_count", "EventSender"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS event_outbox (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT    NOT NULL,      -- ISO8601 (UTC, 'Z')
    camera_id  TEXT    NOT NULL DEFAULT '',
    roi_name   TEXT    NOT NULL DEFAULT '',
    class_name TEXT    NOT NULL DEFAULT '',
    confidence REAL,                  -- 가상 지팡이 박스로 발사된 경우 NULL
    event_type TEXT    NOT NULL DEFAULT 'ANNOUNCEMENT',
    attempts   INTEGER NOT NULL DEFAULT 0
)
"""

# 한 번에 보내는 최대 건수. 명세의 rate limit이 분당 600건이라 여유가 크지만,
# 서버가 오래 꺼져 있다 살아났을 때 한꺼번에 쏟아붓지 않도록 나눠 보낸다.
_BATCH = 20
# 이만큼 실패하면 버린다. 영원히 재시도하면 outbox가 무한히 커지고, 그 사이 들어온
# 최신 이벤트까지 같이 밀린다.
_MAX_ATTEMPTS = 10
# outbox 상한. 넘으면 가장 오래된 것부터 버린다 — 오래된 이벤트보다 최신이 더 쓸모 있다.
_MAX_ROWS = 5000


def _connect(db_path: Union[str, Path]) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_SCHEMA)
    conn.commit()
    return conn


def queue_event(db_path: Union[str, Path], ts: str, camera_id: str,
                class_name: str, roi_name: str,
                confidence: float | None = None,
                event_type: str = "ANNOUNCEMENT") -> None:
    """이벤트 한 건을 outbox에 넣는다. **탐지 루프에서 호출된다 — 절대 던지지 않는다.**

    sqlite 오류로 안내가 멈추면 안 된다(`fp_hotspots`와 같은 원칙).
    """
    try:
        conn = _connect(db_path)
        try:
            conn.execute(
                "INSERT INTO event_outbox (ts, camera_id, roi_name, class_name,"
                " confidence, event_type) VALUES (?, ?, ?, ?, ?, ?)",
                (ts, camera_id, roi_name, class_name, confidence, event_type))
            conn.execute(
                "DELETE FROM event_outbox WHERE id NOT IN "
                "(SELECT id FROM event_outbox ORDER BY id DESC LIMIT ?)", (_MAX_ROWS,))
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:                      # noqa: BLE001
        print(f"[WARN] 이벤트 outbox 기록 실패: {exc}")


def pending_count(db_path: Union[str, Path]) -> int:
    """아직 못 보낸 건수 — 상태 표시/진단용."""
    try:
        conn = _connect(db_path)
        try:
            return conn.execute("SELECT COUNT(*) FROM event_outbox").fetchone()[0]
        finally:
            conn.close()
    except Exception:                             # noqa: BLE001
        return 0


class EventSender(threading.Thread):
    """outbox를 주기적으로 비우는 백그라운드 스레드.

    `roi_editor`가 기동할 때 하나 띄운다. **`camera_live_pi`에서 돌리지 않는 이유**는
    탐지 루프가 있는 프로세스에 네트워크 작업과 그 실패 모드를 들이지 않기 위해서다.

    신원(`device_identity.json`)이 없거나 `server_url`이 비어 있으면 **전송을 시도하지
    않고 쌓기만 한다** — 등록 전에도 이벤트를 잃지 않고, 등록되는 순간 밀린 것이
    함께 올라간다.
    """

    def __init__(self, db_path: Union[str, Path], identity_path: Union[str, Path],
                 interval: float = 5.0, timeout: float = 5.0) -> None:
        super().__init__(daemon=True, name="EventSender")
        self.db_path = Path(db_path)
        self.identity_path = Path(identity_path)
        self.interval = interval
        self.timeout = timeout
        self._stop = threading.Event()
        self.last_error: str | None = None
        self.sent_total = 0

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        # 신원은 매번 다시 읽는다 — 서버가 `POST /api/identity`로 언제든 심을 수 있고,
        # 그때 프로세스를 재시작하지 않아도 전송이 시작되어야 한다.
        from device_identity import load_identity

        backoff = self.interval
        while not self._stop.is_set():
            self._stop.wait(backoff)
            if self._stop.is_set():
                break
            ident = load_identity(self.identity_path)
            if ident is None or not ident.is_usable():
                backoff = self.interval
                continue
            try:
                sent = self._flush(ident)
            except Exception as exc:              # noqa: BLE001
                self.last_error = str(exc)
                sent = 0
            # 보낼 게 있었고 성공했으면 바로 이어서, 실패했으면 점점 뜸하게.
            if sent > 0:
                backoff = 0.5
            elif self.last_error:
                backoff = min(backoff * 2, 60.0)
            else:
                backoff = self.interval

    # ------------------------------------------------------------------ #

    def _flush(self, ident) -> int:
        conn = _connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT id, ts, camera_id, roi_name, class_name, confidence,"
                " event_type, attempts FROM event_outbox ORDER BY id LIMIT ?",
                (_BATCH,)).fetchall()
            if not rows:
                self.last_error = None
                return 0

            url = f"{ident.server_url}/api/events/ingest"
            sent = 0
            for rid, ts, cam, roi, cls, conf, etype, attempts in rows:
                payload = {
                    "camera_id": cam, "roi_name": roi, "class_name": cls,
                    "event_type": etype, "timestamp": ts,
                }
                if conf is not None:
                    payload["confidence"] = round(float(conf), 4)
                ok, fatal = self._post(url, ident.api_key, payload)
                if ok:
                    conn.execute("DELETE FROM event_outbox WHERE id=?", (rid,))
                    sent += 1
                    self.sent_total += 1
                elif fatal or attempts + 1 >= _MAX_ATTEMPTS:
                    # 4xx는 다시 보내도 같은 답이 온다(스키마 불일치·인증 실패 등).
                    # 붙들고 있으면 뒤의 정상 이벤트까지 막힌다.
                    conn.execute("DELETE FROM event_outbox WHERE id=?", (rid,))
                    print(f"[WARN] 이벤트 전송 포기(id={rid}): {self.last_error}")
                else:
                    conn.execute(
                        "UPDATE event_outbox SET attempts=attempts+1 WHERE id=?", (rid,))
                    break          # 서버가 죽었으면 나머지도 실패한다 — 다음 주기에
            conn.commit()
            return sent
        finally:
            conn.close()

    def _post(self, url: str, api_key: str, payload: dict) -> tuple[bool, bool]:
        """(성공 여부, 재시도 무의미 여부)."""
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Content-Type": "application/json", "X-API-Key": api_key,
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                self.last_error = None
                return 200 <= resp.status < 300, False
        except urllib.error.HTTPError as exc:
            self.last_error = f"HTTP {exc.code}"
            # 429(rate limit)는 잠시 뒤 다시 보내면 되지만, 나머지 4xx는 요청 자체가
            # 잘못된 것이라 재시도가 무의미하다.
            return False, 400 <= exc.code < 500 and exc.code != 429
        except Exception as exc:                  # noqa: BLE001  (URLError·소켓 오류 등)
            self.last_error = str(exc)
            return False, False
