"""pedestrian_entity 단위 테스트 — 1:1 배정 · 히스테리시스 · 래치 · 가상 지팡이 박스."""

from pedestrian_entity import (
    LATCH_SEC,
    VIRTUAL_MAX_SEC,
    EntityTracker,
    cane_user_person_ids,
    latched_cane_ids,
    subject_for_canes,
    virtual_cane_boxes,
)

# 이 모듈의 상수는 프레임이 아니라 **초**라, 테스트도 가짜 시계로 시간을 흘린다.
# 값은 전부 2의 거듭제곱 분수라 부동소수점 오차 없이 경계가 정확히 재현된다.
DT = 0.25
TEST_LATCH_SEC = 0.5
TEST_VIRTUAL_SEC = 1.0
TEST_GRACE_SEC = 1.0
# 첫 프레임이 기준점이 되므로 경과 시간을 채우려면 한 프레임이 더 필요하다.
LATCH_TICKS = round(TEST_LATCH_SEC / DT) + 1


class _Clock:
    """update()에 넘길 단조 시각(초)을 만들어 준다. 누적이 아니라 정수배로 계산해
    부동소수점 오차가 쌓이지 않게 한다."""

    def __init__(self, dt=DT):
        self.n, self.dt = 0, dt

    def tick(self):
        self.n += 1
        return self.n * self.dt


def _tracker():
    return (EntityTracker(latch_sec=TEST_LATCH_SEC,
                          virtual_max_sec=TEST_VIRTUAL_SEC,
                          person_grace_sec=TEST_GRACE_SEC), _Clock())


def _person(tid, x1, y1=100, x2=None, y2=300):
    return {"track_id": tid, "class": 1, "bbox": [x1, y1, x2 if x2 else x1 + 100, y2]}


def _cane(tid, x1, y1=250, x2=None, y2=320):
    return {"track_id": tid, "class": 0, "bbox": [x1, y1, x2 if x2 else x1 + 20, y2]}


def _feed(tracker, clock, tracks, gated, times=1):
    out = []
    for _ in range(times):
        out = tracker.update(tracks, gated, clock.tick())
    return out


def test_module_defaults_are_seconds_not_frames():
    """상수의 단위가 초임을 고정한다 — 프레임으로 되돌리면 평가값이 실기기로 전이되지 않는다."""
    assert 0 < LATCH_SEC < 5 and 0 < VIRTUAL_MAX_SEC < 10


# --------------------------------------------------------------------- #
# 1:1 배정 — 밀착한 두 사람 사이에서 지팡이 하나가 양쪽 모두와 짝지어지던 문제
# --------------------------------------------------------------------- #

def test_single_cane_is_assigned_to_exactly_one_person():
    """두 사람이 밀착해도 지팡이는 한 엔티티에만 붙는다.

    `cane_person_assoc.associate()`는 이 상황에서 둘 다 True를 돌려준다(그쪽
    docstring에 한계로 명시). 엔티티는 누적 판정을 하므로 그대로 두면 옆 사람까지
    지팡이 사용자로 래치된다.
    """
    tracker, clock = _tracker()
    ents = tracker.update([_person(1, 100), _person(2, 205), _cane(10, 195)],
                          {10}, clock.tick())

    with_cane = [e for e in ents if e.cane_id is not None]
    assert len(with_cane) == 1
    assert with_cane[0].cane_id == 10


def test_binding_survives_a_closer_rival_appearing():
    """히스테리시스 — 직전 결합이 후보에 남아 있으면 더 가까운 후보가 생겨도 유지."""
    tracker, clock = _tracker()
    # 1프레임: 지팡이가 사람1 안쪽에 있어 사람2는 아예 후보가 되지 못한다
    ents = tracker.update([_person(1, 100), _person(2, 205), _cane(10, 160)],
                          {10}, clock.tick())
    assert {e.person_id: e.cane_id for e in ents} == {1: 10, 2: None}

    # 2프레임: 지팡이가 사람2 쪽으로 이동해 gap 0(사람1은 gap 5)이 됐지만,
    # 직전 결합이 여전히 유효한 후보이므로 사람1이 유지돼야 한다.
    ents = tracker.update([_person(1, 100), _person(2, 205), _cane(10, 205)],
                          {10}, clock.tick())
    assert {e.person_id: e.cane_id for e in ents} == {1: 10, 2: None}


