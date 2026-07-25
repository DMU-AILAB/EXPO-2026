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
  ~/.python39/bin/python3.9 edgetpu_infer.py [conf_cane] [conf_person] [model_path] [input_size]
  conf_cane/conf_person은 yolo_postprocess.CLASS_NAMES 순서(white_cane, person)에 맞춘
  클래스별 신뢰도 임계값이다 — 이 서브프로세스는 기동 시 argv로 한 번만 값을 받고 그
  뒤로는 프레임 루프 동안 값을 바꿀 방법이 없다(stdin은 이미지 프레임 전용 채널이라
  런타임 갱신용 제어 메시지가 없음). 그래서 사람/지팡이 신뢰도를 바꾸려면
  camera_live_pi.py의 _CoralBackend.update_conf()가 이 프로세스를 통째로 재시작한다
  (드문 수동 조작이라 짧은 재시작 지연은 허용 가능한 트레이드오프로 판단).
  model_path/input_size 생략 시 white_cane_v2/640 기본값 사용 (camera_config.MODEL_VARIANTS
  참고 — camera_live_pi.py의 _CoralBackend가 카메라 프로필의 model_variant에 따라 실제로는
  항상 명시적으로 넘겨준다).
"""

from __future__ import annotations

import json
import signal
import struct
import sys
from pathlib import Path

import numpy as np
import tflite_runtime.interpreter as tflite

from yolo_postprocess import CLASS_NAMES, postprocess_multiclass, set_input, get_output


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

_DEFAULT_MODEL = Path(__file__).parent / "runs/white_cane_v2/weights" / "best_int8_edgetpu.tflite"
_DEFAULT_INPUT_SIZE = 640


def main() -> None:
    conf_cane   = float(sys.argv[1]) if len(sys.argv) > 1 else 0.25
    conf_person = float(sys.argv[2]) if len(sys.argv) > 2 else 0.25
    model_path  = Path(sys.argv[3]) if len(sys.argv) > 3 else _DEFAULT_MODEL
    input_size  = int(sys.argv[4]) if len(sys.argv) > 4 else _DEFAULT_INPUT_SIZE
    conf_thr    = dict(zip(CLASS_NAMES, (conf_cane, conf_person)))

    delegate = tflite.load_delegate("libedgetpu.so.1")
    interp   = tflite.Interpreter(
        model_path=str(model_path),
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

        set_input(interp, frame, input_size=input_size)
        interp.invoke()
        dets = postprocess_multiclass(get_output(interp), conf_thr, img_w, img_h)

        data = json.dumps(dets).encode()
        stdout.write(struct.pack(">I", len(data)) + data)
        stdout.flush()


if __name__ == "__main__":
    main()
