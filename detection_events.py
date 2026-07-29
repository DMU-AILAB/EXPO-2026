"""detection_events.py — 최근 감지/안내 이벤트 로그 (Pi 로컬 sqlite, 카메라별 파일 분리).

`foot_traffic_counter.py`와 같은 파일(`profile.traffic_db`)에 별도 테이블로 저장한다 —
카메라별로 이미 db 파일이 분리돼 있어(카메라당 1개) 그 원칙을 그대로 재사용한다.
ROI 트리거 시점(오디오 안내가 실제로 나가는 순간)마다 한 줄만 남기며, 최근 N건만
유지하고 오래된 건 바로 정리한다 — 무제한 누적 방지.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Union

_MAX_EVENTS_DEFAULT = 200


def log_event(
    db_path: Union[str, Path],
    ts_iso: str,
    class_name: str,
    roi_name: str,
    max_events: int = _MAX_EVENTS_DEFAULT,
) -> None:
    """이벤트 1건을 기록하고, `max_events`를 넘는 오래된 행은 즉시 삭제한다."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS detection_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                class_name TEXT NOT NULL,
                roi_name TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO detection_events (ts, class_name, roi_name) VALUES (?, ?, ?)",
            (ts_iso, class_name, roi_name),
        )
        conn.execute(
            """
            DELETE FROM detection_events WHERE id NOT IN (
                SELECT id FROM detection_events ORDER BY id DESC LIMIT ?
            )
            """,
            (max_events,),
        )
        conn.commit()
    finally:
        conn.close()


def read_recent_events(db_path: Union[str, Path], limit: int = 8) -> list[dict]:
    """최근 이벤트를 최신순으로 반환한다. db/테이블이 아직 없으면 빈 리스트."""
    try:
        conn = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return []

    try:
        rows = conn.execute(
            "SELECT ts, class_name, roi_name FROM detection_events ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        conn.close()

    return [{"ts": ts, "class_name": cls, "roi_name": roi} for ts, cls, roi in rows]
