"""cane_person_assoc.py — 프레임 단위 지팡이-사람 연관(association) 판별.

`SimpleTracker.update()`가 반환한 트랙 리스트(지팡이+사람 혼재)를 받아,
지팡이와 사람이 이번 프레임에 짝지어졌는지를 두 방향으로 조회할 수 있게 한다.

- `associate()`     : 사람 track_id -> 지팡이 동반 여부 (유동인구 집계용)
- `associate_canes()`: 지팡이 track_id -> 사람 동반 여부 (ROI 트리거 게이트용)

두 함수는 같은 짝짓기 결과(`_matched_pairs()`)를 방향만 바꿔 읽는다.

트랙 소멸 감지나 누적 판정은 다루지 않는다 — 그건 `foot_traffic_counter.py`의
책임이다. 이 모듈은 순수하게 "이번 한 프레임"만 본다.
"""

from __future__ import annotations

CANE_CLASS_ID = 0
PERSON_CLASS_ID = 1

# 지팡이-사람 짝짓기 허용 간격 — 사람 bbox 폭에 대한 비율. 실측 표본은 전부 거리 0
# (두 박스가 겹침)이라 분포에서 맞춘 값이 아니라, 박스가 살짝 떨어질 때를 위한 여유값이다.
# 픽셀 절대값이 아니라 사람 폭 대비 비율인 이유는 원근(가까운 사람은 크게, 먼 사람은
# 작게 찍힘)에 따라 같은 기준이 유지되어야 하기 때문이다.
_DEFAULT_MAX_GAP_RATIO = 0.15


def _center(bbox: list) -> tuple[float, float]:
    x1, y1, x2, y2 = bbox
    return (x1 + x2) / 2, (y1 + y2) / 2


def _gap(a: list, b: list) -> float:
    """두 bbox의 최단거리(px). 겹치거나 맞닿으면 0."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    dx = max(bx1 - ax2, ax1 - bx2, 0.0)
    dy = max(by1 - ay2, ay1 - by2, 0.0)
    return (dx * dx + dy * dy) ** 0.5


def _matched_pairs(
    tracks: list[dict],
    max_gap_ratio: float,
    cane_cls: int,
    person_cls: int,
) -> tuple[list[dict], list[dict], set[tuple[int, int]]]:
    """(지팡이 트랙, 사람 트랙, 짝지어진 (person_id, cane_id) 집합)을 반환.

    두 조건을 모두 만족해야 짝으로 본다.

    1. 지팡이 bbox와 사람 bbox의 **최단거리**가 사람 폭 × `max_gap_ratio` 이하
    2. 지팡이 bbox 중심의 y가 사람 bbox의 y 범위 안

    이전 구현은 "사람 bbox를 좌우로 확장한 뒤 지팡이 **중심점**이 그 안인가"였는데,
    흰 지팡이는 몸 앞으로 비스듬히 뻗어 짚기 때문에 사람과 명백히 함께 있어도
    중심점이 자주 밖으로 나갔다 — 실측(사람 1명 + 지팡이 탐지 60프레임)에서 확장 여백
    0.15로는 **19/60만 통과**했고, 그 결과 ROI 트리거가 한 번도 발동하지 않았으며
    유동인구의 지팡이 사용자 집계도 0명으로 나왔다.

    같은 표본을 최단거리로 재면 **60/60이 거리 0(두 박스가 겹침)**이고 중심 y도
    60/60이 사람 범위 안이다. 즉 판정 축이 아니라 "중심점 하나로 본다"는 방식이
    문제였다. `max_gap_ratio`는 분포에서 맞춘 값이 아니라(전부 0이라 맞출 게 없다)
    박스가 살짝 떨어지는 경우를 위한 여유값이다.

    세로 조건을 남기는 이유: 빼면 사람 위쪽의 나뭇가지나 아래쪽 난간이 거리만
    가까우면 통과해 사람 동반 게이트의 존재 이유가 약해진다.
    """
    canes = [t for t in tracks if t["class"] == cane_cls]
    people = [t for t in tracks if t["class"] == person_cls]

    pairs: set[tuple[int, int]] = set()
    for person in people:
        px1, py1, px2, py2 = person["bbox"]
        max_gap = (px2 - px1) * max_gap_ratio
        for cane in canes:
            _, cy = _center(cane["bbox"])
            if py1 <= cy <= py2 and _gap(cane["bbox"], person["bbox"]) <= max_gap:
                pairs.add((person["track_id"], cane["track_id"]))

    return canes, people, pairs


def associate(
    tracks: list[dict],
    max_gap_ratio: float = _DEFAULT_MAX_GAP_RATIO,
    cane_cls: int = CANE_CLASS_ID,
    person_cls: int = PERSON_CLASS_ID,
) -> dict[int, bool]:
    """이번 프레임 기준: 사람 track_id -> 지팡이 동반 여부.

    알려진 한계: 두 사람이 밀착해 있으면 지팡이 하나가 양쪽 모두와 조건을
    만족해 둘 다 동반으로 잘못 판정될 수 있다 — 이번 범위에서는 허용 가능한
    단순화로 남겨둔다(1:1 배정이 필요하면 호출부에서 처리할 것).
    """
    _, people, pairs = _matched_pairs(tracks, max_gap_ratio, cane_cls, person_cls)
    matched = {pid for pid, _ in pairs}
    return {person["track_id"]: person["track_id"] in matched for person in people}


def associate_canes(
    tracks: list[dict],
    max_gap_ratio: float = _DEFAULT_MAX_GAP_RATIO,
    cane_cls: int = CANE_CLASS_ID,
    person_cls: int = PERSON_CLASS_ID,
) -> dict[int, bool]:
    """이번 프레임 기준: 지팡이 track_id -> 사람 동반 여부.

    `associate()`의 반대 방향. ROI 트리거 게이트("사람과 함께 감지된 지팡이만
    음성 안내")에 쓴다 — 흰 지팡이는 항상 사람이 들고 다니므로, 사람이 없는
    자리에서 잡힌 지팡이는 배경의 선/기둥/나뭇가지 오탐지일 가능성이 높다.

    사람 클래스가 아예 없는 프레임에서는 모든 지팡이가 False가 된다 — 호출부가
    이 게이트를 켤지 말지(`CameraProfile.require_person_for_trigger`)를 정한다.
    """
    canes, _, pairs = _matched_pairs(tracks, max_gap_ratio, cane_cls, person_cls)
    matched = {cid for _, cid in pairs}
    return {cane["track_id"]: cane["track_id"] in matched for cane in canes}
