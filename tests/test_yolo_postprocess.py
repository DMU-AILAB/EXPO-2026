"""yolo_postprocess.py 단위 테스트 — 특히 postprocess_multiclass의 클래스별(사람/지팡이)
신뢰도 임계값 필터링을 검증한다. 이전엔 이 모듈에 테스트가 전혀 없었다.
"""
import sys
from pathlib import Path

import numpy as np


from yolo_postprocess import CLASS_NAMES, postprocess_multiclass

IMG_W = IMG_H = 100


def _make_output(detections: list[tuple[float, float, float, float, float, float]]) -> np.ndarray:
    """detections: [(cx, cy, w, h, white_cane_score, person_score), ...] (정규화 0~1).

    실제 모델 출력 포맷 [1, 4+nc, N]을 흉내낸다 — N(앵커 수)이 4+nc(=6)보다 커야
    postprocess_multiclass의 전치(transpose) 휴리스틱이 올바르게 동작한다(실제 모델은
    N=8400개라 항상 성립하지만, 합성 테스트에선 패딩으로 N을 충분히 늘려줘야 한다).
    """
    n = max(len(detections), 8)
    nc = len(CLASS_NAMES)
    arr = np.zeros((4 + nc, n), dtype=np.float32)
    for i, (cx, cy, w, h, s0, s1) in enumerate(detections):
        arr[0, i], arr[1, i], arr[2, i], arr[3, i] = cx, cy, w, h
        arr[4, i], arr[5, i] = s0, s1
    return arr[np.newaxis]  # [1, 4+nc, N]


def test_scalar_threshold_applies_uniformly_to_all_classes():
    output = _make_output([
        (0.5, 0.5, 0.2, 0.2, 0.6, 0.1),   # white_cane, score 0.6
        (0.3, 0.3, 0.1, 0.1, 0.1, 0.5),   # person, score 0.5
    ])
    dets = postprocess_multiclass(output, 0.3, IMG_W, IMG_H)
    assert {d["class"] for d in dets} == {0, 1}


def test_per_class_dict_filters_person_out_when_below_its_own_threshold():
    output = _make_output([
        (0.5, 0.5, 0.2, 0.2, 0.6, 0.1),   # white_cane, score 0.6
        (0.3, 0.3, 0.1, 0.1, 0.1, 0.5),   # person, score 0.5
    ])
    conf = {"white_cane": 0.55, "person": 0.65}
    dets = postprocess_multiclass(output, conf, IMG_W, IMG_H)
    assert [d["class"] for d in dets] == [0]


def test_per_class_dict_filters_cane_out_when_below_its_own_threshold():
    output = _make_output([
        (0.5, 0.5, 0.2, 0.2, 0.6, 0.1),   # white_cane, score 0.6
        (0.3, 0.3, 0.1, 0.1, 0.1, 0.5),   # person, score 0.5
    ])
    conf = {"white_cane": 0.9, "person": 0.4}
    dets = postprocess_multiclass(output, conf, IMG_W, IMG_H)
    assert [d["class"] for d in dets] == [1]


def test_empty_result_when_all_below_threshold():
    output = _make_output([(0.5, 0.5, 0.2, 0.2, 0.2, 0.2)])
    dets = postprocess_multiclass(output, {"white_cane": 0.9, "person": 0.9}, IMG_W, IMG_H)
    assert dets == []


# ---------------------------------------------------------------------------
# letterbox 좌표 역보정
#
# 배포 추론이 종횡비를 뭉개는 스쿼시를 쓰고 있어 흰 지팡이(얇고 비스듬한 객체)가
# 학습 분포 밖으로 나가던 것을 레터박스로 고쳤다. set_input이 돌려준
# (scale, pad_x, pad_y)를 후처리에 넘겨 좌표를 원본 프레임으로 되돌리는데,
# 이 역보정이 틀리면 박스가 조용히 어긋난다(탐지 개수는 멀쩡해 보인다).
# ---------------------------------------------------------------------------
def _letterbox_params(img_w: int, img_h: int, size: int) -> tuple[float, float, float]:
    """set_input(letterbox=True)와 같은 규칙으로 (scale, pad_x, pad_y)를 만든다."""
    scale = size / max(img_h, img_w)
    nh, nw = round(img_h * scale), round(img_w * scale)
    return scale, (size - nw) / 2.0, (size - nh) / 2.0


def test_letterbox_roundtrip_recovers_original_box_on_wide_frame():
    """16:9 프레임의 알려진 박스가 레터박스를 거쳐도 제자리로 돌아와야 한다."""
    img_w, img_h, size = 1920, 1080, 320
    scale, pad_x, pad_y = _letterbox_params(img_w, img_h, size)

    # 원본 좌표계의 목표 박스 → 레터박스 입력 텐서의 정규화 좌표로 변환
    x1, y1, x2, y2 = 800.0, 500.0, 1000.0, 700.0
    cx = ((x1 + x2) / 2 * scale + pad_x) / size
    cy = ((y1 + y2) / 2 * scale + pad_y) / size
    bw = (x2 - x1) * scale / size
    bh = (y2 - y1) * scale / size

    dets = postprocess_multiclass(
        _make_output([(cx, cy, bw, bh, 0.9, 0.0)]), 0.3, img_w, img_h,
        letterbox=(scale, pad_x, pad_y))

    assert len(dets) == 1
    got = dets[0]["bbox"]
    assert got == [round(x1), round(y1), round(x2), round(y2)], got


def test_letterbox_none_keeps_legacy_squash_mapping():
    """letterbox=None이면 기존(정규화 × img_w/img_h) 계산이 그대로 유지된다 —
    하위호환이 깨지면 옛 호출부가 조용히 틀린 좌표를 받는다."""
    dets = postprocess_multiclass(
        _make_output([(0.5, 0.5, 0.2, 0.2, 0.9, 0.0)]), 0.3, 1920, 1080)
    assert dets[0]["bbox"] == [768, 432, 1152, 648], dets[0]["bbox"]


def test_squash_distorts_aspect_ratio_but_letterbox_preserves_it():
    """이 수정의 본질 — 스쿼시는 객체의 종횡비를 뭉갠다.

    모델이 정사각(w==h) 박스를 냈을 때, 레터박스 경로는 원본에서도 정사각으로
    복원되지만 스쿼시 경로는 16:9 비율만큼 납작해진다(384x384 vs 384x216).
    흰 지팡이처럼 얇고 비스듬한 객체가 학습 분포 밖으로 나가던 이유이자,
    두 경로를 섞어 쓰면 안 되는 이유다.
    """
    img_w, img_h, size = 1920, 1080, 320
    lb = _letterbox_params(img_w, img_h, size)
    out = _make_output([(0.5, 0.5, 0.2, 0.2, 0.9, 0.0)])

    a = postprocess_multiclass(out, 0.3, img_w, img_h, letterbox=lb)[0]["bbox"]
    b = postprocess_multiclass(out, 0.3, img_w, img_h)[0]["bbox"]

    assert (a[2] - a[0]) == (a[3] - a[1]), a          # 레터박스: 정사각 유지
    assert (b[2] - b[0]) > (b[3] - b[1]) * 1.7, b     # 스쿼시: 16:9만큼 납작
