"""fp_hotspots 단위 테스트 — 셀 양자화, 누적 upsert, min_count 필터."""

import pytest

from fp_hotspots import GRID, _cell, clear_hotspots, log_suppressed, read_hotspots


@pytest.fixture()
def db(tmp_path):
    return tmp_path / "traffic.db"


def test_cell_quantizes_and_clamps_boundaries():
    assert _cell(0.0, 0.0) == (0, 0)
    assert _cell(0.5, 0.5) == (GRID // 2, GRID // 2)
    # 정확히 1.0인 경계값이 범위를 벗어나 GRID가 되면 안 된다
    assert _cell(1.0, 1.0) == (GRID - 1, GRID - 1)


def test_read_returns_empty_when_db_missing(db):
    assert read_hotspots(db) == []


def test_nearby_detections_accumulate_into_one_cell(db):
    # 같은 셀 안에서 bbox가 미세하게 흔들려도 한 셀로 모여야 한다
    for i in range(5):
        offset = i * 0.0005
        log_suppressed(db, 0.500 + offset, 0.600 + offset, 0.520 + offset, 0.700 + offset)

    spots = read_hotspots(db, min_count=5)
    assert len(spots) == 1
    assert spots[0]["count"] == 5
    # bbox는 누적 평균 — 개별 값들 사이에 있어야 한다
    x1 = spots[0]["bbox"][0]
    assert 0.500 <= x1 <= 0.502


def test_min_count_filters_out_rare_spots(db):
    for _ in range(3):
        log_suppressed(db, 0.1, 0.1, 0.15, 0.2)
    for _ in range(10):
        log_suppressed(db, 0.8, 0.8, 0.85, 0.9)

    assert len(read_hotspots(db, min_count=5)) == 1
    assert len(read_hotspots(db, min_count=2)) == 2


def test_hotspots_ordered_by_count_desc_and_limited(db):
    for cx, n in ((0.1, 4), (0.5, 9), (0.9, 6)):
        for _ in range(n):
            log_suppressed(db, cx, 0.5, cx + 0.02, 0.6)

    spots = read_hotspots(db, min_count=1, limit=2)
    assert [s["count"] for s in spots] == [9, 6]


def test_clear_resets_accumulation(db):
    for _ in range(5):
        log_suppressed(db, 0.5, 0.5, 0.55, 0.6)
    clear_hotspots(db)
    assert read_hotspots(db, min_count=1) == []
