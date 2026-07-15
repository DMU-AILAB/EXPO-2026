"""
camera_live_pi.py — Raspberry Pi + Google Coral 실시간 탐지 뷰어

추론 백엔드 자동 선택 (우선순위):
  1. Google Coral Edge TPU  — tflite-runtime + Edge TPU 델리게이트 + _edgetpu.tflite
  2. TFLite INT8 CPU        — tflite-runtime + best_int8.tflite
  3. PyTorch fallback       — ultralytics + best.pt  (PC 호환)

카메라 자동 선택:
  Linux + 숫자 source → picamera2 (Pi Camera Module) → OpenCV 순 시도
  그 외(파일 경로, Windows/Mac) → OpenCV VideoCapture 직접 사용

표시 모드:
  기본값    : cv2.imshow 윈도우 (모니터 연결 필요)
  --headless: http://0.0.0.0:<PORT>/stream.mjpg  MJPEG HTTP 스트리밍

Coral 모델 컴파일 (아직 안 했다면):
  edgetpu_compiler runs/white_cane_v2/weights/best_int8.tflite
  → best_int8_edgetpu.tflite 생성 후 같은 폴더에 배치

사용법:
  python camera_live_pi.py
  python camera_live_pi.py --headless --port 8080
  python camera_live_pi.py --source 0 --conf 0.3
  python camera_live_pi.py --source video.mp4 --headless
"""

from __future__ import annotations

import argparse
import json
import platform
import signal
import struct
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np

from yolo_postprocess import postprocess_multiclass, set_input, get_output
from simple_tracker import SimpleTracker

try:
    from simulator.roi_manager import ROIManager
    from audio_trigger import StandaloneDispatcher, AudioPlayer
    _TRIGGER_AVAILABLE = True
except ImportError as _e:
    _TRIGGER_AVAILABLE = False
    print(f"[WARN] ROI/오디오 기능 비활성 (의존성 누락): {_e}")

try:
    from cane_person_assoc import CANE_CLASS_ID, PERSON_CLASS_ID, associate
    from foot_traffic_counter import FootTrafficCounter
    _TRAFFIC_AVAILABLE = True
except ImportError:
    CANE_CLASS_ID, PERSON_CLASS_ID = 0, 1
    _TRAFFIC_AVAILABLE = False

try:
    from gpiozero import LED as _GPIOLed
    _GPIO_AVAILABLE = True
except ImportError as _e:
    _GPIO_AVAILABLE = False
    print(f"[WARN] GPIO(gpiozero) 기능 비활성 (의존성 누락): {_e}")

# ── 경로 / 상수 ────────────────────────────────────────────────────
_WEIGHTS       = Path(__file__).parent / "runs/white_cane_v2/weights"
_EDGETPU_MODEL = _WEIGHTS / "best_int8_edgetpu.tflite"
_TFLITE_MODEL  = _WEIGHTS / "best_int8.tflite"
_PT_MODEL      = _WEIGHTS / "best.pt"

_EDGETPU_LIB = {
    "Linux":   "libedgetpu.so.1",
    "Darwin":  "libedgetpu.1.dylib",
    "Windows": "edgetpu.dll",
}


# ── 추론 백엔드 ────────────────────────────────────────────────────

