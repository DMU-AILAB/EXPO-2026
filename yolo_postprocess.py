"""yolo_postprocess.py — YOLOv8 TFLite/EdgeTPU 다중 클래스 후처리 공유 모듈.

`camera_live_pi.py`(TFLite CPU 백엔드)와 `edgetpu_infer.py`(Coral EdgeTPU
서브프로세스, 별도 Python 3.9 환경)가 동일한 후처리 로직을 각자 중복 보유하던
것을 이 모듈로 통합했다. numpy/cv2 외 의존성이 없어 두 환경 모두에서 그대로
import 가능하다.
"""

from __future__ import annotations

import cv2
import numpy as np

# datasets/data.yaml의 names 순서와 반드시 일치해야 한다 (인덱스 = class id).
CLASS_NAMES: tuple[str, ...] = ("white_cane", "person")
NMS_IOU_DEFAULT = 0.45
INPUT_SIZE = 640


def set_input(interpreter, frame: np.ndarray, input_size: int = INPUT_SIZE) -> None:
    """BGR 프레임을 YOLOv8 TFLite 입력 텐서에 맞게 전처리.

    export 툴체인에 따라 입력 레이아웃이 NHWC([1,H,W,3])거나 NCHW([1,3,H,W])일
    수 있다 — ultralytics 8.4.83부터 `.tflite` export가 옛 TensorFlow
    SavedModel(NHWC) 경로 대신 새 LiteRT/PyTorch 경로(NCHW)로 바뀌었기
    때문이다. 모델이 실제로 선언한 shape을 보고 자동으로 맞춘다.
    """
    inp     = interpreter.get_input_details()[0]
    dtype   = inp["dtype"]
    is_nchw = inp["shape"][1] == 3

    resized = cv2.resize(frame, (input_size, input_size))
    rgb     = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    blob    = rgb[np.newaxis]  # [1, H, W, 3]
    if is_nchw:
        blob = blob.transpose(0, 3, 1, 2)  # [1, 3, H, W]

    if dtype == np.float32:
        blob = blob.astype(np.float32) / 255.0
    elif dtype == np.int8:
        scale, zp = inp["quantization"]
        # 실측(Pi 4, EdgeTPU 컴파일 모델): scale≈1/255, zero_point=-128인 경우가 흔한데,
        # 이건 정규화([0,1]) 입력을 그대로 양자화한 것과 수학적으로 동일해서
        # uint8 값에서 128만 빼면 끝난다 — float32 변환/나눗셈 2회/clip을 전부
        # 건너뛸 수 있다 (프로파일링 결과 이 경로가 프레임당 ~30ms → ~수ms로 단축).
        # uint8 입력은 항상 [0,255]라 -128을 빼도 int8 범위를 벗어날 수 없어 clip도 불필요.
        if abs(scale - 1.0 / 255.0) < 1e-6 and zp == -128:
            blob = (blob.astype(np.int16) - 128).astype(np.int8)
        else:
            blob = np.clip(blob.astype(np.float32) / 255.0 / scale + zp,
                           -128, 127).astype(np.int8)
    # uint8 (0–255): 그대로 사용

    interpreter.set_tensor(inp["index"], blob)


def get_output(interpreter) -> np.ndarray:
    """출력 텐서를 float32로 반환 (INT8/UINT8 역양자화 자동 처리)."""
    out  = interpreter.get_output_details()[0]
    data = interpreter.get_tensor(out["index"])
    if out["dtype"] in (np.int8, np.uint8):
        scale, zp = out["quantization"]
        data = (data.astype(np.float32) - zp) * scale
    return data.astype(np.float32)


def postprocess_multiclass(
    output: np.ndarray,
    conf_thr: float | dict[str, float],
    img_w: int,
    img_h: int,
    class_names: tuple[str, ...] = CLASS_NAMES,
    nms_iou: float = NMS_IOU_DEFAULT,
) -> list[dict]:
    """YOLOv8 출력 [1, 4+nc, 8400] (또는 전치된 [1, 8400, 4+nc]) → 탐지 결과 리스트.

    nc=1인 현재 배포 모델(white_cane 전용)에도 그대로 동작한다 — 이 경우
    4번 컬럼 하나만 클래스 점수로 취급되어 기존 단일 클래스 로직과 동일하게
    작동한다.

    conf_thr는 스칼라(모든 클래스 동일) 또는 {class_name: threshold} 딕셔너리
    (클래스별 개별 임계값)를 받는다 — 예: 사람은 배경 오탐이 잦아 지팡이보다
    높은 임계값이 필요한 경우가 흔해서 클래스별로 다르게 튜닝할 수 있게 했다.
    """
    pred = output[0]
    if pred.shape[0] < pred.shape[1]:   # [4+nc, 8400] → [8400, 4+nc]
        pred = pred.T

    nc = len(class_names)
    if isinstance(conf_thr, dict):
        conf_arr = np.array([conf_thr[name] for name in class_names], dtype=np.float32)
    else:
        conf_arr = np.full(nc, conf_thr, dtype=np.float32)

    cls_scores = pred[:, 4:4 + nc]
    best_cls   = cls_scores.argmax(axis=1)
    best_score = cls_scores.max(axis=1)

    mask = best_score > conf_arr[best_cls]
    if not mask.any():
        return []

    pred, best_cls, best_score = pred[mask], best_cls[mask], best_score[mask]
    cx, cy, w, h = pred[:, 0], pred[:, 1], pred[:, 2], pred[:, 3]

    bx = (cx - w / 2) * img_w
    by = (cy - h / 2) * img_h
    bw = w * img_w
    bh = h * img_h

    boxes = np.stack([bx, by, bw, bh], axis=1).tolist()
    confs = best_score.tolist()

    # 클래스별로 개별 NMS 후 합친다 — 전체를 한 번에 NMS하면 클래스가 다른
    # 박스끼리(예: 사람과 지팡이) 서로를 억제해버릴 수 있기 때문이다.
    keep_idx: list[int] = []
    for c in range(nc):
        cls_idx = np.nonzero(best_cls == c)[0]
        if cls_idx.size == 0:
            continue
        sub_boxes = [boxes[i] for i in cls_idx]
        sub_confs = [confs[i] for i in cls_idx]
        nms_keep = cv2.dnn.NMSBoxes(sub_boxes, sub_confs, float(conf_arr[c]), nms_iou)
        if len(nms_keep):
            keep_idx.extend(int(cls_idx[k]) for k in np.asarray(nms_keep).flatten())

    return [
        {
            "bbox": [
                round(boxes[i][0]),
                round(boxes[i][1]),
                round(boxes[i][0] + boxes[i][2]),
                round(boxes[i][1] + boxes[i][3]),
            ],
            "conf":  round(confs[i], 4),
            "class": int(best_cls[i]),
            "label": class_names[int(best_cls[i])],
        }
        for i in keep_idx
    ]
