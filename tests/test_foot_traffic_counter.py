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


# --- 재식별로 되살아난 track_id의 중복 집계 방지 ---------------------------------------------
# SimpleTracker는 죽은 트랙을 revive_sec 안에 같은 자리에서 다시 잡으면 같은 track_id로 되살린다.
# 카운터가 사라지는 즉시 확정하면 같은 사람이 두 번 집계된다(실영상 23편: 118 -> 106).

def _person(tid, x=100):
    return {"track_id": tid, "bbox": [x, 100, x + 60, 260], "class": 1}


def _total(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(
            "SELECT COALESCE(SUM(total_count), 0) FROM foot_traffic_hourly").fetchone()[0]
    finally:
        conn.close()


def _run(counter, frames):
    """frames: [(now, [track...]), ...]"""
    for now, tracks in frames:
        counter.update(tracks, {}, now)


def test_같은_id가_재식별_창_안에_돌아오면_한_명으로_센다(tmp_path):
    from foot_traffic_counter import FootTrafficCounter
    c = FootTrafficCounter(tmp_path / "t.db", min_track_frames=5)
    frames = [(i * 0.1, [_person(0)]) for i in range(10)]            # 0.0~0.9초 등장
    frames += [(1.0 + i * 0.1, []) for i in range(10)]               # 1.0~1.9초 사라짐(창 2.0초 안)
    frames += [(2.0 + i * 0.1, [_person(0)]) for i in range(10)]     # 같은 id로 복귀
    _run(c, frames)
    c.finalize_all()
    c.flush()
    assert _total(tmp_path / "t.db") == 1


def test_재식별_창을_넘겨_사라지면_따로_센다(tmp_path):
    from foot_traffic_counter import FootTrafficCounter
    c = FootTrafficCounter(tmp_path / "t.db", min_track_frames=5, revive_grace_sec=2.0)
    frames = [(i * 0.1, [_person(0)]) for i in range(10)]
    frames += [(1.0 + i * 0.1, []) for i in range(40)]               # 4초 공백 > 2초 창
    frames += [(5.0 + i * 0.1, [_person(1)]) for i in range(10)]     # 다른 id(트래커도 되살리지 못한 경우)
    _run(c, frames)
    c.finalize_all()
    c.flush()
    assert _total(tmp_path / "t.db") == 2


def test_보류_중인_트랙도_종료_때_마감된다(tmp_path):
    from foot_traffic_counter import FootTrafficCounter
    c = FootTrafficCounter(tmp_path / "t.db", min_track_frames=5)
    _run(c, [(i * 0.1, [_person(0)]) for i in range(10)])
    _run(c, [(1.0, [])])                  # 막 사라져 보류 중
    c.close()                             # 보류 상태에서 종료해도 집계가 사라지면 안 된다
    assert _total(tmp_path / "t.db") == 1


def test_되살아난_id의_지팡이_래치는_이어진다(tmp_path):
    from foot_traffic_counter import FootTrafficCounter
    c = FootTrafficCounter(tmp_path / "t.db", min_track_frames=5)
    for i in range(10):
        c.update([_person(0)], {}, i * 0.1, cane_user_ids={0} if i == 3 else set())
    for i in range(5):
        c.update([], {}, 1.0 + i * 0.1, cane_user_ids=set())
    for i in range(10):
        c.update([_person(0)], {}, 2.0 + i * 0.1, cane_user_ids=set())   # 복귀 후 래치 신호 없음
    c.finalize_all()
    c.flush()
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    total, cane = conn.execute(
        "SELECT SUM(total_count), SUM(cane_user_count) FROM foot_traffic_hourly").fetchone()
    conn.close()
    assert (total, cane) == (1, 1)        # 한 명이고, 앞에서 확정된 지팡이 사용자 판정이 유지된다
