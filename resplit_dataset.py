"""resplit_dataset.py — 데이터 누수 없이 train/val/test를 다시 나눈다 (로컬 1회성).

## 왜 필요한가

현재 `datasets/{train,val,test}` 분할에는 두 가지 누수가 있다. 둘 다 실측했다.

1. **증강본이 원본 단위로 묶이지 않았다.** Roboflow는 원본 1장을 `<stem>_jpg.rf.
   <hash>.jpg`로 2~4장 증강하는데, 분할이 증강 *후*에 수행됐다. 그 결과 지팡이
   test 931장 중 646장(69.4%)이 train과 **같은 원본**에서 나온 증강본이다.
2. **연속 촬영이 쪼개졌다.** AIHub `20210514_HHMMSS_*`는 27분간 촬영된 단일
   세션인데, 같은 초에 찍힌 프레임이 train과 test에 나뉘어 들어갔다. test 629장
   중 627장(99.7%)이 train에 같은 초의 프레임을 갖고 있다.

즉 **지금의 test는 사실상 train이다.** 여기서 잰 cane mAP50 0.98은 일반화 성능이
아니라 "train에서 본 사진의 증강본을 다시 맞힌 점수"다. 이것이 백본을 바꿔도
(yolov8n/yolo11n/yolo26n) 지표가 0.13%p 안에서 움직이지 않는 이유이자, 정지
이미지 0.98과 실영상 탐지율의 격차가 설명되지 않던 이유다.

## 무엇을 하는가

이미지를 새로 모으지 않는다. 기존 `datasets/{train,val,test}`의 합집합(14,022장)을
**그룹 단위로 다시 나눠** `datasets/v2/`에 하드링크한다.

- 기존 분할은 그대로 둔다 — "누수 전/후"를 대조하려면 옛 지표를 재현할 수 있어야 한다
- 하드링크라 14,022장을 다시 배치해도 디스크가 거의 늘지 않는다
- 라벨은 이미 병합/보완이 끝난 상태라 그대로 쓴다 (merge_person_dataset.py 등 재실행 불필요)

사용:
    python resplit_dataset.py --dry-run     # 구성/누수/라벨 검사만, 파일은 안 만듦
    python resplit_dataset.py               # datasets/v2/ 생성

`prepare_background_dataset.py`와 같은 성격의 로컬 1회성 도구다 — Pi 배포 대상이
아니므로 `Makefile`의 `DEPLOY_PY`에 넣지 말 것.
"""

from __future__ import annotations

import argparse
import json
import hashlib
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))


SRC_SPLITS = ("train", "val", "test")
OUT_DIR = ROOT / "datasets" / "v2"
DATA_YAML = ROOT / "datasets" / "data_v2.yaml"
MANIFEST = OUT_DIR / "split_manifest.json"

# 사람 라벨을 보완한 라벨 파일을 따로 둔다. 원본 datasets/{train,val,test}/labels는
# 커밋된 데이터라 건드리지 않고, datasets/v2는 원본의 하드링크라 거기에 쓰면 원본이
# 같이 바뀐다 — 그래서 제3의 위치에 쓰고 _label_path()가 이쪽을 우선한다.
RELABEL_DIR = ROOT / "datasets" / "staging" / "person_relabel"

# 사람 라벨 보완에 쓰는 COCO 모델. lk_ 네거티브를 만들 때 쓴 yolov8n보다 큰 모델을
# 쓰는 이유는, 여기서는 **놓친 사람 하나가 곧 오염**이기 때문이다 — 자동 라벨은
# 검출된 사람만 라벨하고 못 찾은 사람은 배경으로 남겨두므로, 재현율이 낮으면 고치려던
# 문제가 그대로 남는다. 실측(cane_only 426장, conf 0.40): yolov8n 530박스 /
# yolov8m 569 / yolov8l 588. 로컬 1회성 작업이라 큰 모델의 추론 비용은 문제가 안 된다.
RELABEL_WEIGHTS = ROOT / "yolov8l.pt"
# 임계값 0.25는 yolov8l 스윕에서 정했다 — 0.40에서 588박스, 0.25에서 598, 0.15에서
# 618로 곡선이 0.25 아래에서 평평해진다. 더 낮추면 얻는 건 적고 오탐(사람 아닌 것을
# 사람으로 라벨) 위험만 커진다. yolov8n으로는 conf 0.40에서 명백히 보이는 보행자를
# 놓친 사례가 있었다(35_jpg: 0.10까지 낮춰야 검출).
RELABEL_CONF = 0.25

