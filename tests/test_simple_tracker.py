"""simple_tracker.py 단위 테스트 — IoU 매칭 + 중심점 거리 기반 폴백 매칭."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

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