def test_non_gated_cane_does_not_steal_the_slot_from_a_gated_one():
    """게이트 통과 지팡이를 먼저 배정한다 — 배경 기둥이 자리를 선점하면 안 된다."""
    tracker, clock = _tracker()
    # 기둥(11)이 사람 중심에 더 가깝지만 게이트를 통과한 것은 진짜 지팡이(10)다
    ents = tracker.update([_person(1, 100), _cane(10, 195), _cane(11, 150)],
                          {10}, clock.tick())
    assert ents[0].cane_id == 10


# --------------------------------------------------------------------- #
# 래치
# --------------------------------------------------------------------- #

def test_latch_needs_consecutive_companionship_for_latch_sec():
    tracker, clock = _tracker()
    tracks = [_person(1, 100), _cane(10, 195)]

    ents = _feed(tracker, clock, tracks, {10}, times=LATCH_TICKS - 1)
    assert ents[0].is_cane_user is False

    ents = tracker.update(tracks, {10}, clock.tick())
    assert ents[0].is_cane_user is True
    assert latched_cane_ids(ents) == {10}
    assert cane_user_person_ids(ents) == {1}


def test_latch_timer_restarts_when_companionship_breaks():
    """연속 조건 — 중간에 끊기면 기준점이 리셋된다."""
    tracker, clock = _tracker()
    tracks = [_person(1, 100), _cane(10, 195)]

    _feed(tracker, clock, tracks, {10}, times=LATCH_TICKS - 1)
    ents = tracker.update([_person(1, 100)], {10}, clock.tick())   # 지팡이 없는 한 프레임
    assert ents[0].pair_since is None
    assert ents[0].is_cane_user is False

    ents = tracker.update(tracks, {10}, clock.tick())
    assert ents[0].is_cane_user is False                            # 다시 1프레임째


def test_cane_that_failed_the_gates_never_latches():
    """래치 입력은 정지 억제 + 움직임 게이트를 통과한 지팡이뿐이다.

    배경 기둥처럼 게이트에서 걸린 지팡이가 사람 옆에 오래 있어도 래치되면,
    사람 동반 게이트 완화가 곧바로 오탐지 통로가 된다.
    """
    tracker, clock = _tracker()
    tracks = [_person(1, 100), _cane(10, 195)]

    ents = _feed(tracker, clock, tracks, set(), times=LATCH_TICKS * 3)
    assert ents[0].cane_id == 10        # 짝짓기 자체는 된다 (오프셋 추적용)
    assert ents[0].pair_since is None
    assert ents[0].is_cane_user is False
    assert latched_cane_ids(ents) == set()


# --------------------------------------------------------------------- #
# 가상 지팡이 박스
# --------------------------------------------------------------------- #

def test_virtual_box_appears_when_the_cane_track_dies():
    tracker, clock = _tracker()
    _feed(tracker, clock, [_person(1, 100), _cane(10, 195)], {10}, times=LATCH_TICKS)

    ents = tracker.update([_person(1, 100)], {10}, clock.tick())
    assert ents[0].is_cane_user is True          # 래치는 유지된다
    assert ents[0].cane_id is None
    assert ents[0].virtual_cane_bbox == [195, 250, 215, 320]
    assert virtual_cane_boxes(ents) == [(ents[0].entity_id, [195, 250, 215, 320])]


def test_virtual_box_follows_the_person():
    """사람이 움직이면 가상 박스도 같은 상대 위치를 유지하며 따라간다."""
    tracker, clock = _tracker()
    _feed(tracker, clock, [_person(1, 100), _cane(10, 195)], {10}, times=LATCH_TICKS)

    ents = tracker.update([_person(1, 150)], {10}, clock.tick())  # 사람이 50px 이동
    assert ents[0].virtual_cane_bbox == [245, 250, 265, 320]


def test_virtual_box_expires_after_virtual_max_sec():
    """상한이 없으면 래치된 엔티티가 사람 트랙이 사는 내내 가상 박스를 뿜는다 —
    실측에서 그게 정답 구간 밖 오탐율을 0.0%에서 39.2%로 올렸다."""
    tracker, clock = _tracker()
    _feed(tracker, clock, [_person(1, 100), _cane(10, 195)], {10}, times=LATCH_TICKS)

    ticks = round(TEST_VIRTUAL_SEC / DT)
    ents = _feed(tracker, clock, [_person(1, 100)], {10}, times=ticks)
    assert ents[0].virtual_cane_bbox is not None        # 경계까지는 유지

    ents = tracker.update([_person(1, 100)], {10}, clock.tick())
    assert ents[0].virtual_cane_bbox is None            # 상한을 넘으면 사라진다
    assert ents[0].is_cane_user is True                 # 통계용 래치는 남는다


