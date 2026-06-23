"""
edgetpu_infer.py — Python 3.9 + tflite_runtime 2.5.0.post1 EdgeTPU 워커

이 스크립트는 ~/.python39/bin/python3.9 로 서브프로세스 실행된다.
libedgetpu v16.0 과 공식 호환되는 tflite_runtime 2.5.0.post1(cp39)을 사용해
partial delegation YOLO 모델의 segfault 문제를 우회한다.

stdin/stdout 바이너리 프로토콜:
  초기화: stdout 에 b"READY\\n" 출력
  입력:   8바이트 헤더 (struct ">II" = height, width) + height×width×3 BGR uint8 bytes
  출력:   4바이트 크기 (struct ">I") + JSON bytes  (탐지 결과 list[dict])

실행:
  ~/.python39/bin/python3.9 edgetpu_infer.py [conf_threshold]
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

import cv2
import numpy as np
import tflite_runtime.interpreter as tflite

_WEIGHTS       = Path(__file__).parent / "runs/white_cane_v1-2/weights"
_EDGETPU_MODEL = _WEIGHTS / "best_int8_edgetpu.tflite"
_INPUT_SIZE    = 640
_NMS_IOU       = 0.45


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
    # uint8 (0-255): 그대로 사용

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
                 img_w: int, img_h: int) -> list:
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


def main() -> None:
    conf = float(sys.argv[1]) if len(sys.argv) > 1 else 0.25

    delegate = tflite.load_delegate("libedgetpu.so.1")
    interp   = tflite.Interpreter(
        model_path=str(_EDGETPU_MODEL),
        experimental_delegates=[delegate],
    )
    interp.allocate_tensors()

    stdout = sys.stdout.buffer
    stdin  = sys.stdin.buffer

    stdout.write(b"READY\n")
    stdout.flush()

    while True:
        header = stdin.read(8)
        if len(header) < 8:
            break  # parent closed stdin → exit cleanly

        img_h, img_w = struct.unpack(">II", header)
        nbytes = img_h * img_w * 3
        raw    = stdin.read(nbytes)
        if len(raw) < nbytes:
            break

        frame = np.frombuffer(raw, dtype=np.uint8).reshape(img_h, img_w, 3)

        _set_input(interp, frame)
        interp.invoke()
        dets = _postprocess(_get_output(interp), conf, img_w, img_h)

        data = json.dumps(dets).encode()
        stdout.write(struct.pack(">I", len(data)) + data)
        stdout.flush()


if __name__ == "__main__":
    main()
