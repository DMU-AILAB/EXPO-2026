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
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np

from camera_config import CameraProfile, load_camera_config, validate_camera_config
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


def build_backend(conf: float, prefer: str = "auto") -> _CoralBackend | _TFLiteBackend | _UltralyticsBackend:
    """추론 백엔드를 선택하여 반환.

    prefer="auto"(기본값)는 기존 Coral→TFLite→PyTorch 캐스케이드 그대로 동작한다.
    "tflite"/"pytorch"를 지정하면 해당 티어로 바로 시작한다 — 카메라가 여러 대인데 물리
    Coral 액셀러레이터가 1개뿐일 때, 한 카메라는 edgetpu로 다른 카메라는 tflite/pytorch로
    고정 배정하기 위함이다. "edgetpu"를 지정해도 초기화 실패 시엔 기존처럼 하위 티어로
    폴백한다 (하드 실패보다 저하된 성능으로라도 동작하는 게 안전 기능 특성상 낫다는
    기존 설계 원칙 유지).
    """
    if prefer == "tflite":
        if not _TFLITE_MODEL.exists():
            raise RuntimeError(f"TFLite 모델 없음: {_TFLITE_MODEL}")
        backend = _TFLiteBackend(conf)
        print("[INFO] 백엔드: TFLite INT8 (CPU) (지정됨)")
        return backend

    if prefer == "pytorch":
        if not _PT_MODEL.exists():
            raise RuntimeError(f"PyTorch 모델 없음: {_PT_MODEL}")
        backend = _UltralyticsBackend(conf)
        print("[INFO] 백엔드: PyTorch/ultralytics (지정됨)")
        return backend

    # "auto" 또는 "edgetpu" — 기존 캐스케이드 그대로 (edgetpu 지정 시에도 실패하면 폴백)

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
    def __init__(self, width: int = 640, height: int = 480, camera_num: int = 0) -> None:
        from picamera2 import Picamera2  # type: ignore[import]
        # camera_num은 듀얼 CSI 카메라(카메라 멀티플렉서 HAT 등)를 위한 것 — 이 저장소에서
        # 검증된 조합은 CSI 카메라 1대 + USB 웹캠 1대뿐이라 실기기 미검증 상태.
        self._cam = Picamera2(camera_num=camera_num)
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


def build_camera(source: str, backend: str = "auto") -> _Picamera2Source | _OpenCVSource:
    """카메라 소스를 선택하여 반환.

    backend="auto"(기본값)는 기존 동작 그대로: Linux + 숫자 source면 picamera2를 먼저
    시도하고 실패 시 OpenCV로 폴백한다. backend="opencv"/"picamera2"를 명시하면 해당
    백엔드를 강제한다 — CSI 카메라 + USB 웹캠을 함께 쓸 때, 숫자 source인 USB 웹캠이
    picamera2에 가로채이는 것을 막기 위함(이전엔 강제할 방법이 없었음).
    """
    if backend == "opencv":
        cam = _OpenCVSource(source)
        print(f"[INFO] 카메라: OpenCV VideoCapture (source={source}, 강제 지정)")
        return cam

    if backend == "picamera2":
        cam = _Picamera2Source(camera_num=int(source) if str(source).isdigit() else 0)
        print("[INFO] 카메라: picamera2 (Pi Camera Module, 강제 지정)")
        return cam

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


# ── 회전 ───────────────────────────────────────────────────────────

_ROTATE_MAP = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}


def _apply_rotation(frame: np.ndarray, degrees: int) -> np.ndarray:
    """카메라 read() 직후, 추론 이전에 적용 — 이후 모든 소비 지점이 매 프레임 frame.shape를
    다시 읽으므로 90/270도로 가로세로가 바뀌어도 별도 수정 없이 일관되게 동작한다.
    """
    flag = _ROTATE_MAP.get(degrees % 360)
    return cv2.rotate(frame, flag) if flag is not None else frame