class _CoralBackend:
    """Google Coral Edge TPU 백엔드 (Python 3.9 서브프로세스).

    libedgetpu v16.0 은 tflite_runtime 2.5.0.post1(cp39) 과만 안정적으로
    동작한다. Python 3.11+ 빌드는 partial delegation 모델에서 segfault 발생.
    메인 앱(Python 3.13)은 서브프로세스로 Python 3.9 워커를 기동한 뒤
    stdin/stdout 바이너리 프로토콜로 프레임을 교환한다.
    """

    _PY39   = Path.home() / ".python39" / "bin" / "python3.9"
    _WORKER = Path(__file__).parent / "edgetpu_infer.py"

    def __init__(self, conf: float) -> None:
        if not self._PY39.exists():
            raise FileNotFoundError(
                "~/.python39 없음 — make install-edgetpu-py39 실행 필요"
            )
        if not self._WORKER.exists():
            raise FileNotFoundError(f"워커 스크립트 없음: {self._WORKER}")
        self.conf = conf
        self._proc = subprocess.Popen(
            [str(self._PY39), str(self._WORKER), str(conf)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            bufsize=0,
        )
        ready = self._proc.stdout.readline()
        if ready.strip() != b"READY":
            self._proc.terminate()
            self._proc.wait()
            raise RuntimeError(f"EdgeTPU 워커 초기화 실패: {ready!r}")

    def predict(self, frame: np.ndarray) -> list[dict]:
        h, w = frame.shape[:2]
        self._proc.stdin.write(struct.pack(">II", h, w) + frame.tobytes())
        size_bytes = self._proc.stdout.read(4)
        if len(size_bytes) < 4:
            raise RuntimeError("EdgeTPU 워커가 예기치 않게 종료되었습니다")
        size = struct.unpack(">I", size_bytes)[0]
        return json.loads(self._proc.stdout.read(size))

    def close(self) -> None:
        """워커를 확실히 종료 — Coral 칩이 세션 종료를 인지하도록 정상 종료를 시도하고,
        응답이 없으면 SIGKILL로 강제 종료해 USB 핸들이 절대 남아있지 않게 한다.
        (예전엔 terminate() 후 wait(timeout=2)가 실패해도 그냥 넘어가서 워커가 좀비처럼
        남아 Coral USB를 계속 점유 — 다음 실행 때 델리게이트 초기화가 실패하고 물리적
        재연결 전까진 복구가 안 되는 원인이었다.)"""
        proc = getattr(self, "_proc", None)
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=2)
            return
        except subprocess.TimeoutExpired:
            pass
        proc.kill()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            print("[WARN] EdgeTPU 워커가 SIGKILL 후에도 종료되지 않음")

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


class _TFLiteBackend:
    """TFLite INT8 CPU 백엔드."""

    def __init__(self, conf: float) -> None:
        self.conf = conf
        try:
            import tflite_runtime.interpreter as tflite
        except ImportError:
            try:
                import ai_edge_litert.interpreter as tflite  # type: ignore[no-redef]
            except ImportError:
                import tensorflow.lite as tflite  # type: ignore[no-redef]

        self._interp = tflite.Interpreter(model_path=str(_TFLITE_MODEL), num_threads=4)
        self._interp.allocate_tensors()

    def predict(self, frame: np.ndarray) -> list[dict]:
        set_input(self._interp, frame)
        self._interp.invoke()
        h, w = frame.shape[:2]
        return postprocess_multiclass(get_output(self._interp), self.conf, w, h)

    def close(self) -> None:
        pass  # 서브프로세스/외부 장치 핸들 없음 — 정리할 게 없음


class _UltralyticsBackend:
    """PyTorch 기반 ultralytics 백엔드 (fallback)."""

    def __init__(self, conf: float) -> None:
        self.conf = conf
        from detect import WhiteCaneDetector  # noqa: PLC0415
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            device = "cpu"
        self._det = WhiteCaneDetector(model_path=_PT_MODEL, conf=conf, device=device)

    def predict(self, frame: np.ndarray) -> list[dict]:
        return self._det.predict(frame)

    def close(self) -> None:
        pass  # 서브프로세스/외부 장치 핸들 없음 — 정리할 게 없음


