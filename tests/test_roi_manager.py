"""simulator/roi_manager.py 단위 테스트 — 제외구역(zone_type="exclude") 필터링."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from simulator.roi_manager import ROIManager

TRIGGER_SQUARE = [[0.5, 0.5], [1.0, 0.5], [1.0, 1.0], [0.5, 1.0]]
EXCLUDE_SQUARE = [[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5]]


def _manager_with_both_zone_types() -> ROIManager:
    mgr = ROIManager()
    mgr.add_roi("trig", TRIGGER_SQUARE, priority=1, announcement_text="t", zone_type="trigger")
    mgr.add_roi("excl", EXCLUDE_SQUARE, priority=1, announcement_text="", zone_type="exclude")
    return mgr


def test_zone_type_defaults_to_trigger_for_backward_compat():
    mgr = ROIManager()
    mgr.add_roi("legacy", TRIGGER_SQUARE, priority=1, announcement_text="t")
    assert mgr.rois[0].zone_type == "trigger"


def test_check_ignores_exclude_zones():
    mgr = _manager_with_both_zone_types()
    # 점 (0.25, 0.25)는 제외구역 안에만 있음 — check()는 트리거 매치를 반환하지 않아야 함
    assert mgr.check(0.25, 0.25) is None


def test_check_still_matches_trigger_zones():
    mgr = _manager_with_both_zone_types()
    roi = mgr.check(0.75, 0.75)
    assert roi is not None and roi.name == "trig"


def test_is_excluded_true_inside_exclude_zone():
    mgr = _manager_with_both_zone_types()
    assert mgr.is_excluded(0.25, 0.25) is True


def test_is_excluded_false_inside_trigger_zone():
    mgr = _manager_with_both_zone_types()
    assert mgr.is_excluded(0.75, 0.75) is False


def test_is_excluded_false_outside_any_zone():
    mgr = _manager_with_both_zone_types()
    assert mgr.is_excluded(0.9, 0.1) is False


def test_check_region_ignores_exclude_zones():
    mgr = _manager_with_both_zone_types()
    # 사각형이 exclude 구역과만 겹침
    assert mgr.check_region(0.1, 0.1, 0.2, 0.2) is None


def test_save_load_round_trip_preserves_zone_type(tmp_path):
    mgr = _manager_with_both_zone_types()
    path = tmp_path / "rois.json"
    mgr.save(str(path))
    loaded = ROIManager.load(str(path))
    types = {r.name: r.zone_type for r in loaded.rois}
    assert types == {"trig": "trigger", "excl": "exclude"}


def test_load_defaults_missing_zone_type_field_to_trigger(tmp_path):
    import json
    path = tmp_path / "legacy_rois.json"
    path.write_text(json.dumps({"rois": [
        {"name": "old", "points": TRIGGER_SQUARE, "priority": 1, "announcement_text": "t"},
    ]}), encoding="utf-8")
    loaded = ROIManager.load(str(path))
    assert loaded.rois[0].zone_type == "trigger"
