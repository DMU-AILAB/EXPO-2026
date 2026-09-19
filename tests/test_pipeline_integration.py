"""CameraPipeline 통합 스모크 — **프레임 루프를 끝까지 실제로 돌린다.**

왜 필요한가: 실기기 검증에서 단위 테스트 265개가 전부 통과하는 상태인데도 **탐지
루프가 매 프레임 죽고 있었다**(기기 로그에 Traceback 23회). `_METRICS_AVAILABLE`
미정의(NameError)와 `infer_ema` 미초기화(UnboundLocalError) 두 건이었는데, 둘 다
"루프를 한 바퀴 이상 돌면 즉시 터지는" 종류라 **루프를 돌리기만 했어도 잡혔다.**

기존 `test_camera_pipeline_dual.py`가 그 역할을 해야 했지만 탐지가 없는 빈 결과
(`predict() -> []`)만 흘려보내서 게이트·엔티티·ROI·지표·outbox 경로에 들어가지 못했다.
여기서는 **사람과 지팡이가 실제로 걸어가는 탐지 결과**를 주고 ROI와 traffic_db까지
붙여 그 경로를 전부 지난다.

★ 이 파일의 핵심은 `_thread_exceptions` 픽스처다. 파이프라인은 자체 스레드에서 돌고
**스레드에서 터진 예외는 테스트를 실패시키지 않는다** — 콘솔에 Traceback만 찍히고
pytest는 통과로 본다. 그게 위 두 버그가 살아남은 경로다. 훅을 걸어 전부 실패로 만든다.
"""

import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

import camera_config as cc
import camera_live_pi as m
from device_metrics import read_metrics
from event_logger import pending_count


@pytest.fixture
def thread_exceptions():
    """파이프라인 스레드에서 터진 예외를 모아 테스트를 실패시킨다.

    이게 없으면 스레드가 매 프레임 죽어도 pytest는 통과한다 — 실기기에서 실제로
    그랬다.
    """
    caught: list = []
    original = threading.excepthook

    def hook(args):
        caught.append(args)
        original(args)

    threading.excepthook = hook
    yield caught
    threading.excepthook = original


class _WalkingScene:
    """사람과 지팡이가 나란히 오른쪽으로 걸어가는 장면을 만드는 가짜 카메라+백엔드.

    좌표가 실제로 움직여야 움직임 게이트(원점 대비 2% 변위)를 통과하고, 그래야
    엔티티 래치와 ROI 트리거까지 경로가 열린다.
    """

    W, H = 640, 480

    def __init__(self, frames: int = 60) -> None:
        self.total = frames
        self.i = 0
        self.predict_calls = 0

    # --- 카메라 ---
    def read(self):
        if self.i >= self.total:
            return False, None
        self.i += 1
        return True, np.zeros((self.H, self.W, 3), dtype=np.uint8)

    def release(self):
        pass

    # --- 추론 백엔드 ---
    def predict(self, frame):
        self.predict_calls += 1
        x = 40 + self.predict_calls * 8          # 프레임당 8px 전진
        return [
            {"bbox": [x, 150, x + 90, 400], "conf": 0.91, "class": 1, "label": "person"},
            {"bbox": [x + 85, 330, x + 105, 420], "conf": 0.88, "class": 0,
             "label": "white_cane"},
        ]

    def close(self):
        pass

    def update_conf(self, conf):
        pass


def _run_pipeline(tmp_path, monkeypatch, *, frames=60, with_roi=True,
                  traffic=True, port=18100):
    scene = _WalkingScene(frames)
    monkeypatch.setattr(m, "build_camera",
                        lambda source, backend="auto", capture_preset="auto", tag="":
                        scene)
    monkeypatch.setattr(m, "build_backend",
                        lambda conf, prefer="auto", weights_dir=None, input_size=640:
                        scene)

    roi_path = ""
    if with_roi:
        roi_path = str(tmp_path / "rois.json")
        Path(roi_path).write_text(
            '{"debounce": 0.0, "cooldown": 10.0, "rois": [{"name": "zone",'
            ' "points": [[0,0],[1,0],[1,1],[0,1]], "priority": 1,'
            ' "announcement_text": "안내", "audio_file": "", "zone_type": "trigger"}]}',
            encoding="utf-8")

    db = str(tmp_path / "traffic.db")
    shared = m.SharedResources(
        audio_player=None, status_led=None,
        led_heartbeat={"t": time.time()}, stop_event=threading.Event(),
        announcements=m.AnnouncementRouter(
            None, m.log_event, outbox=m.queue_event),
    )
    profile = cc.CameraProfile(id="camI", source="I", port=port,
                               roi_config=roi_path, traffic_db=db,
                               inference_backend="tflite")
    pipe = m.CameraPipeline(profile, shared, base_conf=0.5, headless=True,
                            disable_traffic_count=not traffic)
    pipe.start()
    deadline = time.time() + 20
    while pipe.is_alive() and time.time() < deadline:
        time.sleep(0.05)
    pipe.stop(timeout=5.0)
    return scene, db