TEST_RATIO = 0.15
VAL_RATIO = 0.15

# AIHub 촬영 세션을 끊는 간격. 이 데이터는 2021-05-14 18:23~18:50에 한 사람이 한
# 대학 캠퍼스를 돌며 찍은 단일 촬영이고, 각 촬영 순간이 Roboflow 증강으로 수십~수백
# 장이 된다. 60초로 끊으면 8개 세션이 나오는데, 대표 이미지를 눈으로 확인한 결과
# 서로 다른 장소다(횡단보도 / 가로수길 / 주차장 / 실내 계단 등).
#
# 30초로 끊으면 10개가 되지만 그중 두 쌍(18:29~18:32, 18:47~18:50)은 같은 장소를
# 이어서 찍은 것이라 분할이 갈리면 누수가 남는다 — 60초가 정확히 그 두 쌍을 병합한다.
# 더 늘리면(120초) 4개로 줄어 val/test에 넣을 장소 다양성이 부족해진다.
AIHUB_SESSION_GAP_SEC = 60

# Roboflow 증강본 접미사. 확장자 토큰은 **원본 파일의 확장자를 그대로 옮긴 것**이라
# 대소문자가 섞인다 — 실측: _jpg 10,625 / _JPG 2,623 / _png 2. 소문자 _jpg만 잡으면
# IMG_7876_JPG.rf.<hash>.jpg 같은 파일이 증강본별로 다른 그룹이 되어, 같은 원본이
# train과 test로 갈라진다(이 버그로 재분할 후에도 18.7%의 누수가 남아 있었다).
_RF_SUFFIX = re.compile(r"_[A-Za-z0-9]+\.rf\.[0-9a-f]+$")
# AIHub 파일명. HHMMSS 뒤 구분자는 밑줄이 대부분이지만 하이픈인 것도 8장 있다
# (20210514_182405-0-_jpg...). 밑줄만 받으면 그 8장이 촬영세션이 아니라 파일명
# 단위로 묶여 인접 세션과 split이 갈린다.
_AIHUB = re.compile(r"^20210514_(\d{2})(\d{2})(\d{2})[-_]")


def stratum_of(name: str) -> str:
    """층(stratum) — 이 단위로 비율을 맞춰 뽑는다.

    층을 나누는 이유는 `dataset_prep.stratified_holdout`의 docstring과 같다: 단순
    무작위로 뽑으면 소수 집단이 val/test에 거의 안 들어가 지표가 둔감해진다.
    특히 **네거티브(bg_/lk_)는 현재 train에만 몰려 있어** 오탐지가 주 지표에 전혀
    잡히지 않는데, 층으로 잡아두면 val/test에도 자동 배분된다.
    """
    if name.startswith("bg_"):
        return "bg"
    if name.startswith("lkc_"):
        return "lkc"      # CCTV 각도 유사물 — lk_(웹 사진 각도)와 도메인이 달라 층을 나눈다
    if name.startswith("lk_"):
        return "lk"
    if name.startswith("pedcctv_"):
        return "pedcctv"
    if _AIHUB.match(name):
        return "aihub_cane"
    return "roboflow_cane"


