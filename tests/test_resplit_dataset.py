"""resplit_dataset.py 단위 테스트 — 그룹키와 누수 0을 못박는다.

이 스크립트의 유일한 목적이 "같은 원본/같은 촬영 장면이 split을 넘나들지 않게
하는 것"이라, 그룹키가 조용히 틀리면 스크립트를 돌려도 누수가 그대로 남는다.
실제로 개발 중에 정규식 두 개가 모두 틀려 재분할 후에도 누수가 18.7% 남았다
(대문자 `_JPG` 확장자, 하이픈 구분자 `HHMMSS-0-`). 그 두 경우를 회귀로 고정한다.
"""
import sys
from pathlib import Path


import resplit_dataset as r


def _sessions(names):
    return r.build_aihub_sessions(names)


# ---------------------------------------------------------------------------
# Roboflow 증강본 — 같은 원본에서 나온 것은 같은 그룹이어야 한다
# ---------------------------------------------------------------------------
def test_roboflow_augmentations_of_same_original_share_a_group():
    s = _sessions([])
    a = "100_jpg.rf.46426c9003a1558409aabc063f7830e2.jpg"
    b = "100_jpg.rf.9ef0690164f18fa821abdcf23eb79488.jpg"
    assert r.group_key(a, s) == r.group_key(b, s)


def test_uppercase_extension_token_is_stripped():
    """확장자 토큰은 원본 파일 확장자를 그대로 옮긴 것이라 대소문자가 섞인다
    (실측: _jpg 10,625 / _JPG 2,623 / _png 2). 소문자만 처리하면 대문자 파일의
    증강본이 서로 다른 그룹이 되어 같은 원본이 train과 test로 갈라진다."""
    s = _sessions([])
    a = "IMG_7876_JPG.rf.fdeac7c8d7476612fae64ea902df5c93.jpg"
    b = "IMG_7876_JPG.rf.ed2dfe01f76c12e719eb2457169843f5.jpg"
    assert r.group_key(a, s) == r.group_key(b, s)
    assert "rf." not in r.group_key(a, s)


def test_png_extension_token_is_stripped():
    s = _sessions([])
    a = "image_png.rf.eedd1111111111111111111111111111.jpg"
    b = "image_png.rf.ffff2222222222222222222222222222.jpg"
    assert r.group_key(a, s) == r.group_key(b, s)


def test_different_originals_stay_in_different_groups():
    s = _sessions([])
    a = "100_jpg.rf.46426c9003a1558409aabc063f7830e2.jpg"
    b = "101_jpg.rf.46426c9003a1558409aabc063f7830e2.jpg"
    assert r.group_key(a, s) != r.group_key(b, s)


# ---------------------------------------------------------------------------
# AIHub 촬영 세션 — 간격이 가까운 촬영은 같은 장면이라 함께 묶여야 한다
# ---------------------------------------------------------------------------
def test_aihub_nearby_seconds_share_a_session():
    names = ["20210514_182348_001_jpg.rf.aaaaaaaa.jpg",
             "20210514_182415_002_jpg.rf.bbbbbbbb.jpg"]   # 27초 차 → 같은 세션
    s = _sessions(names)
    assert r.group_key(names[0], s) == r.group_key(names[1], s)


def test_aihub_far_apart_seconds_split_into_sessions():
    names = ["20210514_182348_001_jpg.rf.aaaaaaaa.jpg",
             "20210514_182600_002_jpg.rf.bbbbbbbb.jpg"]   # 132초 차 → 다른 세션
    s = _sessions(names)
    assert r.group_key(names[0], s) != r.group_key(names[1], s)


def test_aihub_hyphen_separator_is_recognized():
    """HHMMSS 뒤 구분자가 하이픈인 파일이 8장 있다(20210514_182405-0-_jpg...).
    밑줄만 받으면 이 파일들이 촬영세션이 아니라 파일명 단위로 묶여 인접 세션과
    split이 갈린다."""
    names = ["20210514_182359-0-_jpg.rf.aaaaaaaa.jpg",
             "20210514_182405-0-_jpg.rf.bbbbbbbb.jpg"]
    s = _sessions(names)
    assert r.group_key(names[0], s).startswith("aihub_s")
    assert r.group_key(names[0], s) == r.group_key(names[1], s)


# ---------------------------------------------------------------------------
# 층(stratum) — 네거티브가 val/test에도 들어가야 오탐지가 지표에 잡힌다
# ---------------------------------------------------------------------------
def test_strata_classification():
    assert r.stratum_of("bg_0001.jpg") == "bg"
    assert r.stratum_of("lk_0001.jpg") == "lk"
    assert r.stratum_of("pedcctv_x_jpg.rf.aaaa.jpg") == "pedcctv"
    assert r.stratum_of("20210514_182348_001_jpg.rf.aaaa.jpg") == "aihub_cane"
    assert r.stratum_of("100_jpg.rf.aaaa.jpg") == "roboflow_cane"


