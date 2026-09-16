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
