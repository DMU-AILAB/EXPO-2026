"""dataset_prep.py — 데이터 준비 스크립트들이 공유하는 이미지 정규화/분할 헬퍼.

`prepare_background_dataset.py`(일반 배경)와 `prepare_lookalike_dataset.py`(지팡이
유사물)가 같은 정규화 파이프라인을 쓴다:

    EXIF 회전 반영 → RGB → 최대변 640 리사이즈 → jpg → 결정적 리네임

리네임이 필요한 이유는 수집한 원본에 `1.jpg`/`1.JPG`처럼 확장자만 다른 같은 stem이
섞여 있는데, YOLO가 stem으로 이미지↔라벨을 매칭하기 때문이다. EXIF 회전을 반드시
적용해야 하는 이유는 폰/웹 사진에 방향 정보가 들어 있어서, cv2 경로로 그냥 읽으면
옆으로 누운 채 학습되기 때문이다.

1회성 데이터 준비용이라 Pi에 배포하지 않는다(Makefile의 DEPLOY_PY에 추가하지 말 것).
HEIC 원본을 읽으려면 `pip install pi-heif`가 필요하다.
"""

from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageOps

MAX_SIDE = 640
JPEG_QUALITY = 92
SEED = 0


def register_heif() -> bool:
    """HEIC/HEIF 디코더를 PIL에 등록. 설치돼 있지 않으면 False (avif는 Pillow 기본 지원)."""
    try:
        import pi_heif
    except ImportError:
        return False
    pi_heif.register_heif_opener()
    return True


def sort_key(path: Path):
    """숫자 파일명은 숫자 순, 나머지는 이름 순 — 재실행해도 같은 번호가 나오게 고정한다."""
    stem = path.stem
    return (0, int(stem), path.suffix.lower()) if stem.isdigit() else (1, 0, path.name.lower())


def convert(src: Path, dst: Path, max_side: int = MAX_SIDE) -> tuple[int, int]:
    """EXIF 회전 반영 + RGB + 최대변 리사이즈 후 jpg 저장. 저장된 (w, h) 반환."""
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((max_side, max_side), Image.LANCZOS)
        im.save(dst, "JPEG", quality=JPEG_QUALITY)
        return im.size


def stratified_holdout(
    strata: dict[str, str],
    ratio: float,
    order: tuple[str, ...] | None = None,
    seed: int = SEED,
) -> set[str]:
    """층별로 `ratio` 비율씩 뽑아 홀드아웃 집합을 만든다.

    단순 무작위로 뽑으면 소수 층(예: 오탐지가 나는 이미지, 희귀 유사물 카테고리)이
    벤치에 거의 안 들어가 지표가 둔감해진다.

    `order`는 층을 순회하는 순서다. **하나의 RNG를 층마다 이어서 쓰기 때문에 순서가
    바뀌면 뽑히는 표본 자체가 달라진다** — 이미 이 함수로 나눠 학습을 마친 데이터셋은
    반드시 그때와 같은 순서를 넘겨야 한다(안 그러면 학습에 쓴 이미지가 벤치로 넘어와
    누수가 생긴다). 생략하면 층 이름 오름차순.
    """
    rng = random.Random(seed)
    holdout: set[str] = set()
    for stratum in (order if order is not None else tuple(sorted(set(strata.values())))):
        members = sorted(n for n, s in strata.items() if s == stratum)
        holdout.update(rng.sample(members, round(len(members) * ratio)))
    return holdout
