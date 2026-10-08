"""얼굴 모자이크 — 눈에 보이는 출력(스트림·검증 재생·썸네일)에서만 얼굴을 가린다.

탐지·안내는 **원본 프레임**으로 끝낸 뒤에 적용한다. 모자이크를 먼저 하면 사람 탐지와
지팡이 연관 판정이 흔들려 안전 기능(음성 안내)의 성능이 떨어지기 때문이다.

방식은 사람 박스의 **상단 N%** 를 픽셀화하는 것이다. 얼굴 검출기와 달리 뒷모습·측면·
마스크 착용·작은 얼굴에도 같은 비율로 머리 전체가 덮인다. 대가는 사람 탐지가 놓친
프레임에서 얼굴이 노출된다는 것이라, 사라진 박스도 `hold_sec` 동안 마스크를 유지한다.

★ 유지 시간은 프레임이 아니라 **초**다. 프레임 단위 상수는 촬영 fps에 묶여 평가 영상과
Pi에서 같은 값이 다른 시간을 뜻한다(`pedestrian_entity`의 시간 상수와 같은 원칙).
"""
from __future__ import annotations

from typing import Iterable, Sequence

import cv2
import numpy as np

DEFAULT_RATIO = 0.25          # 박스 높이 중 가릴 상단 비율 — 서 있는 성인은 머리가 약 1/7~1/8
MIN_RATIO, MAX_RATIO = 0.05, 0.6
HEAD_W_RATIO = 0.7            # 가림 높이의 하한 = 박스 폭 × 이 값 (머리 크기는 키가 아니라 어깨너비에 비례)
# 서 있는 성인(h ≈ 2.5~3w)은 0.25h ≈ 0.7w라 이 하한이 닿지 않아 종전과 같다. 앉거나 박스가
# 넓으면(h/w 작음) 고정 비율로는 얼굴이 마스크 아래로 빠지므로 비율이 자동으로 커진다.
# 0.7은 일반 체형 추정값이지 이 데이터로 맞춘 값이 아니다 — 앉은 사람 영상으로 확인할 것.
HOLD_SEC = 0.5                # 사람 박스가 **탐지에서 끊긴 뒤** 마지막 위치에 마스크를 유지하는 시간
# (걷는 사람은 1초면 마지막 위치와 실제 위치가 꽤 벌어져, 길게 잡아도 소용이 줄어든다)
MARGIN_X = 0.0                # 좌우 마진(박스 폭 대비) — 0이면 박스 폭 그대로
MARGIN_TOP = 0.0              # 위쪽 마진(박스 높이 대비) — 0이면 박스 위 경계 그대로
# 마진을 0으로 둔 것은 사용자 결정이다(가림 범위를 박스 크기에 맞춤). 대가: 박스가 실제 얼굴과
# 어긋나는 프레임(빠른 이동·박스 지터·모자)에서 가장자리가 노출될 수 있다 — 새는 것이
# 보이면 이 두 값을 0.03~0.05로 올릴 것.
BLOCKS_ACROSS = 6             # 가린 영역의 가로 블록 수 — 너무 많으면 복원될 수 있다
MIN_BLOCK_PX = 6


def clamp_ratio(ratio: float) -> float:
    return max(MIN_RATIO, min(MAX_RATIO, float(ratio)))


def mask_region(box: Sequence[float], ratio: float,
                frame_hw: tuple[int, int]) -> tuple[int, int, int, int] | None:
    """사람 박스 `(x1,y1,x2,y2)`에서 가릴 영역을 프레임 안으로 잘라 돌려준다.

    `ratio`는 **최소** 비율이다 — 박스가 폭에 비해 낮으면(앉은 사람) 폭 기준으로 늘어난다.
    """
    fh, fw = frame_hw
    x1, y1, x2, y2 = (float(v) for v in box[:4])
    w, h = x2 - x1, y2 - y1
    if w <= 0 or h <= 0:
        return None
    rx1 = int(round(x1 - w * MARGIN_X))
    rx2 = int(round(x2 + w * MARGIN_X))
    ry1 = int(round(y1 - h * MARGIN_TOP))
    eff = min(MAX_RATIO, max(clamp_ratio(ratio), HEAD_W_RATIO * w / h))
    ry2 = int(round(y1 + h * eff))
    rx1, rx2 = max(0, rx1), min(fw, rx2)
    ry1, ry2 = max(0, ry1), min(fh, ry2)
    if rx2 - rx1 < 2 or ry2 - ry1 < 2:
        return None
    return rx1, ry1, rx2, ry2


def pixelate(region: np.ndarray) -> None:
    """`region`(뷰)을 제자리에서 픽셀화한다."""
    h, w = region.shape[:2]
    block = max(MIN_BLOCK_PX, w // BLOCKS_ACROSS)
    sw, sh = max(1, w // block), max(1, h // block)
    small = cv2.resize(region, (sw, sh), interpolation=cv2.INTER_AREA)
    region[:] = cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)


def _overlaps(a: Sequence[float], b: Sequence[float]) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def mask_box(frame: np.ndarray, box: Sequence[float], ratio: float = DEFAULT_RATIO) -> bool:
    """한 사람 박스의 상단을 제자리에서 가린다. 가렸으면 True."""
    reg = mask_region(box, ratio, frame.shape[:2])
    if reg is None:
        return False
    x1, y1, x2, y2 = reg
    pixelate(frame[y1:y2, x1:x2])
    return True


class PrivacyMasker:
    """프레임 사이에 사람 박스를 기억해 탐지가 끊겨도 `hold_sec` 동안 가린다.

    **지난 박스는 이번 프레임의 박스와 겹치면 현재 박스로 교체된다** — 안 그러면 걷는
    사람 뒤에 지나온 자리의 모자이크가 꼬리처럼 남는다. 어느 현재 박스와도 겹치지
    않는 지난 박스(= 탐지가 끊긴 사람)만 마지막 위치에 `hold_sec` 동안 유지된다.
    같은 시각(`now`)에 여러 번 부르면 이번 시각의 박스는 서로를 지우지 않고 쌓인다
    (제외구역 필터 이전의 raw 탐지와 트래커 박스를 따로 넣기 때문).
    """

    def __init__(self, ratio: float = DEFAULT_RATIO, hold_sec: float = HOLD_SEC) -> None:
        self.ratio = clamp_ratio(ratio)
        self.hold_sec = float(hold_sec)
        self._boxes: list[tuple[float, tuple[float, float, float, float]]] = []

    def update(self, person_boxes: Iterable[Sequence[float]], now: float) -> None:
        """이번 프레임에 관측된 사람 박스를 등록하고 오래된 것을 버린다."""
        cutoff = now - self.hold_sec
        new = [tuple(float(v) for v in b[:4]) for b in person_boxes]
        self._boxes = [
            (t, b) for t, b in self._boxes
            if t >= cutoff and (t == now or not any(_overlaps(b, n) for n in new))
        ]
        self._boxes.extend((now, b) for b in new)

    def boxes(self) -> list[tuple[float, float, float, float]]:
        return [b for _, b in self._boxes]

    def apply(self, frame: np.ndarray) -> int:
        """기억하는 모든 박스를 가린다. 가린 개수를 돌려준다."""
        n = 0
        for _, b in self._boxes:
            if mask_box(frame, b, self.ratio):
                n += 1
        return n