def build_backend(conf: float) -> _CoralBackend | _TFLiteBackend | _UltralyticsBackend:
    """사용 가능한 최선의 추론 백엔드를 자동 선택하여 반환."""

    # 1. Coral Edge TPU
    if _EDGETPU_MODEL.exists():
        try:
            backend = _CoralBackend(conf)
            print("[INFO] 백엔드: Google Coral Edge TPU")
            return backend
        except Exception as e:
            print(f"[WARN] Coral 초기화 실패: {e}")
            print("[WARN] TFLite CPU 백엔드로 대체합니다.")
    else:
        print(
            f"[WARN] Edge TPU 모델 없음: {_EDGETPU_MODEL}\n"
            "       edgetpu_compiler 로 컴파일 후 같은 폴더에 배치하세요."
        )

    # 2. TFLite INT8 CPU
    if _TFLITE_MODEL.exists():
        try:
            backend = _TFLiteBackend(conf)
            print("[INFO] 백엔드: TFLite INT8 (CPU)")
            return backend
        except Exception as e:
            print(f"[WARN] TFLite 초기화 실패: {e}")
            print("[WARN] PyTorch 백엔드로 대체합니다.")
    else:
        print(f"[WARN] TFLite 모델 없음: {_TFLITE_MODEL}")

    # 3. PyTorch fallback
    if _PT_MODEL.exists():
        try:
            backend = _UltralyticsBackend(conf)
            print("[INFO] 백엔드: PyTorch/ultralytics (fallback)")
            return backend
        except Exception as e:
            raise RuntimeError(f"PyTorch 백엔드 초기화 실패: {e}") from e

    raise RuntimeError(
        "사용 가능한 모델 파일을 찾을 수 없습니다.\n"
        f"  확인 경로: {_WEIGHTS}"
    )


# ── 카메라 소스 ────────────────────────────────────────────────────

class _Picamera2Source:
    def __init__(self, width: int = 640, height: int = 480) -> None:
        from picamera2 import Picamera2  # type: ignore[import]
        self._cam = Picamera2()
        # picamera2 naming is counter-intuitive: the "RGB888" format returns a
        # numpy array in B,G,R memory order — exactly what OpenCV expects.
        # ("BGR888" would return R,G,B order.) So NO conversion is needed here.
        cfg = self._cam.create_preview_configuration(
            main={"format": "RGB888", "size": (width, height)}
        )
        self._cam.configure(cfg)
        self._cam.start()
        time.sleep(0.5)  # 카메라 센서 워밍업

    def read(self) -> tuple[bool, np.ndarray]:
        frame = self._cam.capture_array()
        # Drop 4th channel if picamera2 returns XBGR/BGRX on some configurations
        if frame.ndim == 3 and frame.shape[2] == 4:
            frame = frame[:, :, :3]
        return True, frame

    def release(self) -> None:
        self._cam.stop()
        self._cam.close()


class _OpenCVSource:
    def __init__(self, source: str | int) -> None:
        src = int(source) if str(source).isdigit() else source
        self._cap = cv2.VideoCapture(src)
        if not self._cap.isOpened():
            raise RuntimeError(f"카메라/영상을 열 수 없습니다: {source}")

    def read(self) -> tuple[bool, np.ndarray]:
        return self._cap.read()

    def release(self) -> None:
        self._cap.release()


def build_camera(source: str) -> _Picamera2Source | _OpenCVSource:
    """카메라 소스를 자동 선택하여 반환."""
    is_index = str(source).isdigit()

    if is_index and platform.system() == "Linux":
        try:
            cam = _Picamera2Source()
            print("[INFO] 카메라: picamera2 (Pi Camera Module)")
            return cam
        except Exception as e:
            print(f"[WARN] picamera2 초기화 실패: {e}")
            print("[WARN] OpenCV VideoCapture로 대체합니다.")

    cam = _OpenCVSource(source)
    print(f"[INFO] 카메라: OpenCV VideoCapture (source={source})")
    return cam


# ── MJPEG HTTP 스트리밍 서버 ───────────────────────────────────────

