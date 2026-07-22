"""camera_live_pi.py 단위 테스트 — 회전, 제외구역 필터, 카메라/백엔드 강제 선택.

카메라/실제 모델 파일 없이도 돌아가도록 순수 함수 레벨만 검증한다 (하드웨어 필요 없음).
"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import camera_live_pi as m
from simulator.roi_manager import ROIManager


def test_apply_rotation_0_is_noop():
    frame = np.zeros((10, 20, 3), dtype=np.uint8)
    out = m._apply_rotation(frame, 0)
    assert out.shape == (10, 20, 3)


@pytest.mark.parametrize("degrees,expected_shape", [(90, (20, 10, 3)), (270, (20, 10, 3))])
def test_apply_rotation_90_270_swap_dimensions(degrees, expected_shape):
    frame = np.zeros((10, 20, 3), dtype=np.uint8)
    out = m._apply_rotation(frame, degrees)
    assert out.shape == expected_shape


def test_apply_rotation_180_keeps_dimensions():
    frame = np.zeros((10, 20, 3), dtype=np.uint8)
    out = m._apply_rotation(frame, 180)
    assert out.shape == (10, 20, 3)


def test_apply_channel_swap_disabled_is_noop():
    frame = np.arange(2 * 2 * 3, dtype=np.uint8).reshape(2, 2, 3)
    out = m._apply_channel_swap(frame, False)
    assert out is frame


def test_apply_channel_swap_enabled_reverses_channels():
    frame = np.zeros((2, 2, 3), dtype=np.uint8)
    frame[0, 0] = [10, 20, 30]  # B, G, R 순서라 치면
    out = m._apply_channel_swap(frame, True)
    assert list(out[0, 0]) == [30, 20, 10]
    # 원본 프레임이 훼손되지 않아야 한다 (in-place 아님)
    assert list(frame[0, 0]) == [10, 20, 30]


def test_apply_channel_swap_returns_contiguous_array():
    """반전된 뷰 그대로 두면 이후 cv2.rectangle 같은 in-place 그리기가 깨질 수 있다 —
    반드시 연속 메모리 배열(copy)로 반환해야 한다."""
    frame = np.zeros((4, 4, 3), dtype=np.uint8)
    out = m._apply_channel_swap(frame, True)
    assert out.flags['C_CONTIGUOUS']


def test_filter_excluded_returns_all_when_no_roi_manager():
    dets = [{"bbox": [0, 0, 10, 10], "conf": 0.9, "class": 0}]
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    assert m._filter_excluded(dets, None, frame) == dets


def test_filter_excluded_drops_detections_inside_exclude_zone():
    mgr = ROIManager()
    mgr.add_roi("excl", [[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5]],
                priority=1, announcement_text="", zone_type="exclude")
    mgr.add_roi("trig", [[0.5, 0.5], [1.0, 0.5], [1.0, 1.0], [0.5, 1.0]],
                priority=1, announcement_text="t", zone_type="trigger")

    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    dets = [
        {"bbox": [5, 5, 15, 15], "conf": 0.9, "class": 0},     # center (10,10) -> inside exclude
        {"bbox": [60, 60, 80, 80], "conf": 0.9, "class": 0},   # center (70,70) -> inside trigger, kept
        {"bbox": [60, 60, 80, 80], "conf": 0.9, "class": 1},   # person class also filtered uniformly
    ]
    out = m._filter_excluded(dets, mgr, frame)
    assert len(out) == 2
    assert all(d["bbox"] != [5, 5, 15, 15] for d in out)


def test_build_camera_backend_opencv_forced(monkeypatch):
    calls = []
    monkeypatch.setattr(m, "_OpenCVSource", lambda source: calls.append(("opencv", source)))
    monkeypatch.setattr(m, "_Picamera2Source", lambda camera_num=0: calls.append(("picamera2", camera_num)))
    m.build_camera("3", backend="opencv")
    assert calls == [("opencv", "3")]


def test_build_camera_backend_picamera2_forced(monkeypatch):
    calls = []
    monkeypatch.setattr(m, "_OpenCVSource", lambda source: calls.append(("opencv", source)))
    monkeypatch.setattr(m, "_Picamera2Source", lambda camera_num=0: calls.append(("picamera2", camera_num)))
    m.build_camera("1", backend="picamera2")
    assert calls == [("picamera2", 1)]


def test_build_backend_tflite_requires_model_file(tmp_path):
    with pytest.raises(RuntimeError):
        m.build_backend(0.5, prefer="tflite", weights_dir=str(tmp_path / "missing_weights"))


def test_build_backend_pytorch_requires_model_file(tmp_path):
    with pytest.raises(RuntimeError):
        m.build_backend(0.5, prefer="pytorch", weights_dir=str(tmp_path / "missing_weights"))


def test_model_paths_resolves_expected_filenames():
    paths = m._model_paths("runs/white_cane_v2/weights")
    assert paths["edgetpu"].name == "best_int8_edgetpu.tflite"
    assert paths["tflite"].name == "best_int8.tflite"
    assert paths["pytorch"].name == "best.pt"


def test_normalize_conf_expands_scalar_to_all_classes():
    assert m._normalize_conf(0.5) == {"white_cane": 0.5, "person": 0.5}


def test_normalize_conf_keeps_per_class_dict():
    conf = {"white_cane": 0.6, "person": 0.4}
    assert m._normalize_conf(conf) == conf


def test_normalize_conf_rejects_dict_missing_a_class():
    with pytest.raises(KeyError):
        m._normalize_conf({"white_cane": 0.6})
