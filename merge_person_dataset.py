"""merge_person_dataset.py — Roboflow "Pedestrian Detection CCTV" 데이터셋을
기존 white_cane 데이터셋(datasets/train|val|test)에 1회성으로 병합한다.

이 데이터셋은 사실 마스크 착용 탐지용이라 nc=7
(face_mask, human_face, incorrect_face_mask,
 person_female, person_female_back, person_male, person_male_back)이다.
그중 몸통 박스인 person_female/person_female_back/person_male/person_male_back
(class 3~6)만 person(class 1)으로 남기고 얼굴 전용 클래스(0~2)는 버린다.

한 번 실행하면 끝나는 데이터 준비 스크립트라 Pi에 배포하지 않는다.
"""

from __future__ import annotations

import shutil
from pathlib import Path

SRC_ROOT = Path(__file__).parent / "datasets" / "Pedestrian Detection CCTV yolov8"
DST_ROOT = Path(__file__).parent / "datasets"
SPLIT_MAP = {"train": "train", "valid": "val", "test": "test"}
KEEP_CLASSES = {3, 4, 5, 6}   # person_female, person_female_back, person_male, person_male_back
PERSON_CLASS_ID = 1
PREFIX = "pedcctv_"


def merge_split(src_split: str, dst_split: str) -> dict:
    src_images = SRC_ROOT / src_split / "images"
    src_labels = SRC_ROOT / src_split / "labels"
    dst_images = DST_ROOT / dst_split / "images"
    dst_labels = DST_ROOT / dst_split / "labels"

    copied, skipped = 0, 0
    for label_path in sorted(src_labels.glob("*.txt")):
        lines = label_path.read_text().splitlines()
        kept = []
        for line in lines:
            parts = line.split()
            if not parts:
                continue
            cls = int(parts[0])
            if cls in KEEP_CLASSES:
                kept.append(" ".join([str(PERSON_CLASS_ID), *parts[1:]]))
        if not kept:
            skipped += 1
            continue

        img_path = src_images / f"{label_path.stem}.jpg"
        if not img_path.exists():
            skipped += 1
            continue

        dst_img_name = f"{PREFIX}{img_path.name}"
        dst_label_name = f"{PREFIX}{label_path.name}"

        shutil.copy2(img_path, dst_images / dst_img_name)
        (dst_labels / dst_label_name).write_text("\n".join(kept) + "\n")
        copied += 1

    return {"copied": copied, "skipped": skipped}


def main() -> None:
    totals = {"copied": 0, "skipped": 0}
    for src_split, dst_split in SPLIT_MAP.items():
        stats = merge_split(src_split, dst_split)
        print(f"[{src_split} -> {dst_split}] copied={stats['copied']} skipped={stats['skipped']}")
        totals["copied"] += stats["copied"]
        totals["skipped"] += stats["skipped"]
    print(f"TOTAL copied={totals['copied']} skipped={totals['skipped']}")


if __name__ == "__main__":
    main()
