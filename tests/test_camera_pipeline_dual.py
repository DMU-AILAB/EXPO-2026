"""camera_live_pi.py 듀얼카메라 스모크 테스트 — PC에서 실제 하드웨어/모델 없이, 두
CameraPipeline이 각자 스레드에서 동시에 돌고, 각자의 MJPEG 포트를 서빙하며, 프레임이
바닥나면 깨끗이 종료되는지 확인한다.

실제 Coral 동글 경합/Pi 실기기 동작은 여기서 검증되지 않는다 — PC 스모크 테스트일 뿐이다.
"""
import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

import camera_config as cc
import camera_live_pi as m


class _FakeCamera:
    """N프레임 제공 후 EOF(ok=False)를 반환하는 가짜 카메라."""

    def __init__(self, frames_before_eof: int = 8) -> None:
        self._n = 0
        self._frames_before_eof = frames_before_eof

    def read(self):
        if self._n >= self._frames_before_eof:
            return False, None
        self._n += 1
        return True, np.zeros((60, 80, 3), dtype=np.uint8)

    def release(self) -> None:
        pass


class _FakeBackend:
    def __init__(self, conf: float) -> None:
        self.conf = conf
        self.predict_calls = 0

    def predict(self, frame):
        self.predict_calls += 1
        return []  # 탐지 없음 — ROI/오디오 로직 경로는 다른 테스트에서 이미 검증됨

    def close(self) -> None:
        pass


def test_two_camera_pipelines_run_concurrently_and_shut_down_cleanly(monkeypatch):
    fake_cameras = {}
    fake_backends = {}

    def fake_build_camera(source, backend="auto"):
        cam = _FakeCamera()
        fake_cameras[source] = cam
        return cam

    def fake_build_backend(conf, prefer="auto", weights_dir=None, input_size=640):
        backend = _FakeBackend(conf)
        fake_backends[prefer] = backend
        return backend

    monkeypatch.setattr(m, "build_camera", fake_build_camera)
    monkeypatch.setattr(m, "build_backend", fake_build_backend)

    shared = m.SharedResources(
        audio_player=None,
        status_led=None,
        led_heartbeat={"t": time.time()},
        stop_event=threading.Event(),
    )

    profile_a = cc.CameraProfile(id="camA", source="A", port=18080, roi_config="", inference_backend="tflite")
    profile_b = cc.CameraProfile(id="camB", source="B", port=18081, roi_config="", inference_backend="pytorch")

    pipeline_a = m.CameraPipeline(profile_a, shared, base_conf=0.5, headless=True, disable_traffic_count=True)
    pipeline_b = m.CameraPipeline(profile_b, shared, base_conf=0.5, headless=True, disable_traffic_count=True)

    pipeline_a.start()
    pipeline_b.start()

    # 두 파이프라인이 동시에 살아있는 짧은 창(fake camera가 EOF를 내기 전)을 확인
    time.sleep(0.05)
    assert pipeline_a.is_alive() or fake_cameras.get("A") is not None
    assert pipeline_b.is_alive() or fake_cameras.get("B") is not None

    # fake camera가 8프레임 후 EOF를 반환하므로 각 파이프라인 스레드는 스스로 종료된다
    pipeline_a.stop(timeout=3.0)
    pipeline_b.stop(timeout=3.0)

    assert not pipeline_a.is_alive()
    assert not pipeline_b.is_alive()

    # 서로 다른 backend prefer 값으로 각자 독립된 추론 백엔드가 생성되었는지 확인
    assert "tflite" in fake_backends
    assert "pytorch" in fake_backends
    assert fake_backends["tflite"].predict_calls > 0
    assert fake_backends["pytorch"].predict_calls > 0


class _InfiniteFakeCamera:
    """중지 신호를 받기 전까지는 EOF를 내지 않는 가짜 카메라 — stop() 경로를 실제로 태운다."""

    def read(self):
        return True, np.zeros((60, 80, 3), dtype=np.uint8)

    def release(self) -> None:
        pass


def test_pipeline_restart_on_same_port_does_not_leak_socket(monkeypatch):
    """회귀 테스트: MJPEGServer.stop()이 server_close()를 호출하지 않으면, 같은 포트로
    파이프라인을 재생성할 때 "Address already in use"로 스레드가 처리되지 않은 예외와
    함께 죽는다 (카메라 프로필 변경 시 _reconcile_pipelines가 겪었던 실제 버그).
    """
    monkeypatch.setattr(m, "build_camera", lambda source, backend="auto": _InfiniteFakeCamera())
    monkeypatch.setattr(m, "build_backend", lambda conf, prefer="auto", weights_dir=None, input_size=640: _FakeBackend(conf))

    shared = m.SharedResources(
        audio_player=None,
        status_led=None,
        led_heartbeat={"t": time.time()},
        stop_event=threading.Event(),
    )
    profile = cc.CameraProfile(id="camX", source="X", port=18090, roi_config="")

    first = m.CameraPipeline(profile, shared, base_conf=0.5, headless=True, disable_traffic_count=True)
    first.start()
    time.sleep(0.1)
    assert first.is_alive()
    first.stop(timeout=3.0)
    assert not first.is_alive()

    # 같은 포트로 두 번째 파이프라인을 곧바로 재생성 — 소켓이 제대로 닫혔다면 바인드에
    # 성공하고 계속 살아있어야 한다. 예전 버그에서는 여기서 OSError로 즉시 죽었다.
    second = m.CameraPipeline(profile, shared, base_conf=0.5, headless=True, disable_traffic_count=True)
    second.start()
    time.sleep(0.2)
    try:
        assert second.is_alive(), "same-port pipeline restart failed (port not released — regression)"
    finally:
        second.stop(timeout=3.0)
