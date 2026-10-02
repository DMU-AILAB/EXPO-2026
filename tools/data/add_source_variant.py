#!/usr/bin/env python3
"""add_source_variant.py — 확정된 split을 유지한 채 **새 소스를 train에만 더한** 변형.

로컬 전용(Pi 배포 대상 아님) 데이터 준비 도구. `make_stratum_variant.py`가 한 층을
**덜어내는** 변형이라면 이쪽은 **더하는** 변형이다.

## 왜 재분할(`resplit_dataset.py`)이 아닌가

- 새 소스(예: `autolabel_videos.py`로 만든 영상 프레임)를 **전부 train에 넣기로**
  했다면 새로 나눌 것이 없다.
- `resplit_dataset.py`는 입력이 `datasets/v1`로 고정이고 `datasets/v2`를 **덮어쓴다** —
  v10 등 기존 모델을 잰 잣대가 바뀐다.
- **val/test를 통째로 하드링크**해야 train 구성 하나만 바뀐 A/B가 된다
  (`make_stratum_variant.py`와 같은 원칙).

## 무엇을 검사하는가 (하나라도 실패하면 아무것도 만들지 않는다)

1. source의 모든 파일이 **학습용 클립 프레임**이다 — `resplit_dataset._CLIP_FRAME`에
   맞고 층이 `clip_tr`이다. `ev_`(평가용)는 여기서 막힌다.
2. source의 그룹키가 base의 **val/test 그룹과 겹치지 않는다** — 같은 클립의 프레임이
   이미 평가 쪽에 있으면 그대로 누수다.
3. base와 **파일명이 충돌하지 않는다.**
4. **검수 완료율 100%** (`reviewed.json`, label_tool이 쓰는 형식). 검수 전 오토 라벨은
   teacher가 놓친 지팡이를 "배경"으로 가르친다 — 오토 라벨 → 사람 검수 흐름이
   막으려는 바로 그 오염이다. `--allow-unreviewed`로만 풀리며 yaml에 그 사실이 남는다.
5. 라벨 기하 검사 — `resplit_dataset.audit_labels`를 그대로 쓴다(기준은 한 곳에만).

## ★ 하드링크라 검수가 끝난 **뒤에** 만든다

label_tool은 임시파일 + `os.replace`로 저장하므로, 변형을 만든 뒤 source 라벨을
고치면 **링크가 끊겨 변형에는 옛 라벨이 남는다.** 라벨을 고쳤으면 이 스크립트를 다시
돌려 변형을 새로 만들 것(출력 디렉터리는 매번 비우고 다시 채운다).

사용:
    python tools/data/add_source_variant.py --dataset datasets/v2_nolkc \\
        --source datasets/sources/vid_20260930 --out datasets/v4_vid --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from resplit_dataset import (_CLIP_FRAME, audit_labels,  # noqa: E402
                             build_aihub_sessions, group_key, stratum_of)

_BASE = _HERE.parent.parent             # tools/data/ → 저장소 루트
SPLITS = ("train", "val", "test")
TRAIN_CLIP_STRATUM = "clip_tr"


def _resolve(p: str) -> Path:
    q = Path(p)
    return q if q.is_absolute() else _BASE / q


def load_reviewed(source: Path) -> set[str]:
    """label_tool의 reviewed.json → 검수 완료 train 파일명 집합."""
    try:
        data = json.loads((source / "reviewed.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()
    return {k.split("/", 1)[1] for k, v in data.items() if v and k.startswith("train/")}


def check(base: Path, source: Path, allow_unreviewed: bool = False) -> dict:
    """검사만 한다. 반환: {"errors": [...], "warnings": [...], "names": [...], "groups": {...}}."""
    errors: list[str] = []
    warnings: list[str] = []
    src_img = source / "train" / "images"
    src_lbl = source / "train" / "labels"
    names = sorted(p.name for p in src_img.iterdir()) if src_img.is_dir() else []
    if not names:
        errors.append(f"source에 이미지가 없다: {src_img}")
        return {"errors": errors, "warnings": warnings, "names": [], "groups": {}}

    # 1) 학습용 클립 프레임인가
    not_clip = [n for n in names
                if not _CLIP_FRAME.match(Path(n).stem) or stratum_of(n) != TRAIN_CLIP_STRATUM]
    if not_clip:
        errors.append(f"학습용 클립 프레임(tr_…_fNNNNNN)이 아닌 파일 {len(not_clip)}개: {not_clip[:3]}")

    missing_lbl = [n for n in names if not (src_lbl / (Path(n).stem + ".txt")).exists()]
    if missing_lbl:
        errors.append(f"라벨 파일이 없는 이미지 {len(missing_lbl)}개: {missing_lbl[:3]}")

    # 2) base val/test 그룹과 겹치지 않는가
    base_names = {s: sorted(p.name for p in (base / s / "images").iterdir()) for s in SPLITS}
    sessions = build_aihub_sessions([n for s in SPLITS for n in base_names[s]])
    src_groups: dict[str, list[str]] = defaultdict(list)
    for n in names:
        src_groups[group_key(n, sessions)].append(n)
    for s in ("val", "test"):
        eval_groups = {group_key(n, sessions) for n in base_names[s]}
        inter = set(src_groups) & eval_groups
        if inter:
            errors.append(f"base {s}와 그룹이 겹친다(누수): {sorted(inter)[:3]}")

    # 3) 파일명 충돌
    all_base = {n for s in SPLITS for n in base_names[s]}
    clash = sorted(set(names) & all_base)
    if clash:
        errors.append(f"base와 파일명 충돌 {len(clash)}개: {clash[:3]}")

    # 4) 검수 완료율
    reviewed = load_reviewed(source)
    unreviewed = [n for n in names if n not in reviewed]
    if unreviewed:
        msg = f"검수 안 된 프레임 {len(unreviewed)}/{len(names)}개: {unreviewed[:3]}"
        (warnings if allow_unreviewed else errors).append(msg)

    # 5) 라벨 기하 검사
    if not missing_lbl:
        problems = audit_labels(names, label_path=lambda n: src_lbl / (Path(n).stem + ".txt"))
        if problems:
            errors.append(f"라벨 기하 검사 {len(problems)}건: {problems[:3]}")

    return {"errors": errors, "warnings": warnings, "names": names,
            "groups": dict(src_groups), "unreviewed": unreviewed}


def _count(lbl: Path) -> tuple[int, int]:
    cane = person = 0
    for line in lbl.read_text().splitlines():
        p = line.split()
        if p:
            cane += p[0] == "0"
            person += p[0] == "1"
    return cane, person


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default="datasets/v2_nolkc",
                    help="기준 데이터셋 (기본: v10의 학습 데이터 datasets/v2_nolkc)")
    ap.add_argument("--source", required=True, help="더할 소스 루트 (train/{images,labels} 구조)")
    ap.add_argument("--out", required=True, help="변형 출력 디렉터리")
    ap.add_argument("--data-yaml", default=None,
                    help="생성할 data yaml (기본: datasets/data_<변형이름>.yaml)")
    ap.add_argument("--allow-unreviewed", action="store_true",
                    help="검수 안 된 오토 라벨도 넣는다 (비권장 — yaml에 기록된다)")
    ap.add_argument("--dry-run", action="store_true", help="검사·집계만 하고 파일을 만들지 않음")
    args = ap.parse_args()

    base, source, out = _resolve(args.dataset), _resolve(args.source), _resolve(args.out)
    if not (base / "train" / "images").is_dir():
        raise SystemExit(f"[ERR] 없는 경로: {base}")
    if out.resolve() in (base.resolve(), source.resolve()):
        raise SystemExit("[ERR] --out이 원본과 같다. 원본을 덮어쓸 수 없다.")

    r = check(base, source, args.allow_unreviewed)
    names = r["names"]
    src_lbl = source / "train" / "labels"

    print(f"기준      : {base}")
    print(f"소스      : {source}  ({len(names)}장, 클립 {len(r['groups'])}개)")
    if names and not any("라벨 파일이 없는" in e for e in r["errors"]):
        print(f"\n{'클립(그룹)':<34} {'장수':>5} {'지팡이':>6} {'사람':>6} {'빈 프레임':>8}")
        tot = Counter()
        for g, ns in sorted(r["groups"].items()):
            c = Counter()
            for n in ns:
                a, b = _count(src_lbl / (Path(n).stem + ".txt"))
                c.update(cane=a, person=b, empty=int(not (a or b)))
            tot.update(c)
            print(f"{g:<34} {len(ns):>5} {c['cane']:>6} {c['person']:>6} {c['empty']:>8}")
        print(f"{'합계':<34} {len(names):>5} {tot['cane']:>6} {tot['person']:>6} {tot['empty']:>8}")
    n_train = len(list((base / "train" / "images").iterdir()))
    print(f"\ntrain     : {n_train} → {n_train + len(names)}  (+{len(names)})")
    for s in ("val", "test"):
        print(f"{s:<10}: {len(list((base / s / 'images').iterdir()))} (변경 없음 — 잣대를 바꾸면 비교가 불가능해진다)")

    for w in r["warnings"]:
        print(f"[WARN] {w}")
    for e in r["errors"]:
        print(f"[ERR]  {e}")
    if r["errors"]:
        raise SystemExit("\n검증 실패 — 아무것도 만들지 않았습니다.")
    if args.dry_run:
        print("\n[dry-run] 검증 통과. 파일을 만들지 않았다.")
        return

    for s in SPLITS:
        for sub in ("images", "labels"):
            d = out / s / sub
            if d.exists():
                for f in d.iterdir():
                    f.unlink()
            d.mkdir(parents=True, exist_ok=True)
    linked = 0
    for s in SPLITS:
        for img in sorted((base / s / "images").iterdir()):
            lbl = base / s / "labels" / (img.stem + ".txt")
            os.link(img, out / s / "images" / img.name)
            if lbl.exists():
                os.link(lbl, out / s / "labels" / lbl.name)
            linked += 1
    for n in names:
        os.link(source / "train" / "images" / n, out / "train" / "images" / n)
        os.link(src_lbl / (Path(n).stem + ".txt"), out / "train" / "labels" / (Path(n).stem + ".txt"))

    def rel(p: Path) -> str:
        return str(p.relative_to(_BASE)) if p.is_relative_to(_BASE) else str(p)

    unreviewed = r.get("unreviewed", [])
    (out / "source_manifest.json").write_text(json.dumps({
        "base": rel(base), "source": rel(source),
        "reviewed": len(names) - len(unreviewed), "unreviewed": len(unreviewed),
        "groups": {g: sorted(ns) for g, ns in sorted(r["groups"].items())},
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    yaml_path = _resolve(args.data_yaml) if args.data_yaml else _BASE / "datasets" / f"data_{out.name}.yaml"
    warn = (f"# ★ 검수 안 된 오토 라벨 {len(unreviewed)}장이 포함됐다(--allow-unreviewed).\n"
            if unreviewed else "")
    yaml_path.write_text(
        f"# add_source_variant.py가 생성 — {rel(base)}에 {rel(source)}의 {len(names)}장을\n"
        f"# **train에만** 더한 변형. val/test는 원본 그대로다 — 학습셋 구성 하나만 바뀐 A/B.\n"
        f"# source 라벨을 고치면 하드링크가 끊기므로 이 변형을 다시 만들 것.\n"
        f"{warn}"
        f"path: {out}\n"
        "train: train/images\n"
        "val:   val/images\n"
        "test:  test/images\n\n"
        "nc: 2\n"
        "names: ['white_cane', 'person']\n", encoding="utf-8")

    print(f"\n하드링크 base {linked}개 + source {len(names)}개 → {out}")
    print(f"data yaml → {yaml_path}")


if __name__ == "__main__":
    main()
