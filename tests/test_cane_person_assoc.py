"""cane_person_assoc 단위 테스트 — 두 방향 조회(associate / associate_canes)."""

from cane_person_assoc import associate, associate_canes

PERSON = {"track_id": 1, "class": 1, "bbox": [100, 100, 200, 300]}
CANE_HELD = {"track_id": 2, "class": 0, "bbox": [190, 250, 215, 320]}   # 몸 옆으로 짚은 지팡이
CANE_FAR = {"track_id": 3, "class": 0, "bbox": [500, 500, 520, 560]}    # 배경 기둥 등

# 앞으로 뻗어 짚은 지팡이 — 사람 bbox와 겹치지만 **중심점은 사람 bbox 밖**이다.
# 이전(중심점 포함) 구현이 놓치던 유형이고, 이번 수정의 본체다.
CANE_FORWARD = {"track_id": 4, "class": 0, "bbox": [195, 260, 280, 310]}
# 사람과 완전히 떨어진 지팡이 — 가로 간격 30px = 사람 폭(100)의 0.30
CANE_DETACHED = {"track_id": 5, "class": 0, "bbox": [230, 250, 250, 320]}
# 사람 머리 위의 물체(나뭇가지 등) — 거리는 10px로 가깝지만 세로 조건에서 걸러져야 한다
CANE_ABOVE = {"track_id": 6, "class": 0, "bbox": [150, 20, 170, 90]}


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


def test_forward_reaching_cane_is_matched():
    """앞으로 뻗어 짚어 중심점이 사람 bbox 밖으로 나간 지팡이도 짝으로 잡힌다.

    이전 구현(사람 bbox 확장 + 중심점 포함)이 놓치던 유형 — 실측에서 60프레임 중
    41프레임이 이 이유로 탈락해 ROI 트리거가 한 번도 발동하지 않았다.
    """
    assert associate_canes([PERSON, CANE_FORWARD]) == {4: True}
    assert associate([PERSON, CANE_FORWARD]) == {1: True}


def test_object_above_person_is_rejected_by_vertical_condition():
    """거리가 가까워도 지팡이 중심 y가 사람 y 범위 밖이면 짝이 아니다."""
    assert associate_canes([PERSON, CANE_ABOVE]) == {6: False}


def test_max_gap_ratio_controls_reach():
    """떨어진 지팡이는 허용 간격 임계값 양쪽에서 판정이 갈린다.

    CANE_DETACHED의 가로 간격은 30px, 사람 폭은 100px이므로 비율 0.30이다.
    """
    assert associate_canes([PERSON, CANE_DETACHED], max_gap_ratio=0.25) == {5: False}
    assert associate_canes([PERSON, CANE_DETACHED], max_gap_ratio=0.35) == {5: True}


def test_overlapping_cane_matches_even_at_zero_gap_ratio():
    """박스가 겹치면 최단거리가 0이라 허용 간격이 0이어도 짝이 된다."""
    assert associate_canes([PERSON, CANE_HELD], max_gap_ratio=0.0) == {2: True}


def test_empty_tracks():
    assert associate([]) == {}
    assert associate_canes([]) == {}
