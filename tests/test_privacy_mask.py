"""얼굴 모자이크 단위 테스트 — `device/privacy_mask.py`."""
from __future__ import annotations

import numpy as np
import pytest

import privacy_mask as pm


def _frame(h=200, w=300, seed=1):
    return np.random.default_rng(seed).integers(0, 256, (h, w, 3), dtype=np.uint8)


def test_상단만_가리고_하단은_그대로():
    f = _frame()
    orig = f.copy()
    assert pm.mask_box(f, (100, 40, 160, 190), 0.25)    # 박스 높이 150 → 상단 37px
    assert not np.array_equal(f[40:75, 100:160], orig[40:75, 100:160])
    assert np.array_equal(f[120:200, :], orig[120:200, :])      # 몸통 아래는 불변
    assert np.array_equal(f[:, :80], orig[:, :80])              # 좌우 먼 곳 불변


def test_가림_영역은_박스_안에_머문다():
    reg = pm.mask_region((100, 50, 200, 350), 0.25, (400, 400))
    assert reg == (100, 50, 200, 125)      # 폭은 박스 그대로, 높이는 상단 25%(h/w=3)


def test_서_있는_박스는_비율_그대로():
    # h/w = 3 → 0.7w = 0.233h < 0.25h 이므로 종전과 같다
    assert pm.mask_region((100, 50, 150, 200), 0.25, (400, 400)) == (100, 50, 150, 88)


def test_앉은_박스는_폭_기준으로_늘어난다():
    # w=100, h=150 (h/w=1.5) → 0.7w=70px (0.467h), 고정 25%면 37px
    reg = pm.mask_region((100, 50, 200, 200), 0.25, (400, 400))
    assert reg == (100, 50, 200, 120)


def test_넓은_박스도_상한을_넘지_않는다():
    reg = pm.mask_region((0, 0, 200, 100), 0.25, (400, 400))
    assert reg[3] == int(round(100 * pm.MAX_RATIO))


def test_프레임_경계를_넘어도_안전하다():
    f = _frame()
    assert pm.mask_box(f, (-30, -20, 40, 120), 0.3)
    assert pm.mask_box(f, (280, 150, 400, 400), 0.3) in (True, False)   # 예외 없이


@pytest.mark.parametrize("box", [(10, 10, 10, 50), (10, 10, 5, 5), (500, 500, 600, 700)])
def test_퇴화_박스는_무시한다(box):
    f = _frame()
    orig = f.copy()
    assert pm.mask_box(f, box, 0.25) is False
    assert np.array_equal(f, orig)


def test_비율은_범위로_잘린다():
    assert pm.clamp_ratio(0.0) == pm.MIN_RATIO
    assert pm.clamp_ratio(5.0) == pm.MAX_RATIO


def test_픽셀화는_블록_상수_영역을_만든다():
    f = _frame()
    pm.mask_box(f, (100, 20, 220, 190), 0.4)
    reg = pm.mask_region((100, 20, 220, 190), 0.4, f.shape[:2])
    x1, y1, x2, y2 = reg
    sub = f[y1:y2, x1:x2]
    # 가로로 인접한 픽셀이 같은 값인 구간이 대부분 — 원본 노이즈라면 거의 없다
    same = (sub[:, 1:] == sub[:, :-1]).all(axis=2).mean()
    assert same > 0.6


def test_사라진_박스도_hold_동안_가린다():
    mk = pm.PrivacyMasker(0.25, hold_sec=0.5)
    mk.update([(100, 40, 160, 190)], now=10.0)
    mk.update([], now=10.4)                 # 탐지 끊김
    f = _frame()
    orig = f.copy()
    assert mk.apply(f) == 1
    assert not np.array_equal(f, orig)


def test_hold가_지나면_해제된다():
    mk = pm.PrivacyMasker(0.25, hold_sec=0.5)
    mk.update([(100, 40, 160, 190)], now=10.0)
    mk.update([], now=10.6)
    f = _frame()
    orig = f.copy()
    assert mk.apply(f) == 0
    assert np.array_equal(f, orig)


def test_여러_사람을_모두_가린다():
    mk = pm.PrivacyMasker(0.25)
    mk.update([(20, 20, 70, 150), (200, 30, 260, 180)], now=1.0)
    f = _frame()
    assert mk.apply(f) == 2


def test_apply는_원본_dtype과_shape를_유지한다():
    mk = pm.PrivacyMasker(0.3)
    mk.update([(20, 20, 70, 150)], now=1.0)
    f = _frame()
    mk.apply(f)
    assert f.dtype == np.uint8 and f.shape == (200, 300, 3)


def test_기본_유지시간은_0_5초():
    assert pm.HOLD_SEC == 0.5


def test_걷는_사람_뒤에_꼬리가_남지_않는다():
    mk = pm.PrivacyMasker(0.25, hold_sec=0.5)
    for i in range(10):                              # 프레임마다 8px 이동, 박스는 서로 겹친다
        mk.update([(20 + 8 * i, 40, 80 + 8 * i, 190)], now=i * 0.1)
    assert len(mk.boxes()) == 1


def test_같은_시각의_여러_호출은_쌓인다():
    mk = pm.PrivacyMasker(0.25)
    mk.update([(100, 40, 160, 190)], now=5.0)        # raw 탐지
    mk.update([(105, 40, 165, 190)], now=5.0)        # 트래커 박스 — 앞의 것을 지우면 안 된다
    assert len(mk.boxes()) == 2


def test_겹치지_않는_끊긴_박스만_남는다():
    mk = pm.PrivacyMasker(0.25, hold_sec=0.5)
    mk.update([(10, 40, 60, 190), (200, 40, 260, 190)], now=1.0)
    mk.update([(12, 40, 62, 190)], now=1.1)          # 오른쪽 사람은 탐지가 끊겼다
    boxes = mk.boxes()
    assert len(boxes) == 2 and any(b[0] >= 200 for b in boxes)
