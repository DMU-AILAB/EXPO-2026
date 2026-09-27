"""구조물 마스크(`device/static_mask.py`)의 계약을 고정한다.

**여기서 지켜야 하는 것은 "사람을 가리지 않는다"이다.** 마스크는 현장 고정물 오탐을
없애려고 넣는 기능인데, 잘못 만들면 그 앞을 지나는 사람까지 지워 **안내가 조용히
사라진다**. 사람 동반 게이트는 모든 트리거의 필수 조건이라, 이 실패는 터지지 않고
안 들리는 형태로 나타난다 — 그래서 테스트로 막는다.
"""

from __future__ import annotations

import json

import pytest

static_mask = pytest.importorskip("static_mask")

W, H = 640, 480


def det(x1, y1, x2, y2, cls=0, conf=0.9):
    """픽셀 좌표 탐지 하나 (device 쪽 탐지 dict 형식과 같은 키)."""
    return {"bbox": [x1, y1, x2, y2], "class": cls, "conf": conf}


def mask_of(*boxes_px, cls=0):
    """픽셀 박스를 정규화해 마스크를 만든다."""
    return static_mask.StaticMask(
        [{"cls": cls, "bbox": [x1 / W, y1 / H, x2 / W, y2 / H]}
         for (x1, y1, x2, y2) in boxes_px])


# ---------------------------------------------------------------------------
# 매칭 규칙
# ---------------------------------------------------------------------------
def test_같은_위치_크기_클래스는_마스크에_걸린다():
    m = mask_of((100, 100, 140, 300))
    assert m.matches(det(100, 100, 140, 300), W, H)


def test_박스가_살짝_흔들려도_걸린다():
    """탐지 박스는 프레임마다 몇 px씩 떨린다 — 그 정도로 마스크가 빗나가면 쓸모없다."""
    m = mask_of((100, 100, 140, 300))
    assert m.matches(det(102, 98, 142, 303), W, H)


def test_같은_자리라도_크기가_다르면_안_걸린다():
    """**기둥 앞에 선 사람**이 이 경우다 — 중심점은 같지만 박스 모양이 다르다.

    기존 제외구역(중심점 ∈ 폴리곤)이 못 가르던 바로 그 구분이고,
    이 기능이 존재하는 이유다.
    """
    m = mask_of((100, 100, 140, 300))          # 가는 기둥
    person = det(60, 80, 190, 420)             # 그 앞에 선 사람 — 훨씬 넓고 크다
    assert not m.matches(person, W, H)


def test_클래스가_다르면_안_걸린다():
    """지팡이로 오탐되는 구조물을 막을 때 사람 탐지까지 죽이면 안 된다."""
    m = mask_of((100, 100, 140, 300), cls=0)
    assert not m.matches(det(100, 100, 140, 300, cls=1), W, H)


def test_먼_위치는_안_걸린다():
    m = mask_of((100, 100, 140, 300))
    assert not m.matches(det(400, 100, 440, 300), W, H)


def test_빈_마스크는_아무것도_걸지_않는다():
    """마스크 파일이 없는 기존 기기에서 동작이 1비트도 바뀌면 안 된다."""
    m = static_mask.StaticMask([])
    assert not m.matches(det(100, 100, 140, 300), W, H)
    dets = [det(100, 100, 140, 300)]
    assert all(d["static_masked"] is False for d in m.annotate(dets, W, H))


def test_annotate는_버리지_않고_표시만_한다():
    """실제 제거는 gate_chain이 트랙의 움직임까지 보고 정한다 — 여기서 버리면
    움직이는 사람을 구제할 기회가 사라진다."""
    m = mask_of((100, 100, 140, 300))
    dets = [det(100, 100, 140, 300), det(400, 100, 440, 300)]
    out = m.annotate(dets, W, H)
    assert len(out) == 2                      # 개수가 줄지 않는다
    assert out[0]["static_masked"] is True
    assert out[1]["static_masked"] is False


def test_깨진_탐지에도_예외를_던지지_않는다():
    """탐지 루프에서 불리므로 어떤 입력에도 죽으면 안 된다."""
    m = mask_of((100, 100, 140, 300))
    assert not m.matches({}, W, H)
    assert not m.matches({"bbox": [1, 2]}, W, H)
    assert not m.matches(det(100, 100, 140, 300), 0, 0)      # 0 나눗셈


# ---------------------------------------------------------------------------
# 수집
# ---------------------------------------------------------------------------
def test_흔들리는_박스가_한_클러스터로_묶인다():
    c = static_mask.MaskCollector()
    for i in range(10):
        c.add([det(100 + i % 3, 100, 140 + i % 3, 300)], W, H)
    rows = c.finish()
    assert len(rows) == 1
    assert rows[0]["hits"] == 10
    assert rows[0]["frames"] == 10


def test_잠깐_스쳐간_것은_후보에서_빠진다():
    """청소 인력이 한 번 지나간 자리를 구조물로 굳히면 그게 사각지대가 된다."""
    c = static_mask.MaskCollector()
    for _ in range(20):
        c.add([det(100, 100, 140, 300)], W, H)          # 붙박이
    c.add([det(100, 100, 140, 300), det(400, 50, 500, 400)], W, H)   # 한 번만 등장
    rows = c.finish(min_hit_ratio=0.3)
    assert len(rows) == 1
    assert rows[0]["bbox"][0] == pytest.approx(100 / W, abs=1e-3)


