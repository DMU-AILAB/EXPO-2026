"""device_metrics.py — 탐지 프로세스의 런타임 지표를 프로세스 사이로 넘긴다.

하트비트가 필요로 하는 값 중 일부는 **탐지 루프에만 있다** — 추론 시간(`npu_ms`),
프레임 처리 시간(`latency_ms`), 카메라가 실제로 스트리밍 중인지 여부. 그런데
하트비트를 보내는 쪽은 `roi_editor`다(탐지 루프에 네트워크와 그 실패 모드를 들이지
않기 위해서 — `event_logger.py` 헤더의 같은 원칙).

그래서 **sqlite 한 줄**로 넘긴다. 새 메커니즘이 아니라 이 프로젝트가 이미 쓰는
패턴이다 — 유동인구·감지 이벤트·오탐지 핫스팟이 전부 같은 db 파일을 두 프로세스가
나눠 쓴다(WAL 모드라 동시 읽기/쓰기가 안전하다).

**매 프레임 쓰지 않는다.** 탐지 루프가 sqlite I/O에 막히면 안 되므로 호출부가
수 초 간격으로만 보고하고, 그 사이 값은 EMA로 눌러 넘긴다(`fp_hotspots`가 트랙당
1회만 쓰는 것과 같은 이유).
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Union

__all__ = ["report", "read_metrics", "REPORT_INTERVAL_SEC"]

# 탐지 루프가 보고하는 간격. 하트비트가 10~30초 주기라 이보다 촘촘할 이유가 없다.
REPORT_INTERVAL_SEC = 5.0

# 이 시간이 지난 보고는 "멎었다"로 본다. 탐지 프로세스가 죽었는데 마지막 값이
# 계속 살아있는 것처럼 보고되면, 하트비트가 서버에 거짓말을 하게 된다.
_STALE_AFTER_SEC = 20.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS device_metrics (
    camera_id  TEXT PRIMARY KEY,
    ts         REAL NOT NULL,
    streaming  INTEGER NOT NULL DEFAULT 0,
    infer_ms   REAL,
    loop_ms    REAL,
    fps        REAL
)
"""


def _connect(db_path: Union[str, Path]) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_SCHEMA)
    conn.commit()
    return conn


def report(db_path: Union[str, Path], camera_id: str, *, streaming: bool,
           infer_ms: float | None = None, loop_ms: float | None = None,
           fps: float | None = None, now: float | None = None) -> None:
    """카메라 한 대의 현재 지표를 갱신한다(카메라당 1행 UPSERT).

    **탐지 루프에서 호출된다 — 절대 던지지 않는다.** sqlite 오류로 안내가 멈추면
    안 된다(`fp_hotspots`·`event_logger`와 같은 원칙).
    """
    try:
        conn = _connect(db_path)
        try:
            conn.execute(
                "INSERT INTO device_metrics (camera_id, ts, streaming, infer_ms,"
                " loop_ms, fps) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(camera_id) DO UPDATE SET ts=excluded.ts,"
                " streaming=excluded.streaming, infer_ms=excluded.infer_ms,"
                " loop_ms=excluded.loop_ms, fps=excluded.fps",
                (camera_id, now if now is not None else time.time(),
                 1 if streaming else 0, infer_ms, loop_ms, fps))
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:                      # noqa: BLE001
        print(f"[WARN] 런타임 지표 기록 실패: {exc}")


def read_metrics(db_path: Union[str, Path], *, stale_after: float = _STALE_AFTER_SEC,
                 now: float | None = None) -> list[dict]:
    """카메라별 최신 지표. 오래된 보고는 `streaming=False`로 내린다.

    탐지 프로세스가 죽었을 때 마지막 값이 계속 "정상"으로 보고되는 것을 막는다.
    """
    t = now if now is not None else time.time()
    try:
        conn = _connect(db_path)
        try:
            rows = conn.execute(
                "SELECT camera_id, ts, streaming, infer_ms, loop_ms, fps"
                " FROM device_metrics ORDER BY camera_id").fetchall()
        finally:
            conn.close()
    except Exception:                             # noqa: BLE001
        return []

    out = []
    for cam, ts, streaming, infer_ms, loop_ms, fps in rows:
        stale = (t - ts) > stale_after
        out.append({
            "camera_id": cam,
            "streaming": bool(streaming) and not stale,
            "stale": stale,
            "age_sec": round(t - ts, 1),
            "infer_ms": None if stale else infer_ms,
            "loop_ms": None if stale else loop_ms,
            "fps": None if stale else fps,
        })
    return out
