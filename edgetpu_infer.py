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
import signal
import struct
import sys
from pathlib import Path

import numpy as np
import tflite_runtime.interpreter as tflite

from yolo_postprocess import postprocess_multiclass, set_input, get_output


def _on_sigterm(signum, frame):
    """부모(camera_live_pi.py)가 종료될 때 SIGTERM으로 이 워커도 같이 종료된다.
    핸들러 없이 죽으면 stdin.read()에서 블록된 채로 강제 종료되어 tflite
    Interpreter/delegate 객체가 정상적으로 파괴되지 않고, Coral 칩이 세션 종료를
    인지하지 못한 채 USB 연결만 뚝 끊긴다 — 다음 실행 때 델리게이트 초기화 실패,
    물리적 재연결 전엔 복구가 안 되는 원인이었다. SystemExit을 직접 발생시켜
    (자동 EINTR 재시도로 넘어가지 않도록) 블로킹 read()를 확실히 끊고 정상
    인터프리터 종료 경로를 타게 해서 delegate/interpreter가 제대로 정리되게 한다."""
    raise SystemExit(0)


signal.signal(signal.SIGTERM, _on_sigterm)

_WEIGHTS       = Path(__file__).parent / "runs/white_cane_v2/weights"
_EDGETPU_MODEL = _WEIGHTS / "best_int8_edgetpu.tflite"


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

        set_input(interp, frame)
        interp.invoke()
        dets = postprocess_multiclass(get_output(interp), conf, img_w, img_h)

        data = json.dumps(dets).encode()
        stdout.write(struct.pack(">I", len(data)) + data)
        stdout.flush()


if __name__ == "__main__":
    main()
