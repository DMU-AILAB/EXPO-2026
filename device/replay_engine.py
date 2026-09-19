"""replay_engine.py — 저장된 영상을 **배포와 같은 경로**로 돌려 화면에서 확인한다.

지금까지 게이트 동작은 터미널 숫자(`eval_video_recall.py`)로만 볼 수 있었다. 그런데
**가상 지팡이 박스가 맞는 자리에 그려졌는지 같은 것은 수치로 판단할 수 없고**, ROI를
그려 놓고 "이 영상이라면 안내가 나갔을까"를 확인할 방법도 없었다. 이 모듈이 그 자리를
채운다 — roi_editor의 "검증" 탭이 이걸 띄운다.

**게이트 로직을 재현하지 않는다.** 추론은 `camera_live_pi.build_backend`, 판정은
`gate_chain.GateChain`, ROI 판별은 `ROIManager`, 안내 발사는 `StandaloneDispatcher`로
배포와 **같은 객체**를 같은 순서로 돌린다. 화면에 그리는 것도 배포의
`_draw_rois`/`_draw_detections`/`_draw_gate_debug`를 그대로 쓴다.

시각은 **영상 시간**(프레임 번호 ÷ fps)을 쓴다. 벽시계를 쓰면 디코딩 속도에 따라
래치·재식별·쿨다운 판정이 달라져 같은 영상에서 결과가 재현되지 않는다.

**오디오는 재생하지 않는다.** 기기에서는 실제 안내 서비스가 같은 사운드 장치를 쓰고
있어 검증 재생이 끼어들면 안 된다. 대신 발사 시점을 이벤트 목록으로 남긴다.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import cv2

from audio_trigger import StandaloneDispatcher
from gate_chain import GateChain

__all__ = ["ReplaySession"]

_STREAM_MAX_WIDTH = 960      # 스트리밍용 축소 폭. 추론은 원본 해상도로 한다.
_JPEG_QUALITY = 80


class ReplaySession:
    """영상 한 편을 백그라운드 스레드로 재생하며 주석 프레임을 만들어 둔다.

    `latest_jpeg`를 폴링해 MJPEG로 내보내면 된다. 한 번에 하나만 돌린다는 전제이며,
    호출부(roi_editor)가 그 관리를 맡는다.
    """

    def __init__(self, video: str | Path, weights_dir: str, *, conf: float = 0.55,
                 input_size: int = 320, backend: str = "tflite",
                 roi_manager=None, require_person: bool = True,
                 speed: float = 1.0, loop: bool = False,
                 debounce: float = 0.5, cooldown: float = 10.0,
                 debug_gates: bool = True) -> None:
        self.video = str(video)
        self.weights_dir = weights_dir
        self.conf = conf
        self.input_size = input_size
        self.backend_kind = backend
        self.roi_manager = roi_manager
        self.speed = max(0.1, speed)
        self.loop = loop
        self.debug_gates = debug_gates

        self._chain = GateChain(require_person=require_person)
        self._dispatcher = StandaloneDispatcher(debounce, cooldown)

        self._lock = threading.Lock()
        self._jpeg: bytes | None = None
        self._stop = threading.Event()
        # set = 일시정지. `_step`은 정지 상태에서 딱 한 프레임만 진행시킨다 —
        # 가상 지팡이 박스가 맞는 자리인지 같은 것은 멈춰 놓고 봐야 판단이 된다.
        self._paused = threading.Event()
        self._step = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: str | None = None
        self._done = False

        self._wall0 = 0.0        # 속도 기준 벽시계 — 일시정지만큼 뒤로 민다
        self.total_frames = 0
        self.frame_index = 0
        self.fps = 0.0
        self.events: list[dict] = []          # 안내 발사 기록
        self.counters = {"raw": 0, "gated": 0, "virtual": 0,
                         "latched": 0, "announcements": 0}
        # 래치는 "이번 프레임 값"이 아니라 **재생 내내 한 번이라도 래치된 보행자 수**를
        # 보여줘야 한다. 프레임 값만 쓰면 마지막 프레임에 아무도 없을 때 0으로 보인다.
        self._latched_ever: set[int] = set()

    # ------------------------------------------------------------------ #

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def set_paused(self, paused: bool) -> None:
        if paused:
            self._paused.set()
        else:
            self._paused.clear()

    def toggle_pause(self) -> bool:
        self.set_paused(not self._paused.is_set())
        return self._paused.is_set()

    def step_once(self) -> None:
        """일시정지 상태에서 한 프레임만 진행한다. 재생 중이면 아무 일도 없다."""
        if self._paused.is_set():
            self._step.set()

    def stop(self) -> None:
        self._stop.set()
        self._paused.clear()      # 정지 대기 중인 루프를 깨운다
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=3.0)

    @property
    def latest_jpeg(self) -> bytes | None:
        with self._lock:
            return self._jpeg

    def status(self) -> dict:
        return {
            "video": Path(self.video).name,
            "running": self._thread is not None and not self._done,
            "paused": self._paused.is_set(),
            "done": self._done,
            "error": self._error,
            "frame": self.frame_index,
            "total": self.total_frames,
            "fps": round(self.fps, 1),
            "conf": self.conf,
            "counters": dict(self.counters),
            "events": self.events[-30:],
        }

    # ------------------------------------------------------------------ #

    def _run(self) -> None:
        # 배포와 같은 백엔드 선택 경로. import를 여기서 하는 이유는 roi_editor가
        # 기동할 때마다 추론 스택을 끌어오지 않게 하기 위해서다 — 검증 탭을 쓰지
        # 않는 기기에서는 영영 필요 없다.
        try:
            from camera_live_pi import build_backend
        except Exception as exc:                      # noqa: BLE001
            self._error = f"추론 백엔드를 불러올 수 없습니다: {exc}"
            self._done = True
            return

        cap = cv2.VideoCapture(self.video)
        if not cap.isOpened():
            self._error = f"영상을 열 수 없습니다: {self.video}"
            self._done = True
            return
        self.total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        self.fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

        backend = None
        try:
            backend = build_backend(self.conf, prefer=self.backend_kind,
                                    weights_dir=self.weights_dir,
                                    input_size=self.input_size)
            self._loop_frames(cap, backend)
        except Exception as exc:                      # noqa: BLE001
            self._error = f"재생 중 오류: {exc}"
        finally:
            cap.release()
            if backend is not None:
                try:
                    backend.close()
                except Exception:                     # noqa: BLE001
                    pass
            self._done = True

    def _loop_frames(self, cap, backend) -> None:
        from camera_live_pi import (_draw_detections, _draw_gate_debug,
                                    _draw_rois, _filter_excluded)

        idx = 0
        self._wall0 = time.time()
        while not self._stop.is_set():
            if self._paused.is_set():
                self._wait_while_paused()
                if self._stop.is_set():
                    break

            ok, frame = cap.read()
            if not ok:
                if self.loop:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    idx = 0
                    self._wall0 = time.time()
                    continue
                break

            # 영상 시간을 쓴다 — 벽시계를 쓰면 디코딩 속도에 따라 래치·재식별·쿨다운
            # 판정이 달라져 같은 영상에서 결과가 재현되지 않는다.
            now = idx / self.fps if self.fps else float(idx)

            dets = backend.predict(frame)
            if any(d["label"] == "white_cane" for d in dets):
                self.counters["raw"] += 1
            # 제외구역은 트래킹 이전 raw detection 단계에서 거른다(배포와 같은 위치).
            dets = _filter_excluded(dets, self.roi_manager, frame)

            g = self._chain.step(dets, frame.shape[:2], now)
            if g.cane_tracks:
                self.counters["gated"] += 1
            if g.virtual:
                self.counters["virtual"] += 1
            self._latched_ever |= {e.entity_id for e in g.entities if e.is_cane_user}
            self.counters["latched"] = len(self._latched_ever)

            _draw_detections(frame, g.tracks)
            self._judge_rois(frame, g, now)
            if self.debug_gates:
                _draw_gate_debug(
                    frame, g.all_cane_tracks,
                    {t["track_id"] for t in g.cane_tracks}, g.moved_min,
                    g.with_person or self._chain.debug_with_person(g.tracks),
                    self._chain.require_person, g.entities, g.latched_ids)

            self._hud(frame, idx, now)
            self._publish(frame)
            self.frame_index = idx
            idx += 1

            # 재생 속도 맞추기 — 추론이 영상보다 느리면 그냥 최대 속도로 흐른다.
            target = self._wall0 + (idx / self.fps) / self.speed if self.fps else 0
            delay = target - time.time()
            if delay > 0:
                self._stop.wait(delay)

    def _wait_while_paused(self) -> None:
        """정지가 풀리거나 한 프레임 요청이 올 때까지 기다린다.

        멈춰 있던 시간만큼 속도 기준점(`_wall0`)을 밀어준다 — 안 밀면 재개 순간
        밀린 시간을 따라잡으려고 영상이 몰아쳐 흐른다.
        """
        paused_at = time.time()
        while self._paused.is_set() and not self._stop.is_set():
            if self._step.is_set():
                self._step.clear()
                break                       # 한 프레임만 진행하고 다시 멈춘다
            self._stop.wait(0.05)
        self._wall0 += time.time() - paused_at

    def _judge_rois(self, frame, g, now: float) -> None:
        """배포의 ROI 판정 루프와 같은 순서 — 하단 10% strip, ROI별 프레임당 1회."""
        if self.roi_manager is None:
            return
        fh, fw = frame.shape[:2]
        present: dict[str, set] = {}
        for subject, (x1, y1, x2, y2) in g.roi_targets:
            strip_h = (y2 - y1) * 0.10
            roi = self.roi_manager.check_region(
                x1 / fw, (y2 - strip_h) / fh, x2 / fw, y2 / fh)
            if roi:
                present.setdefault(roi.name, set()).add(subject)

        for r in self.roi_manager.rois:
            if r.zone_type == "exclude":
                continue
            subject = self._dispatcher.update(r.name, present.get(r.name, ()), now)
            if subject is None:
                continue
            # 실제 배포에서는 오디오 재생이 끝난 시각으로 쿨다운이 시작된다. 여기서는
            # 소리를 내지 않으므로 지금 시각으로 바로 풀어준다.
            self._dispatcher.update_last_triggered(r.name, now)
            self.counters["announcements"] += 1
            self.events.append({
                "t": round(now, 2), "frame": self.frame_index,
                "roi": r.name, "subject": str(subject),
                "audio": r.audio_file or "",
            })

        from camera_live_pi import _draw_rois
        _draw_rois(frame, self.roi_manager, self._dispatcher, now)

    def _hud(self, frame, idx: int, now: float) -> None:
        h, w = frame.shape[:2]
        cv2.rectangle(frame, (0, 0), (w, 34), (0, 0, 0), -1)
        # cv2.putText는 한글을 못 그린다(ROI 이름이 화면에서 깨지는 것과 같은 이유).
        txt = (f"REPLAY {Path(self.video).name}  f{idx}/{self.total_frames or '?'}  "
               f"t={now:5.1f}s  conf={self.conf:.2f}  "
               f"announce={self.counters['announcements']}")
        cv2.putText(frame, txt, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (255, 255, 255), 2, cv2.LINE_AA)

    def _publish(self, frame) -> None:
        if frame.shape[1] > _STREAM_MAX_WIDTH:
            scale = _STREAM_MAX_WIDTH / frame.shape[1]
            frame = cv2.resize(frame, (_STREAM_MAX_WIDTH,
                                       int(frame.shape[0] * scale)))
        ok, buf = cv2.imencode(".jpg", frame,
                               [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY])
        if ok:
            with self._lock:
                self._jpeg = buf.tobytes()
