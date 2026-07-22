"""yolo_postprocess.py 단위 테스트 — 특히 postprocess_multiclass의 클래스별(사람/지팡이)
신뢰도 임계값 필터링을 검증한다. 이전엔 이 모듈에 테스트가 전혀 없었다.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

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