def group_key(name: str, aihub_sessions: dict[int, int]) -> str:
    """같은 split에 함께 들어가야 하는 이미지들의 묶음 이름.

    - Roboflow 증강본은 해시를 떼면 같은 원본 stem으로 모인다
    - AIHub는 초 단위 시각을 촬영 세션으로 묶는다 (이웃한 초는 같은 장면이라
      원본 stem만으로는 부족하다)
    - 나머지(pedcctv/bg/lk)는 서로 독립된 사진이라 1파일 1그룹
    """
    stem = Path(name).stem
    m = _AIHUB.match(name)
    if m:
        h, mi, s = (int(x) for x in m.groups())
        return f"aihub_s{aihub_sessions[h * 3600 + mi * 60 + s]}"
    return "rf_" + _RF_SUFFIX.sub("", stem)


def build_aihub_sessions(names: list[str]) -> dict[int, int]:
    """AIHub 파일명의 초 단위 시각 → 세션 번호. 간격이 벌어지면 새 세션."""
    secs = set()
    for n in names:
        m = _AIHUB.match(n)
        if m:
            h, mi, s = (int(x) for x in m.groups())
            secs.add(h * 3600 + mi * 60 + s)
    out: dict[int, int] = {}
    sid = 0
    prev = None
    for t in sorted(secs):
        if prev is not None and t - prev > AIHUB_SESSION_GAP_SEC:
            sid += 1
        out[t] = sid
        prev = t
    return out


def split_groups(groups: dict[str, list[str]], strata: dict[str, str]) -> dict[str, str]:
    """그룹 → split. 층별로 비율을 맞춰 test → val 순으로 뽑는다.

    층별로 그룹키 해시 순서대로 앞 15%를 test, 다음 15%를 val로 자른다. 무작위
    추출과 통계적으로 같으면서 **결정적이고, 다른 층의 변경에 영향받지 않는다**
    (자세한 근거는 아래 주석).

    AIHub만 예외로 best-fit bin-packing을 쓴다. 세션이 8개뿐이고 크기가 43~1936장으로
    45배 차이나서, 비율로 자르면 목표 비율을 전혀 못 맞추기 때문이다.
    """
    assign: dict[str, str] = {}
    by_stratum: dict[str, list[str]] = {}
    for g, st in strata.items():
        if st != "aihub_cane":
            by_stratum.setdefault(st, []).append(g)

    for st, gs in by_stratum.items():
        # 그룹키 해시로 정렬해 앞에서부터 자른다. 무작위 추출과 통계적으로 같지만
        # **어떤 층이 추가/제거돼도 다른 층의 배정이 바뀌지 않는다**는 성질이 있다.
        #
        # 처음에는 dataset_prep.stratified_holdout()을 썼는데, 그 함수는 하나의 RNG를
        # 층마다 이어 쓰기 때문에(그 docstring이 경고하는 바로 그 문제) 층을 하나
        # 추가하자 기존 층의 표본이 통째로 달라졌다 — 실측: lkc 층을 넣었을 뿐인데
        # 14,022장 중 2,418장(17.2%)이 split을 옮겼고, 새 test의 20.6%가 이전 train
        # 이었다. 그러면 이전에 학습한 모델을 새 test로 평가할 수 없다.
        ordered = sorted(gs, key=lambda g: hashlib.md5(g.encode()).hexdigest())
        n = len(ordered)
        n_test = round(n * TEST_RATIO)
        n_val = round(n * VAL_RATIO)
        for i, g in enumerate(ordered):
            assign[g] = "test" if i < n_test else "val" if i < n_test + n_val else "train"
    assign.update(_pack_aihub({g: groups[g] for g, s in strata.items() if s == "aihub_cane"}))
    return assign


