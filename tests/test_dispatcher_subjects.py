"""StandaloneDispatcher 주체(subject)별 안내 판정 — 2단계의 본체.

주체를 구분하기 전에는 상태가 ROI 이름 하나로만 묶여 있어, 먼저 온 사람의 쿨다운이
뒤에 오는 사람의 안내를 잡아먹고 머무는 사람에게는 쿨다운마다 같은 안내가 반복됐다.
"""

import pytest

from audio_trigger import StandaloneDispatcher

DEBOUNCE = 0.5
COOLDOWN = 10.0
MIN_GAP = 3.0


def _disp(**kw):
    kw.setdefault("debounce", DEBOUNCE)
    kw.setdefault("cooldown", COOLDOWN)
    kw.setdefault("min_gap", MIN_GAP)
    return StandaloneDispatcher(**kw)


def _hold(d, roi, subjects, t0, until, step=0.1):
    """t0부터 until까지 매 프레임 같은 주체들이 ROI 안에 있다고 알린다."""
    fired = []
    t = t0
    while t <= until + 1e-9:
        got = d.update(roi, subjects, t)
        if got is not None:
            fired.append((round(t, 2), got))
            d.update_last_triggered(roi, t)     # 오디오가 즉시 끝났다고 가정
        t = round(t + step, 6)
    return fired


# --------------------------------------------------------------------- #
# 핵심 회귀 — 통과하는 두 번째 사람
# --------------------------------------------------------------------- #

def test_second_person_passing_through_still_gets_announced():
    """A의 쿨다운 중에 B가 ROI를 통과해도 안내를 받는다.

    옛 구조(ROI 단위 쿨다운)에서는 B가 쿨다운이 풀리기 전에 나가버려 아무것도
    받지 못했다 — 걸어서 지나가는 보행자가 정확히 이 경우다.
    """
    d = _disp()
    assert _hold(d, "횡단보도", {"A"}, 0.0, 2.0) == [(0.5, "A")]

    # B는 t=5에 들어와 t=9에 나간다 (A의 쿨다운 10초 안)
    fired_b = _hold(d, "횡단보도", {"A", "B"}, 5.0, 9.0)
    assert [s for _t, s in fired_b] == ["B"]
    assert fired_b[0][0] == pytest.approx(5.5, abs=0.11)


def test_same_person_is_not_reannounced_while_lingering():
    """머물러 있는 사람에게 쿨다운마다 안내가 반복되던 문제."""
    d = _disp()
    fired = _hold(d, "입구", {"A"}, 0.0, 30.0)
    assert [s for _t, s in fired] == ["A"]      # 30초 동안 정확히 1회


def test_same_person_is_not_reannounced_right_after_stepping_out():
    """나갔다 바로 다시 들어와도 주체별 쿨다운이 재안내를 막는다."""
    d = _disp()
    _hold(d, "입구", {"A"}, 0.0, 1.0)                    # A 안내
    _hold(d, "입구", set(), 1.1, 3.0)                    # 이탈(유예 초과 → 방문 종료)
    assert _hold(d, "입구", {"A"}, 3.1, 8.0) == []       # 쿨다운 10초 안이라 조용


def test_same_person_is_announced_again_after_the_cooldown():
    d = _disp()
    _hold(d, "입구", {"A"}, 0.0, 1.0)
    _hold(d, "입구", set(), 1.1, 3.0)
    fired = _hold(d, "입구", {"A"}, 12.0, 14.0)
    assert [s for _t, s in fired] == ["A"]


# --------------------------------------------------------------------- #
# 스팸 방지
# --------------------------------------------------------------------- #

def test_three_people_entering_together_produce_one_announcement():
    """안내는 스피커로 공간에 나가므로 한 번이면 그 자리의 모두가 듣는다."""
    d = _disp()
    fired = _hold(d, "복도", {"A", "B", "C"}, 0.0, 9.0)
    assert len(fired) == 1


def test_min_gap_throttles_a_stream_of_new_people():
    """사람이 줄지어 지나가도 ROI 최소 간격만큼은 벌어진다."""
    d = _disp()
    fired = []
    t = 0.0
    for i in range(6):                      # 1초 간격으로 새 사람이 계속 진입
        fired += _hold(d, "복도", {f"P{i}"}, t, t + 0.9)
        t += 1.0
    gaps = [b[0] - a[0] for a, b in zip(fired, fired[1:])]
    assert fired, "아무도 안내를 못 받으면 안 된다"
    assert all(g >= MIN_GAP - 0.11 for g in gaps), gaps


def test_min_gap_never_exceeds_cooldown():
    """쿨다운을 짧게 설정한 사용자가 스팸 방지값 때문에 더 둔해지면 안 된다."""
    assert _disp(cooldown=1.0).min_gap == 1.0


# --------------------------------------------------------------------- #
# 디바운스 · 이탈 히스테리시스 · 오디오 재생 중 차단
# --------------------------------------------------------------------- #

def test_debounce_must_be_satisfied():
    d = _disp()
    assert d.update("입구", {"A"}, 0.0) is None
    assert d.update("입구", {"A"}, 0.4) is None
    assert d.update("입구", {"A"}, 0.5) == "A"


def test_debounce_restarts_when_the_subject_leaves_for_good():
    d = _disp()
    d.update("입구", {"A"}, 0.0)
    d.update("입구", set(), 0.3)
    d.update("입구", set(), 1.5)            # 유예(1.0초) 초과 → 방문 종료
    assert d.update("입구", {"A"}, 1.6) is None
    assert d.update("입구", {"A"}, 1.9) is None    # 다시 0.5초를 채워야 한다
    assert d.update("입구", {"A"}, 2.2) == "A"


def test_flicker_on_the_roi_border_does_not_restart_the_visit():
    """경계에 걸친 박스가 한두 프레임 안팎을 오가도 방문은 이어진다."""
    d = _disp()
    d.update("입구", {"A"}, 0.0)
    d.update("입구", set(), 0.2)            # 깜빡 (유예 안)
    d.update("입구", {"A"}, 0.3)
    assert d.update("입구", {"A"}, 0.5) == "A"   # 0.0 기준 그대로 디바운스 충족


def test_nothing_fires_while_the_audio_is_still_playing():
    d = _disp()
    assert d.update("입구", {"A"}, 0.0) is None
    assert d.update("입구", {"A"}, 0.5) == "A"
    # update_last_triggered를 부르지 않으면 재생 중(inf) 상태가 유지된다
    assert d.update("입구", {"B"}, 20.0) is None
    d.update_last_triggered("입구", 20.0)
    assert d.update("입구", {"B"}, 23.5) == "B"


def test_states_reports_the_visit_state():
    d = _disp()
    d.update("입구", {"A", "B"}, 0.0)
    assert d.states("입구") == {"A": "PENDING", "B": "PENDING"}
    d.update("입구", {"A", "B"}, 0.5)
    assert d.states("입구") == {"A": "ANNOUNCED", "B": "ANNOUNCED"}


# --------------------------------------------------------------------- #
# 주체를 구분하지 않는 레거시 래퍼
# --------------------------------------------------------------------- #

def test_legacy_wrappers_still_work():
    d = _disp()
    assert d.on_detected("입구", 0.0) is False
    assert d.on_detected("입구", 0.5) is True
    d.on_not_detected("입구", 2.0)
    assert d.cooldown_remaining("입구", 0.6) > 0