class MJPEGServer:
    """스레드 안전 MJPEG 스트리밍 서버."""

    def __init__(self, port: int = 8080) -> None:
        self._port  = port
        self._jpeg: bytes = b""
        self._lock  = threading.Lock()
        self._httpd: ThreadingHTTPServer | None = None

    def push(self, frame: np.ndarray) -> None:
        """메인 루프에서 매 프레임마다 호출."""
        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        with self._lock:
            self._jpeg = buf.tobytes()

    def _handler_class(self):
        srv = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/stream.mjpg":
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header(
                    "Content-Type",
                    "multipart/x-mixed-replace; boundary=frame",
                )
                self.end_headers()
                try:
                    while True:
                        with srv._lock:
                            data = srv._jpeg
                        if data:
                            self.wfile.write(
                                b"--frame\r\n"
                                b"Content-Type: image/jpeg\r\n"
                                + f"Content-Length: {len(data)}\r\n\r\n".encode()
                                + data + b"\r\n"
                            )
                            self.wfile.flush()
                        time.sleep(0.033)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # 클라이언트가 연결을 끊은 경우

            def log_message(self, *_):
                pass  # HTTP 접근 로그 억제

        return _Handler

    def start(self) -> None:
        # 일반 HTTPServer는 싱글 스레드라 요청 하나(스트림은 무한 루프로 붙어있음)가
        # 스레드를 계속 물고 있으면 다른 뷰어(핫스팟 쪽 접속 등)가 영원히 대기하게 된다.
        # ThreadingHTTPServer로 접속마다 별도 스레드를 띄워 동시 접속을 지원한다.
        self._httpd = ThreadingHTTPServer(("", self._port), self._handler_class())
        self._httpd.daemon_threads = True
        t = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        t.start()
        print(f"[INFO] MJPEG 스트리밍 주소: http://0.0.0.0:{self._port}/stream.mjpg")

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()


# ── ROI 로드 / 그리기 ──────────────────────────────────────────────

def _load_rois(path: str) -> tuple:
    """JSON 설정 파일에서 ROIManager, debounce, cooldown, conf(신뢰도 임계값)를 로드."""
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    mgr = ROIManager()
    for r in cfg.get("rois", []):
        mgr.add_roi(
            name=r["name"],
            points=r["points"],
            priority=r.get("priority", 1),
            announcement_text=r.get("announcement_text", r.get("name", "")),
            audio_file=r.get("audio_file", ""),
        )
    print(f"[INFO] ROI {len(mgr.rois)}개 로드: {[r.name for r in mgr.rois]}")
    return mgr, cfg.get("debounce", 0.5), cfg.get("cooldown", 10.0), cfg.get("conf")


def _draw_rois(frame: np.ndarray, roi_manager: "ROIManager",
               dispatcher: "StandaloneDispatcher", now: float) -> None:
    """프레임에 ROI 폴리곤 + 이름 + 쿨다운 오버레이를 그린다."""
    fh, fw = frame.shape[:2]
    for roi in roi_manager.rois:
        pts = np.array(
            [[int(x * fw), int(y * fh)] for x, y in roi.points],
            dtype=np.int32,
        )
        if len(pts) < 3:
            continue
        remaining = dispatcher.cooldown_remaining(roi.name, now)
        color = (0, 0, 200) if remaining > 0 else roi.color

        overlay = frame.copy()
        cv2.fillPoly(overlay, [pts], color)
        cv2.addWeighted(overlay, 0.2, frame, 0.8, 0, frame)
        cv2.polylines(frame, [pts], isClosed=True, color=color, thickness=2)

        cx = int(np.mean(pts[:, 0]))
        cy = int(np.mean(pts[:, 1]))
        label = roi.name + (f" ({remaining:.1f}s)" if remaining > 0 else "")
        cv2.putText(frame, label, (cx - 40, cy),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, cv2.LINE_AA)


# ── 그리기 헬퍼 ────────────────────────────────────────────────────

