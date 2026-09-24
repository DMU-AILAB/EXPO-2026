"""simple_tracker.py 단위 테스트 — IoU 매칭 + 중심점 거리 기반 폴백 매칭."""
import sys
from pathlib import Path


from simple_tracker import SimpleTracker


def _det(bbox, cls=0, conf=0.9, label="white_cane"):
    return {"bbox": bbox, "class": cls, "conf": conf, "label": label}


def test_same_track_id_when_boxes_overlap_well():
    t = SimpleTracker()
    tracks1 = t.update([_det([100, 100, 120, 200])])
    tracks2 = t.update([_det([102, 102, 122, 202])])  # 거의 그대로, IoU 높음
    assert tracks1[0]["track_id"] == tracks2[0]["track_id"]


def test_new_track_created_when_no_prior_detection():
    t = SimpleTracker()
    tracks = t.update([_det([100, 100, 120, 200])])
    assert len(tracks) == 1
    assert tracks[0]["track_id"] == 0


def test_fast_small_object_survives_via_distance_fallback():
    """지팡이처럼 작은 박스가 한 프레임 사이 박스 크기보다 크게 이동해 IoU가 0이 되어도
    (거의 겹치지 않음) 중심점 거리가 대각선의 1.5배 이내면 같은 track_id를 유지해야 한다."""
    t = SimpleTracker()
    tracks1 = t.update([_det([100, 100, 110, 150])])  # 얇고 긴 지팡이 박스, 대각선 ~51px
    # 다음 프레임: 오른쪽으로 40px 이동 — IoU는 0 (안 겹침)이지만 대각선(51px)의 1.5배(76.5px) 이내
    tracks2 = t.update([_det([140, 100, 150, 150])])
    assert tracks1[0]["track_id"] == tracks2[0]["track_id"], (
        "빠르게 움직인 작은 물체가 거리 기반 폴백 매칭 없이 새 track_id를 받았음"
    )


def test_distance_fallback_does_not_match_far_away_object():
    """폴백 매칭도 무한정 허용하지 않는다 — 대각선의 1.5배를 넘게 멀어지면 새 트랙이어야 한다.
    원래 트랙은 미탐지 상태로 코스팅되어 남고(다른 track_id), 먼 곳의 탐지는 별도 신규
    트랙이 되어야 한다 — 즉 결과는 track_id가 서로 다른 트랙 2개."""
    t = SimpleTracker()
    tracks1 = t.update([_det([100, 100, 110, 150])])  # 대각선 ~51px
    original_id = tracks1[0]["track_id"]
    # 300px 이동 — 1.5배(76.5px) 훨씬 초과
    tracks2 = t.update([_det([400, 100, 410, 150])])
    ids2 = {t2["track_id"] for t2 in tracks2}
    assert len(tracks2) == 2, "먼 곳의 탐지가 기존 트랙과 잘못 매칭되지 않고 신규 트랙이 되어야 함"
    assert original_id in ids2  # 기존 트랙은 코스팅으로 남아있어야 함
    new_id = (ids2 - {original_id}).pop()
    assert new_id != original_id


def test_different_classes_never_match_via_fallback():
    t = SimpleTracker()
    t.update([_det([100, 100, 110, 150], cls=0, label="white_cane")])
    tracks2 = t.update([_det([105, 105, 115, 155], cls=1, label="person")])
    person_tracks = [tr for tr in tracks2 if tr["class"] == 1]
    # 같은 위치 근처지만 클래스가 다르므로 기존 지팡이 트랙과 매칭되지 않고 신규 트랙이어야 함
    assert len(person_tracks) == 1
    assert person_tracks[0]["track_id"] == 1


def test_static_frames_increments_when_not_moving():
    """배경 케이블/문틀 경계선처럼 고정된 오탐지 대상은 매칭될 때마다 static_frames가
    쌓인다 — 호출부가 이 값으로 "너무 오래 정지된 지팡이는 배경 오탐지"로 판단할 수 있다."""
    t = SimpleTracker(static_move_px=3.0)
    t.update([_det([100, 100, 110, 150])])
    tracks = t.update([_det([100, 100, 110, 150])])  # 완전히 동일한 위치
    assert tracks[0]["static_frames"] == 1
    tracks = t.update([_det([101, 100, 111, 150])])  # 1px 이동 — 임계값(3px) 이내, 계속 정지로 간주
    assert tracks[0]["static_frames"] == 2