def test_이동량이_기록된다():
    """진짜 고정물은 0에 가깝다 — 운영자가 '이건 정말 안 움직였나'를 보는 근거.

    같은 클러스터에 머무는 범위 안에서 흔들린 경우를 본다(크게 움직이면 아래
    테스트처럼 아예 후보가 되지 않는다).
    """
    c = static_mask.MaskCollector()
    for i in range(10):
        c.add([det(100 + i, 100, 140 + i, 300)], W, H)      # 조금씩 흔들림
    moved = c.finish()[0]["max_disp"]

    c2 = static_mask.MaskCollector()
    for _ in range(10):
        c2.add([det(100, 100, 140, 300)], W, H)
    still = c2.finish()[0]["max_disp"]

    assert still == pytest.approx(0.0, abs=1e-6)
    assert moved > still


def test_빠르게_이동하는_물체는_후보가_되지_않는다():
    """지나가는 사람이 구조물로 굳으면 그 자리가 사각지대가 된다.

    매 프레임 클러스터를 벗어날 만큼 움직이면 어느 클러스터도 관측 비율을 못 채워
    자동으로 걸러진다 — 별도 로직 없이 클러스터링 자체가 막아 준다.
    """
    c = static_mask.MaskCollector()
    for i in range(10):
        c.add([det(50 + i * 60, 100, 90 + i * 60, 300)], W, H)
    assert c.finish() == []


def test_클래스가_다르면_다른_클러스터다():
    c = static_mask.MaskCollector()
    for _ in range(10):
        c.add([det(100, 100, 140, 300, cls=0), det(100, 100, 140, 300, cls=1)], W, H)
    rows = c.finish()
    assert {r["cls"] for r in rows} == {0, 1}


def test_수집_프레임이_없으면_빈_결과():
    assert static_mask.MaskCollector().finish() == []


# ---------------------------------------------------------------------------
# 저장 (sqlite / json)
# ---------------------------------------------------------------------------
def test_후보_저장과_조회(tmp_path):
    db = tmp_path / "t.db"
    rows = [{"cls": 0, "bbox": [0.1, 0.2, 0.3, 0.4], "hits": 50, "frames": 60,
             "max_disp": 0.001, "max_conf": 0.8, "thumb": "a.jpg"}]
    assert static_mask.save_candidates(db, rows, rotation=90, preset="640x480") == 1
    got = static_mask.read_candidates(db)
    assert len(got) == 1
    assert got[0]["cls"] == 0 and got[0]["hits"] == 50
    assert got[0]["rotation"] == 90 and got[0]["preset"] == "640x480"


def test_후보_저장은_누적이_아니라_교체다(tmp_path):
    """재캘리브레이션은 '지금 이 현장'을 다시 찍는 것이다 — 낡은 후보가 남으면 안 된다."""
    db = tmp_path / "t.db"
    mk = lambda n: [{"cls": 0, "bbox": [0.1, 0.2, 0.3, 0.4], "hits": n, "frames": n,
                     "max_disp": 0.0, "max_conf": 0.5, "thumb": None}]
    static_mask.save_candidates(db, mk(10))
    static_mask.save_candidates(db, mk(20))
    got = static_mask.read_candidates(db)
    assert len(got) == 1 and got[0]["hits"] == 20


def test_없는_db를_읽어도_예외가_없다(tmp_path):
    assert static_mask.read_candidates(tmp_path / "없음.db") == []
    assert static_mask.read_mask_hits(tmp_path / "없음.db") == []


def test_적중_로그는_클래스별로_누적된다(tmp_path):
    db = tmp_path / "t.db"
    for _ in range(3):
        static_mask.log_mask_hit(db, 0)
    static_mask.log_mask_hit(db, 1)
    hits = {h["cls"]: h["count"] for h in static_mask.read_mask_hits(db)}
    assert hits == {0: 3, 1: 1}


def test_마스크_파일_왕복(tmp_path):
    p = tmp_path / "static_mask.json"
    static_mask.save_mask_file(p, [{"cls": 0, "bbox": [0.1, 0.2, 0.3, 0.4]}],
                               meta={"rotation": 0})
    m = static_mask.load_mask_file(p)
    assert len(m) == 1
    assert json.loads(p.read_text())["meta"]["rotation"] == 0


def test_마스크_파일이_없거나_깨져도_빈_마스크(tmp_path):
    """기존 기기에는 이 파일이 없다 — 그때 동작이 바뀌면 안 된다."""
    assert len(static_mask.load_mask_file(tmp_path / "없음.json")) == 0
    bad = tmp_path / "bad.json"
    bad.write_text("{{{", encoding="utf-8")
    assert len(static_mask.load_mask_file(bad)) == 0


def test_깨진_항목은_건너뛰고_나머지를_읽는다(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"boxes": [
        {"cls": 0, "bbox": [0.1, 0.2, 0.3, 0.4]},
        {"cls": 0, "bbox": [0.1, 0.2]},          # 길이 부족
        {"bbox": [0.1, 0.2, 0.3, 0.4]},          # cls 없음
    ]}), encoding="utf-8")
    assert len(static_mask.load_mask_file(p)) == 1
