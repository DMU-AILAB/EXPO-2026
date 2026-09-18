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


def set_input(interpreter, frame: np.ndarray, input_size: int = INPUT_SIZE,
              letterbox: bool = True) -> tuple[float, float, float] | None:
    """BGR 프레임을 YOLOv8 TFLite 입력 텐서에 맞게 전처리.

    export 툴체인에 따라 입력 레이아웃이 NHWC([1,H,W,3])거나 NCHW([1,3,H,W])일
    수 있다 — ultralytics 8.4.83부터 `.tflite` export가 옛 TensorFlow
    SavedModel(NHWC) 경로 대신 새 LiteRT/PyTorch 경로(NCHW)로 바뀌었기
    때문이다. 모델이 실제로 선언한 shape을 보고 자동으로 맞춘다.

    **letterbox=True(기본값)는 종횡비를 보존**하고 남는 영역을 회색(114)으로
    채운다. 학습(ultralytics)이 레터박스를 쓰므로 추론도 같아야 한다. 예전에는
    `cv2.resize`로 정사각 스쿼시를 했는데, 16:9 프레임이 1:1로 눌리면 가로가
    1.78배 압축되어 **비스듬히 뻗은 가늘고 긴 흰 지팡이가 학습 분포 밖으로
    나갔다**. 실측(805프레임 실영상, v6 INT8, conf 0.25): 스쿼시는 지팡이를
    1프레임에서만 찾고 ROI 트리거가 한 번도 발동하지 않은 반면, 레터박스는
    108프레임에서 찾고 트리거가 발동했다.

    반환값 `(scale, pad_x, pad_y)`는 `postprocess_multiclass(letterbox=...)`에
    그대로 넘겨 좌표를 원본 프레임 기준으로 되돌리는 데 쓴다. letterbox=False면
    None을 반환하며, 이때 후처리는 기존 스쿼시 좌표 계산을 그대로 쓴다.
    """
    inp     = interpreter.get_input_details()[0]
    dtype   = inp["dtype"]
    is_nchw = inp["shape"][1] == 3

    if letterbox:
        h, w  = frame.shape[:2]
        scale = input_size / max(h, w)
        nh, nw = max(1, int(round(h * scale))), max(1, int(round(w * scale)))
        pad_x, pad_y = (input_size - nw) / 2.0, (input_size - nh) / 2.0
        resized = np.full((input_size, input_size, 3), 114, np.uint8)
        top, left = int(pad_y), int(pad_x)
        resized[top:top + nh, left:left + nw] = cv2.resize(frame, (nw, nh))
        lb = (scale, pad_x, pad_y)
    else:
        resized = cv2.resize(frame, (input_size, input_size))
        lb = None

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
    return lb


def get_output(interpreter) -> np.ndarray:
    """출력 텐서를 float32로 반환 (INT8/UINT8 역양자화 자동 처리)."""
    out  = interpreter.get_output_details()[0]
    data = interpreter.get_tensor(out["index"])
    if out["dtype"] in (np.int8, np.uint8):
        scale, zp = out["quantization"]
        data = (data.astype(np.float32) - zp) * scale
    return data.astype(np.float32)


def _input_size_from(img_w: int, img_h: int, scale: float,
                     pad_x: float, pad_y: float) -> float:
    """letterbox 파라미터에서 입력 텐서 한 변의 길이를 역산한다.

    set_input은 `size = scale*max(h,w) + 2*pad`가 성립하도록 패딩을 계산하므로,
    긴 변 쪽(패딩이 0에 가까운 쪽)으로 복원하면 반올림 오차가 가장 작다.
    호출부가 input_size를 또 넘기지 않아도 되게 하기 위한 헬퍼다.
    """
    return scale * max(img_w, img_h) + 2.0 * (pad_x if img_w >= img_h else pad_y)


def postprocess_multiclass(
    output: np.ndarray,
    conf_thr: float | dict[str, float],
    img_w: int,
    img_h: int,
    class_names: tuple[str, ...] = CLASS_NAMES,
    nms_iou: float = NMS_IOU_DEFAULT,
    letterbox: tuple[float, float, float] | None = None,
) -> list[dict]:
    """YOLOv8 출력 [1, 4+nc, 8400] (또는 전치된 [1, 8400, 4+nc]) → 탐지 결과 리스트.

    nc=1인 현재 배포 모델(white_cane 전용)에도 그대로 동작한다 — 이 경우
    4번 컬럼 하나만 클래스 점수로 취급되어 기존 단일 클래스 로직과 동일하게
    작동한다.

    conf_thr는 스칼라(모든 클래스 동일) 또는 {class_name: threshold} 딕셔너리
    (클래스별 개별 임계값)를 받는다 — 예: 사람은 배경 오탐이 잦아 지팡이보다
    높은 임계값이 필요한 경우가 흔해서 클래스별로 다르게 튜닝할 수 있게 했다.

    letterbox는 `set_input()`이 돌려준 `(scale, pad_x, pad_y)`다. 주어지면 패딩을
    빼고 스케일을 되돌려 원본 프레임 좌표를 만든다. None이면 입력이 정사각으로
    스쿼시됐다고 보고 기존 계산(정규화 좌표 × img_w/img_h)을 그대로 쓴다 —
    둘을 섞으면 박스가 조용히 어긋나므로 set_input의 반환값을 그대로 넘길 것.
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

    if letterbox is None:
        bx = (cx - w / 2) * img_w
        by = (cy - h / 2) * img_h
        bw = w * img_w
        bh = h * img_h
    else:
        # 모델 출력은 입력 텐서(정사각 input_size) 기준 정규화 좌표다.
        # 픽셀로 되돌린 뒤 패딩을 빼고 스케일을 나눠 원본 프레임 좌표로 만든다.
        scale, pad_x, pad_y = letterbox
        size = _input_size_from(img_w, img_h, scale, pad_x, pad_y)
        bx = ((cx - w / 2) * size - pad_x) / scale
        by = ((cy - h / 2) * size - pad_y) / scale
        bw = w * size / scale
        bh = h * size / scale

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