def _pack_aihub(sessions: dict[str, list[str]]) -> dict[str, str]:
    """큰 세션부터 "목표 대비 가장 덜 찬" split에 통째로 넣는다.

    세션은 쪼갤 수 없으므로(쪼개면 그게 곧 누수다) 정확한 비율은 불가능하다.
    큰 것부터, **넣었을 때의 충족률(=(현재+크기)/목표)이 가장 낮아지는 곳**에 넣는다
    (best-fit). 넣기 *전*의 충족률로 고르면 비어 있는 split이 무조건 이겨서 가장 큰
    세션(1,936장 = AIHub의 30%)이 val이나 test로 가버린다 — 실제로 그렇게 해봤더니
    test가 전체의 22%가 됐다. 넣은 *뒤*를 보면 큰 덩어리는 목표가 큰 train으로 간다.

    동점일 때 목표가 큰 쪽을 먼저 채우도록 -target으로 한 번 더 정렬한다. 마지막
    key는 재실행 결정성을 위한 것이다(무작위 요소 없음).
    """
    total = sum(len(v) for v in sessions.values())
    target = {"train": total * (1 - TEST_RATIO - VAL_RATIO),
              "val": total * VAL_RATIO, "test": total * TEST_RATIO}
    cur = {k: 0 for k in target}
    out: dict[str, str] = {}
    for g in sorted(sessions, key=lambda g: (-len(sessions[g]), g)):
        n = len(sessions[g])
        pick = min(cur, key=lambda k: ((cur[k] + n) / target[k], -target[k], k))
        out[g] = pick
        cur[pick] += n
    return out


def _label_path(name: str) -> Path:
    """이미지 파일명 → 라벨 경로. 사람 라벨 보완본이 있으면 그것을 우선한다."""
    fixed = RELABEL_DIR / (Path(name).stem + ".txt")
    if fixed.exists():
        return fixed
    for s in SRC_SPLITS:
        p = ROOT / "datasets" / s / "labels" / (Path(name).stem + ".txt")
        if p.exists():
            return p
    raise FileNotFoundError(name)


def _image_path(name: str) -> Path:
    for s in SRC_SPLITS:
        p = ROOT / "datasets" / s / "images" / name
        if p.exists():
            return p
    raise FileNotFoundError(name)


def audit_labels(names: list[str]) -> list[str]:
    """라벨의 기하학적 유효성 검사 — 0폭/프레임 이탈/극소 박스/완전 중복.

    현재 데이터는 39,110박스 전수에서 전부 0건이지만, 앞으로 데이터를 추가할 때
    조용히 깨지는 걸 막기 위해 재분할마다 다시 본다.
    """
    problems: list[str] = []
    for n in names:
        rows = []
        for i, line in enumerate(_label_path(n).read_text().splitlines(), 1):
            parts = line.split()
            if not parts:
                continue
            rows.append(tuple(parts[:5]))
            cls, cx, cy, w, h = int(parts[0]), *map(float, parts[1:5])
            if cls not in (0, 1):
                problems.append(f"{n}:{i} 알 수 없는 class {cls}")
            if w <= 0 or h <= 0:
                problems.append(f"{n}:{i} 0폭/0높이 박스")
            elif w < 0.004 or h < 0.004:
                problems.append(f"{n}:{i} 극소 박스 (w={w:.4f} h={h:.4f})")
            if (cx - w / 2 < -1e-6 or cy - h / 2 < -1e-6
                    or cx + w / 2 > 1 + 1e-6 or cy + h / 2 > 1 + 1e-6):
                problems.append(f"{n}:{i} 프레임 밖으로 넘침")
        if len(rows) != len(set(rows)):
            problems.append(f"{n} 완전 중복 박스")
    return problems


