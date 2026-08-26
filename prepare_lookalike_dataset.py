"""prepare_lookalike_dataset.py — 흰지팡이 "유사물" 사진을 네거티브로 학습에 편입한다.

배경의 선·케이블, 나뭇가지, 난간, 기둥, 등산스틱, 우산, 목발처럼 흰 지팡이와 형태가
비슷한 물체가 지팡이로 오탐지되어 엉뚱한 음성 안내가 나가는 문제를 겨냥한다. 현재
데이터셋에는 이런 유사물이 "지팡이 아님"으로 라벨링된 사례가 **하나도 없어서**, 모델이
"가늘고 긴 것 = 지팡이"만 배우고 구분하는 법은 배운 적이 없다.

수집 원본을 두 갈래로 나눠 넣는다 — 이 구분이 핵심이다:

    lookalike_data/
      solo/<카테고리>/         사람 없이 유사물만       → 빈 라벨(배경)
      with_person/<카테고리>/  사람이 유사물을 들고 있음 → person(class 1)만 라벨

`with_person/`이 훨씬 강한 학습 신호다. "사람 옆의 이 막대는 지팡이가 아니다"를 직접
대비시켜 가르치기 때문이다. **유사물 자체에는 어떤 박스도 그리지 않는다** — 그게 이
데이터의 본체다.

person 라벨은 COCO yolov8n으로 자동 생성하고, `--review`가 만드는 컨택트시트로 육안
검수한다(`label_tool/`은 기존 라벨 파일이 있는 이미지를 전제로 해서 신규 이미지에
쓸 수 없다). 사람이 하나도 안 잡힌 `with_person` 이미지는 **버린다** — 사용자가 그
갈래에 넣었다는 건 사람이 있다는 뜻이므로, 자동 검출이 실패했다고 배경으로 돌리면
"사람 = 배경"을 가르치는 바로 그 오염이 생긴다(실제로 사람이 멀리 작게 찍힌 사진에서
COCO가 놓치는 경우가 확인됐다). 배경으로 쓰고 싶다면 원본을 `solo/`로 옮기면 된다.

1회성 데이터 준비 스크립트라 Pi에 배포하지 않는다(Makefile의 DEPLOY_PY에 넣지 말 것).
HEIC 원본을 읽으려면 `pip install pi-heif`가 필요하다.

사용법:
    python prepare_lookalike_dataset.py --dry-run --review   # 통계 + 검수 시트만
    python prepare_lookalike_dataset.py                      # 변환 + train 편입
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from dataset_prep import convert, register_heif, sort_key, stratified_holdout

ROOT = Path(__file__).parent
SRC_DIR = ROOT / "lookalike_data"
STAGE_DIR = ROOT / "datasets" / "lookalike"
TRAIN_IMAGES = ROOT / "datasets" / "train" / "images"
TRAIN_LABELS = ROOT / "datasets" / "train" / "labels"
COCO_WEIGHTS = ROOT / "yolov8n.pt"

PREFIX = "lk_"
HOLDOUT_RATIO = 0.20     # 카테고리별 20%
PERSON_CONF = 0.40       # COCO person 자동 라벨 임계값
PERSON_CLASS_ID = 1      # datasets/data.yaml의 names 순서 (0=white_cane, 1=person)
BRANCHES = ("solo", "with_person")


def collect_sources(src_dir: Path, exclude: set[str]) -> tuple[list[tuple[Path, str, str]], list[Path]]:
    """(경로, 갈래, 카테고리) 목록과 제외된 파일 목록을 반환.

    카테고리는 갈래 아래 하위 디렉토리명이며, 하위 디렉토리 없이 바로 놓인 파일은
    "misc"로 묶는다.
    """
    kept: list[tuple[Path, str, str]] = []
    excluded: list[Path] = []
    for branch in BRANCHES:
        branch_dir = src_dir / branch
        if not branch_dir.is_dir():
            continue
        for path in sorted(branch_dir.rglob("*"), key=sort_key):
            if not path.is_file():
                continue
            if path.name in exclude:
                excluded.append(path)
                continue
            rel = path.relative_to(branch_dir)
            category = rel.parts[0] if len(rel.parts) > 1 else "misc"
            kept.append((path, branch, category))
    return kept, excluded


def label_people(names: list[str], stage_images: Path) -> dict[str, list[tuple[float, float, float, float]]]:
    """with_person 이미지에 COCO yolov8n으로 person 박스를 자동 검출 → YOLO 정규화 좌표."""
    from ultralytics import YOLO

    model = YOLO(str(COCO_WEIGHTS))
    boxes: dict[str, list[tuple[float, float, float, float]]] = {}
    batch = 16
    for i in range(0, len(names), batch):
        chunk = names[i:i + batch]
        results = model.predict([str(stage_images / n) for n in chunk],
                                imgsz=640, conf=PERSON_CONF, classes=[0], verbose=False)
        for name, res in zip(chunk, results):
            boxes[name] = [tuple(b) for b in res.boxes.xywhn.tolist()]
    return boxes


def write_review_sheets(items: dict[str, list], stage_images: Path, out_dir: Path) -> None:
    """검수용 컨택트시트 — person 박스를 그려 한 장에 모아 붙인다."""
    import cv2
    import numpy as np

    out_dir.mkdir(parents=True, exist_ok=True)
    names = sorted(items)
    tile, cols = 300, 6
    for page_start in range(0, len(names), cols * 5):
        page = names[page_start:page_start + cols * 5]
        rows = (len(page) + cols - 1) // cols
        sheet = np.full((tile * rows, tile * cols, 3), 40, np.uint8)
        for idx, name in enumerate(page):
            img = cv2.imread(str(stage_images / name))
            if img is None:
                continue
            h, w = img.shape[:2]
            for cx, cy, bw, bh in items[name]:
                cv2.rectangle(img,
                              (int((cx - bw / 2) * w), int((cy - bh / 2) * h)),
                              (int((cx + bw / 2) * w), int((cy + bh / 2) * h)),
                              (0, 0, 255), 2)
            scale = tile / max(h, w)
            img = cv2.resize(img, (int(w * scale), int(h * scale)))
            cv2.putText(img, f"{name} p={len(items[name])}", (3, 14), 0, 0.42, (0, 255, 255), 1)
            y, x = (idx // cols) * tile, (idx % cols) * tile
            sheet[y:y + img.shape[0], x:x + img.shape[1]] = img
        cv2.imwrite(str(out_dir / f"review_{page_start // (cols * 5):02d}.jpg"),
                    sheet, [cv2.IMWRITE_JPEG_QUALITY, 88])
    print(f"검수 시트 → {out_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="복사 없이 통계만 출력")
    parser.add_argument("--review", action="store_true",
                        help="person 자동 라벨을 그린 컨택트시트 생성 (육안 검수용)")
    parser.add_argument("--review-dir", default=None,
                        help="검수 시트 출력 위치 (기본: datasets/lookalike/review)")
    parser.add_argument("--exclude-file", default=None,
                        help="제외할 원본 파일명 목록 txt (한 줄에 하나)")
    parser.add_argument("--src", default=None,
                        help=f"수집 원본 디렉토리 (기본: {SRC_DIR.name})")
    parser.add_argument("--stage", default=None,
                        help=f"변환 스테이징 디렉토리 (기본: datasets/{STAGE_DIR.name})")
    args = parser.parse_args()

    src_dir = Path(args.src) if args.src else SRC_DIR
    stage_dir = Path(args.stage) if args.stage else STAGE_DIR

    if not src_dir.is_dir():
        raise SystemExit(f"원본 디렉토리가 없습니다: {src_dir}\n"
                         f"  {src_dir}/solo/<카테고리>/ 와 {src_dir}/with_person/<카테고리>/ 에\n"
                         f"  수집한 사진을 넣고 다시 실행하세요.")

    if not register_heif():
        print("[warn] pi-heif 미설치 — HEIC 파일을 읽지 못합니다 (pip install pi-heif)")

    exclude = set()
    if args.exclude_file:
        exclude = {line.strip() for line in Path(args.exclude_file).read_text().splitlines()
                   if line.strip()}

    kept, excluded = collect_sources(src_dir, exclude)
    if not kept:
        raise SystemExit(f"{src_dir} 아래에서 이미지를 찾지 못했습니다.")

    print(f"원본 {len(kept) + len(excluded)}장 → 사용 {len(kept)}장 / 제외 {len(excluded)}장")
    for branch in BRANCHES:
        n = sum(1 for _, b, _ in kept if b == branch)
        print(f"  {branch}: {n}장")

    stage_images = stage_dir / "images"
    stage_labels = stage_dir / "labels"
    stage_images.mkdir(parents=True, exist_ok=True)
    stage_labels.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, dict] = {}
    failed: list[tuple[str, str]] = []
    for idx, (src, branch, category) in enumerate(kept, start=1):
        name = f"{PREFIX}{idx:04d}.jpg"
        try:
            convert(src, stage_images / name)
        except Exception as exc:                      # noqa: BLE001 - 원본 포맷이 제각각
            failed.append((src.name, repr(exc)))
            continue
        manifest[name] = {"source": str(src.relative_to(src_dir)),
                          "branch": branch, "category": category}

    if failed:
        print(f"\n[warn] 변환 실패 {len(failed)}장:")
        for n, e in failed:
            print(f"  {n}: {e}")

    names = sorted(manifest)
    print(f"\n변환 완료 {len(names)}장 → {stage_images}")

    # with_person 자동 person 라벨 + solo 강등
    wp_names = [n for n in names if manifest[n]["branch"] == "with_person"]
    person_boxes = label_people(wp_names, stage_images) if wp_names else {}
    if args.review and wp_names:
        review_dir = Path(args.review_dir) if args.review_dir else stage_dir / "review"
        write_review_sheets({n: person_boxes.get(n, []) for n in wp_names},
                            stage_images, review_dir)

    # person이 하나도 안 잡힌 with_person 이미지는 버린다 — solo(배경)로 돌리면
    # 라벨 없는 사람을 "배경"으로 가르치게 되어, 이 스크립트가 막으려는 오염이
    # 그대로 발생한다. 정말 사람이 없는 사진이라면 원본을 solo/로 옮기면 된다.
    dropped = [n for n in wp_names if not person_boxes.get(n)]
    for name in dropped:
        (stage_images / name).unlink(missing_ok=True)
        del manifest[name]
    if dropped:
        print(f"person 미검출로 제외: {len(dropped)}장 "
              f"(사람이 정말 없다면 원본을 solo/로 옮기세요)")
        for name in dropped:
            print(f"  {name}")

    names = sorted(manifest)

    # 라벨 작성 — solo는 빈 파일, with_person은 person 박스만
    for name in names:
        label = stage_labels / f"{Path(name).stem}.txt"
        if manifest[name]["branch"] == "with_person":
            label.write_text("".join(
                f"{PERSON_CLASS_ID} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}\n"
                for cx, cy, w, h in person_boxes[name]))
        else:
            label.write_text("")

    # 카테고리별 층화 — 벤치가 모든 유사물 유형을 고르게 담아야 한다
    strata = {n: manifest[n]["category"] for n in names}
    counts = {c: sum(1 for v in strata.values() if v == c) for c in sorted(set(strata.values()))}
    print(f"카테고리: {counts}")

    holdout = stratified_holdout(strata, HOLDOUT_RATIO)
    # 벤치(eval_background_fp.py)는 "모든 박스가 오탐지"를 전제로 하므로 solo만 넣는다.
    bench = sorted(n for n in holdout if manifest[n]["branch"] == "solo")
    train_names = [n for n in names if n not in holdout]
    print(f"홀드아웃 {len(holdout)}장 (그중 벤치용 solo {len(bench)}장) / train 편입 {len(train_names)}장")

    if args.dry_run:
        print("\n--dry-run: train 복사와 메타데이터 기록을 건너뜁니다.")
        return

    (stage_dir / "holdout.txt").write_text(
        "\n".join(str(stage_images / n) for n in bench) + "\n")
    (stage_dir / "manifest.json").write_text(
        json.dumps({"images": manifest, "holdout": sorted(holdout), "bench": bench,
                    "excluded": [p.name for p in excluded]},
                   indent=1, ensure_ascii=False), encoding="utf-8")

    for name in train_names:
        stem = Path(name).stem
        shutil.copy2(stage_images / name, TRAIN_IMAGES / name)
        shutil.copy2(stage_labels / f"{stem}.txt", TRAIN_LABELS / f"{stem}.txt")
    print(f"train 편입 완료 → {TRAIN_IMAGES}")


if __name__ == "__main__":
    main()
