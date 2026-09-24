"""foot_traffic_counter.py 단위 테스트 — 시간대별/기간별 조회 함수의 0-채움 동작 검증."""
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path


from foot_traffic_counter import read_hourly_breakdown, read_range_daily_totals


def _make_db(tmp_path, rows):
    """rows: [(hour_start, total_count, cane_user_count), ...]"""
    db_path = tmp_path / "foot_traffic.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE foot_traffic_hourly ("
        "hour_start TEXT PRIMARY KEY, total_count INTEGER, cane_user_count INTEGER)"
    )
    conn.executemany(
        "INSERT INTO foot_traffic_hourly VALUES (?, ?, ?)", rows
    )
    conn.commit()
    conn.close()
    return db_path


def test_read_hourly_breakdown_returns_24_zero_filled_items(tmp_path):
    today = datetime.now().strftime("%Y-%m-%d")
    db_path = _make_db(tmp_path, [
        (f"{today}T09:00:00", 12, 3),
        (f"{today}T14:00:00", 5, 1),
    ])
    result = read_hourly_breakdown(db_path, date=today)
    assert len(result) == 24
    assert [r["hour"] for r in result] == list(range(24))
    by_hour = {r["hour"]: r for r in result}
    assert by_hour[9] == {"hour": 9, "total_count": 12, "cane_user_count": 3}
    assert by_hour[14] == {"hour": 14, "total_count": 5, "cane_user_count": 1}
    assert by_hour[0] == {"hour": 0, "total_count": 0, "cane_user_count": 0}


def test_read_hourly_breakdown_ignores_other_dates(tmp_path):
    db_path = _make_db(tmp_path, [("2020-01-01T09:00:00", 99, 99)])
    result = read_hourly_breakdown(db_path, date="2026-07-29")
    assert all(r["total_count"] == 0 and r["cane_user_count"] == 0 for r in result)


def test_read_hourly_breakdown_missing_db_returns_all_zero(tmp_path):
    result = read_hourly_breakdown(tmp_path / "nonexistent.db", date="2026-07-29")
    assert len(result) == 24
    assert all(r["total_count"] == 0 for r in result)


def test_read_range_daily_totals_returns_ascending_zero_filled(tmp_path):
    today = datetime.now().date()
    yesterday = today - timedelta(days=1)
    db_path = _make_db(tmp_path, [
        (f"{today.isoformat()}T09:00:00", 10, 2),
        (f"{yesterday.isoformat()}T18:00:00", 4, 1),
    ])
    result = read_range_daily_totals(db_path, days=7)
    assert len(result) == 7
    dates = [r["date"] for r in result]
    assert dates == sorted(dates)
    assert dates[-1] == today.isoformat()
    by_date = {r["date"]: r for r in result}
    assert by_date[today.isoformat()]["total_count"] == 10
    assert by_date[yesterday.isoformat()]["total_count"] == 4
    older = today - timedelta(days=5)
    assert by_date[older.isoformat()]["total_count"] == 0


def test_read_range_daily_totals_missing_db_returns_all_zero(tmp_path):
    result = read_range_daily_totals(tmp_path / "nonexistent.db", days=7)
    assert len(result) == 7
    assert all(r["total_count"] == 0 for r in result)


def test_read_range_daily_totals_excludes_dates_before_range(tmp_path):
    today = datetime.now().date()
    too_old = today - timedelta(days=10)
    db_path = _make_db(tmp_path, [(f"{too_old.isoformat()}T09:00:00", 999, 999)])
    result = read_range_daily_totals(db_path, days=7)
    assert all(r["total_count"] == 0 for r in result)


# --------------------------------------------------------------------- #
# 지팡이 사용자 판정 — 비율(레거시) vs 엔티티 래치
# --------------------------------------------------------------------- #

def _run_counter(tmp_path, frames_total, cane_frames, use_latch):
    """사람 트랙 1개를 frames_total 프레임 동안 돌리고, 마지막 cane_frames 프레임에만
    지팡이를 동반시킨 뒤 마감한다. 트랙이 길수록 비율이 희석되는 상황 그대로다."""
    from foot_traffic_counter import FootTrafficCounter

    counter = FootTrafficCounter(tmp_path / "ft.db", commit_interval_sec=1e9)
    track = {"track_id": 1, "class": 1, "bbox": [0, 0, 10, 10]}
    for i in range(frames_total):
        has_cane = i >= frames_total - cane_frames
        counter.update([track], {1: has_cane}, now=1000.0 + i,
                       cane_user_ids=({1} if (use_latch and has_cane) else set())
                       if use_latch else None)
    counter.close()

    conn = sqlite3.connect(str(tmp_path / "ft.db"))
    row = conn.execute(
        "SELECT SUM(total_count), SUM(cane_user_count) FROM foot_traffic_hourly"
    ).fetchone()
    conn.close()
    return row


def test_ratio_path_misses_a_cane_user_whose_track_is_long(tmp_path):
    """레거시 비율 판정의 구조적 결함 — 트래킹이 좋아질수록(트랙이 길수록) 불리해진다."""
    total, cane = _run_counter(tmp_path, frames_total=100, cane_frames=10,
                               use_latch=False)
    assert (total, cane) == (1, 0)      # 10% < cane_ratio_threshold 0.3


def test_latch_path_counts_the_same_track_as_a_cane_user(tmp_path):
    """같은 입력을 래치로 판정하면 트랙 길이와 무관하게 지팡이 사용자로 잡힌다."""
    total, cane = _run_counter(tmp_path, frames_total=100, cane_frames=10,
                               use_latch=True)
    assert (total, cane) == (1, 1)


def test_latch_path_does_not_count_a_person_who_never_latched(tmp_path):
    total, cane = _run_counter(tmp_path, frames_total=100, cane_frames=0,
                               use_latch=True)
    assert (total, cane) == (1, 0)
