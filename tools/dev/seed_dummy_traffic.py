#!/usr/bin/env python3
"""seed_dummy_traffic.py — 유동인구/감지 이벤트 더미 데이터 생성.

Pi 또는 PC에서 실행:
    python seed_dummy_traffic.py
    python seed_dummy_traffic.py --db /path/to/foot_traffic.db --days 30

기존 데이터를 덮어쓰지 않고 INSERT OR REPLACE 방식으로 누적한다.
"""

from __future__ import annotations

import argparse
import math
import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

random.seed(42)


def _hour_traffic(hour: int, is_weekend: bool) -> tuple[int, int]:
    """시간대별 현실적 유동인구 패턴 — 출퇴근 피크, 점심 피크, 야간 감소."""
    base = [
        2, 1, 1, 1, 2, 4,   # 0-5시 (심야)
        8, 18, 30, 22, 16, 20,  # 6-11시 (아침/오전)
        35, 28, 18, 20, 28, 38,  # 12-17시 (점심/오후)
        32, 24, 18, 14, 10, 6,  # 18-23시 (저녁/밤)
    ][hour]
    if is_weekend:
        # 주말은 출퇴근 피크 없이 낮 전반이 고름
        base = int(base * 0.7 + random.uniform(-2, 2))
    else:
        base = int(base + random.uniform(-3, 3))
    base = max(0, base)
    # 지팡이 사용자: 전체의 3~8%
    cane = max(0, round(base * random.uniform(0.03, 0.08)))
    return base, cane


def seed(db_path: Path, days: int = 30) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS foot_traffic_hourly (
            hour_start      TEXT PRIMARY KEY,
            total_count     INTEGER NOT NULL DEFAULT 0,
            cane_user_count INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS detection_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT NOT NULL,
            class_name  TEXT NOT NULL,
            roi_name    TEXT NOT NULL
        )
        """
    )
    conn.commit()

    now = datetime.now()
    roi_names = ["횡단보도 앞", "버스 정류장", "지하철 입구", "건물 출입구"]
    inserted_traffic = 0
    inserted_events = 0

    for day_offset in range(days - 1, -1, -1):
        day = now.date() - timedelta(days=day_offset)
        is_weekend = day.weekday() >= 5
        for hour in range(24):
            total, cane = _hour_traffic(hour, is_weekend)
            hour_start = f"{day.isoformat()}T{hour:02d}:00:00"
            conn.execute(
                "INSERT OR REPLACE INTO foot_traffic_hourly VALUES (?, ?, ?)",
                (hour_start, total, cane),
            )
            inserted_traffic += 1

            # 최근 7일치만 이벤트 생성 (너무 많으면 느림)
            if day_offset < 7 and cane > 0:
                for _ in range(min(cane, 3)):
                    minute = random.randint(0, 59)
                    sec = random.randint(0, 59)
                    ts = f"{day.isoformat()}T{hour:02d}:{minute:02d}:{sec:02d}"
                    roi = random.choice(roi_names)
                    conn.execute(
                        "INSERT INTO detection_events (ts, class_name, roi_name) VALUES (?, ?, ?)",
                        (ts, "white_cane", roi),
                    )
                    inserted_events += 1

    conn.commit()
    conn.close()
    print(f"[seed] {db_path}")
    print(f"  foot_traffic_hourly : {inserted_traffic}행 삽입/교체")
    print(f"  detection_events    : {inserted_events}행 삽입")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="유동인구 더미 데이터 생성")
    parser.add_argument("--db", default="foot_traffic.db", help="SQLite DB 경로 (기본: foot_traffic.db)")
    parser.add_argument("--days", type=int, default=30, help="생성할 기간(일) (기본: 30)")
    args = parser.parse_args()
    seed(Path(args.db), args.days)
