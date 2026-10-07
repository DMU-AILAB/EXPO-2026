from esp32_relay import RoiEntryPulseTracker


def test_roi_entry_emits_once_until_roi_becomes_empty():
    tracker = RoiEntryPulseTracker()

    assert not tracker.update("entry", True, 10.0, 0.5)
    assert tracker.update("entry", True, 10.5, 0.5)
    assert not tracker.update("entry", True, 11.0, 0.5)

    assert not tracker.update("entry", False, 11.1, 0.5)
    assert not tracker.update("entry", True, 12.0, 0.5)
    assert tracker.update("entry", True, 12.5, 0.5)


def test_each_roi_tracks_its_own_entry():
    tracker = RoiEntryPulseTracker()

    assert not tracker.update("north", True, 1.0, 0.5)
    assert tracker.update("north", True, 1.5, 0.5)
    assert not tracker.update("south", True, 1.0, 0.5)
    assert tracker.update("south", True, 1.5, 0.5)
