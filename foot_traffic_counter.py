"""foot_traffic_counter.py — 트래킹 기반 유동인구 카운터 (Pi 로컬 sqlite 저장).

매 프레임 `SimpleTracker.update()`가 반환한 사람 트랙과 `cane_person_assoc`의
연관 판정 결과를 받아, 같은 사람이 여러 프레임에 걸쳐 중복 카운트되지
않도록 track_id 단위로 고유 인원수를 센다.

트랙 소멸 감지: `SimpleTracker`는 `max_age` 동안 미탐지 트랙을 coasting으로
살려두므로(camera_live_pi.py의 SimpleTracker), 매 프레임 반환되는
person track_id 집합의 "직전 프레임 대비 diff"만으로 진짜 소멸 시점을
정확히 잡아낼 수 있다 — coasting 중엔 여전히 집합에 포함되기 때문이다.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Union


@dataclass
class _TrackStat:
    first_seen: float
    last_seen: float = 0.0
    frames_total: int = 0
    frames_with_cane: int = 0


class FootTrafficCounter:
    def __init__(
        self,
        db_path: Union[str, Path],
        cane_ratio_threshold: float = 0.3,
        min_track_frames: int = 5,
        commit_interval_sec: float = 30.0,
    ) -> None:
        self.cane_ratio_threshold = cane_ratio_threshold
        self.min_track_frames = min_track_frames
        self.commit_interval_sec = commit_interval_sec

        self._stats: dict[int, _TrackStat] = {}
        self._active_ids: set[int] = set()
        self._pending: dict[str, dict[str, int]] = {}
        self._last_commit = time.time()

        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS foot_traffic_hourly (
                hour_start      TEXT PRIMARY KEY,
                total_count     INTEGER NOT NULL DEFAULT 0,
                cane_user_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self._conn.commit()

    def update(
        self,
        person_tracks: list[dict],
        cane_person_map: dict[int, bool],
        now: float,
    ) -> None:
        current_ids: set[int] = set()
        for pt in person_tracks:
            tid = pt["track_id"]
            current_ids.add(tid)
            st = self._stats.setdefault(tid, _TrackStat(first_seen=now))
            st.last_seen = now
            st.frames_total += 1
            if cane_person_map.get(tid, False):
                st.frames_with_cane += 1

        died = self._active_ids - current_ids
        for tid in died:
            self._finalize(tid)
        self._active_ids = current_ids

        if now - self._last_commit >= self.commit_interval_sec:
            self._flush()
            self._last_commit = now

    def finalize_all(self) -> None:
        """아직 활성 상태인 모든 트랙을 강제로 마감 처리한다 (종료 시 사용)."""
        for tid in list(self._active_ids):
            self._finalize(tid)
        self._active_ids.clear()

    def close(self) -> None:
        self.finalize_all()
        self._flush()
        self._conn.close()

    # ------------------------------------------------------------------ #

    def _finalize(self, track_id: int) -> None:
        st = self._stats.pop(track_id, None)
        if st is None or st.frames_total < self.min_track_frames:
            return  # 찰나의 오탐 — 노이즈로 간주하고 버림

        hour_start = datetime.fromtimestamp(st.last_seen).strftime("%Y-%m-%dT%H:00:00")
        bucket = self._pending.setdefault(hour_start, {"total": 0, "cane": 0})
        bucket["total"] += 1
        if st.frames_with_cane / st.frames_total >= self.cane_ratio_threshold:
            bucket["cane"] += 1

    def _flush(self) -> None:
        if not self._pending:
            return
        self._conn.executemany(
            """
            INSERT INTO foot_traffic_hourly (hour_start, total_count, cane_user_count)
            VALUES (?, ?, ?)
            ON CONFLICT(hour_start) DO UPDATE SET
                total_count = total_count + excluded.total_count,
                cane_user_count = cane_user_count + excluded.cane_user_count
            """,
            [
                (hour_start, bucket["total"], bucket["cane"])
                for hour_start, bucket in self._pending.items()
            ],
        )
        self._conn.commit()
        self._pending.clear()