def relabel_person(names: list[str], review: bool = False) -> dict[str, int]:
    """지팡이만 라벨된 이미지에 빠진 사람 라벨을 COCO yolov8n으로 보완한다.

    ## 왜 필요한가

    지팡이 데이터셋과 사람 데이터셋은 서로 다른 소스에서 각각 라벨링된 뒤 합쳐졌다
    (`merge_person_dataset.py`). 대부분은 병합 과정에서 양쪽 라벨을 갖게 됐지만
    (both 8,877장), **class 0만 있는 426장이 남아 있다.** 그중 344장(80.8%)에는
    실제로 사람이 찍혀 있어, YOLO가 그 영역을 "배경(사람 아님)"으로 학습한다 —
    `prepare_background_dataset.py`가 사람 찍힌 배경 4장을 굳이 제외한 것과
    정확히 같은 종류의 오염이다.

    `label_tool/`이 바로 이 작업을 위해 만들어졌으나 한 번도 쓰이지 않았다
    (`label_tool/reviewed.json`이 없다 = 검토 0건).

    ## 사람이 안 잡히는 이미지는 버리지 않는다

    `prepare_lookalike_dataset.py`는 person 미검출 이미지를 버리는데, 그쪽은 "사람이
    유사물을 든 사진"이라 사람이 반드시 있어야 하기 때문이다. 여기는 다르다 —
    흰 지팡이 단독 카탈로그 사진이 실제로 섞여 있다. 실측: conf 0.40에서 사람이 안
    잡힌 82장 중 임계값을 0.15까지 낮춰도 잡히는 건 2장뿐이고, 육안 확인 결과 흰
    배경의 제품 사진/클로즈업이었다. 버리면 진짜 지팡이 학습 데이터를 잃는다.
    """
    from prepare_lookalike_dataset import label_people, write_review_sheets

    targets = []
    for n in names:
        rows = [l.split() for l in _label_path(n).read_text().splitlines() if l.split()]
        if rows and all(r[0] == "0" for r in rows):
            targets.append(n)

    rel = {n: str(_image_path(n).relative_to(ROOT)) for n in targets}
    boxes = label_people(sorted(rel.values()), ROOT,
                         weights=RELABEL_WEIGHTS, conf=RELABEL_CONF)

    RELABEL_DIR.mkdir(parents=True, exist_ok=True)
    added = 0
    for n in targets:
        found = boxes.get(rel[n], [])
        if not found:
            continue
        src = _label_path(n).read_text().rstrip("\n")
        lines = [src] if src else []
        lines += [f"1 {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}" for cx, cy, w, h in found]
        (RELABEL_DIR / (Path(n).stem + ".txt")).write_text("\n".join(lines) + "\n",
                                                          encoding="utf-8")
        added += 1

    if review:
        write_review_sheets({v: boxes.get(v, []) for v in rel.values()},
                            ROOT, RELABEL_DIR / "review")
    return {"cane_only": len(targets), "relabeled": added,
            "kept_as_is": len(targets) - added}


def summarize(assign: dict[str, str], groups: dict[str, list[str]],
              strata: dict[str, str]) -> dict[str, dict]:
    stats: dict[str, dict] = {s: {"images": 0, "cane": 0, "person": 0, "empty": 0,
                                  "strata": Counter()} for s in SRC_SPLITS}
    for g, split in assign.items():
        for n in groups[g]:
            st = stats[split]
            st["images"] += 1
            st["strata"][strata[g]] += 1
            a = b = 0
            for line in _label_path(n).read_text().splitlines():
                p = line.split()
                if not p:
                    continue
                a += p[0] == "0"
                b += p[0] != "0"
            st["cane"] += a
            st["person"] += b
            if not (a or b):
                st["empty"] += 1
    return stats


