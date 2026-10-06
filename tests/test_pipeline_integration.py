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


# --------------------------------------------------------------------- #
# raw 녹화 — 학습용 촬영은 오버레이가 없어야 한다
#
# 기본 녹화본은 탐지 박스·ROI가 이미 그려진 최종 프레임이라 학습에 넣으면 모델이
# **그려진 박스를 단서로 배우는** 오염이 생긴다(docs/data_collection_plan.md §1-2).
# --------------------------------------------------------------------- #

def test_recorder_defaults_to_annotated_frames(tmp_path):
    """기존 동작 보존 — 데모·검토용 녹화는 오버레이가 있는 쪽이 쓸모 있다."""
    rec = m.ClipRecorder(tmp_path)
    assert rec.wants_raw is False
    rec.start((60, 80), 10.0)
    assert rec.wants_raw is False
    rec.stop()


def test_raw_mode_reports_wants_raw_only_while_recording(tmp_path):
    """`wants_raw`는 호출부가 **복사 비용을 낼지** 정하는 신호다 — 녹화 중이 아니면
    원본을 뜰 이유가 없다(1080p 한 장 복사가 Pi에서 1~2ms)."""
    rec = m.ClipRecorder(tmp_path)
    assert rec.wants_raw is False              # 시작 전
    rec.start((60, 80), 10.0, raw=True)
    assert rec.wants_raw is True
    rec.stop()
    assert rec.wants_raw is False              # 종료 후


def test_raw_flag_is_recorded_in_the_sidecar(tmp_path):
    """파일만 보고는 학습에 쓸 수 있는 클립인지 알 수 없다."""
    import json

    rec = m.ClipRecorder(tmp_path)
    rec.start((60, 80), 10.0, raw=True)
    rec.write(np.zeros((60, 80, 3), dtype=np.uint8))
    rec.stop()

    sidecars = list(tmp_path.glob("*.json"))
    assert sidecars, "사이드카가 만들어지지 않았다"
    assert json.loads(sidecars[0].read_text())["raw"] is True


def test_push_records_raw_but_streams_annotated(tmp_path):
    """★ 핵심 계약 — 화면/스트리밍은 오버레이, **녹화만** 원본.

    둘을 헷갈리면 학습 데이터가 오염되거나(오버레이 저장) 운영 화면에서 탐지 결과가
    사라진다(원본 스트리밍).
    """
    srv = m.MJPEGServer(port=18199, recordings_dir=tmp_path)
    annotated = np.full((60, 80, 3), 200, dtype=np.uint8)   # 밝게 = 그려진 것
    raw = np.zeros((60, 80, 3), dtype=np.uint8)             # 어둡게 = 원본

    written = []
    srv.recorder.write = lambda f: written.append(f)
    srv.recorder._raw = True
    srv.recorder._writer = object()                          # wants_raw True 조건

    srv.push(annotated, fps=10.0, raw=raw)

    assert written and int(written[0].mean()) == 0, "녹화에 오버레이본이 들어갔다"
    with srv._lock:
        assert srv._jpeg, "스트리밍 프레임이 만들어지지 않았다"


def test_push_falls_back_to_annotated_when_raw_is_off(tmp_path):
    srv = m.MJPEGServer(port=18198, recordings_dir=tmp_path)
    written = []
    srv.recorder.write = lambda f: written.append(f)

    annotated = np.full((60, 80, 3), 200, dtype=np.uint8)
    srv.push(annotated, fps=10.0, raw=np.zeros((60, 80, 3), dtype=np.uint8))

    assert written and int(written[0].mean()) == 200, "raw가 꺼졌는데 원본이 저장됐다"


# --------------------------------------------------------------------- #
# 구조물 수집 — 배포 기준값 아래의 탐지까지 모은다

def test_backend_collect_floor_does_not_change_what_the_pipeline_sees(monkeypatch):
    """하한을 켜도 `predict()`의 반환값은 비트 단위로 같아야 한다 — 탐지·안내 경로가
    수집 중에 달라지면 그 시간대의 안내가 평소와 다르게 나간다."""
    rng = np.random.default_rng(0)
    out = rng.random((1, 6, 2100)).astype(np.float32)       # [1, 4+nc, N] — 정규화 좌표
    out[0, :4] *= 0.3
    out[0, 0] += 0.3
    out[0, 1] += 0.3
    monkeypatch.setattr(m, "set_input", lambda interp, frame, input_size: None)
    monkeypatch.setattr(m, "get_output", lambda interp: out)

    be = object.__new__(m._TFLiteBackend)
    be.conf = m._normalize_conf({"white_cane": 0.6, "person": 0.8})
    be.input_size = 320
    be._interp = type("I", (), {"invoke": lambda self: None})()
    be.collect_floor = None
    be.last_collect_dets = []
    frame = np.zeros((480, 640, 3), np.uint8)

    before = be.predict(frame)
    be.set_collect_floor(0.0)
    during = be.predict(frame)
    assert during == before
    assert len(be.last_collect_dets) > len(during)
    assert any(d["conf"] < 0.6 for d in be.last_collect_dets)
    assert be.last_collect_dets is not during                 # 크롭 좌표 보정이 두 번 걸리지 않게

    be.set_collect_floor(None)
    assert be.predict(frame) == before and be.last_collect_dets == []


