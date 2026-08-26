"""cane_person_assoc 단위 테스트 — 두 방향 조회(associate / associate_canes)."""

from cane_person_assoc import associate, associate_canes

PERSON = {"track_id": 1, "class": 1, "bbox": [100, 100, 200, 300]}
CANE_HELD = {"track_id": 2, "class": 0, "bbox": [190, 250, 215, 320]}   # 몸 옆으로 짚은 지팡이
CANE_FAR = {"track_id": 3, "class": 0, "bbox": [500, 500, 520, 560]}    # 배경 기둥 등


def test_person_direction_reports_cane_companion():
    assert associate([PERSON, CANE_HELD, CANE_FAR]) == {1: True}


def test_cane_direction_separates_held_from_orphan():
    assert associate_canes([PERSON, CANE_HELD, CANE_FAR]) == {2: True, 3: False}


def test_cane_without_any_person_is_never_accompanied():
    """사람 클래스가 아예 없는 프레임 — 배경 오탐지만 있는 상황."""
    assert associate_canes([CANE_FAR]) == {3: False}


def test_person_without_cane():
    assert associate([PERSON]) == {1: False}
    assert associate_canes([PERSON]) == {}


def test_x_margin_controls_reach():
    """확장 마진을 0으로 주면 사람 bbox 밖의 지팡이는 동반으로 잡히지 않는다."""
    assert associate_canes([PERSON, CANE_HELD], x_margin_ratio=0.0) == {2: False}
    assert associate_canes([PERSON, CANE_HELD], x_margin_ratio=0.15) == {2: True}


def test_empty_tracks():
    assert associate([]) == {}
    assert associate_canes([]) == {}
