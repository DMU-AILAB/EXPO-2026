"""cane_person_assoc.py — 프레임 단위 지팡이-사람 연관(association) 판별.

`SimpleTracker.update()`가 반환한 트랙 리스트(지팡이+사람 혼재)를 받아,
각 사람 track_id가 이번 프레임에 흰 지팡이를 동반했는지 여부를 판별한다.
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


def associate(
    tracks: list[dict],
    x_margin_ratio: float = 0.15,
    cane_cls: int = CANE_CLASS_ID,
    person_cls: int = PERSON_CLASS_ID,
) -> dict[int, bool]:
    """이번 프레임 기준: 사람 track_id -> 지팡이 동반 여부.

    사람 bbox를 좌우로 `x_margin_ratio`만큼 확장(지팡이를 몸 옆으로 짚는
    경우 허용)한 뒤, 그 안에 중심점이 들어오는 지팡이가 하나라도 있으면
    동반으로 판정한다.

    알려진 한계: 두 사람이 밀착해 있으면 지팡이 하나가 양쪽 확장 영역에
    동시에 걸쳐 둘 다 동반으로 잘못 판정될 수 있다 — 이번 범위에서는
    허용 가능한 단순화로 남겨둔다.
    """
    canes = [t for t in tracks if t["class"] == cane_cls]
    people = [t for t in tracks if t["class"] == person_cls]

    result: dict[int, bool] = {}
    for person in people:
        expanded = _expand_x(person["bbox"], x_margin_ratio)
        result[person["track_id"]] = any(
            _contains(expanded, _center(cane["bbox"])) for cane in canes
        )

    return result
