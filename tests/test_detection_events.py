"""detection_events.py 단위 테스트 — 이벤트 기록/조회, 최근 N건 유지."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from detection_events import log_event, read_recent_events


def test_read_recent_events_missing_db_returns_empty(tmp_path):
    assert read_recent_events(tmp_path / "nonexistent.db") == []


def test_log_and_read_events_newest_first(tmp_path):
    db_path = tmp_path / "events.db"
    log_event(db_path, "2026-07-29T10:00:00", "white_cane", "ROI-1")
    log_event(db_path, "2026-07-29T10:00:05", "white_cane", "ROI-2")

    events = read_recent_events(db_path)
    assert len(events) == 2
    assert events[0] == {"ts": "2026-07-29T10:00:05", "class_name": "white_cane", "roi_name": "ROI-2"}
    assert events[1]["roi_name"] == "ROI-1"


def test_read_recent_events_respects_limit(tmp_path):
    db_path = tmp_path / "events.db"
    for i in range(5):
        log_event(db_path, f"2026-07-29T10:00:0{i}", "white_cane", f"ROI-{i}")

    events = read_recent_events(db_path, limit=2)
    assert len(events) == 2
    assert events[0]["roi_name"] == "ROI-4"
    assert events[1]["roi_name"] == "ROI-3"


def test_log_event_trims_beyond_max_events(tmp_path):
    db_path = tmp_path / "events.db"
    for i in range(10):
        log_event(db_path, f"2026-07-29T10:00:{i:02d}", "white_cane", f"ROI-{i}", max_events=3)

    events = read_recent_events(db_path, limit=100)
    assert len(events) == 3
    assert [e["roi_name"] for e in events] == ["ROI-9", "ROI-8", "ROI-7"]