def test_no_virtual_box_while_the_real_cane_is_visible():
    tracker, clock = _tracker()
    ents = _feed(tracker, clock, [_person(1, 100), _cane(10, 195)], {10},
                 times=LATCH_TICKS)
    assert ents[0].virtual_cane_bbox is None
    assert virtual_cane_boxes(ents) == []


def test_no_virtual_box_before_the_latch():
    """래치되지 않은 엔티티는 지팡이가 없어도 가상 박스를 만들지 않는다."""
    tracker, clock = _tracker()
    tracker.update([_person(1, 100), _cane(10, 195)], {10}, clock.tick())
    ents = tracker.update([_person(1, 100)], {10}, clock.tick())
    assert ents[0].virtual_cane_bbox is None


# --------------------------------------------------------------------- #
# 생애주기
# --------------------------------------------------------------------- #

def test_absent_person_is_not_returned_even_while_remembered():
    """보관은 '신원을 기억한다'까지다 — 낡은 사람 bbox로 가상 지팡이를 만들면 안 된다."""
    tracker, clock = _tracker()
    _feed(tracker, clock, [_person(1, 100), _cane(10, 195)], {10}, times=LATCH_TICKS)
    assert tracker.update([_cane(10, 195)], {10}, clock.tick()) == []


def test_entity_survives_a_short_person_gap_and_keeps_the_latch():
    """트래커 재식별이 같은 track_id로 사람을 되살리면 래치도 이어져야 한다.

    엔티티를 즉시 없애면 되살아난 사람에게 새 엔티티가 붙어 지팡이 사용자 확정과
    안내 주체가 끊기고, 같은 사람에게 안내가 다시 나간다 — 재식별로 얻으려던 것이
    바로 그 연속성이다.
    """
    tracker, clock = _tracker()
    ents = _feed(tracker, clock, [_person(1, 100), _cane(10, 195)], {10},
                 times=LATCH_TICKS)
    eid = ents[0].entity_id
    assert ents[0].is_cane_user is True

    _feed(tracker, clock, [], set(), times=round(TEST_GRACE_SEC / DT))   # 유예 안
    back = tracker.update([_person(1, 100), _cane(10, 195)], {10}, clock.tick())[0]
    assert back.entity_id == eid
    assert back.is_cane_user is True


def test_entity_is_dropped_after_the_grace_window():
    tracker, clock = _tracker()
    _feed(tracker, clock, [_person(1, 100), _cane(10, 195)], {10}, times=LATCH_TICKS)

    _feed(tracker, clock, [], set(), times=round(TEST_GRACE_SEC / DT) + 2)  # 유예 초과
    back = tracker.update([_person(1, 100), _cane(10, 195)], {10}, clock.tick())[0]
    assert back.is_cane_user is False          # 신원을 잊었으므로 다시 래치해야 한다


def test_a_different_person_never_inherits_the_latch():
    tracker, clock = _tracker()
    _feed(tracker, clock, [_person(1, 100), _cane(10, 195)], {10}, times=LATCH_TICKS)
    ents = tracker.update([_person(2, 100), _cane(10, 195)], {10}, clock.tick())
    assert ents[0].is_cane_user is False
    assert ents[0].entity_id != 0


def test_person_without_any_cane_is_still_an_entity():
    """유동인구 집계가 사람 단위이므로 지팡이가 없어도 엔티티는 생긴다."""
    tracker, clock = _tracker()
    ents = tracker.update([_person(1, 100)], set(), clock.tick())
    assert len(ents) == 1
    assert ents[0].cane_id is None
    assert ents[0].is_cane_user is False


def test_cane_only_frame_creates_no_entity():
    tracker, clock = _tracker()
    assert tracker.update([_cane(10, 195)], {10}, clock.tick()) == []


# --------------------------------------------------------------------- #
# 안내 주체 매핑 — 래치는 엄격한 1:1, 주체 식별은 느슨한 연관
# --------------------------------------------------------------------- #

