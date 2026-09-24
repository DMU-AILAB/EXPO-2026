"""camera_live_pi.py 단위 테스트 — 회전, 제외구역 필터, 카메라/백엔드 강제 선택.

카메라/실제 모델 파일 없이도 돌아가도록 순수 함수 레벨만 검증한다 (하드웨어 필요 없음).
"""
import os
import sys
import time
from pathlib import Path

import numpy as np
import pytest


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
    monkeypatch.setattr(m, "_OpenCVSource",
                        lambda source, capture_preset="auto", tag="": calls.append(("opencv", source)))
    monkeypatch.setattr(m, "_Picamera2Source",
                        lambda camera_num=0, capture_preset="auto": calls.append(("picamera2", camera_num)))
    m.build_camera("3", backend="opencv")
    assert calls == [("opencv", "3")]


def test_build_camera_backend_picamera2_forced(monkeypatch):
    calls = []
    monkeypatch.setattr(m, "_OpenCVSource",
                        lambda source, capture_preset="auto", tag="": calls.append(("opencv", source)))
    monkeypatch.setattr(m, "_Picamera2Source",
                        lambda camera_num=0, capture_preset="auto": calls.append(("picamera2", camera_num)))
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


# 녹화 기능 — _route_recording()은 순수 라우팅 함수라 실제 소켓 서버 없이 검증 가능,
# ClipRecorder._enforce_quota()도 cv2.VideoWriter 없이 파일시스템 로직만 단위테스트한다.


@pytest.mark.parametrize("method,path,expected", [
    ("GET", "/recording/status", ("status", "")),
    ("POST", "/recording/start", ("start", "")),
    ("POST", "/recording/stop", ("stop", "")),
    ("GET", "/recording/list", ("list", "")),
    ("GET", "/recording/clips/clip_20260729_143210.mp4", ("clip_video", "clip_20260729_143210")),
    ("GET", "/recording/clips/clip_20260729_143210.jpg", ("clip_thumb", "clip_20260729_143210")),
])
def test_route_recording_matches_known_routes(method, path, expected):
    assert m._route_recording(method, path) == expected


@pytest.mark.parametrize("method,path", [
    ("GET", "/stream.mjpg"),
    ("POST", "/recording/status"),   # 메서드 불일치
    ("GET", "/recording/start"),     # 메서드 불일치
    ("GET", "/recording/clips/clip_20260729_143210.txt"),  # 지원하지 않는 확장자
    ("GET", "/unknown"),
])
def test_route_recording_returns_none_for_unmatched(method, path):
    assert m._route_recording(method, path) is None


def _touch(path, mtime_offset_sec, size_bytes=100):
    path.write_bytes(b"x" * size_bytes)
    now = 1_800_000_000.0  # 임의의 고정 기준시각 — 실제 시각과 무관하게 상대 순서만 중요
    t = now + mtime_offset_sec
    os.utime(path, (t, t))


def test_clip_recorder_quota_deletes_oldest_clip_by_count(tmp_path):
    rec = m.ClipRecorder(tmp_path, max_clips=2, max_total_bytes=10 ** 9)
    for i, clip_id in enumerate(["clip_20260101_000000", "clip_20260101_000001", "clip_20260101_000002"]):
        _touch(tmp_path / f"{clip_id}.mp4", mtime_offset_sec=i)
        _touch(tmp_path / f"{clip_id}.jpg", mtime_offset_sec=i)
    rec._enforce_quota()
    remaining = {p.stem for p in tmp_path.glob("clip_*.mp4")}
    assert remaining == {"clip_20260101_000001", "clip_20260101_000002"}
    assert not (tmp_path / "clip_20260101_000000.jpg").exists()


def test_clip_recorder_quota_deletes_oldest_clip_by_total_size(tmp_path):
    rec = m.ClipRecorder(tmp_path, max_clips=100, max_total_bytes=150)
    _touch(tmp_path / "clip_20260101_000000.mp4", mtime_offset_sec=0, size_bytes=100)
    _touch(tmp_path / "clip_20260101_000001.mp4", mtime_offset_sec=1, size_bytes=100)
    rec._enforce_quota()
    remaining = {p.stem for p in tmp_path.glob("clip_*.mp4")}
    assert remaining == {"clip_20260101_000001"}


