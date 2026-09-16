"""prepare_stick_cctv_dataset.py — CCTV 각도 유사물 네거티브 편입 (로컬 1회성).

##############################################################################
# 결론: 이 데이터는 **편입하지 않는다.** 실험 결과 해로웠다.                  #
#                                                                            #
# 같은 설정(augB)으로 편입 전/후를 비교한 실측:                              #
#   실영상 지팡이 탐지(conf 0.25): 568 → 330프레임 (−42%)                    #
#   실영상 (conf 0.40):            380 → 172프레임 (−55%)                    #
#   소형 지팡이 mAP50:             0.851 → 0.835                             #
#   유사물(lk) 오탐지@0.50:        2 → 4박스                                 #
#   얻은 것: CCTV 유사물 홀드아웃 오탐지 3 → 0박스 (conf 0.25 기준)          #
#                                                                            #
# 얻은 것이 거의 없다 — 그 3박스는 conf 0.40 이상에서 이미 0이라 애초에 고칠 #
# 문제가 아니었다. 반면 실영상 탐지가 절반 가까이 떨어졌다.                  #
#                                                                            #
# 원인: 이 데이터는 고각 CCTV에서 사람이 보도 위를 막대를 들고 걷는 장면이라 #
# **실제 흰 지팡이 사용자와 구도·자세가 동일하다.** 모델이 막대의 생김새가   #
# 아니라 그 구도 자체를 "지팡이 아님"으로 학습해 진짜 지팡이 사용자까지      #
# 억제한 것으로 보인다. 배포 도메인과 일치한다는 점이 장점이자 함정이었다.   #
#                                                                            #
# 교훈: 네거티브는 "탐지 대상과 구분되는 것"이어야 한다. 구도·맥락까지       #
# 같으면 모델이 구분할 단서가 외형뿐인데, 320 입력에서 얇은 막대의 외형      #
# 차이(흰색 vs 나무색)는 몇 픽셀에 불과하다.                                 #
#                                                                            #
# 스크립트는 재현/재검토를 위해 남겨둔다. 다시 시도한다면 근거리 유사물만    #
# 골라내거나(구도 중복 회피), 흰색 계열 막대만 쓰는 식의 선별이 필요하다.    #
##############################################################################

## 왜 이 데이터인가

기존 유사물 네거티브(`lk_*.jpg`, LVIS/Open Images 출처)는 **웹 사진 각도**다.
등산스틱·목발·빗자루가 "지팡이가 아니다"라는 건 가르치지만, 그 사진들은 대부분
눈높이에서 찍힌 근거리 사진이라 **실제 배치 환경(고각 CCTV, 원거리)과 도메인이
다르다**.

`datasets/sources/lookalike_stick_cctv/`(Roboflow `yankchina/person-stick`, MIT)는
정확히 그 빈 자리를 메운다 — 고각 CCTV로 찍은 거리 장면에서 사람들이 막대기를
들고 있는 644장이다. 촬영 각도가 배포 환경과 같아서, 같은 유사물 신호를 **배포
도메인에서** 가르칠 수 있다.

## 원본 stick 박스는 전량 버린다

소스는 `nc: 1, names: ['stick']`으로 막대기에 박스가 그려져 있지만 **쓰지 않는다.**
이 데이터의 본체는 "여기 있는 막대는 흰 지팡이가 아니다"라는 **부정** 신호이고,
그건 박스를 그리지 않음으로써 전달된다. 유사물에 박스를 그리면 오히려 그것을
탐지 대상으로 가르치게 된다 (`prepare_lookalike_dataset.py`와 같은 원칙).

사람은 반대로 반드시 라벨해야 한다. 라벨 없이 넣으면 YOLO가 그 사람 영역을
"배경(사람 아님)"으로 학습해 person 재현율이 떨어진다 — `prepare_background_dataset.py`
가 사람 찍힌 배경 4장을 제외한 것과 같은 이유다. 그래서 COCO 모델로 person만
새로 라벨한다.

## 흰 지팡이 혼입이 최대 위험

CCTV 거리 장면에 실제 흰 지팡이 사용자가 섞여 있으면 "흰 지팡이는 지팡이가 아니다"를
가르쳐 정확히 반대 효과가 난다. `--review` 컨택트시트로 전수 육안 검수하고,
`--exclude-file`로 뺀다 (`lookalike_exclude.txt`와 같은 방식).

사용:
    python prepare_stick_cctv_dataset.py --review --dry-run
    python prepare_stick_cctv_dataset.py --review

Pi 배포 대상이 아니다 (`Makefile`의 `DEPLOY_PY`에 넣지 말 것).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dataset_prep import convert, sort_key, stratified_holdout
from prepare_lookalike_dataset import label_people, write_review_sheets

ROOT = Path(__file__).resolve().parents[2]   # 저장소 루트 (이 파일은 tools/<분류>/ 아래에 있다)
SRC_DIR = ROOT / "datasets" / "sources" / "lookalike_stick_cctv"
STAGE_DIR = ROOT / "datasets" / "staging" / "stick_cctv"
TRAIN_IMAGES = ROOT / "datasets" / "v1" / "train" / "images"
TRAIN_LABELS = ROOT / "datasets" / "v1" / "train" / "labels"

PREFIX = "lkc_"
HOLDOUT_RATIO = 0.20
PERSON_CLASS_ID = 1

# S2.5에서 정한 값과 같다 — 사람을 놓치면 그 영역이 "배경"으로 학습돼 오염이 되므로
# 재현율이 중요하고, yolov8n은 명백히 보이는 보행자도 놓쳤다(yolov8l이 11% 더 찾음).
# 다만 이 데이터는 원거리 CCTV라 사람이 작다 — 임계값을 더 낮춰야 할 수 있어
# --person-conf로 조정 가능하게 뒀다.
COCO_WEIGHTS = ROOT / "yolov8l.pt"
PERSON_CONF = 0.25


def collect_sources(exclude: set[str]) -> list[Path]:
    """소스의 train/valid/test를 구분 없이 모두 모은다.

    원본 분할을 따르지 않는 이유: 이 데이터는 네거티브라 우리 쪽 분할 기준
    (`resplit_dataset.py`의 그룹 단위 분할)에 맞춰 다시 나눠야 하기 때문이다.
    """
    out: list[Path] = []
    for split in ("train", "valid", "test"):
        d = SRC_DIR / split / "images"
        if d.is_dir():
            out += [p for p in d.iterdir()
                    if p.suffix.lower() in (".jpg", ".jpeg", ".png") and p.name not in exclude]
    return sorted(out, key=sort_key)


def main() -> None:
    ap = argparse.ArgumentParser(description="CCTV 각도 유사물 네거티브 편입")
    ap.add_argument("--dry-run", action="store_true", help="집계만 하고 파일은 만들지 않음")
    ap.add_argument("--review", action="store_true", help="검수용 컨택트시트 생성")
    ap.add_argument("--exclude-file", help="제외할 원본 파일명 목록 (# 주석 허용)")
    ap.add_argument("--person-conf", type=float, default=PERSON_CONF)
    args = ap.parse_args()

    exclude: set[str] = set()
    if args.exclude_file:
        for line in Path(args.exclude_file).read_text(encoding="utf-8").splitlines():
            line = line.split("#")[0].strip()
            if line:
                exclude.add(line)

    srcs = collect_sources(exclude)
    print(f"소스 {len(srcs)}장" + (f" (제외 {len(exclude)}장)" if exclude else ""))
    if args.dry_run:
        print("--dry-run: 변환하지 않았습니다.")
        return

    stage_images = STAGE_DIR / "images"
    stage_labels = STAGE_DIR / "labels"
    for d in (stage_images, stage_labels):
        d.mkdir(parents=True, exist_ok=True)
        for f in d.iterdir():
            f.unlink()

    # 1) 정규화 + 리네임. 원본 stem이 겹치면 YOLO의 stem 기반 이미지-라벨 매칭이
    #    충돌하므로 연속 번호로 다시 붙인다 (prepare_background_dataset.py와 같은 이유).
    manifest: dict[str, dict] = {}
    names: list[str] = []
    for i, src in enumerate(srcs):
        name = f"{PREFIX}{i:04d}.jpg"
        convert(src, stage_images / name)
        manifest[name] = {"source": str(src.relative_to(ROOT))}
        names.append(name)

    # 2) person만 자동 라벨. stick 박스는 읽지도 않는다.
    boxes = label_people(names, stage_images, weights=COCO_WEIGHTS, conf=args.person_conf)
    with_person = 0
    for name in names:
        found = boxes.get(name, [])
        lines = [f"{PERSON_CLASS_ID} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}" for cx, cy, w, h in found]
        (stage_labels / (Path(name).stem + ".txt")).write_text(
            ("\n".join(lines) + "\n") if lines else "", encoding="utf-8")
        manifest[name]["persons"] = len(found)
        with_person += bool(found)

    # 3) 홀드아웃 — 벤치 전용. 사람 유무로 층화한다(사람이 있는 쪽이 더 어려운
    #    네거티브라, 무작위로 뽑으면 한쪽에 쏠려 지표가 둔해진다).
    strata = {n: ("with_person" if manifest[n]["persons"] else "solo") for n in names}
    holdout = stratified_holdout(strata, HOLDOUT_RATIO, order=("solo", "with_person"))
    (STAGE_DIR / "holdout.txt").write_text(
        "\n".join(str((stage_images / n).resolve()) for n in sorted(holdout)) + "\n",
        encoding="utf-8")

    # 4) 홀드아웃이 아닌 것만 학습셋에 편입. 이후 resplit_dataset.py가 'lkc' 층으로
    #    train/val/test에 다시 나눈다.
    added = 0
    for name in names:
        if name in holdout:
            manifest[name]["split"] = "holdout"
            continue
        manifest[name]["split"] = "train"
        (TRAIN_IMAGES / name).write_bytes((stage_images / name).read_bytes())
        (TRAIN_LABELS / (Path(name).stem + ".txt")).write_bytes(
            (stage_labels / (Path(name).stem + ".txt")).read_bytes())
        added += 1

    (STAGE_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    if args.review:
        write_review_sheets({n: boxes.get(n, []) for n in names}, stage_images, STAGE_DIR / "review")

    print(f"변환 {len(names)}장 (사람 검출 {with_person}장 / 사람 없음 {len(names) - with_person}장)")
    print(f"학습셋 편입 {added}장, 홀드아웃 {len(holdout)}장 → {STAGE_DIR / 'holdout.txt'}")
    print("다음: 컨택트시트로 흰 지팡이 혼입 검수 → 있으면 --exclude-file로 재실행")


if __name__ == "__main__":
    main()