def test_negatives_are_their_own_groups():
    """배경/유사물은 서로 독립된 사진이라 1파일 1그룹이어야 한다 — 묶이면
    val/test 배분이 거칠어진다."""
    s = _sessions([])
    assert r.group_key("bg_0001.jpg", s) != r.group_key("bg_0002.jpg", s)


# ---------------------------------------------------------------------------
# 산출물 — 실제로 생성된 datasets/v2가 있으면 누수 0을 검증한다
# ---------------------------------------------------------------------------
def test_generated_split_has_no_group_leakage():
    import json
    import pytest
    if not r.MANIFEST.exists():
        pytest.skip("datasets/v2 미생성 — `python resplit_dataset.py` 먼저 실행")
    manifest = json.loads(r.MANIFEST.read_text(encoding="utf-8"))
    by_split: dict[str, set[str]] = {}
    for info in manifest.values():
        by_split.setdefault(info["split"], set()).add(info["group"])
    for a, b in (("train", "val"), ("train", "test"), ("val", "test")):
        assert not (by_split[a] & by_split[b]), f"{a}∩{b} 누수"


def test_generated_split_puts_negatives_in_val_and_test():
    """네거티브가 train에만 몰려 있던 것이 이 재분할의 수정 대상 중 하나다."""
    import json
    import pytest
    if not r.MANIFEST.exists():
        pytest.skip("datasets/v2 미생성")
    manifest = json.loads(r.MANIFEST.read_text(encoding="utf-8"))
    for split in ("val", "test"):
        negs = sum(1 for i in manifest.values()
                   if i["split"] == split and i["stratum"] in ("bg", "lk"))
        assert negs >= 50, f"{split}의 네거티브 {negs}장 — 오탐지 지표가 둔감해진다"


# --------------------------------------------------------------------- #
# 영상 프레임 — 한 클립 = 한 그룹
#
# 이 규칙이 없으면 프레임마다 다른 그룹이 되어 거의 같은 장면이 train/val/test에
# 흩어진다. AIHub 연속 촬영에서 실측 누수율 99.7%를 만든 바로 그 경로다.
# --------------------------------------------------------------------- #

def test_frames_of_same_clip_share_a_group():
    s = r.build_aihub_sessions([])
    a = "tr_lab_20260920_007_f000180.jpg"
    b = "tr_lab_20260920_007_f000600.jpg"
    assert r.group_key(a, s) == r.group_key(b, s)


def test_different_clips_are_different_groups():
    s = r.build_aihub_sessions([])
    a = "tr_lab_20260920_007_f000180.jpg"
    b = "tr_lab_20260920_008_f000180.jpg"
    assert r.group_key(a, s) != r.group_key(b, s)


def test_train_and_eval_clips_never_share_a_group():
    """학습용(tr_)과 평가용(ev_)은 촬영 세션 자체가 달라야 한다는 규칙의 최소 방어선."""
    s = r.build_aihub_sessions([])
    assert r.group_key("tr_lab_20260920_001_f000001.jpg", s) != \
           r.group_key("ev_lab_20260921_001_f000001.jpg", s)


def test_clip_frames_get_their_own_stratum():
    """자체 촬영본은 기존 소스(실외/AIHub)와 도메인이 다르다.

    같은 층에 섞으면 실내 프레임이 val/test에 거의 안 들어가 실내 지표가 둔감해진다.
    """
    assert r.stratum_of("tr_lab_20260920_007_f000180.jpg").startswith("clip_")
    assert r.stratum_of("tr_lab_20260920_007_f000180.jpg") != \
           r.stratum_of("IMG_7876_JPG.rf.abc.jpg")


def test_existing_filenames_are_untouched_by_the_clip_rule():
    """회귀 방어 — 기존 21,631장(v1·v2 train)에 `_f<숫자>` 형태가 0건임을 확인하고
    도입했다. 새 규칙이 기존 파일을 잡기 시작하면 그룹이 통째로 뒤바뀐다."""
    s = r.build_aihub_sessions(["20210514_182405_0_jpg.rf.def.jpg"])
    for name, expect_prefix in [
        ("IMG_7876_JPG.rf.abc123.jpg", "rf_"),
        ("20210514_182405_0_jpg.rf.def.jpg", "aihub_"),
        ("bg_0042.jpg", "rf_"),
        ("lk_0100.jpg", "rf_"),
        ("pedcctv_0007.jpg", "rf_"),
    ]:
        assert r.group_key(name, s).startswith(expect_prefix), name


def test_short_frame_suffix_is_not_treated_as_a_clip():
    """`_f` + 3자리 이하는 클립으로 보지 않는다 — 우연한 파일명과 충돌하지 않게."""
    s = r.build_aihub_sessions([])
    assert r.group_key("some_photo_f12.jpg", s).startswith("rf_")