def test_clip_recorder_quota_never_deletes_active_clip(tmp_path):
    rec = m.ClipRecorder(tmp_path, max_clips=1, max_total_bytes=10 ** 9)
    _touch(tmp_path / "clip_20260101_000000.mp4", mtime_offset_sec=0)
    rec._clip_id = "clip_20260101_000001"  # 아직 진행 중(파일은 실제로 없어도 상태만 필요)
    _touch(tmp_path / "clip_20260101_000001.mp4", mtime_offset_sec=1)
    rec._enforce_quota()
    remaining = {p.stem for p in tmp_path.glob("clip_*.mp4")}
    assert "clip_20260101_000001" in remaining


def test_clip_recorder_clip_path_rejects_path_traversal():
    rec = m.ClipRecorder(Path("recordings/legacy"))
    assert rec.clip_path("../../etc/passwd", "video") is None
    assert rec.clip_path("clip_20260101_000000", "bogus_kind") is None


def test_clip_recorder_list_clips_excludes_active_and_missing_dir(tmp_path):
    rec = m.ClipRecorder(tmp_path / "does_not_exist")
    assert rec.list_clips() == []


def test_clip_recorder_segments_long_recording_and_keeps_session_elapsed(tmp_path):
    """segment_duration_sec를 아주 짧게 줘서 실제 cv2.VideoWriter로 회전(분할 저장)이
    일어나는지 검증한다 — 10분을 실제로 기다릴 수 없으므로 짧은 값으로 대체."""
    # clip_id는 초 단위 타임스탬프라 회전 간격이 1초 미만이면 같은 파일명이 나올 수
    # 있다(실사용 600초 간격에서는 절대 벌어지지 않는 일) — 테스트에서도 1초 넘게
    # 벌려서 검증한다.
    rec = m.ClipRecorder(tmp_path, segment_duration_sec=1.1)
    frame = np.zeros((16, 16, 3), dtype=np.uint8)

    start_result = rec.start((16, 16), fps=10.0)
    assert start_result["ok"], start_result
    first_clip_id = start_result["clip_id"]

    rec.write(frame)
    time.sleep(1.3)  # segment_duration_sec 초과 — 다음 write()에서 회전 발생
    rec.write(frame)
    rec.write(frame)

    status = rec.status()
    assert status["recording"] is True
    second_clip_id = status["clip_id"]
    assert second_clip_id != first_clip_id, "세그먼트가 회전해 새 clip_id가 발급돼야 한다"

    # 회전된(마감된) 첫 세그먼트는 사이드카가 기록돼 목록에 바로 나타나야 한다
    clips_while_recording = {c["id"]: c for c in rec.list_clips()}
    assert first_clip_id in clips_while_recording
    assert clips_while_recording[first_clip_id]["duration_sec"] is not None
    assert second_clip_id not in clips_while_recording  # 아직 진행 중인 세그먼트는 목록 제외

    # elapsed_sec는 세그먼트가 아니라 세션(최초 시작 시각) 기준으로 계속 누적돼야 한다
    assert status["elapsed_sec"] >= 1.3

    stop_result = rec.stop()
    assert stop_result["ok"], stop_result
    assert stop_result["clip_id"] == second_clip_id

    final_clips = {c["id"] for c in rec.list_clips()}
    assert first_clip_id in final_clips
    assert second_clip_id in final_clips


# ---------------------------------------------------------------------------
# ROI 크롭 추론 (_roi_crop_box)
#
# 흰 지팡이는 폭 2~3px의 얇은 막대라 320 입력에서 소실되기 쉽다. 카메라가 고정이고
# trigger 구역이 이미 정의돼 있다는 구조를 이용해 그 영역만 잘라 넣으면, 연산량을
# 그대로 두고 객체 픽셀 밀도를 올릴 수 있다. 다만 **정사각으로 넓히지 않으면
# 역효과**(가로로 긴 크롭은 레터박스 패딩이 캔버스를 먹어 배율 이득이 사라짐)라,
# 그 기하 계산을 여기서 못박는다.
# ---------------------------------------------------------------------------
class _FakeROI:
    def __init__(self, points, zone_type="trigger"):
        self.points = points
        self.zone_type = zone_type


class _FakeROIManager:
    def __init__(self, rois):
        self.rois = rois


def test_roi_crop_box_expands_to_square_to_fill_model_canvas():
    """가로로 긴 ROI라도 크롭은 정사각이어야 한다 — 안 그러면 레터박스 패딩이
    캔버스의 절반 이상을 먹어 크롭하지 않느니만 못해진다(실측 95 vs 108프레임)."""
    rm = _FakeROIManager([_FakeROI([[0.1, 0.4], [0.5, 0.4], [0.5, 0.5], [0.1, 0.5]])])
    box = m._roi_crop_box(rm, 1920, 1080)
    assert box is not None
    w, h = box[2] - box[0], box[3] - box[1]
    assert abs(w - h) <= 2, box


