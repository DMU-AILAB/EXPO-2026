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
import json
import platform
import signal
import struct
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import cv2
import numpy as np

try:
    from simulator.roi_manager import ROIManager
    from audio_trigger import StandaloneDispatcher, AudioPlayer
    _TRIGGER_AVAILABLE = True
except ImportError as _e:
    _TRIGGER_AVAILABLE = False
    print(f"[WARN] ROI/오디오 기능 비활성 (의존성 누락): {_e}")

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

    bx = (cx - w / 2) * img_w
    by = (cy - h / 2) * img_h
    bw = w * img_w
    bh = h * img_h

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

    def __del__(self) -> None:
        proc = getattr(self, "_proc", None)
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
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
        self._httpd = HTTPServer(("", self._port), self._handler_class())
        t = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        t.start()
        print(f"[INFO] MJPEG 스트리밍 주소: http://0.0.0.0:{self._port}/stream.mjpg")

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()


# ── 객체 트래커 ────────────────────────────────────────────────────

class SimpleTracker:
    """IoU 기반 단순 객체 트래커.

    매칭된 트랙: EMA 스무딩으로 bbox 떨림 제거, age 초기화
    미탐지 트랙: max_age 프레임 동안 마지막 bbox 유지 (coasting)
    새 탐지:     신규 트랙 생성
    """

    def __init__(self, max_age: int = 10, min_iou: float = 0.3,
                 ema_alpha: float = 0.6) -> None:
        self.max_age  = max_age
        self.min_iou  = min_iou
        self.alpha    = ema_alpha   # 높을수록 새 탐지에 빠르게 반응
        self._tracks: list[dict] = []
        self._next_id = 0

    @staticmethod
    def _iou(a: list, b: list) -> float:
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        ix1 = max(ax1, bx1); iy1 = max(ay1, by1)
        ix2 = min(ax2, bx2); iy2 = min(ay2, by2)
        inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
        if inter == 0:
            return 0.0
        return inter / ((ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter)

    def update(self, detections: list[dict]) -> list[dict]:
        """탐지 결과를 받아 트랙 목록을 갱신하고 반환."""
        matched_det: set[int] = set()
        matched_trk: set[int] = set()

        # 탐지-트랙 greedy IoU 매칭
        for di, det in enumerate(detections):
            best_iou, best_ti = self.min_iou, -1
            for ti, trk in enumerate(self._tracks):
                if ti in matched_trk:
                    continue
                iou = self._iou(det["bbox"], trk["bbox"])
                if iou > best_iou:
                    best_iou, best_ti = iou, ti
            if best_ti >= 0:
                matched_det.add(di)
                matched_trk.add(best_ti)
                a = self.alpha
                old = self._tracks[best_ti]["bbox"]
                new = det["bbox"]
                self._tracks[best_ti]["bbox"] = [
                    round(a * new[i] + (1 - a) * old[i]) for i in range(4)
                ]
                self._tracks[best_ti]["age"]  = 0
                self._tracks[best_ti]["conf"] = det["conf"]

        # 미매칭 탐지 → 신규 트랙 생성
        for di, det in enumerate(detections):
            if di not in matched_det:
                self._tracks.append({
                    "track_id": self._next_id,
                    "bbox":     det["bbox"][:],
                    "conf":     det["conf"],
                    "class":    det["class"],
                    "label":    det["label"],
                    "age":      0,
                })
                self._next_id += 1

        # 미매칭 트랙 age 증가
        for ti in range(len(self._tracks)):
            if ti not in matched_trk:
                self._tracks[ti]["age"] += 1

        # max_age 초과 트랙 제거
        self._tracks = [t for t in self._tracks if t["age"] <= self.max_age]

        return [dict(t) for t in self._tracks]


# ── ROI 로드 / 그리기 ──────────────────────────────────────────────

def _load_rois(path: str) -> tuple:
    """JSON 설정 파일에서 ROIManager, debounce, cooldown 을 로드."""
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
    return mgr, cfg.get("debounce", 0.5), cfg.get("cooldown", 10.0)


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
    return p.parse_args()


# ── 메인 루프 ──────────────────────────────────────────────────────

def main() -> None:
    args = _parse_args()

    backend = build_backend(args.conf)
    camera  = build_camera(args.source)
    tracker = SimpleTracker()

    # ROI + 오디오 초기화 (--roi-config 미지정 시 None)
    roi_manager = None
    dispatcher  = None
    audio_player = None
    if args.roi_config:
        if not _TRIGGER_AVAILABLE:
            print("[WARN] --roi-config 지정됐으나 ROI 모듈 로드 실패 — 무시")
        else:
            try:
                roi_manager, debounce, cooldown = _load_rois(args.roi_config)
                dispatcher  = StandaloneDispatcher(debounce, cooldown)
                audio_player = AudioPlayer()
            except Exception as exc:
                print(f"[WARN] ROI 설정 로드 실패: {exc} — ROI 기능 비활성")
                roi_manager = None

    mjpeg: MJPEGServer | None = None
    if args.headless:
        mjpeg = MJPEGServer(args.port)
        mjpeg.start()
    else:
        print("[INFO] 실시간 탐지 시작 — 'q' 키로 종료")

    # roi_editor(같은 기기, 포트 5000)가 rois.json을 수정하면 재시작 없이 반영되도록
    # mtime을 주기적으로 확인 — 완전 로컬 동작이라 네트워크 폴링 없이 파일만 검사한다.
    roi_mtime = 0.0
    if args.roi_config and roi_manager is not None:
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

            # ROI 설정 변경 감지 (roi_editor 저장 → 재시작 없이 자동 반영)
            if args.roi_config and roi_manager is not None and now - last_roi_check >= ROI_CHECK_INTERVAL:
                last_roi_check = now
                try:
                    mtime = Path(args.roi_config).stat().st_mtime
                    if mtime != roi_mtime:
                        roi_mtime = mtime
                        roi_manager, debounce, cooldown = _load_rois(args.roi_config)
                        dispatcher = StandaloneDispatcher(debounce, cooldown)
                        print("[INFO] ROI 설정 변경 감지 — 자동 반영 완료")
                except (OSError, json.JSONDecodeError, KeyError) as exc:
                    print(f"[WARN] ROI 설정 재로드 실패: {exc}")

            # ROI 판별 + 트리거 + 오디오
            if roi_manager is not None:
                fh, fw = frame.shape[:2]
                active: set[str] = set()
                for trk in tracks:
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
        if mjpeg:
            mjpeg.stop()
        if not args.headless:
            cv2.destroyAllWindows()
        print("[INFO] 종료 완료")


if __name__ == "__main__":
    main()