def _draw_detections(frame: np.ndarray, detections: list[dict]) -> None:
    fh, fw = frame.shape[:2]
    for det in detections:
        x1, y1, x2, y2 = det["bbox"]
        # 좌표를 프레임 범위로 클램핑
        x1 = max(0, min(x1, fw - 1))
        y1 = max(0, min(y1, fh - 1))
        x2 = max(0, min(x2, fw - 1))
        y2 = max(0, min(y2, fh - 1))

        age   = det.get("age", 0)
        tid   = det.get("track_id", "")
        # 탐지 중: 초록, coasting(미탐지 유지): 노랑
        color = (0, 255, 0) if age == 0 else (0, 200, 255)

        label = f"#{tid} {det['conf']:.2f}"
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
        # 레이블이 프레임 위로 나가면 박스 아래에 표시
        label_y = y1 if y1 >= th + 6 else y2 + th + 6
        cv2.rectangle(frame, (x1, label_y - th - 6), (x1 + tw + 4, label_y), color, -1)
        cv2.putText(frame, label, (x1 + 2, label_y - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 1, cv2.LINE_AA)


# ── CLI ────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Raspberry Pi + Coral 실시간 흰 지팡이 탐지 뷰어"
    )
    p.add_argument("--source",   default="0",
                   help="웹캠 인덱스 또는 영상 파일 경로 (기본값: 0)")
    p.add_argument("--conf",     type=float, default=0.55,
                   help="신뢰도 임계값 0~1 (기본값: 0.55)")
    p.add_argument("--headless", action="store_true",
                   help="MJPEG 서버 모드로 실행 (모니터 없이 네트워크 스트리밍)")
    p.add_argument("--port",     type=int, default=8080,
                   help="MJPEG 서버 포트 (기본값: 8080, --headless 시 사용)")
    p.add_argument("--roi-config", default=None, metavar="PATH",
                   help="ROI 설정 JSON 경로 (없으면 ROI/오디오 기능 비활성)")
    p.add_argument("--status-led", type=int, default=None, metavar="GPIO_PIN",
                   help="탐지 루프 동작 확인용 LED GPIO 핀 (기본값: 비활성)")
    p.add_argument("--led-stall-sec", type=float, default=3.0, metavar="SEC",
                   help="이 시간 동안 프레임 처리가 없으면 문제로 간주해 LED 깜빡임 (기본값: 3.0)")
    p.add_argument("--traffic-db", default="foot_traffic.db", metavar="PATH",
                   help="유동인구 집계 sqlite 경로 (기본값: foot_traffic.db)")
    p.add_argument("--disable-traffic-count", action="store_true",
                   help="유동인구(사람 트래킹) 집계 비활성화")
    return p.parse_args()


def _led_watchdog(led: "_GPIOLed", heartbeat: dict, stop_flag: threading.Event,
                   stall_after: float, poll: float = 0.3) -> None:
    """평소엔 고정 점등, heartbeat 갱신이 stall_after 초 이상 끊기면 깜빡임으로 전환.

    메인 루프와 별도 스레드에서 돌기 때문에, 루프 자체가 멈춰도(추론 행 등)
    이 스레드는 계속 살아서 "문제 발생"을 LED로 표시할 수 있다.
    """
    blinking = False
    while not stop_flag.is_set():
        stale = (time.time() - heartbeat["t"]) > stall_after
        if stale and not blinking:
            led.blink(on_time=0.15, off_time=0.15)
            blinking = True
        elif not stale and blinking:
            led.on()
            blinking = False
        stop_flag.wait(poll)


# ── 메인 루프 ──────────────────────────────────────────────────────