def test_roi_crop_box_fills_frame_height_when_square_does_not_fit():
    """정사각이 프레임 높이를 넘으면 넓힐 수 있는 만큼만 넓힌다(높이를 꽉 채움).
    잘라낼 수 없는 것을 잘라내려 하면 좌표가 프레임 밖으로 나간다."""
    rm = _FakeROIManager([_FakeROI([[0.1, 0.4], [0.6, 0.4], [0.6, 0.5], [0.1, 0.5]])])
    box = m._roi_crop_box(rm, 1920, 1080)
    assert box is not None
    assert box[1] == 0 and box[3] == 1080, box       # 높이는 프레임 전체
    assert box[2] - box[0] >= box[3] - box[1], box   # 가로가 더 길거나 같음
    assert box[2] <= 1920, box


def test_roi_crop_box_ignores_exclude_zones():
    """제외구역은 트리거가 일어날 수 없는 영역이라 크롭 범위에 넣을 이유가 없다."""
    rm = _FakeROIManager([
        _FakeROI([[0.40, 0.40], [0.50, 0.40], [0.50, 0.50], [0.40, 0.50]]),
        _FakeROI([[0.00, 0.00], [0.99, 0.00], [0.99, 0.99], [0.00, 0.99]], "exclude"),
    ])
    box = m._roi_crop_box(rm, 1920, 1080)
    assert box is not None
    assert (box[2] - box[0]) * (box[3] - box[1]) < 1920 * 1080 * 0.5, box


def test_roi_crop_box_declines_when_roi_covers_most_of_frame():
    """ROI가 프레임 대부분을 덮으면 배율 이득은 없이 크롭 밖 사람만 놓친다 —
    그럴 땐 크롭하지 않는 게 맞다."""
    rm = _FakeROIManager([_FakeROI([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]])])
    assert m._roi_crop_box(rm, 1920, 1080) is None


def test_roi_crop_box_returns_none_without_trigger_zones():
    assert m._roi_crop_box(_FakeROIManager([]), 1920, 1080) is None
    assert m._roi_crop_box(None, 1920, 1080) is None


def test_roi_crop_box_stays_inside_frame_when_roi_touches_edge():
    """경계에 붙은 ROI를 정사각으로 넓힐 때 프레임 밖으로 나가면 안 된다."""
    rm = _FakeROIManager([_FakeROI([[0.0, 0.80], [0.30, 0.80], [0.30, 1.0], [0.0, 1.0]])])
    box = m._roi_crop_box(rm, 1920, 1080)
    assert box is not None
    assert 0 <= box[0] < box[2] <= 1920, box
    assert 0 <= box[1] < box[3] <= 1080, box


# ---------------------------------------------------------------------------
# 구조물 수집(새벽 캘리브레이션) 라우팅 — 녹화와 같은 이유로 순수 함수로 뽑았다
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("method,path,expected", [
    ("POST", "/calibrate/start", "start"),
    ("POST", "/calibrate/start?seconds=600", "start"),
    ("POST", "/calibrate/cancel", "cancel"),
    ("GET", "/calibrate/status", "status"),
    ("GET", "/calibrate/start", None),        # 시작은 POST만
    ("POST", "/calibrate/status", None),
    ("GET", "/stream.mjpg", None),            # 다른 경로를 삼키면 안 된다
    ("GET", "/recording/status", None),
])
def test_route_calibrate_matches_known_routes(method, path, expected):
    assert m._route_calibrate(method, path) == expected


def test_수집_시작과_취소_상태전이():
    srv = m.MJPEGServer(port=0)
    assert srv.calibration_status()["running"] is False
    assert srv.calibration_deadline() is None

    assert srv.start_calibration(60)["ok"] is True
    assert srv.calibration_status()["running"] is True
    assert srv.calibration_deadline() is not None

    # 중복 시작은 거절 — 수집 중에 또 시작하면 앞의 결과가 조용히 사라진다
    assert srv.start_calibration(60)["ok"] is False

    assert srv.cancel_calibration()["ok"] is True
    assert srv.calibration_status()["running"] is False
    assert srv.cancel_calibration()["ok"] is False      # 이미 취소됨


def test_수집_완료_결과가_남는다():
    srv = m.MJPEGServer(port=0)
    srv.start_calibration(60)
    srv.calibration_tick()
    srv.finish_calibration({"candidates": 3, "frames": 120})
    st = srv.calibration_status()
    assert st["running"] is False
    assert st["result"] == {"candidates": 3, "frames": 120}