def _apply_channel_swap(frame: np.ndarray, enabled: bool) -> np.ndarray:
    """R/B 채널 반전 — picamera2 "RGB888" 포맷은 CSI 센서에서는 이미 BGR 순서로
    나오지만(주석 참고), libcamera의 uvcvideo(USB UVC) 경로를 타는 카메라는 실측 결과
    R/B가 뒤바뀌어 나왔다. 하드웨어/드라이버 조합마다 달라 자동 판별 대신 카메라
    프로필의 swap_rb 토글로 카메라별로 켜고 끌 수 있게 한다.
    """
    # copy()로 연속 메모리 배열을 만든다 — 이후 cv2.rectangle 등으로 이 프레임에
    # 직접 그리기(in-place) 때문에, 반전된(non-contiguous) 뷰 그대로 두면 안 된다.
    return frame[:, :, ::-1].copy() if enabled else frame


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
            # shutdown()은 serve_forever() 루프만 멈추고 리스닝 소켓은 열어둔 채로 남긴다 —
            # server_close()를 호출하지 않으면 포트가 계속 점유돼서, 같은 포트로 새
            # MJPEGServer를 다시 bind하려 할 때(카메라 프로필 변경으로 파이프라인이
            # 재생성될 때) "Address already in use"로 실패한다.
            self._httpd.server_close()


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
            zone_type=r.get("zone_type", "trigger"),
        )
    print(f"[INFO] ROI {len(mgr.rois)}개 로드: {[r.name for r in mgr.rois]}")
    return mgr, cfg.get("debounce", 0.5), cfg.get("cooldown", 10.0), cfg.get("conf")


def _filter_excluded(dets: list[dict], roi_manager: "ROIManager | None",
                      frame: np.ndarray) -> list[dict]:
    """제외구역(zone_type="exclude") 안에 bbox 중심이 있는 detection을 트래킹 이전에
    걸러낸다 — 지팡이/사람 클래스 구분 없이 전부 적용. 트래킹 이후에 거르면 트랙이 구역
    경계에서 깜빡이는 문제가 있어(EMA 스무딩/coasting 때문), 반드시 raw detection 단계에서
    필터링한다.
    """
    if roi_manager is None:
        return dets
    fh, fw = frame.shape[:2]
    return [
        d for d in dets
        if not roi_manager.is_excluded(
            ((d["bbox"][0] + d["bbox"][2]) / 2) / fw,
            ((d["bbox"][1] + d["bbox"][3]) / 2) / fh,
        )
    ]


def _draw_rois(frame: np.ndarray, roi_manager: "ROIManager",
               dispatcher: "StandaloneDispatcher", now: float) -> None:
    """프레임에 ROI 폴리곤 + 쿨다운 오버레이를 그린다.

    ROI 이름(사용자가 입력한 한글 등)은 일부러 안 그린다 — cv2.putText는
    Hershey 폰트만 지원해 유니코드가 "???"로 깨진다. PIL 기반 렌더링으로 고칠
    수도 있지만 프레임마다 폰트 렌더링을 추가하는 비용이 있어(Pi CPU가 이미
    듀얼카메라로 빠듯함), 이름은 ROI 에디터 웹 UI(캔버스 텍스트라 문제 없음)
    에서만 보이게 하고 여기서는 쿨다운 남은 시간(숫자라 안 깨짐)만 표시한다.
    """
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

        if remaining > 0:
            cx = int(np.mean(pts[:, 0]))
            cy = int(np.mean(pts[:, 1]))
            cv2.putText(frame, f"{remaining:.1f}s", (cx - 20, cy),
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

        label = f"{det.get('label', '?')} #{tid} {det['conf']:.2f}"
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
                   help="웹캠 인덱스 또는 영상 파일 경로 (기본값: 0, --camera-config 미지정 시에만 사용)")
    p.add_argument("--conf",     type=float, default=0.55,
                   help="신뢰도 임계값 0~1 (기본값: 0.55)")
    p.add_argument("--headless", action="store_true",
                   help="MJPEG 서버 모드로 실행 (모니터 없이 네트워크 스트리밍)")
    p.add_argument("--port",     type=int, default=8080,
                   help="MJPEG 서버 포트 (기본값: 8080, --headless 시 사용, --camera-config 미지정 시에만 사용)")
    p.add_argument("--roi-config", default=None, metavar="PATH",
                   help="ROI 설정 JSON 경로 (없으면 ROI/오디오 기능 비활성, --camera-config 미지정 시에만 사용)")
    p.add_argument("--camera-config", default=None, metavar="PATH",
                   help="다중 카메라 프로필 JSON 경로 — 지정하면 카메라 1대(위 --source 등)가 아니라 "
                        "이 파일에 정의된 카메라들을 각각 독립 파이프라인으로 동시 구동한다")
    p.add_argument("--status-led", type=int, default=None, metavar="GPIO_PIN",
                   help="탐지 루프 동작 확인용 LED GPIO 핀 (기본값: 비활성)")
    p.add_argument("--led-stall-sec", type=float, default=3.0, metavar="SEC",
                   help="이 시간 동안 프레임 처리가 없으면 문제로 간주해 LED 깜빡임 (기본값: 3.0)")
    p.add_argument("--traffic-db", default="foot_traffic.db", metavar="PATH",
                   help="유동인구 집계 sqlite 경로 (기본값: foot_traffic.db, --camera-config 미지정 시에만 사용)")
    p.add_argument("--disable-traffic-count", action="store_true",
                   help="유동인구(사람 트래킹) 집계 비활성화")
    return p.parse_args()


