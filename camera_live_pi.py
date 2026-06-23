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
  edgetpu_compiler runs/white_cane_v1-2/weights/best_int8.tflite
  → best_int8_edgetpu.tflite 생성 후 같은 폴더에 배치

사용법:
  python camera_live_pi.py
  python camera_live_pi.py --headless --port 8080
  python camera_live_pi.py --source 0 --conf 0.3
  python camera_live_pi.py --source video.mp4 --headless
"""

from __future__ import annotations

import argparse
import platform
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import cv2
import numpy as np

# ── 경로 / 상수 ────────────────────────────────────────────────────
_WEIGHTS       = Path(__file__).parent / "runs/white_cane_v1-2/weights"
_EDGETPU_MODEL = _WEIGHTS / "best_int8_edgetpu.tflite"
_TFLITE_MODEL  = _WEIGHTS / "best_int8.tflite"
_PT_MODEL      = _WEIGHTS / "best.pt"
_INPUT_SIZE    = 640
_NMS_IOU       = 0.45

_EDGETPU_LIB = {
    "Linux":   "libedgetpu.so.1",
    "Darwin":  "libedgetpu.1.dylib",
    "Windows": "edgetpu.dll",
}


# ── TFLite 공통 입출력 헬퍼 ────────────────────────────────────────

def _set_input(interpreter, frame: np.ndarray) -> None:
    """BGR 프레임을 YOLOv8 TFLite 입력 텐서에 맞게 전처리."""
    inp   = interpreter.get_input_details()[0]
    dtype = inp["dtype"]

    resized = cv2.resize(frame, (_INPUT_SIZE, _INPUT_SIZE))
    rgb     = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    blob    = rgb[np.newaxis]  # [1, 640, 640, 3]

    if dtype == np.float32:
        blob = blob.astype(np.float32) / 255.0
    elif dtype == np.int8:
        scale, zp = inp["quantization"]
        blob = np.clip(blob.astype(np.float32) / 255.0 / scale + zp,
                       -128, 127).astype(np.int8)
    # uint8 (0–255): 그대로 사용

    interpreter.set_tensor(inp["index"], blob)


def _get_output(interpreter) -> np.ndarray:
    """출력 텐서를 float32로 반환 (INT8/UINT8 역양자화 자동 처리)."""
    out  = interpreter.get_output_details()[0]
    data = interpreter.get_tensor(out["index"])
    if out["dtype"] in (np.int8, np.uint8):
        scale, zp = out["quantization"]
        data = (data.astype(np.float32) - zp) * scale
    return data.astype(np.float32)


def _postprocess(output: np.ndarray, conf_thr: float,
                 img_w: int, img_h: int) -> list[dict]:
    """YOLOv8 출력 [1,5,8400] or [1,8400,5] → 탐지 결과 리스트."""
    pred = output[0]
    if pred.shape[0] < pred.shape[1]:   # [5, 8400] → [8400, 5]
        pred = pred.T

    scores = pred[:, 4]
    mask   = scores > conf_thr
    if not mask.any():
        return []

    pred, scores = pred[mask], scores[mask]
    cx, cy, w, h = pred[:, 0], pred[:, 1], pred[:, 2], pred[:, 3]

    sx, sy = img_w / _INPUT_SIZE, img_h / _INPUT_SIZE
    bx = (cx - w / 2) * sx
    by = (cy - h / 2) * sy
    bw = w * sx
    bh = h * sy

    boxes = np.stack([bx, by, bw, bh], axis=1).tolist()
    confs = scores.tolist()
    idxs  = cv2.dnn.NMSBoxes(boxes, confs, conf_thr, _NMS_IOU)

    if not len(idxs):
        return []
    idxs = np.asarray(idxs).flatten()

    return [
        {
            "bbox":  [
                round(boxes[i][0]),
                round(boxes[i][1]),
                round(boxes[i][0] + boxes[i][2]),
                round(boxes[i][1] + boxes[i][3]),
            ],
            "conf":  round(confs[i], 4),
            "class": 0,
            "label": "white_cane",
        }
        for i in idxs
    ]


# ── 추론 백엔드 ────────────────────────────────────────────────────

class _CoralBackend:
    """Google Coral Edge TPU 백엔드."""

    def __init__(self, conf: float) -> None:
        self.conf = conf
        try:
            import tflite_runtime.interpreter as tflite
        except ImportError:
            import tensorflow.lite as tflite  # type: ignore[no-redef]

        lib      = _EDGETPU_LIB.get(platform.system(), "libedgetpu.so.1")
        delegate = tflite.load_delegate(lib)
        self._interp = tflite.Interpreter(
            model_path=str(_EDGETPU_MODEL),
            experimental_delegates=[delegate],
        )
        self._interp.allocate_tensors()

    def predict(self, frame: np.ndarray) -> list[dict]:
        _set_input(self._interp, frame)
        self._interp.invoke()
        h, w = frame.shape[:2]
        return _postprocess(_get_output(self._interp), self.conf, w, h)


class _TFLiteBackend:
    """TFLite INT8 CPU 백엔드."""

    def __init__(self, conf: float) -> None:
        self.conf = conf
        try:
            import tflite_runtime.interpreter as tflite
        except ImportError:
            import tensorflow.lite as tflite  # type: ignore[no-redef]

        self._interp = tflite.Interpreter(model_path=str(_TFLITE_MODEL))
        self._interp.allocate_tensors()

    def predict(self, frame: np.ndarray) -> list[dict]:
        _set_input(self._interp, frame)
        self._interp.invoke()
        h, w = frame.shape[:2]
        return _postprocess(_get_output(self._interp), self.conf, w, h)


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
        cfg = self._cam.create_preview_configuration(
            main={"format": "BGR888", "size": (width, height)}
        )
        self._cam.configure(cfg)
        self._cam.start()
        time.sleep(0.5)  # 카메라 센서 워밍업

    def read(self) -> tuple[bool, np.ndarray]:
        return True, self._cam.capture_array()

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
        self._httpd: HTTPServer | None = None

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
                                b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                                + data + b"\r\n"
                            )
                        time.sleep(0.033)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # 클라이언트가 연결을 끊은 경우

            def log_message(self, *_):
                pass  # HTTP 접근 로그 억제

        return _Handler

    def start(self) -> None:
        self._httpd = HTTPServer(("", self._port), self._handler_class())
        t = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        t.start()
        print(f"[INFO] MJPEG 스트리밍 주소: http://0.0.0.0:{self._port}/stream.mjpg")

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()


# ── 그리기 헬퍼 ────────────────────────────────────────────────────

def _draw_detections(frame: np.ndarray, detections: list[dict]) -> None:
    for det in detections:
        x1, y1, x2, y2 = det["bbox"]
        label = f"{det['label']} {det['conf']:.2f}"
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
        cv2.rectangle(frame, (x1, y1 - th - 6), (x1 + tw + 4, y1), (0, 255, 0), -1)
        cv2.putText(frame, label, (x1 + 2, y1 - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 1, cv2.LINE_AA)


# ── CLI ────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Raspberry Pi + Coral 실시간 흰 지팡이 탐지 뷰어"
    )
    p.add_argument("--source",   default="0",
                   help="웹캠 인덱스 또는 영상 파일 경로 (기본값: 0)")
    p.add_argument("--conf",     type=float, default=0.25,
                   help="신뢰도 임계값 0~1 (기본값: 0.25)")
    p.add_argument("--headless", action="store_true",
                   help="MJPEG 서버 모드로 실행 (모니터 없이 네트워크 스트리밍)")
    p.add_argument("--port",     type=int, default=8080,
                   help="MJPEG 서버 포트 (기본값: 8080, --headless 시 사용)")
    return p.parse_args()


# ── 메인 루프 ──────────────────────────────────────────────────────

def main() -> None:
    args = _parse_args()

    backend = build_backend(args.conf)
    camera  = build_camera(args.source)

    mjpeg: MJPEGServer | None = None
    if args.headless:
        mjpeg = MJPEGServer(args.port)
        mjpeg.start()
    else:
        print("[INFO] 실시간 탐지 시작 — 'q' 키로 종료")

    stop_event = threading.Event()

    def _on_sigint(sig, frame):
        print("\n[INFO] 종료 신호 수신")
        stop_event.set()

    signal.signal(signal.SIGINT, _on_sigint)

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

            _draw_detections(frame, dets)

            now   = time.time()
            fps   = 1.0 / (now - prev_t) if (now - prev_t) > 0 else 0.0
            prev_t = now
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
        if mjpeg:
            mjpeg.stop()
        if not args.headless:
            cv2.destroyAllWindows()
        print("[INFO] 종료 완료")


if __name__ == "__main__":
    main()
