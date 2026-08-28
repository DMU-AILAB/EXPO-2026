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


def _center(bbox: list) -> tuple[float, float]:
    x1, y1, x2, y2 = bbox
    return (x1 + x2) / 2, (y1 + y2) / 2


def _expand_x(bbox: list, margin_ratio: float) -> list:
    x1, y1, x2, y2 = bbox
    margin = (x2 - x1) * margin_ratio
    return [x1 - margin, y1, x2 + margin, y2]


def _contains(bbox: list, point: tuple[float, float]) -> bool:
    x1, y1, x2, y2 = bbox
    px, py = point
    return x1 <= px <= x2 and y1 <= py <= y2


def _matched_pairs(
    tracks: list[dict],
    x_margin_ratio: float,
    cane_cls: int,
    person_cls: int,
) -> tuple[list[dict], list[dict], set[tuple[int, int]]]:
    """(지팡이 트랙, 사람 트랙, 짝지어진 (person_id, cane_id) 집합)을 반환.

    사람 bbox를 좌우로 `x_margin_ratio`만큼 확장(지팡이를 몸 옆으로 짚는
    경우 허용)한 뒤, 그 안에 중심점이 들어오는 지팡이를 짝으로 본다.
    """
    canes = [t for t in tracks if t["class"] == cane_cls]
    people = [t for t in tracks if t["class"] == person_cls]

    pairs: set[tuple[int, int]] = set()
    for person in people:
        expanded = _expand_x(person["bbox"], x_margin_ratio)
        for cane in canes:
            if _contains(expanded, _center(cane["bbox"])):
                pairs.add((person["track_id"], cane["track_id"]))

    return canes, people, pairs


def associate(
    tracks: list[dict],
    x_margin_ratio: float = 0.15,
    cane_cls: int = CANE_CLASS_ID,
    person_cls: int = PERSON_CLASS_ID,
) -> dict[int, bool]:
    """이번 프레임 기준: 사람 track_id -> 지팡이 동반 여부.

    알려진 한계: 두 사람이 밀착해 있으면 지팡이 하나가 양쪽 확장 영역에
    동시에 걸쳐 둘 다 동반으로 잘못 판정될 수 있다 — 이번 범위에서는
    허용 가능한 단순화로 남겨둔다.
    """
    _, people, pairs = _matched_pairs(tracks, x_margin_ratio, cane_cls, person_cls)
    matched = {pid for pid, _ in pairs}
    return {person["track_id"]: person["track_id"] in matched for person in people}


def associate_canes(
    tracks: list[dict],
    x_margin_ratio: float = 0.15,
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
    canes, _, pairs = _matched_pairs(tracks, x_margin_ratio, cane_cls, person_cls)
    matched = {cid for _, cid in pairs}
    return {cane["track_id"]: cane["track_id"] in matched for cane in canes}
