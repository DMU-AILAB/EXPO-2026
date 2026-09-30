"""autolabel_videos.py 순수 함수 테스트 — 모델·영상 없이 라벨/후보 결정 규칙을 고정한다."""
import json
from pathlib import Path

import pytest

pytest.importorskip("cv2")

import autolabel_videos as al
from resplit_dataset import group_key, stratum_of

W, H = 1920, 1080


def _moving_track(tid, frames, conf=0.8, step=10.0):
    """프레임마다 오른쪽으로 step px씩 움직이는 트랙."""
    return al.Track(tid, [al.Obs(f, (100 + f * step, 100, 140 + f * step, 300), conf) for f in frames])


def _static_track(tid, frames, conf=0.9):
    return al.Track(tid, [al.Obs(f, (500, 500, 520, 700), conf) for f in frames])


# --- 파일명 ---------------------------------------------------------------
def test_파일명과_그룹키가_resplit_규칙과_맞물린다():
    clip = al.clip_name("tr_vid_20260930", 1)
    name = al.frame_name(clip, 180)
    assert name == "tr_vid_20260930_001_f000180.jpg"
    assert group_key(name, {}) == "clip_tr_vid_20260930_001"
    assert stratum_of(name) == "clip_tr"


def test_같은_클립의_프레임은_같은_그룹이다():
    clip = al.clip_name("tr_vid_20260930", 7)
    assert group_key(al.frame_name(clip, 0), {}) == group_key(al.frame_name(clip, 3000), {})


def test_샘플_간격은_원본_fps_기준이다():
    assert al.sample_step(30.0) == 15
    assert al.sample_indices(31, 15) == [0, 15, 30]


def test_숫자_파일명만_받는다():
    assert al.video_index(Path("7.mp4")) == 7
    with pytest.raises(ValueError):
        al.video_index(Path("clip.mp4"))


# --- 평가 영상 차단 ---------------------------------------------------------
@pytest.mark.parametrize("name", ["test2.mp4", "pseudo_182540.mp4", "ev_lab_20260920_001.mp4"])
def test_평가_영상은_차단된다(name):
    assert al.is_eval_video(Path(name), set())


def test_video_gt에_있는_영상도_차단된다():
    assert al.is_eval_video(Path("custom.mp4"), {"custom.mp4"})
    assert not al.is_eval_video(Path("3.mp4"), {"test2.mp4"})


# --- 트랙 분류 ---------------------------------------------------------------
def test_길고_확신하고_움직인_트랙은_확정이다():
    assert al.classify_track(_moving_track(1, range(30)), 30.0, (W**2 + H**2) ** 0.5) == "confirmed"


def test_정지_트랙은_conf가_높아도_확정되지_않는다():
    assert al.classify_track(_static_track(1, range(100), conf=0.95), 30.0,
                             (W**2 + H**2) ** 0.5) == "static"


def test_짧은_트랙은_약하다():
    assert al.classify_track(_moving_track(1, range(5), step=30), 30.0,
                             (W**2 + H**2) ** 0.5) == "weak"


def test_확신한_적_없는_트랙은_약하다():
    assert al.classify_track(_moving_track(1, range(30), conf=0.3), 30.0,
                             (W**2 + H**2) ** 0.5) == "weak"


# --- 보간 --------------------------------------------------------------------
def test_짧은_공백은_선형_보간한다():
    tr = al.Track(1, [al.Obs(0, (0, 0, 10, 10), 0.9), al.Obs(20, (20, 0, 30, 10), 0.9)])
    out = al.interpolate_gaps(tr, [10], max_gap=30)
    assert out[10] == pytest.approx((10, 0, 20, 10))


def test_긴_공백은_보간하지_않는다():
    tr = al.Track(1, [al.Obs(0, (0, 0, 10, 10), 0.9), al.Obs(60, (60, 0, 70, 10), 0.9)])
    assert al.interpolate_gaps(tr, [30], max_gap=30) == {}


def test_트랙_밖으로_외삽하지_않는다():
    tr = al.Track(1, [al.Obs(10, (0, 0, 10, 10), 0.9), al.Obs(20, (20, 0, 30, 10), 0.9)])
    assert al.interpolate_gaps(tr, [0, 30], max_gap=30) == {}


# --- 프레임별 라벨/후보 결정 ---------------------------------------------------
def test_확정_트랙의_검출은_라벨이고_공백은_후보다():
    # 0~29 관측하되 15번만 빠짐 → 15는 보간 후보, 0·30(=관측 없음, 트랙 밖)은 제외
    frames = [f for f in range(30) if f != 15]
    labels, cands = al.plan_frame_labels([_moving_track(1, frames)], {}, [0, 15], 30.0, W, H)
    assert len(labels[0]) == 1
    assert 15 not in labels
    assert [c["why"] for c in cands[15]] == ["interp"]


def test_정지_트랙은_라벨이_되지_않고_static_후보가_된다():
    labels, cands = al.plan_frame_labels([_static_track(1, range(60))], {}, [0, 15], 30.0, W, H)
    assert labels == {}
    assert cands[0][0]["why"] == "static"


def test_추적되지_않은_검출은_저신뢰_후보다():
    labels, cands = al.plan_frame_labels([], {15: [al.Obs(15, (0, 0, 50, 200), 0.12)]},
                                         [15], 30.0, W, H)
    assert labels == {}
    assert cands[15][0]["why"] == "low_conf"
    assert cands[15][0]["cls"] == 0


def test_라벨과_겹치는_후보는_버린다():
    confirmed = _moving_track(1, range(30))
    dup = al.Obs(0, confirmed.obs[0].box, 0.2)       # 같은 지팡이를 다시 잡은 저신뢰 검출
    labels, cands = al.plan_frame_labels([confirmed], {0: [dup]}, [0], 30.0, W, H)
    assert len(labels[0]) == 1
    assert 0 not in cands


def test_좌표는_정규화되고_화면_밖은_잘린다():
    cx, cy, w, h = al.xyxy_to_cxcywh_norm((-10, 0, 1930, 1080), W, H)
    assert (cx, cy, w, h) == pytest.approx((0.5, 0.5, 1.0, 1.0))


# --- 재실행 보호 ---------------------------------------------------------------
def test_검수_이력은_label_tool_형식을_읽는다(tmp_path):
    (tmp_path / "reviewed.json").write_text(json.dumps({
        "train/tr_vid_20260930_001_f000000.jpg": True,
        "train/tr_vid_20260930_001_f000015.jpg": False,
    }), encoding="utf-8")
    assert al.load_reviewed(tmp_path) == {"tr_vid_20260930_001_f000000.jpg"}


def test_검수_이력이_없으면_빈_집합(tmp_path):
    assert al.load_reviewed(tmp_path) == set()