def write_output(assign: dict[str, str], groups: dict[str, list[str]],
                 strata: dict[str, str]) -> None:
    """하드링크로 datasets/v2/를 만든다.

    복사가 아니라 하드링크인 이유: 14,022장(약 480MB)을 복제할 이유가 없고, 원본
    `datasets/{train,val,test}`를 그대로 남겨 옛 지표를 재현할 수 있어야 하기 때문이다.
    같은 파일시스템 안이라 하드링크가 가능하다.
    """
    for s in SRC_SPLITS:
        for sub in ("images", "labels"):
            d = OUT_DIR / s / sub
            if d.exists():
                for f in d.iterdir():
                    f.unlink()
            d.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, dict] = {}
    for g, split in assign.items():
        for n in groups[g]:
            img, lbl = _image_path(n), _label_path(n)
            os.link(img, OUT_DIR / split / "images" / n)
            os.link(lbl, OUT_DIR / split / "labels" / lbl.name)
            manifest[n] = {"group": g, "stratum": strata[g], "split": split}

    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    DATA_YAML.write_text(
        "# resplit_dataset.py가 생성 — 그룹(원본/촬영세션) 단위로 나눠 누수가 없다.\n"
        "# 기존 datasets/data.yaml은 증강본과 연속 프레임이 split을 넘나들어\n"
        "# test가 사실상 train이었다(자세한 내용은 resplit_dataset.py docstring).\n"
        f"path: {OUT_DIR}\n"
        "train: train/images\n"
        "val:   val/images\n"
        "test:  test/images\n\n"
        "nc: 2\n"
        "names: ['white_cane', 'person']\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="누수 없는 train/val/test 재분할")
    ap.add_argument("--dry-run", action="store_true", help="검사/요약만 출력, 파일은 만들지 않음")
    ap.add_argument("--skip-audit", action="store_true", help="라벨 기하 검사 생략(빠른 확인용)")
    ap.add_argument("--relabel-person", action="store_true",
                    help="지팡이만 라벨된 이미지에 빠진 사람 라벨을 COCO yolov8n으로 보완")
    ap.add_argument("--review", action="store_true",
                    help="--relabel-person의 결과를 검수용 컨택트시트로 출력")
    args = ap.parse_args()

    names: list[str] = []
    for s in SRC_SPLITS:
        names += os.listdir(ROOT / "datasets" / s / "images")
    names.sort()

    if args.relabel_person:
        r = relabel_person(names, review=args.review)
        print(f"사람 라벨 보완: cane_only {r['cane_only']}장 중 {r['relabeled']}장에 person 추가, "
              f"{r['kept_as_is']}장은 사람이 없어 그대로 둠 → {RELABEL_DIR}\n")

    sessions = build_aihub_sessions(names)
    groups: dict[str, list[str]] = defaultdict(list)
    strata: dict[str, str] = {}
    for n in names:
        g = group_key(n, sessions)
        groups[g].append(n)
        strata[g] = stratum_of(n)

    assign = split_groups(groups, strata)
    stats = summarize(assign, groups, strata)

    print(f"입력 {len(names)}장 → 그룹 {len(groups)}개 "
          f"(AIHub 촬영세션 {len({g for g, s in strata.items() if s == 'aihub_cane'})}개 포함)\n")
    print(f"{'split':>6} {'이미지':>7} {'지팡이박스':>10} {'사람박스':>9} {'배경':>6}  구성")
    print("-" * 86)
    for s in SRC_SPLITS:
        st = stats[s]
        comp = " ".join(f"{k}:{v}" for k, v in sorted(st["strata"].items()))
        print(f"{s:>6} {st['images']:>7} {st['cane']:>10} {st['person']:>9} "
              f"{st['empty']:>6}  {comp}")

    # 누수 검증 — 모든 split 쌍의 그룹키 교집합이 공집합이어야 한다.
    by_split: dict[str, set[str]] = defaultdict(set)
    for g, s in assign.items():
        by_split[s].add(g)
    print()
    ok = True
    for a, b in (("train", "val"), ("train", "test"), ("val", "test")):
        inter = by_split[a] & by_split[b]
        print(f"  누수 {a}∩{b}: 그룹 {len(inter)}개" + ("" if not inter else f"  ← {sorted(inter)[:3]}"))
        ok &= not inter

    if not args.skip_audit:
        problems = audit_labels(names)
        print(f"  라벨 기하 검사: {len(problems)}건" + ("" if not problems else f"  ← {problems[:3]}"))
        ok &= not problems

    if not ok:
        raise SystemExit("\n검증 실패 — 출력하지 않았습니다.")

    if args.dry_run:
        print("\n--dry-run: 파일을 만들지 않았습니다.")
        return
    write_output(assign, groups, strata)
    print(f"\n생성 완료: {OUT_DIR}  (하드링크)\n설정 파일: {DATA_YAML}")


if __name__ == "__main__":
    main()
