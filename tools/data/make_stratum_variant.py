#!/usr/bin/env python3
"""make_stratum_variant.py — 확정된 split을 유지한 채 **한 층(stratum)만 비율로 줄인** 변형.

로컬 전용(Pi 배포 대상 아님) 데이터 준비 도구.

## `resplit_dataset.py`와 무엇이 다른가

`resplit_dataset.py`는 **재분할**이다 — 그룹을 train/val/test에 새로 배정한다.
이 스크립트는 **재분할을 하지 않는다.** 이미 확정된 `datasets/v2`의 배정을 그대로 쓰고
train에서 특정 층의 일부만 덜어낸다. 그래야 **학습셋 구성 하나만 바뀐 A/B**가 된다.

**`val`/`test`는 손대지 않고 통째로 하드링크한다.** 건드리면 지표의 잣대가 바뀌어
기준 모델과 비교가 불가능해지고, 무엇보다 **줄인 층이 val/test에 남아 있어야 그 층에
대한 성능 회귀를 측정할 수 있다** — pedcctv를 줄이는 실험에서 person recall이 얼마나
떨어지는지가 최대 리스크인데, val에서도 빼면 그 하락이 지표에 안 잡힌다.

## 왜 그룹 단위인가

`resplit_dataset.group_key()`를 그대로 import해 쓴다(복붙 금지 — 기준이 한 곳에만
있어야 한다). Roboflow 증강본은 `<stem>_jpg.rf.<hash>.jpg`로 흩어져 있어, 파일 단위로
덜어내면 **같은 원본의 증강본 일부만 남아** "줄였다"의 의미가 흐려진다. 실측: pedcctv
2,753장은 고유 그룹 1,171개(그룹당 평균 2.35장)다.

## 왜 md5 해시로 고르는가

`random.sample`을 쓰면 keep 비율을 바꿀 때마다 **완전히 다른 집합**이 나와,
50% 변형과 25% 변형이 포함 관계가 아니게 된다. 해시를 쓰면
**keep=0.25 ⊂ keep=0.5 ⊂ keep=1.0**이 보장돼 비율 스윕이 단조로운 실험이 된다.
`resplit_dataset`이 RNG 체이닝을 버리고 층별 md5를 쓰는 것과 같은 이유다.

## 사용법

    python tools/data/make_stratum_variant.py --stratum pedcctv --keep 0.5 \
        --out datasets/v3_ped50

    # → datasets/v3_ped50/{train,val,test} + datasets/data_v3_ped50.yaml
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))          # 같은 디렉터리의 resplit_dataset을 쓴다

from resplit_dataset import build_aihub_sessions, group_key, stratum_of  # noqa: E402

_BASE = _HERE.parent.parent             # tools/data/ → 저장소 루트
SPLITS = ("train", "val", "test")


# ★ 해시에 소금을 친다 — 없으면 선택이 split 배정과 상관된다.
#
# `resplit_dataset.split_groups()`는 그룹을 **`md5(group)` 순서로 정렬해** 상위 15%를
# test, 다음 15%를 val로 보낸다. 그래서 **train에 남은 그룹은 정의상 md5가 상위 70%
# 구간에만 존재한다** — 실측으로 pedcctv train 1,171개 그룹의 md5/2³² 최솟값이
# 0.2935였고 첫 hex 자리에 0~3이 하나도 없었다.
#
# 소금 없이 같은 md5로 `< keep`을 판정하면 그 구간과 겹쳐서, keep=0.5가 29%만 남기고
# keep=0.25는 **한 장도 남기지 않는다**(실제로 그렇게 나왔다). 소금을 치면 split
# 배정과 독립이 되고, 같은 소금을 쓰므로 keep=0.25 ⊂ keep=0.5 ⊂ keep=1.0 포함
# 관계는 그대로 유지된다.
_SALT = "make_stratum_variant/v1:"


def _keep(group: str, ratio: float) -> bool:
    """그룹을 남길지 결정적으로 정한다 — 비율을 낮추면 남는 집합이 줄어들기만 한다."""
    h = int(hashlib.md5((_SALT + group).encode("utf-8")).hexdigest()[:8], 16)
    return (h / 0xFFFFFFFF) < ratio


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default="datasets/v2", help="원본 데이터셋 루트")
    ap.add_argument("--stratum", required=True,
                    help="줄일 층 이름 (resplit_dataset.stratum_of 기준: "
                         "pedcctv · bg · lk · aihub_cane · roboflow_cane …)")
    ap.add_argument("--keep", type=float, required=True,
                    help="train에서 남길 비율 0.0~1.0 (그룹 단위)")
    ap.add_argument("--out", required=True, help="변형 출력 디렉터리")
    ap.add_argument("--data-yaml", default=None,
                    help="생성할 data yaml 경로 (기본: datasets/data_<변형이름>.yaml)")
    ap.add_argument("--dry-run", action="store_true", help="집계만 하고 파일을 만들지 않음")
    args = ap.parse_args()

    if not 0.0 <= args.keep <= 1.0:
        raise SystemExit("[ERR] --keep은 0.0~1.0")

    src = _BASE / args.dataset
    out = _BASE / args.out
    if not (src / "train" / "images").is_dir():
        raise SystemExit(f"[ERR] 없는 경로: {src}")
    if out.resolve() == src.resolve():
        raise SystemExit("[ERR] --out이 원본과 같다. 원본을 덮어쓸 수 없다.")

    train_names = sorted(p.name for p in (src / "train" / "images").iterdir())
    sessions = build_aihub_sessions(train_names)

    in_stratum = [n for n in train_names if stratum_of(n) == args.stratum]
    if not in_stratum:
        raise SystemExit(f"[ERR] '{args.stratum}' 층이 train에 없다. "
                         f"stratum_of가 내는 이름을 확인할 것.")

    all_groups = {group_key(n, sessions) for n in in_stratum}
    kept_groups = {g for g in all_groups if _keep(g, args.keep)}
    dropped_names = {n for n in in_stratum
                     if group_key(n, sessions) not in kept_groups}

    kept_train = len(train_names) - len(dropped_names)
    print(f"원본      : {src}")
    print(f"줄일 층   : {args.stratum}  (keep={args.keep})")
    print(f"  그룹    : {len(all_groups)} → {len(kept_groups)}")
    print(f"  이미지  : {len(in_stratum)} → {len(in_stratum) - len(dropped_names)}")
    print(f"train 전체: {len(train_names)} → {kept_train}  (−{len(dropped_names)})")
    for s in ("val", "test"):
        n = len(list((src / s / "images").iterdir()))
        print(f"{s:<10}: {n} (변경 없음 — 잣대를 바꾸면 비교가 불가능해진다)")

    if args.dry_run:
        print("\n[dry-run] 파일을 만들지 않았다.")
        return

    linked = 0
    for s in SPLITS:
        for sub in ("images", "labels"):
            d = out / s / sub
            if d.exists():
                for f in d.iterdir():
                    f.unlink()
            d.mkdir(parents=True, exist_ok=True)

        for img in sorted((src / s / "images").iterdir()):
            if s == "train" and img.name in dropped_names:
                continue
            lbl = src / s / "labels" / (img.stem + ".txt")
            os.link(img, out / s / "images" / img.name)
            if lbl.exists():
                os.link(lbl, out / s / "labels" / lbl.name)
            linked += 1

    yaml_path = (_BASE / args.data_yaml if args.data_yaml
                 else _BASE / "datasets" / f"data_{out.name}.yaml")
    yaml_path.write_text(
        f"# make_stratum_variant.py가 생성 — {args.dataset}에서 '{args.stratum}' 층을\n"
        f"# **train에서만** keep={args.keep}로 줄인 변형(그룹 단위, md5 결정적 선택).\n"
        f"# split 배정은 원본과 동일하고 val/test는 손대지 않았다 — 학습셋 구성\n"
        f"# 하나만 바뀐 A/B가 되도록 한 것이다.\n"
        f"path: {out}\n"
        "train: train/images\n"
        "val:   val/images\n"
        "test:  test/images\n\n"
        "nc: 2\n"
        "names: ['white_cane', 'person']\n", encoding="utf-8")

    print(f"\n하드링크 {linked}개 → {out}")
    print(f"data yaml → {yaml_path}")


if __name__ == "__main__":
    main()