# --------------------------------------------------------------------- #

def test_full_frame_loop_runs_without_thread_exceptions(tmp_path, monkeypatch,
                                                        thread_exceptions):
    """★ 회귀 테스트 — 실기기에서 나온 NameError/UnboundLocalError를 잡는 자리.

    탐지가 있고 ROI가 있고 traffic_db가 있어야 게이트·엔티티·지표·outbox 경로에
    전부 들어간다. 빈 탐지만 흘려보내면 그 코드가 한 줄도 실행되지 않는다.
    """
    scene, _db = _run_pipeline(tmp_path, monkeypatch, frames=60)

    assert not thread_exceptions, (
        "파이프라인 스레드에서 예외 발생: "
        + "; ".join(f"{a.exc_type.__name__}: {a.exc_value}" for a in thread_exceptions))
    assert scene.predict_calls >= 50, f"프레임을 거의 못 돌았다 ({scene.predict_calls})"


def test_loop_reports_runtime_metrics(tmp_path, monkeypatch, thread_exceptions):
    """하트비트가 쓰는 지표가 실제로 sqlite에 기록되는지.

    `infer_ema` 미초기화 버그가 정확히 이 블록에서 터졌다.
    """
    _scene, db = _run_pipeline(tmp_path, monkeypatch, frames=80)
    assert not thread_exceptions

    rows = read_metrics(db, stale_after=1e9)
    assert rows, "device_metrics에 아무것도 보고되지 않았다"
    (r,) = rows
    assert r["camera_id"] == "camI"
    assert r["infer_ms"] is not None and r["infer_ms"] >= 0
    assert r["loop_ms"] is not None and r["loop_ms"] >= r["infer_ms"]
    assert r["fps"] is not None


def test_trigger_path_reaches_the_outbox(tmp_path, monkeypatch, thread_exceptions):
    """게이트 → 엔티티 → ROI → 안내 → 서버 전송 대기열까지 한 번에.

    사람과 지팡이가 함께 움직이므로 3중 게이트를 모두 통과해야 하고, 통과하면
    전체 화면 ROI에서 트리거가 나 outbox에 쌓인다.
    """
    _scene, db = _run_pipeline(tmp_path, monkeypatch, frames=90)
    assert not thread_exceptions
    assert pending_count(db) >= 1, "트리거가 한 번도 발생하지 않았다"


def test_loop_survives_a_backend_that_returns_nothing(tmp_path, monkeypatch,
                                                      thread_exceptions):
    """탐지가 0인 구간에서도 루프가 멀쩡해야 한다 — 지표 보고는 탐지와 무관하다."""
    scene = _WalkingScene(40)
    scene.predict = lambda frame: []                     # 항상 빈 결과
    monkeypatch.setattr(m, "build_camera",
                        lambda source, backend="auto", capture_preset="auto", tag="":
                        scene)
    monkeypatch.setattr(m, "build_backend",
                        lambda conf, prefer="auto", weights_dir=None, input_size=640:
                        scene)

    db = str(tmp_path / "t.db")
    shared = m.SharedResources(audio_player=None, status_led=None,
                               led_heartbeat={"t": time.time()},
                               stop_event=threading.Event())
    profile = cc.CameraProfile(id="camE", source="E", port=18103, roi_config="",
                               traffic_db=db, inference_backend="tflite")
    pipe = m.CameraPipeline(profile, shared, base_conf=0.5, headless=True,
                            disable_traffic_count=False)
    pipe.start()
    deadline = time.time() + 15
    while pipe.is_alive() and time.time() < deadline:
        time.sleep(0.05)
    pipe.stop(timeout=5.0)

    assert not thread_exceptions
    assert read_metrics(db, stale_after=1e9), "탐지가 없어도 지표는 보고되어야 한다"


def test_thread_exception_hook_actually_catches(thread_exceptions):
    """픽스처 자체의 회귀 테스트 — 훅이 동작하지 않으면 위 테스트들이 전부 거짓 통과한다."""
    def boom():
        raise RuntimeError("의도된 예외")

    t = threading.Thread(target=boom)
    t.start()
    t.join()
    assert len(thread_exceptions) == 1
    assert thread_exceptions[0].exc_type is RuntimeError
    thread_exceptions.clear()          # 이 테스트는 예외가 나는 게 정상이다
