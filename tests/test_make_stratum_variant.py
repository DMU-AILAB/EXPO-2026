"""층 변형 생성기(`tools/data/make_stratum_variant.py`)의 선택 규칙을 고정한다.

**여기서 막는 것은 split 배정과의 상관이다.** `resplit_dataset.split_groups()`는
그룹을 `md5(group)` 순서로 정렬해 상위 15%를 test, 다음 15%를 val로 보낸다. 따라서
**train에 남은 그룹은 md5가 상위 70% 구간에만 존재한다.**

이 사실을 모르고 같은 `md5(group)`로 "비율만큼 남기기"를 하면 두 분포가 겹쳐서
selection이 망가진다 — 실제로 소금을 치기 전에는 `keep=0.5`가 29%만 남기고
`keep=0.25`는 **한 장도 남기지 않았다**. 터지지 않고 조용히 틀리는 종류라
(빈 층으로 학습이 그냥 돌아간다) 테스트로 고정한다.
"""

from __future__ import annotations

import hashlib

import pytest

msv = pytest.importorskip("make_stratum_variant")
resplit = pytest.importorskip("resplit_dataset")

_keep = msv._keep


def _train_groups(n: int = 4000) -> list[str]:
    """`resplit_dataset`과 **같은 규칙**으로 split을 배정하고 train 몫만 돌려준다.

    규칙을 여기 옮겨 적는 이유는, 이 테스트가 지키려는 것이 "두 해시가 독립인가"이고
    그러려면 배정 규칙을 명시적으로 재현해야 하기 때문이다. 규칙이 바뀌면 이 테스트도
    같이 갱신되어야 한다.
    """
    gs = [f"rf_pedcctv_{i}" for i in range(n)]
    ordered = sorted(gs, key=lambda g: hashlib.md5(g.encode()).hexdigest())
    n_test = round(n * resplit.TEST_RATIO)
    n_val = round(n * resplit.VAL_RATIO)
    return ordered[n_test + n_val:]


def test_train_그룹은_해시_상위구간에_몰려있다():
    """전제 확인 — 이 편향이 실재하기 때문에 소금이 필요하다."""
    train = _train_groups()
    vals = [int(hashlib.md5(g.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
            for g in train]
    assert min(vals) > 0.25, "전제가 깨졌다 — split 배정 규칙이 바뀌었는지 확인할 것"


@pytest.mark.parametrize("ratio", [0.25, 0.5, 0.75])
def test_선택_비율이_split_배정과_무관하다(ratio):
    """소금이 빠지면 여기서 걸린다 — keep=0.25가 0%가 되던 버그."""
    train = _train_groups()
    kept = [g for g in train if _keep(g, ratio)]
    frac = len(kept) / len(train)
    assert abs(frac - ratio) < 0.05, (
        f"keep={ratio}인데 실제 {frac:.3f}가 남았다 — "
        f"선택 해시가 split 배정 해시와 상관됐을 가능성이 높다")


def test_비율을_낮추면_남는_집합이_줄어들기만_한다():
    """포함 관계 — 비율 스윕이 단조로운 실험이 되려면 필요하다.

    `random.sample`을 쓰면 비율마다 완전히 다른 집합이 나와, 50% 변형과 25% 변형이
    포함 관계가 아니게 된다. 그러면 "줄일수록 어떻게 되는가"를 읽을 수 없다.
    """
    gs = [f"g{i}" for i in range(3000)]
    sets = {r: {g for g in gs if _keep(g, r)} for r in (0.0, 0.25, 0.5, 1.0)}
    assert sets[0.0] == set()
    assert sets[1.0] == set(gs)
    assert sets[0.25] <= sets[0.5] <= sets[1.0]


def test_같은_입력은_항상_같은_결과다():
    """결정적이어야 변형을 재생성해도 같은 데이터셋이 나온다."""
    gs = [f"g{i}" for i in range(500)]
    once = [_keep(g, 0.4) for g in gs]
    twice = [_keep(g, 0.4) for g in gs]
    assert once == twice
