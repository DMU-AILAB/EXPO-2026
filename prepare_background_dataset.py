"""prepare_background_dataset.py — extra_data/의 배경 사진을 YOLO 네거티브 샘플로 편입한다.

extra_data/에는 흰 지팡이도 사람도 없는 일반 배경 사진이 모여 있다. YOLO는 라벨 파일이
비어 있는 이미지를 배경(negative)으로 학습하므로, 이 사진들을 빈 라벨과 함께 train에
넣으면 오탐지(false positive)를 줄일 수 있다. 실제로 white_cane_v4_320 모델은 이 폴더의
272장 중 78장에서 오탐지를 냈다 — 즉 하드 네거티브 모음이다.

한 번 실행하면 끝나는 데이터 준비 스크립트라 Pi에 배포하지 않는다
(merge_person_dataset.py와 같은 성격 — Makefile의 DEPLOY_PY에 추가하지 말 것).

원본이 HEIC/avif/webp 혼재이고 폰 촬영본이라 EXIF 회전 정보가 있어서, 단순 복사가 아니라
  exif_transpose → RGB → 최대변 640 리사이즈 → jpg
로 정규화한 뒤 bg_XXXX.jpg 로 리네임한다. 리네임이 필요한 이유는 extra_data에 1.jpg/1.JPG
처럼 확장자만 다른 같은 stem이 있는데, YOLO는 stem으로 이미지↔라벨을 매칭하기 때문이다.

HEIC 읽기에는 pi-heif가 필요하다:  pip install pi-heif

사용법:
    python prepare_background_dataset.py --dry-run   # 통계만 출력
    python prepare_background_dataset.py             # 변환 + train 편입
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from dataset_prep import convert, register_heif, sort_key, stratified_holdout

ROOT = Path(__file__).parent
SRC_DIR = ROOT / "extra_data"
STAGE_DIR = ROOT / "datasets" / "background"
TRAIN_IMAGES = ROOT / "datasets" / "train" / "images"
TRAIN_LABELS = ROOT / "datasets" / "train" / "labels"
BASELINE_WEIGHTS = ROOT / "runs" / "white_cane_v4_320" / "weights" / "best.pt"

PREFIX = "bg_"
HOLDOUT_RATIO = 0.15   # 층별 15% → 268장 기준 약 40장

# 수동 검수 + COCO yolov8n 자동 스크리닝에서 실제 사람이 찍힌 것으로 확인된 이미지.
# 라벨 없이 배경으로 학습시키면 "사람 = 배경"을 가르치게 되어 person 재현율이 떨어진다.
EXCLUDE = {
    "263.jpg",    # 거리 사진, 보행자 5~6명이 뚜렷하게 걸어감
    "39.jpeg",    # 벚꽃 거리, 상점 앞 인물 1명
    "270.jpg",    # 런던 거리, 하단 인물 실루엣 1명
    "228.HEIC",   # 인물 등신대/포스터 — 실제 사람은 아니나 사람 형상이 크게 차지
}


def collect_sources() -> tuple[list[Path], list[Path]]:
    files = [p for p in SRC_DIR.iterdir() if p.is_file()]
    kept = sorted((p for p in files if p.name not in EXCLUDE), key=sort_key)
    excluded = sorted((p for p in files if p.name in EXCLUDE), key=sort_key)
    return kept, excluded


def stratify(names: list[str], stage_images: Path) -> dict[str, str]:
    """v4 모델을 돌려 각 배경 이미지를 cane_fp / person_fp / clean 층으로 분류한다.

    홀드아웃(FP 벤치)을 단순 무작위로 뽑으면 오탐지 이미지가 거의 안 들어가 지표가 둔감해진다.
    학습셋과 같은 오탐지 분포를 갖도록 층화 추출하기 위한 사전 분류다.
    """
    from ultralytics import YOLO

    model = YOLO(str(BASELINE_WEIGHTS))
    strata: dict[str, str] = {}
    batch = 16
    for i in range(0, len(names), batch):
        chunk = names[i:i + batch]
        results = model.predict([str(stage_images / n) for n in chunk],
                                imgsz=320, conf=0.25, verbose=False)
        for name, res in zip(chunk, results):
            classes = set(int(c) for c in res.boxes.cls.tolist())
            strata[name] = "cane_fp" if 0 in classes else "person_fp" if 1 in classes else "clean"
    return strata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="복사 없이 통계만 출력")
    args = parser.parse_args()

    if not register_heif():
        print("[warn] pi-heif 미설치 — HEIC 파일을 읽지 못합니다 (pip install pi-heif)")

    kept, excluded = collect_sources()
    print(f"원본 {len(kept) + len(excluded)}장 → 사용 {len(kept)}장 / 제외 {len(excluded)}장")
    for p in excluded:
        print(f"  제외: {p.name}")

    stage_images = STAGE_DIR / "images"
    stage_labels = STAGE_DIR / "labels"
    stage_images.mkdir(parents=True, exist_ok=True)
    stage_labels.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, str] = {}
    failed: list[tuple[str, str]] = []
    for idx, src in enumerate(kept, start=1):
        name = f"{PREFIX}{idx:04d}.jpg"
        try:
            convert(src, stage_images / name)
        except Exception as exc:                      # noqa: BLE001 - 원본 포맷이 제각각
            failed.append((src.name, repr(exc)))
            continue
        (stage_labels / f"{PREFIX}{idx:04d}.txt").write_text("")   # 빈 라벨 = 배경
        manifest[name] = src.name

    if failed:
        print(f"\n[warn] 변환 실패 {len(failed)}장:")
        for n, e in failed:
            print(f"  {n}: {e}")

    names = sorted(manifest)
    print(f"\n변환 완료 {len(names)}장 → {stage_images}")

    strata = stratify(names, stage_images)
    counts = {s: sum(1 for v in strata.values() if v == s) for s in ("cane_fp", "person_fp", "clean")}
    print(f"v4 오탐지 층화: {counts}")

    # 층 순회 순서는 최초 실행 때와 동일하게 고정 — 순서가 바뀌면 표본이 달라져
    # 이미 v5b 학습에 쓴 이미지가 벤치로 넘어온다(누수).
    holdout = stratified_holdout(strata, HOLDOUT_RATIO,
                                 order=("cane_fp", "person_fp", "clean"))
    train_names = [n for n in names if n not in holdout]
    holdout_counts = {s: sum(1 for n in holdout if strata[n] == s) for s in counts}
    print(f"홀드아웃(FP 벤치) {len(holdout)}장 {holdout_counts} / train 편입 {len(train_names)}장")

    if args.dry_run:
        print("\n--dry-run: train 복사와 메타데이터 기록을 건너뜁니다.")
        return

    (STAGE_DIR / "holdout.txt").write_text(
        "\n".join(str(stage_images / n) for n in sorted(holdout)) + "\n")
    (STAGE_DIR / "manifest.json").write_text(
        json.dumps({"images": manifest, "strata": strata,
                    "holdout": sorted(holdout), "excluded": [p.name for p in excluded]},
                   indent=1, ensure_ascii=False))

    for name in train_names:
        shutil.copy2(stage_images / name, TRAIN_IMAGES / name)
        shutil.copy2(stage_labels / f"{Path(name).stem}.txt", TRAIN_LABELS / f"{Path(name).stem}.txt")
    print(f"train 편입 완료 → {TRAIN_IMAGES}")


if __name__ == "__main__":
    main()