class _FlickeringStructure(_WalkingScene):
    """사람이 없는 시간, 구조물 하나를 기준값(0.6)을 사이에 두고 깜빡이며 오탐한다.

    기기 실측과 같은 모양이다 — 휴대폰 거치대가 INT8 단계값 0.59 ↔ 0.675를 오갔고,
    배포 기준값으로 모으던 시절에는 3,001프레임 수집에서 후보가 0개였다.
    """

    BOX = [24, 268, 176, 470]

    def __init__(self, frames: int) -> None:
        super().__init__(frames)
        self.collect_floor = None
        self.last_collect_dets: list = []

    def read(self):
        time.sleep(0.02)                         # 수집 마감(실시간)이 프레임 중에 오도록
        return super().read()

    def set_collect_floor(self, floor):
        self.collect_floor = floor

    def predict(self, frame):
        self.predict_calls += 1
        conf = 0.675 if self.predict_calls % 4 == 0 else 0.59    # 기준 이상은 25%뿐
        raw = [{"bbox": list(self.BOX), "conf": conf, "class": 0, "label": "white_cane"}]
        if self.collect_floor is not None:
            self.last_collect_dets = [dict(d) for d in raw if d["conf"] >= self.collect_floor]
        return [d for d in raw if d["conf"] > 0.6]


def test_calibration_collects_a_structure_that_flickers_around_the_threshold(
        tmp_path, monkeypatch, thread_exceptions):
    import static_mask
    scene = _FlickeringStructure(frames=120)
    monkeypatch.setattr(m, "build_camera",
                        lambda source, backend="auto", capture_preset="auto", tag="": scene)
    monkeypatch.setattr(m, "build_backend",
                        lambda conf, prefer="auto", weights_dir=None, input_size=640: scene)
    original_init = m.MJPEGServer.__init__

    def init_and_calibrate(self, *a, **kw):
        original_init(self, *a, **kw)
        self.start_calibration(1.0)              # 파이프라인이 뜨자마자 1초 수집

    monkeypatch.setattr(m.MJPEGServer, "__init__", init_and_calibrate)
    monkeypatch.chdir(tmp_path)                  # 녹화 폴더가 tmp 아래에 생기게
    monkeypatch.setattr(m, "_BASE", tmp_path)    # 수집 썸네일(`_BASE/recordings/<tag>/calib`)도

    db = str(tmp_path / "traffic.db")
    shared = m.SharedResources(
        audio_player=None, status_led=None,
        led_heartbeat={"t": time.time()}, stop_event=threading.Event(),
        announcements=m.AnnouncementRouter(None, m.log_event, outbox=m.queue_event),
    )
    profile = cc.CameraProfile(id="camC", source="C", port=18180, roi_config="",
                               traffic_db=db, inference_backend="tflite")
    pipe = m.CameraPipeline(profile, shared, base_conf=0.6, headless=True,
                            disable_traffic_count=True)
    pipe.start()
    deadline = time.time() + 20
    while pipe.is_alive() and time.time() < deadline:
        time.sleep(0.05)
    pipe.stop(timeout=5.0)

    assert not thread_exceptions
    rows = static_mask.read_candidates(db)
    assert len(rows) == 1, "기준값 근처에서 깜빡이는 구조물이 후보에서 빠졌다"
    assert rows[0]["cls"] == 0 and rows[0]["max_conf"] == pytest.approx(0.675)
    assert rows[0]["hits"] == rows[0]["frames"]  # 하한 0이라 모든 프레임에서 잡힌다
    assert scene.collect_floor is None           # 수집이 끝나면 하한을 끈다
    thumbs = list((tmp_path / "recordings").rglob(rows[0]["thumb"] or "<none>"))
    assert thumbs, "후보 썸네일이 저장되지 않았다"
    crop = __import__("cv2").imread(str(thumbs[0]))
    assert crop is not None and crop.shape[0] > 150    # 구조물 박스(높이 202px) 자리에서 잘렸다