def main() -> None:
    args = _parse_args()

    backend = build_backend(args.conf)
    camera  = build_camera(args.source)
    tracker = SimpleTracker()

    foot_counter = None
    if _TRAFFIC_AVAILABLE and not args.disable_traffic_count:
        foot_counter = FootTrafficCounter(args.traffic_db)

    # ROI + 오디오 초기화 (--roi-config 미지정 시 None)
    roi_manager = None
    dispatcher  = None
    audio_player = None
    if args.roi_config:
        if not _TRIGGER_AVAILABLE:
            print("[WARN] --roi-config 지정됐으나 ROI 모듈 로드 실패 — 무시")
        else:
            try:
                roi_manager, debounce, cooldown, conf = _load_rois(args.roi_config)
                dispatcher  = StandaloneDispatcher(debounce, cooldown)
                audio_player = AudioPlayer()
                if conf is not None:
                    backend.conf = conf
                    print(f"[INFO] 신뢰도 임계값: {conf} (rois.json 설정값 적용)")
            except Exception as exc:
                print(f"[WARN] ROI 설정 로드 실패: {exc} — ROI 기능 비활성")
                roi_manager = None

    mjpeg: MJPEGServer | None = None
    if args.headless:
        mjpeg = MJPEGServer(args.port)
        mjpeg.start()
    else:
        print("[INFO] 실시간 탐지 시작 — 'q' 키로 종료")

    # 동작 확인 LED — 평소엔 고정 점등, 탐지 루프가 멈추면(행/크래시) 워치독
    # 스레드가 감지해서 깜빡임으로 전환한다.
    status_led = None
    led_heartbeat = None
    led_watchdog_stop = None
    led_watchdog_thread = None
    if args.status_led is not None:
        if not _GPIO_AVAILABLE:
            print("[WARN] --status-led 지정됐으나 gpiozero 없음 — 무시")
        else:
            status_led = _GPIOLed(args.status_led)
            status_led.on()
            led_heartbeat = {"t": time.time()}
            led_watchdog_stop = threading.Event()
            led_watchdog_thread = threading.Thread(
                target=_led_watchdog,
                args=(status_led, led_heartbeat, led_watchdog_stop, args.led_stall_sec),
                daemon=True,
            )
            led_watchdog_thread.start()

    # roi_editor(같은 기기, 포트 5000)가 rois.json을 수정하면 재시작 없이 반영되도록
    # mtime을 주기적으로 확인 — 완전 로컬 동작이라 네트워크 폴링 없이 파일만 검사한다.
    # roi_manager 유무와 무관하게 항상 감시한다 — 시작 시점엔 rois.json이 없어서
    # ROI 기능이 비활성으로 시작했더라도, 이후 roi_editor에서 파일이 새로 생성되면
    # 재시작 없이 바로 활성화되어야 하기 때문이다 (예전엔 roi_manager is not None을
    # 조건에 걸어놔서, 시작 시 파일이 없으면 이후 생겨도 영영 인식하지 못했다).
    roi_mtime = 0.0
    if args.roi_config:
        try:
            roi_mtime = Path(args.roi_config).stat().st_mtime
        except OSError:
            roi_mtime = 0.0
    last_roi_check = time.time()
    ROI_CHECK_INTERVAL = 2.0

    stop_event = threading.Event()

    def _on_sigint(sig, frame):
        print("\n[INFO] 종료 신호 수신")
        stop_event.set()

    signal.signal(signal.SIGINT, _on_sigint)
    # systemctl stop/restart는 SIGTERM을 보낸다 (SIGINT 아님) — 이걸 처리하지 않으면
    # 아래 finally 블록(backend.close() 포함)이 실행되지 않고 프로세스가 즉시 죽는다.
    # Coral 워커 서브프로세스가 정상 종료 기회를 못 받아 USB 핸들을 계속 쥐고 있게 되고,
    # 다음 실행 때 EdgeTPU 델리게이트 초기화가 실패하는 원인이었다.
    signal.signal(signal.SIGTERM, _on_sigint)

    prev_t = time.time()
    try:
        while not stop_event.is_set():
            ok, frame = camera.read()
            if not ok:
                print("[INFO] 영상 종료 또는 카메라 연결 끊김")
                break

            try:
                dets = backend.predict(frame)
            except Exception as e:
                print(f"[ERROR] 추론 중 오류 발생: {e}")
                break

            tracks = tracker.update(dets)
            _draw_detections(frame, tracks)

            now    = time.time()
            fps    = 1.0 / (now - prev_t) if (now - prev_t) > 0 else 0.0
            prev_t = now

            if led_heartbeat is not None:
                led_heartbeat["t"] = now

            # 클래스별 분리 — ROI/오디오 트리거는 지팡이 트랙만, 유동인구
            # 집계는 사람 트랙만 대상으로 한다 (2-class 모델 기준).
            cane_tracks = [t for t in tracks if t["class"] == CANE_CLASS_ID]
            if foot_counter is not None:
                person_tracks   = [t for t in tracks if t["class"] == PERSON_CLASS_ID]
                cane_person_map = associate(tracks)
                foot_counter.update(person_tracks, cane_person_map, now)

            # ROI 설정 변경/신규 생성 감지 (roi_editor 저장 → 재시작 없이 자동 반영)
            if args.roi_config and _TRIGGER_AVAILABLE and now - last_roi_check >= ROI_CHECK_INTERVAL:
                last_roi_check = now
                try:
                    mtime = Path(args.roi_config).stat().st_mtime
                except FileNotFoundError:
                    mtime = None  # roi_editor에서 아직 저장 전 — 정상 상태, 경고 아님
                except OSError as exc:
                    print(f"[WARN] ROI 설정 확인 실패: {exc}")
                    mtime = roi_mtime

                if mtime is not None and mtime != roi_mtime:
                    roi_mtime = mtime
                    try:
                        roi_manager, debounce, cooldown, conf = _load_rois(args.roi_config)
                        dispatcher = StandaloneDispatcher(debounce, cooldown)
                        if audio_player is None:
                            audio_player = AudioPlayer()
                        if conf is not None:
                            backend.conf = conf
                        print(f"[INFO] ROI 설정 변경 감지 — 자동 반영 완료 (conf={conf if conf is not None else args.conf})")
                    except (json.JSONDecodeError, KeyError) as exc:
                        print(f"[WARN] ROI 설정 재로드 실패: {exc}")

            # ROI 판별 + 트리거 + 오디오
            if roi_manager is not None:
                fh, fw = frame.shape[:2]
                active: set[str] = set()
                for trk in cane_tracks:
                    x1, y1, x2, y2 = trk["bbox"]
                    cx_n = ((x1 + x2) / 2) / fw
                    cy_n = ((y1 + y2) / 2) / fh
                    roi = roi_manager.check(cx_n, cy_n)
                    if roi:
                        active.add(roi.name)
                        if dispatcher.on_detected(roi.name, now):
                            print(f"[TRIGGER] ROI={roi.name}  audio={roi.audio_file or '없음'}")
                            audio_player.play(roi.audio_file)
                for r in roi_manager.rois:
                    if r.name not in active:
                        dispatcher.on_not_detected(r.name)
                _draw_rois(frame, roi_manager, dispatcher, now)

            cv2.putText(frame, f"FPS: {fps:.1f}", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2, cv2.LINE_AA)

            if args.headless:
                assert mjpeg is not None
                mjpeg.push(frame)
            else:
                cv2.imshow("VisionGuide — White Cane Detection", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

    finally:
        camera.release()
        backend.close()
        if foot_counter is not None:
            foot_counter.close()
        if mjpeg:
            mjpeg.stop()
        if status_led is not None:
            if led_watchdog_stop is not None:
                led_watchdog_stop.set()
            if led_watchdog_thread is not None:
                led_watchdog_thread.join(timeout=1.0)
            status_led.off()
            status_led.close()
        if not args.headless:
            cv2.destroyAllWindows()
        print("[INFO] 종료 완료")


if __name__ == "__main__":
    main()