def test_static_frames_resets_when_object_moves():
    t = SimpleTracker(static_move_px=3.0)
    t.update([_det([100, 100, 110, 150])])
    tracks = t.update([_det([100, 100, 110, 150])])
    assert tracks[0]["static_frames"] == 1
    tracks = t.update([_det([120, 100, 130, 150])])  # 20px 이동 — 임계값 초과, 리셋
    assert tracks[0]["static_frames"] == 0


def test_coasting_then_removed_after_max_age():
    # 신규 트랙은 생성된 프레임에 곧바로 미매칭 age 증가 패스를 한 번 거쳐 age=1로
    # 시작한다(기존 동작) — 그 뒤 미탐지 프레임마다 1씩 증가하다 max_age를 넘으면 제거.
    t = SimpleTracker(max_age=2)
    tracks = t.update([_det([100, 100, 120, 200])])
    assert tracks[0]["age"] == 1
    tracks = t.update([])  # 미탐지 프레임 1 (age == max_age, 아직 유지)
    assert len(tracks) == 1 and tracks[0]["age"] == 2
    tracks = t.update([])  # 미탐지 프레임 2 (age > max_age, 제거)
    assert len(tracks) == 0


def test_max_disp_stays_small_for_static_object_with_jitter():
    """고정 물체는 탐지 지터가 있어도 원점 대비 변위가 커지지 않아야 한다.

    누적 경로 길이로 쟀다면 지터가 매 프레임 더해져 결국 "움직였다"가 되지만,
    원점 대비 최대 변위는 지터 진폭에 bounded된다 — 움직임 게이트의 전제다.
    """
    import random
    rng = random.Random(0)
    t = SimpleTracker()
    for _ in range(300):
        x = 100 + rng.uniform(-2, 2)
        y = 200 + rng.uniform(-2, 2)
        tracks = t.update([{"bbox": [x, y, x + 20, y + 60], "conf": 0.8,
                            "class": 0, "label": "white_cane"}])
    assert tracks[0]["max_disp"] < 10.0


def test_max_disp_grows_for_moving_object():
    t = SimpleTracker()
    for i in range(30):
        x = 100 + i * 4
        tracks = t.update([{"bbox": [x, 200, x + 20, 260], "conf": 0.8,
                            "class": 0, "label": "white_cane"}])
    assert tracks[0]["max_disp"] > 100.0


def test_max_disp_is_retained_after_object_stops():
    """움직인 뒤 멈춰도 '움직인 적 있음'은 유지된다 (static_frames와 목적이 다름)."""
    t = SimpleTracker()
    for i in range(30):
        x = 100 + i * 4
        t.update([{"bbox": [x, 200, x + 20, 260], "conf": 0.8,
                   "class": 0, "label": "white_cane"}])
    for _ in range(30):
        tracks = t.update([{"bbox": [216, 200, 236, 260], "conf": 0.8,
                            "class": 0, "label": "white_cane"}])
    assert tracks[0]["max_disp"] > 100.0      # 유지
    assert tracks[0]["static_frames"] >= 24   # 지금은 멈춰 있음


def test_new_track_starts_with_zero_displacement():
    """새 트랙은 움직임이 증명되지 않은 상태로 시작 — 게이트가 프레임 0부터 막는다."""
    t = SimpleTracker()
    tracks = t.update([{"bbox": [100, 200, 120, 260], "conf": 0.8,
                        "class": 0, "label": "white_cane"}])
    assert tracks[0]["max_disp"] == 0.0


# --------------------------------------------------------------------- #
# 재식별(re-id) — max_age를 넘겨 죽은 트랙을 원래 track_id로 되살린다
# --------------------------------------------------------------------- #

DT = 0.1                    # 가짜 시계 간격(초). Pi 실측 12.76 FPS에 가깝게 10 FPS 가정


