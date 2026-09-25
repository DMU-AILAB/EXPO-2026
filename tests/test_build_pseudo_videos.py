"""의사 영상 생성기(`tools/eval/build_pseudo_videos.py`)의 계약을 고정한다.

**여기서 지켜야 하는 것은 GT 왕복이다.** `_spans()`가 만든 [[시작초, 끝초], ...]를
`eval_video_recall._load_gt()`가 되읽어 **정확히 같은 프레임 집합**을 복원해야 한다.
한 프레임만 밀려도 재현율·오탐율의 분모가 조용히 틀어지고, 그 위에서 내린 모델 채택
판정이 전부 오염된다 — 터지지 않고 틀리는 종류의 버그라 테스트로 막는다.

`_load_gt`를 직접 부르지 않고 그 계산식을 그대로 옮겨 쓴다. 그 함수는 argparse
네임스페이스와 실제 영상 파일을 요구해서 단위 테스트로 부르기에 무겁고, 여기서
검증하려는 것은 **두 쪽의 인덱스 계산이 맞물리는가**이기 때문이다. 식이 바뀌면
이 테스트가 깨져야 한다(그게 목적이다).
"""

from __future__ import annotations

import numpy as np
import pytest

build_pseudo_videos = pytest.importorskip("build_pseudo_videos")

_spans = build_pseudo_videos._spans
_build_chain = build_pseudo_videos._build_chain

FPS = 12.76          # Pi 실측값 — 스크립트 기본값과 같다


def _restore(spans, n_frames, fps=FPS):
    """`eval_video_recall._load_gt`와 같은 방식으로 구간 → 프레임 마스크."""
    mask = np.zeros(n_frames, dtype=bool)
    for a, b in spans:
        mask[int(round(a * fps)):int(round(b * fps)) + 1] = True
    return mask


@pytest.mark.parametrize("flags", [
    pytest.param([True] * 10, id="전부-지팡이"),
    pytest.param([False] * 10, id="전부-없음"),
    pytest.param([True] * 5 + [False] * 5, id="앞쪽만"),
    pytest.param([False] * 5 + [True] * 5, id="뒤쪽만"),
    pytest.param([True, False, True, False, True], id="교대"),
    pytest.param([True] * 49 + [False] + [True] * 50, id="가운데-한프레임-구멍"),
    pytest.param([False] + [True] * 98 + [False], id="양끝만-없음"),
    pytest.param([True], id="한프레임"),
])
def test_gt_왕복이_정확히_같은_프레임을_복원한다(flags):
    spans = _spans(flags, FPS)
    restored = _restore(spans, len(flags))
    assert restored.tolist() == flags


def test_실제_의사영상_길이대로_왕복한다():
    """생성된 세션들의 실제 길이(49~100프레임)에서도 어긋나지 않아야 한다.

    round()가 개입하므로 길이에 따라 경계가 밀릴 수 있다 — 전 길이를 훑는다.
    """
    for n in range(1, 121):
        flags = [(i % 7) != 3 for i in range(n)]     # 주기적으로 구멍이 뚫린 패턴
        assert _restore(_spans(flags, FPS), n).tolist() == flags, f"n={n}"


def test_빈_구간은_빈_목록이다():
    assert _spans([False] * 20, FPS) == []


class _FakeImg:
    """`_thumbs`가 읽을 수 있는 가짜 경로 — cv2.imread를 거치지 않도록 주입한다."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.stem = name


def test_반전본이_섞여도_한_방향으로_정렬된다(monkeypatch):
    """`_build_chain`의 핵심 계약 — 되뒤집기 플래그가 방향을 일관되게 만든다.

    좌우가 뚜렷이 다른 패턴을 만들고, 홀수 인덱스만 미리 반전시켜 둔다.
    정렬이 제대로 되면 그 홀수 인덱스들에 flip=True가 붙어야 한다.
    """
    base = np.zeros((8, 8), dtype=np.float32)
    base[:, :3] = 1.0                      # 왼쪽이 밝은 비대칭 패턴

    frames, byidx = {}, {}
    for i in range(10):
        img = np.fliplr(base) if i % 2 else base
        p = _FakeImg(f"f{i}")
        frames[p] = img.copy()
        byidx[i] = [p]

    monkeypatch.setattr(build_pseudo_videos, "_thumbs", lambda paths: frames)

    chain = _build_chain(byidx)
    assert len(chain) == 10
    flips = [f for _, f in chain]
    assert flips[0] is False                       # 기준 프레임은 그대로 둔다
    assert all(flips[i] for i in range(1, 10, 2)), "반전본을 되뒤집지 못했다"
    assert not any(flips[i] for i in range(2, 10, 2)), "멀쩡한 프레임을 뒤집었다"


def test_애매한_차이에서는_직전_방향을_유지한다(monkeypatch):
    """히스테리시스 — 좌우 대칭이라 direct/mirror 차이가 없으면 뒤집지 않는다.

    이 편향이 없으면 카메라 팬 구간에서 약 10%의 전이가 뒤집힌다(리포트 §13-1).
    """
    sym = np.ones((8, 8), dtype=np.float32)        # 완전 대칭 = 차이 0
    frames, byidx = {}, {}
    for i in range(6):
        p = _FakeImg(f"s{i}")
        frames[p] = sym.copy()
        byidx[i] = [p]

    monkeypatch.setattr(build_pseudo_videos, "_thumbs", lambda paths: frames)

    assert not any(f for _, f in _build_chain(byidx))