def _led_watchdog(led: "_GPIOLed", heartbeat: dict, stop_flag: threading.Event,
                   stall_after: float, poll: float = 0.3) -> None:
    """평소엔 고정 점등, heartbeat 갱신이 stall_after 초 이상 끊기면 깜빡임으로 전환.

    메인 루프와 별도 스레드에서 돌기 때문에, 루프 자체가 멈춰도(추론 행 등)
    이 스레드는 계속 살아서 "문제 발생"을 LED로 표시할 수 있다. 카메라가 여러 대면
    heartbeat는 "적어도 하나의 파이프라인이 살아있음"을 의미한다 — 카메라 중 하나만
    죽고 나머지가 정상이면 LED는 평소와 다르게 표시되지 않으니, 개별 카메라 상태는
    로그(`[ERROR][<camera-id>] ...`)로 확인해야 한다.
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


# ── 카메라 파이프라인 ──────────────────────────────────────────────

ROI_CHECK_INTERVAL = 2.0

# 지팡이 트랙이 이만큼 연속으로 거의 안 움직이면(SimpleTracker.static_frames)
# 배경 오탐지(케이블/문틀 경계선 등)로 간주해 ROI 트리거 대상에서 제외한다.
# 실측 FPS(~8~9)에서 대략 3초 정도에 해당 — 사람이 잠시 멈춰 서서 지팡이를
# 짚고 있는 정상적인 상황보다는 넉넉하게 잡았다.
STATIC_CANE_SUPPRESS_FRAMES = 24

# 파이프라인이 (설정 변경이 아니라) 예기치 않게 죽었을 때 재시작을 시도하는 최소
# 간격 — 예: 카메라 여러 대가 Coral USB 동글 하나를 동시에 열려다 충돌해서 한쪽이
# 죽는 경우, 계속 실패하는 원인이 바로 안 없어지면 재시작이 빠르게 반복되는(크래시
# 루프) 것을 막기 위한 백오프.
RESTART_BACKOFF_SEC = 5.0


@dataclass
class SharedResources:
    """여러 CameraPipeline이 공유하는 자원 — 프로세스당 1개씩만 존재해야 한다."""
    audio_player: "AudioPlayer | None"
    status_led: "_GPIOLed | None"
    led_heartbeat: dict
    stop_event: threading.Event


class CameraPipeline:
    """카메라 1대에 대한 완전한 탐지 파이프라인 (카메라→회전→추론→제외구역 필터→트래킹→
    유동인구 집계→ROI 판별→오디오 트리거→드로우→스트리밍). 자체 스레드에서 독립 실행되며,
    카메라 여러 대를 동시에 쓸 때는 이 클래스를 여러 개 인스턴스화해서 각각 start()한다.

    스레드로 병렬 실행하는 이유: TFLite/EdgeTPU invoke()나 OpenCV 호출은 네이티브 코드라
    GIL을 상당 부분 해제하므로 실질적으로 병렬 처리되고(MJPEGServer도 이미 동일 전제로
    ThreadingHTTPServer를 씀), Coral 백엔드는 애초에 별도 OS 서브프로세스라 스레딩 모델과
    무관하게 병렬이다. 대신 한 파이프라인 스레드에서 네이티브 크래시가 나면(세그폴트 등)
    프로세스 전체가 죽어 다른 카메라도 같이 내려갈 수 있다는 트레이드오프가 있다 — Pi
    실기기 테스트에서 이게 실제로 재현되면 멀티프로세싱으로 격상을 검토해야 한다.
    """

    def __init__(self, profile: CameraProfile, shared: SharedResources, base_conf: float,
                 headless: bool, disable_traffic_count: bool) -> None:
        self.profile = profile
        self.shared = shared
        self.base_conf = base_conf
        self.headless = headless
        self.disable_traffic_count = disable_traffic_count
        self._thread: threading.Thread | None = None
        self._local_stop = threading.Event()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=f"cam-{self.profile.id}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._local_stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        profile = self.profile
        tag = profile.id

        # 아래 setup 단계 중 어디서든 실패해도 finally에서 "이미 만들어진 것만" 정리할 수
        # 있도록 전부 None으로 시작한다 — 예전엔 backend/camera/mjpeg 생성이 이 try/finally
        # 바깥에 있어서, 예를 들어 mjpeg.start()가 포트 충돌로 실패하면 스레드가 처리되지
        # 않은 예외로 죽고 camera.release()/backend.close()가 전혀 호출되지 않았다
        # (카메라 프로필을 바꿔 파이프라인이 재생성될 때 실제로 발생한 버그).
        backend = None
        camera = None
        mjpeg: MJPEGServer | None = None
        foot_counter = None

        try:
            try:
                backend = build_backend(self.base_conf, prefer=profile.inference_backend)
            except Exception as e:
                print(f"[ERROR][{tag}] 추론 백엔드 초기화 실패: {e}")
                return

            try:
                camera = build_camera(profile.source, backend=profile.backend)
            except Exception as e:
                print(f"[ERROR][{tag}] 카메라 초기화 실패: {e}")
                return

            tracker = SimpleTracker()

            if _TRAFFIC_AVAILABLE and not self.disable_traffic_count:
                foot_counter = FootTrafficCounter(profile.traffic_db)

            roi_manager = None
            dispatcher = None
            if profile.roi_config:
                if not _TRIGGER_AVAILABLE:
                    print(f"[WARN][{tag}] roi_config 지정됐으나 ROI 모듈 로드 실패 — 무시")
                else:
                    try:
                        roi_manager, debounce, cooldown, conf = _load_rois(profile.roi_config)
                        dispatcher = StandaloneDispatcher(debounce, cooldown)
                        if conf is not None:
                            backend.conf = conf
                            print(f"[INFO][{tag}] 신뢰도 임계값: {conf} (rois.json 설정값 적용)")
                    except Exception as exc:
                        print(f"[WARN][{tag}] ROI 설정 로드 실패: {exc} — ROI 기능 비활성")
                        roi_manager = None

            if self.headless:
                mjpeg = MJPEGServer(profile.port)
                try:
                    mjpeg.start()
                except OSError as e:
                    print(f"[ERROR][{tag}] MJPEG 서버 바인드 실패(포트 {profile.port}): {e}")
                    return
            else:
                print(f"[INFO][{tag}] 실시간 탐지 시작 — 'q' 키로 종료")

            # roi_editor가 이 카메라의 roi_config를 수정하면 재시작 없이 반영되도록 mtime을
            # 주기적으로 확인 (기존 단일 카메라 로직과 동일 — 카메라별로 각자 폴링한다).
            roi_mtime = 0.0
            if profile.roi_config:
                try:
                    roi_mtime = Path(profile.roi_config).stat().st_mtime
                except OSError:
                    roi_mtime = 0.0
            last_roi_check = time.time()

            prev_t = time.time()
            while not (self._local_stop.is_set() or self.shared.stop_event.is_set()):
                ok, frame = camera.read()
                if not ok:
                    print(f"[INFO][{tag}] 영상 종료 또는 카메라 연결 끊김")
                    break

                frame = _apply_rotation(frame, profile.rotation)
                frame = _apply_channel_swap(frame, profile.swap_rb)

                try:
                    dets = backend.predict(frame)
                except Exception as e:
                    print(f"[ERROR][{tag}] 추론 중 오류 발생: {e}")
                    break

                # 지형지물 오탐지 방지용 제외구역 — 트래킹 이전에 raw detection 단계에서
                # 걸러낸다 (트랙 생성 이후 거르면 구역 경계에서 트랙이 깜빡이는 문제가 있음).
                dets = _filter_excluded(dets, roi_manager, frame)

                tracks = tracker.update(dets)
                _draw_detections(frame, tracks)

                now    = time.time()
                fps    = 1.0 / (now - prev_t) if (now - prev_t) > 0 else 0.0
                prev_t = now

                self.shared.led_heartbeat["t"] = now

                # 클래스별 분리 — ROI/오디오 트리거는 지팡이 트랙만, 유동인구
                # 집계는 사람 트랙만 대상으로 한다 (2-class 모델 기준).
                # 배경의 케이블/문틀 경계선 같은 고정 오탐지 대상은 지팡이와 달리 절대
                # 움직이지 않는다 — static_frames가 임계값을 넘은 트랙은 ROI 트리거
                # 대상에서 제외한다(화면 표시는 그대로 두어 디버깅은 가능하게 함).
                cane_tracks = [
                    t for t in tracks
                    if t["class"] == CANE_CLASS_ID
                    and t.get("static_frames", 0) < STATIC_CANE_SUPPRESS_FRAMES
                ]
                if foot_counter is not None:
                    person_tracks   = [t for t in tracks if t["class"] == PERSON_CLASS_ID]
                    cane_person_map = associate(tracks)
                    foot_counter.update(person_tracks, cane_person_map, now)

                # ROI 설정 변경/신규 생성 감지 (roi_editor 저장 → 재시작 없이 자동 반영)
                if profile.roi_config and _TRIGGER_AVAILABLE and now - last_roi_check >= ROI_CHECK_INTERVAL:
                    last_roi_check = now
                    try:
                        mtime = Path(profile.roi_config).stat().st_mtime
                    except FileNotFoundError:
                        mtime = None  # roi_editor에서 아직 저장 전 — 정상 상태, 경고 아님
                    except OSError as exc:
                        print(f"[WARN][{tag}] ROI 설정 확인 실패: {exc}")
                        mtime = roi_mtime

                    if mtime is not None and mtime != roi_mtime:
                        roi_mtime = mtime
                        try:
                            roi_manager, debounce, cooldown, conf = _load_rois(profile.roi_config)
                            dispatcher = StandaloneDispatcher(debounce, cooldown)
                            if conf is not None:
                                backend.conf = conf
                            print(f"[INFO][{tag}] ROI 설정 변경 감지 — 자동 반영 완료 "
                                  f"(conf={conf if conf is not None else self.base_conf})")
                        except (json.JSONDecodeError, KeyError) as exc:
                            print(f"[WARN][{tag}] ROI 설정 재로드 실패: {exc}")

                # ROI 판별 + 트리거 + 오디오 (audio_player는 여러 카메라가 공유 —
                # 겹쳐 재생 대신 큐에 쌓였다가 순서대로 재생된다)
                if roi_manager is not None:
                    fh, fw = frame.shape[:2]
                    active: set[str] = set()
                    for trk in cane_tracks:
                        x1, y1, x2, y2 = trk["bbox"]
                        # 바운딩 박스 하단 10% 구간(지팡이 끝이 바닥에 닿는 지점)으로 ROI
                        # 교차 판정 — 박스 전체 중심점은 손으로 쥔 위치까지 포함해 실제
                        # 접지 지점과 어긋날 수 있다 (simulator/app.py와 동일 로직).
                        strip_h = (y2 - y1) * 0.10
                        roi = roi_manager.check_region(
                            x1 / fw, (y2 - strip_h) / fh,
                            x2 / fw,  y2 / fh,
                        )
                        if roi:
                            active.add(roi.name)
                            if dispatcher.on_detected(roi.name, now):
                                print(f"[TRIGGER][{tag}] ROI={roi.name}  audio={roi.audio_file or '없음'}")
                                if self.shared.audio_player is not None:
                                    self.shared.audio_player.play(roi.audio_file)
                    for r in roi_manager.rois:
                        if r.name not in active:
                            dispatcher.on_not_detected(r.name)
                    _draw_rois(frame, roi_manager, dispatcher, now)

                cv2.putText(frame, f"[{tag}] FPS: {fps:.1f}", (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2, cv2.LINE_AA)

                if self.headless:
                    assert mjpeg is not None
                    mjpeg.push(frame)
                else:
                    cv2.imshow(f"VisionGuide — {tag}", frame)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        self.shared.stop_event.set()
                        break

        finally:
            # setup 도중 실패했을 수 있어(backend/camera/mjpeg 중 일부만 만들어진 채
            # return된 경우) 각각 None이 아닐 때만 정리한다.
            if camera is not None:
                camera.release()
            if backend is not None:
                backend.close()
            if foot_counter is not None:
                foot_counter.close()
            if mjpeg is not None:
                mjpeg.stop()
            if not self.headless:
                cv2.destroyWindow(f"VisionGuide — {tag}")
            print(f"[INFO][{tag}] 파이프라인 종료 완료")


def _reconcile_pipelines(pipelines: dict[str, CameraPipeline], new_profiles: list[CameraProfile],
                          shared: SharedResources, base_conf: float, headless: bool,
                          disable_traffic_count: bool) -> None:
    """camera_config.json 변경 감지 시 호출 — enabled=false/삭제된 카메라는 정지하고,
    새로 추가되거나 구조적 필드(backend/source/rotation/inference_backend/roi_config/port 등)가
    바뀐 카메라는 정지 후 새 프로필로 재생성한다. 실행 중인 루프 상태를 부분적으로
    mutate하지 않는다 — 더 단순하고 안전하며, 대가는 그 카메라만 잠깐(수 초) 스트림이
    끊기는 정도다.
    """
    new_by_id = {p.id: p for p in new_profiles}

    for cam_id in list(pipelines.keys()):
        new_profile = new_by_id.get(cam_id)
        if new_profile is None or not new_profile.enabled:
            print(f"[INFO][{cam_id}] 카메라 비활성화/삭제 감지 — 파이프라인 정지")
            pipelines.pop(cam_id).stop()

    for profile in new_profiles:
        if not profile.enabled:
            continue
        existing = pipelines.get(profile.id)
        if existing is None:
            print(f"[INFO][{profile.id}] 신규 카메라 활성화 — 파이프라인 시작")
        elif existing.profile != profile:
            print(f"[INFO][{profile.id}] 카메라 설정 변경 감지 — 파이프라인 재시작")
            existing.stop()
        else:
            continue
        pipeline = CameraPipeline(profile, shared, base_conf, headless, disable_traffic_count)
        pipeline.start()
        pipelines[profile.id] = pipeline


# ── 메인 (슈퍼바이저) ──────────────────────────────────────────────

def main() -> None:
    args = _parse_args()

    audio_player = AudioPlayer() if _TRIGGER_AVAILABLE else None

    # 동작 확인 LED — 여러 카메라가 있어도 하나만 존재 (heartbeat는 "적어도 하나의
    # 파이프라인이 살아있음"을 의미하도록 의미가 바뀐다 — _led_watchdog 참고).
    status_led = None
    led_heartbeat = {"t": time.time()}
    led_watchdog_stop = None
    led_watchdog_thread = None
    if args.status_led is not None:
        if not _GPIO_AVAILABLE:
            print("[WARN] --status-led 지정됐으나 gpiozero 없음 — 무시")
        else:
            status_led = _GPIOLed(args.status_led)
            status_led.on()
            led_watchdog_stop = threading.Event()
            led_watchdog_thread = threading.Thread(
                target=_led_watchdog,
                args=(status_led, led_heartbeat, led_watchdog_stop, args.led_stall_sec),
                daemon=True,
            )
            led_watchdog_thread.start()

    stop_event = threading.Event()
    shared = SharedResources(audio_player=audio_player, status_led=status_led,
                             led_heartbeat=led_heartbeat, stop_event=stop_event)

    def _on_sigint(sig, frame):
        print("\n[INFO] 종료 신호 수신")
        stop_event.set()

    signal.signal(signal.SIGINT, _on_sigint)
    # systemctl stop/restart는 SIGTERM을 보낸다 (SIGINT 아님) — 이걸 처리하지 않으면
    # 아래 finally 블록(backend.close() 포함)이 실행되지 않고 프로세스가 즉시 죽는다.
    # Coral 워커 서브프로세스가 정상 종료 기회를 못 받아 USB 핸들을 계속 쥐고 있게 되고,
    # 다음 실행 때 EdgeTPU 델리게이트 초기화가 실패하는 원인이었다.
    signal.signal(signal.SIGTERM, _on_sigint)

    # camera_config.json이 있으면 그 프로필들로, 없으면 기존 CLI 인자 그대로 단일
    # 카메라(레거시 모드)를 구성한다 — 레거시 단일카메라 설치는 마이그레이션 불필요.
    cfg_path = Path(args.camera_config) if args.camera_config else None
    profiles = load_camera_config(cfg_path) if cfg_path is not None else []
    if profiles:
        errors = validate_camera_config(profiles)
        if errors:
            for e in errors:
                print(f"[ERROR] camera_config 유효성 오류: {e}")
            raise SystemExit(1)
    else:
        profiles = [CameraProfile(
            id="legacy", enabled=True, backend="auto", source=args.source,
            rotation=0, inference_backend="auto", roi_config=args.roi_config or "",
            port=args.port, traffic_db=args.traffic_db,
        )]

    pipelines: dict[str, CameraPipeline] = {}
    for profile in profiles:
        if not profile.enabled:
            continue
        pipeline = CameraPipeline(profile, shared, args.conf, args.headless, args.disable_traffic_count)
        pipeline.start()
        pipelines[profile.id] = pipeline

    if not pipelines:
        print("[WARN] 활성화된 카메라가 없습니다 — 종료")
        stop_event.set()

    cfg_mtime = None
    if cfg_path is not None:
        try:
            cfg_mtime = cfg_path.stat().st_mtime
        except OSError:
            cfg_mtime = None

    last_restart: dict[str, float] = {}

    try:
        while not stop_event.is_set():
            if cfg_path is not None:
                try:
                    mtime = cfg_path.stat().st_mtime
                except OSError:
                    mtime = None
                if mtime is not None and mtime != cfg_mtime:
                    cfg_mtime = mtime
                    new_profiles = load_camera_config(cfg_path)
                    errors = validate_camera_config(new_profiles)
                    if errors:
                        for e in errors:
                            print(f"[WARN] camera_config 갱신 무시 — 유효성 오류: {e}")
                    else:
                        _reconcile_pipelines(pipelines, new_profiles, shared, args.conf,
                                              args.headless, args.disable_traffic_count)

            # 예기치 않게 죽은 파이프라인 자동 재시작 — camera_config.json 변경으로
            # 의도적으로 stop()된 카메라는 위 _reconcile_pipelines가 이미 pipelines
            # 딕셔너리에서 제거하므로, 여기 남아있는데 죽어있는 항목은 전부 "의도치
            # 않은 크래시"로 간주해도 안전하다 (예: 다른 카메라가 Coral USB 동글을
            # 동시에 열려다 충돌해서 죽는 경우 — 이전엔 한 번 죽으면 영영 복구가
            # 안 됐다).
            now = time.time()
            for cam_id, pipeline in list(pipelines.items()):
                if pipeline.is_alive():
                    continue
                if now - last_restart.get(cam_id, 0.0) < RESTART_BACKOFF_SEC:
                    continue
                print(f"[WARN][{cam_id}] 파이프라인이 예기치 않게 종료됨 — 재시작 시도")
                last_restart[cam_id] = now
                new_pipeline = CameraPipeline(pipeline.profile, shared, args.conf,
                                               args.headless, args.disable_traffic_count)
                new_pipeline.start()
                pipelines[cam_id] = new_pipeline

            stop_event.wait(2.0)
    finally:
        for pipeline in pipelines.values():
            pipeline.stop()
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