def _kill(t, clock, frames=None, dets=()):
    """탐지를 끊어 트랙이 max_age를 넘겨 죽게 만든다."""
    for _ in range(frames if frames is not None else t.max_age + 2):
        t.update(list(dets), clock())
    return t


def _clock(dt=DT):
    state = {"n": 0}

    def tick():
        state["n"] += 1
        return state["n"] * dt
    return tick


def test_reid_is_off_when_now_is_not_given():
    """`now`를 안 넘기면 보관소를 쓰지 않아 동작이 종전과 완전히 같다."""
    t = SimpleTracker()
    first = t.update([_det([100, 100, 120, 200])])[0]["track_id"]
    for _ in range(t.max_age + 2):
        t.update([])
    again = t.update([_det([100, 100, 120, 200])])[0]["track_id"]
    assert again != first


def test_track_revives_with_the_same_id_after_a_short_gap():
    t, c = SimpleTracker(), _clock()
    first = t.update([_det([100, 100, 120, 200])], c())[0]["track_id"]
    _kill(t, c)
    revived = t.update([_det([104, 100, 124, 200])], c())[0]
    assert revived["track_id"] == first


def test_revived_track_inherits_the_movement_gate_credit():
    """되살릴 때 origin_center/max_disp를 물려받는다 — 이게 재식별의 목적이다.

    새 트랙으로 시작하면 움직임 게이트(원점 대비 변위)를 처음부터 다시 벌어야 하고,
    `pedestrian_entity`의 래치와 안내 주체도 끊긴다.
    """
    t, c = SimpleTracker(), _clock()
    t.update([_det([100, 100, 120, 200])], c())
    for x in range(110, 210, 10):                  # 오른쪽으로 크게 이동
        moved = t.update([_det([x, 100, x + 20, 200])], c())[0]
    assert moved["max_disp"] > 50

    _kill(t, c)
    revived = t.update([_det([205, 100, 225, 200])], c())[0]
    assert revived["max_disp"] == moved["max_disp"]
    assert revived["origin_center"] == moved["origin_center"]


def test_revived_track_does_not_inherit_static_frames():
    """죽어 있는 동안 물체가 움직였을 수 있어 '정지'를 물려줄 근거가 없다."""
    t, c = SimpleTracker(), _clock()
    for _ in range(30):                            # 제자리 → static_frames 누적
        still = t.update([_det([100, 100, 120, 200])], c())[0]
    assert still["static_frames"] > 20

    _kill(t, c)
    revived = t.update([_det([100, 100, 120, 200])], c())[0]
    assert revived["track_id"] == still["track_id"]
    assert revived["static_frames"] == 0


def test_no_revival_after_the_window_expires():
    t, c = SimpleTracker(revive_sec=0.5), _clock()
    first = t.update([_det([100, 100, 120, 200])], c())[0]["track_id"]
    _kill(t, c, frames=20)                         # 2.0초 공백 > 0.5초
    assert t.update([_det([100, 100, 120, 200])], c())[0]["track_id"] != first


def test_no_revival_when_it_reappears_far_away():
    t, c = SimpleTracker(), _clock()
    first = t.update([_det([100, 100, 120, 200])], c())[0]["track_id"]
    _kill(t, c)
    far = t.update([_det([900, 700, 920, 800])], c())[0]
    assert far["track_id"] != first


def test_no_revival_across_classes():
    """지팡이 자리에 사람이 잡혔다고 지팡이 트랙을 되살리면 안 된다."""
    t, c = SimpleTracker(), _clock()
    first = t.update([_det([100, 100, 120, 200])], c())[0]["track_id"]
    _kill(t, c)
    person = t.update([_det([100, 100, 120, 200], cls=1, label="person")], c())[0]
    assert person["track_id"] != first


def test_one_grave_revives_at_most_one_detection():
    """한 무덤이 두 탐지를 되살리면 같은 track_id가 둘 생긴다."""
    t, c = SimpleTracker(), _clock()
    t.update([_det([100, 100, 120, 200])], c())
    _kill(t, c)
    tracks = t.update([_det([102, 100, 122, 200]), _det([108, 104, 128, 204])], c())
    assert len({x["track_id"] for x in tracks}) == 2