def test_extra_cane_boxes_of_one_person_map_to_the_same_subject():
    """탐지기가 같은 지팡이를 여러 박스로 내놓아도 주체는 한 사람이다.

    1:1 배정 결과를 그대로 주체로 쓰면 밀린 박스가 트랙 id로 폴백해 한 사람이
    여러 주체로 쪼개지고, 같은 사람에게 안내가 반복된다 — test1 실측에서
    1,078프레임분이 밀렸고 그 전부가 사람 후보를 가지고 있었다.
    """
    tracker, clock = _tracker()
    tracks = [_person(1, 100), _cane(10, 195), _cane(11, 185)]
    ents = tracker.update(tracks, {10, 11}, clock.tick())

    bound = [e.cane_id for e in ents]
    assert len(bound) == 1 and bound[0] in (10, 11)   # 배정은 여전히 1:1

    owner = subject_for_canes(ents, tracks)
    assert owner[10] == owner[11] == ents[0].entity_id


def test_cane_with_no_person_nearby_has_no_subject():
    """사람 후보가 없는 지팡이는 매핑에 없다 — 호출부가 트랙 id로 폴백한다."""
    tracker, clock = _tracker()
    tracks = [_person(1, 100), _cane(10, 195), _cane(99, 900, y1=600, y2=700)]
    ents = tracker.update(tracks, {10, 99}, clock.tick())
    assert 99 not in subject_for_canes(ents, tracks)


def test_two_people_keep_separate_subjects():
    tracker, clock = _tracker()
    tracks = [_person(1, 100), _person(2, 400), _cane(10, 195), _cane(20, 495)]
    ents = tracker.update(tracks, {10, 20}, clock.tick())
    owner = subject_for_canes(ents, tracks)
    assert owner[10] != owner[20]


# --------------------------------------------------------------------- #
# 게이트 체인 통합 — `eval_video_recall.run_gates`가 배포와 같은 순서를 재현하므로
# 여기서 재면 `camera_live_pi.py`의 트리거 경로를 그대로 검증하는 것이 된다.
# --------------------------------------------------------------------- #

def _walking_frames(cane_gap_frames: int):
    """사람은 계속 걷고, 지팡이는 중간에 `cane_gap_frames`만큼 탐지가 끊기는 영상.

    지팡이 탐지가 트래커의 coasting(max_age=10)보다 오래 끊기는 구간을 만든다 —
    엔티티 레이어가 여는 것이 정확히 그 구간이다.
    """
    frames = []
    for i in range(60):
        x = 100 + i * 6                      # 프레임당 6px 전진 (움직임 게이트 통과)
        dets = [{"bbox": [x, 100, x + 100, 300], "conf": 0.9,
                 "class": 1, "label": "person"}]
        if not (15 <= i < 15 + cane_gap_frames):
            dets.append({"bbox": [x + 95, 250, x + 115, 320], "conf": 0.9,
                         "class": 0, "label": "white_cane"})
        frames.append(dets)
    return frames


def test_entity_layer_is_a_monotone_relaxation_of_the_person_gate():
    """엔티티를 켜면 통과 프레임이 늘거나 같아야 한다 — 줄어들면 배선 버그다.

    사람 동반 게이트를 `프레임 단위 판정 OR 래치`로 바꿨을 뿐이므로 기존에
    통과하던 것은 구조적으로 전부 계속 통과한다.
    """
    from eval_video_recall import run_gates

    frames = _walking_frames(cane_gap_frames=20)
    kw = dict(shape=(480, 640), conf=0.25, fps=10.0, debounce=0.5,
              require_person=True)

    base = run_gates(frames, use_entity=False, **kw)
    ent = run_gates(frames, use_entity=True, entity_virtual_sec=2.0, **kw)

    assert ent["longest"] >= base["longest"]
    assert ent["stage"]["person"] >= base["stage"]["person"]


def test_virtual_box_carries_the_trigger_through_a_long_cane_dropout():
    """지팡이가 트래커 max_age보다 오래 끊겨도 가상 박스가 판정을 이어간다."""
    from eval_video_recall import run_gates

    frames = _walking_frames(cane_gap_frames=20)
    ent = run_gates(frames, shape=(480, 640), conf=0.25, fps=10.0,
                    debounce=0.5, require_person=True, use_entity=True,
                    entity_virtual_sec=2.0)

    assert ent["latched_entities"] >= 1
    assert ent["virtual_frames"] > 0
