"""add_source_variant.py 테스트 — 새 소스를 train에만 더하고 잣대(val/test)는 그대로 두는지."""
import json
import os
import sys
from pathlib import Path

import pytest

import add_source_variant as asv

CLIP = "tr_vid_20260930_001"


def _put(root: Path, split: str, name: str, label: str = "0 0.5 0.5 0.1 0.3\n"):
    (root / split / "images").mkdir(parents=True, exist_ok=True)
    (root / split / "labels").mkdir(parents=True, exist_ok=True)
    (root / split / "images" / name).write_bytes(b"\xff\xd8\xff")
    (root / split / "labels" / (Path(name).stem + ".txt")).write_text(label, encoding="utf-8")


def _base(tmp_path):
    base = tmp_path / "base"
    _put(base, "train", "a_jpg.rf.0001.jpg")
    _put(base, "val", "b_jpg.rf.0002.jpg")
    _put(base, "test", "c_jpg.rf.0003.jpg")
    return base


def _source(tmp_path, names, reviewed=True):
    src = tmp_path / "src"
    for n in names:
        _put(src, "train", n)
    if reviewed:
        (src / "reviewed.json").write_text(
            json.dumps({f"train/{n}": True for n in names}), encoding="utf-8")
    return src


def _run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["add_source_variant.py", *map(str, argv)])
    asv.main()


def test_train에만_더하고_val_test는_같은_파일이다(tmp_path, monkeypatch):
    base = _base(tmp_path)
    src = _source(tmp_path, [f"{CLIP}_f000000.jpg", f"{CLIP}_f000015.jpg"])
    out = tmp_path / "v4"
    _run(monkeypatch, "--dataset", base, "--source", src, "--out", out,
         "--data-yaml", tmp_path / "data_v4.yaml")

    assert sorted(p.name for p in (out / "train" / "images").iterdir()) == \
        ["a_jpg.rf.0001.jpg", f"{CLIP}_f000000.jpg", f"{CLIP}_f000015.jpg"]
    for s, n in (("val", "b_jpg.rf.0002"), ("test", "c_jpg.rf.0003")):
        assert os.path.samefile(out / s / "labels" / f"{n}.txt", base / s / "labels" / f"{n}.txt")
        assert len(list((out / s / "images").iterdir())) == 1
    manifest = json.loads((out / "source_manifest.json").read_text(encoding="utf-8"))
    assert manifest["unreviewed"] == 0
    assert list(manifest["groups"]) == [f"clip_{CLIP}"]
    assert "path:" in (tmp_path / "data_v4.yaml").read_text(encoding="utf-8")


def test_평가용_ev_프레임은_거부한다(tmp_path):
    r = asv.check(_base(tmp_path), _source(tmp_path, ["ev_lab_20260920_001_f000000.jpg"]))
    assert any("학습용 클립 프레임" in e for e in r["errors"])


def test_클립_규칙이_아닌_파일은_거부한다(tmp_path):
    r = asv.check(_base(tmp_path), _source(tmp_path, ["random.jpg"]))
    assert r["errors"]


def test_검수_안_된_프레임이_있으면_거부한다(tmp_path):
    r = asv.check(_base(tmp_path), _source(tmp_path, [f"{CLIP}_f000000.jpg"], reviewed=False))
    assert any("검수 안 된" in e for e in r["errors"])


def test_allow_unreviewed는_경고로_낮춘다(tmp_path):
    r = asv.check(_base(tmp_path), _source(tmp_path, [f"{CLIP}_f000000.jpg"], reviewed=False),
                  allow_unreviewed=True)
    assert not r["errors"]
    assert any("검수 안 된" in w for w in r["warnings"])


def test_base와_파일명이_충돌하면_거부한다(tmp_path):
    base = _base(tmp_path)
    _put(base, "train", f"{CLIP}_f000000.jpg")
    r = asv.check(base, _source(tmp_path, [f"{CLIP}_f000000.jpg"]))
    assert any("충돌" in e for e in r["errors"])


def test_val_test와_같은_클립이면_거부한다(tmp_path):
    base = _base(tmp_path)
    _put(base, "val", f"{CLIP}_f000300.jpg")            # 같은 클립의 다른 프레임이 val에
    r = asv.check(base, _source(tmp_path, [f"{CLIP}_f000000.jpg"]))
    assert any("누수" in e for e in r["errors"])


def test_라벨_기하가_깨지면_거부한다(tmp_path):
    src = _source(tmp_path, [f"{CLIP}_f000000.jpg"])
    (src / "train" / "labels" / f"{CLIP}_f000000.txt").write_text("0 0.5 0.5 0 0.3\n")
    r = asv.check(_base(tmp_path), src)
    assert any("기하" in e for e in r["errors"])


def test_검증_실패시_아무것도_만들지_않는다(tmp_path, monkeypatch):
    base = _base(tmp_path)
    src = _source(tmp_path, [f"{CLIP}_f000000.jpg"], reviewed=False)
    out = tmp_path / "v4"
    with pytest.raises(SystemExit):
        _run(monkeypatch, "--dataset", base, "--source", src, "--out", out,
             "--data-yaml", tmp_path / "d.yaml")
    assert not out.exists()
    assert not (tmp_path / "d.yaml").exists()
