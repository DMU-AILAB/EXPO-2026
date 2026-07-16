"""camera_live.py 단위 테스트 — PC용 뷰어의 --rotation 처리."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

import camera_live as m


def test_apply_rotation_0_is_noop():
    frame = np.zeros((10, 20, 3), dtype=np.uint8)
    assert m._apply_rotation(frame, 0).shape == (10, 20, 3)


def test_apply_rotation_90_swaps_dimensions():
    frame = np.zeros((10, 20, 3), dtype=np.uint8)
    assert m._apply_rotation(frame, 90).shape == (20, 10, 3)


def test_apply_rotation_180_keeps_dimensions():
    frame = np.zeros((10, 20, 3), dtype=np.uint8)
    assert m._apply_rotation(frame, 180).shape == (10, 20, 3)
