"""fetch_openimages_lookalikes.py — Open Images에서 목발(Crutch) 이미지를 네거티브로 수집한다.

fetch_lvis_lookalikes.py의 보완재다. LVIS에는 crutch가 13장뿐이라 "사람이 든 가늘고 긴
막대" 유형 중 목발만 표본이 크게 부족했다. Open Images V7은 601개 boxable 클래스에
`Crutch`(/m/05441v)를 갖고 있어 85장을 더 얻을 수 있다.

**Open Images의 박스도 쓰지 않는다** — 유사물에 박스를 그리지 않는 것이 이 데이터의
본체이고(그래야 "이건 지팡이가 아니다"를 가르친다), person 라벨은
prepare_lookalike_dataset.py가 COCO yolov8n으로 따로 붙인다. 어노테이션 CSV는 순전히
"목발이 찍힌 이미지 id를 찾는 색인" 역할이다.

어노테이션 CSV는 스플릿별로 크기가 크게 다르다(val 25MB / test 77MB / **train 2.5GB**).
train은 파일로 받지 않고 스트리밍하며 해당 클래스 행만 걸러낸다.

이미지는 공개 S3 버킷에서 id 단위로 받는다:
    https://open-images-dataset.s3.amazonaws.com/<split>/<image_id>.jpg

1회성 데이터 준비 스크립트라 Pi에 배포하지 않는다(Makefile의 DEPLOY_PY에 넣지 말 것).

사용법:
    python fetch_openimages_lookalikes.py --dry-run
    python fetch_openimages_lookalikes.py
"""

from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import io
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).parent
DST_ROOT = ROOT / "datasets" / "raw" / "lookalike"
COCO_WEIGHTS = ROOT / "yolov8n.pt"
PERSON_CONF = 0.40
DOWNLOAD_WORKERS = 8

# 수집 대상 — Open Images 클래스 MID. LVIS에서 표본이 모자란 것만 보완한다.
# (Umbrella /m/0hnnb 는 LVIS와 같은 이유로 뺐다 — 펼친 우산이 대부분이라 지팡이와 안 닮음)
TARGETS: dict[str, tuple[str, int | None]] = {
    "crutch_oi": ("/m/05441v", None),   # 전량. LVIS 이름과 구분해 출처를 남긴다
}

ANN_URLS = {
    "validation": "https://storage.googleapis.com/openimages/v5/validation-annotations-bbox.csv",
    "test": "https://storage.googleapis.com/openimages/v5/test-annotations-bbox.csv",
    "train": "https://storage.googleapis.com/openimages/v6/oidv6-train-annotations-bbox.csv",
}
IMAGE_URL = "https://open-images-dataset.s3.amazonaws.com/{split}/{image_id}.jpg"


def scan_split(split: str, mids: set[str]) -> dict[str, dict[str, float]]:
    """스플릿 CSV를 스트리밍하며 대상 MID 행만 골라 (mid -> {image_id: 최대 면적비})를 만든다.

    2.5GB짜리 train CSV를 디스크에 받지 않기 위해 응답을 그대로 흘려보내며 처리한다.
    좌표가 정규화(0~1)라 면적비는 (XMax-XMin)*(YMax-YMin)으로 바로 나온다.
    """
    out: dict[str, dict[str, float]] = {m: {} for m in mids}
    with urllib.request.urlopen(ANN_URLS[split], timeout=60) as resp:
        reader = csv.DictReader(io.TextIOWrapper(resp, encoding="utf-8"))
        for row in reader:
            mid = row["LabelName"]
            if mid not in mids:
                continue
            ratio = (float(row["XMax"]) - float(row["XMin"])) * \
                    (float(row["YMax"]) - float(row["YMin"]))
            img = row["ImageID"]
            if ratio > out[mid].get(img, 0.0):
                out[mid][img] = ratio
    return out


def existing_hashes(dst_root: Path, skip: Path) -> set[str]:
    """이미 수집한 이미지의 md5 — LVIS 수집분과 겹치는 사진을 두 번 넣지 않기 위함.

    `skip`(이번에 받은 staging)은 반드시 제외해야 한다 — 포함하면 방금 받은 파일이
    자기 자신과 중복으로 판정되어 전량이 삭제된다.
    """
    seen = set()
    for p in dst_root.rglob("*.jpg"):
        if skip in p.parents:
            continue
        seen.add(hashlib.md5(p.read_bytes()).hexdigest())
    return seen


def download(split: str, image_id: str, dst: Path) -> bool:
    if dst.exists():
        return True
    try:
        with urllib.request.urlopen(
                IMAGE_URL.format(split=split, image_id=image_id), timeout=60) as r:
            dst.write_bytes(r.read())
        return True
    except Exception:                    # noqa: BLE001 - 개별 실패는 건너뛰고 계속
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="다운로드 없이 스플릿별 장수만 출력")
    parser.add_argument("--dst", default=None, help=f"출력 루트 (기본: {DST_ROOT.relative_to(ROOT)})")
    parser.add_argument("--splits", nargs="+", default=["validation", "test", "train"],
                        choices=list(ANN_URLS), help="훑을 스플릿")
    args = parser.parse_args()

    dst_root = Path(args.dst) if args.dst else DST_ROOT
    mids = {mid for mid, _ in TARGETS.values()}
    name_of = {mid: name for name, (mid, _) in TARGETS.items()}

    # (name -> [(split, image_id, 면적비)])
    found: dict[str, list[tuple[str, str, float]]] = collections.defaultdict(list)
    for split in args.splits:
        print(f"{split} 어노테이션 스캔 중… ({ANN_URLS[split].rsplit('/', 1)[-1]})")
        per_mid = scan_split(split, mids)
        for mid, imgs in per_mid.items():
            for img, ratio in imgs.items():
                found[name_of[mid]].append((split, img, ratio))
            print(f"  {name_of[mid]:<12} {len(imgs):>5}장")

    print(f"\n{'카테고리':<14} {'발견':>6} {'선택':>6}  {'선택분 면적 중앙값':>16}")
    print("-" * 50)
    chosen: dict[str, list[tuple[str, str]]] = {}
    for name, (_, cap) in TARGETS.items():
        items = sorted(found[name], key=lambda t: (-t[2], t[1]))
        picked = items[:cap] if cap is not None else items
        ratios = sorted(r for _, _, r in picked)
        med = ratios[len(ratios) // 2] if ratios else 0.0
        chosen[name] = [(s, i) for s, i, _ in picked]
        print(f"  {name:<12} {len(items):>6} {len(picked):>6}  {med*100:>14.1f}%")
    total = sum(len(v) for v in chosen.values())
    print("-" * 50)
    print(f"  {'합계':<12} {'':>6} {total:>6}")

    if args.dry_run:
        print("\n--dry-run: 다운로드를 건너뜁니다.")
        return

    staging = dst_root / "_incoming"
    jobs = []
    for name, items in chosen.items():
        (staging / name).mkdir(parents=True, exist_ok=True)
        for split, img in items:
            jobs.append((split, img, staging / name / f"{img}.jpg"))
    print(f"\n이미지 {len(jobs)}장 다운로드 중…")
    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as ex:
        results = list(ex.map(lambda j: download(*j), jobs))
    print(f"  성공 {sum(results)} / 실패 {len(jobs) - sum(results)}")

    seen = existing_hashes(dst_root, staging)
    dup = 0
    for name in chosen:
        for f in sorted((staging / name).glob("*.jpg")):
            h = hashlib.md5(f.read_bytes()).hexdigest()
            if h in seen:
                f.unlink()
                dup += 1
            else:
                seen.add(h)
    if dup:
        print(f"  기존 수집분과 중복 {dup}장 제거")

    # COCO yolov8n으로 사람 유무 판정 → solo / with_person 분류 (LVIS 경로와 동일 규칙)
    from ultralytics import YOLO
    model = YOLO(str(COCO_WEIGHTS))
    print("\n사람 유무 판정 중…")
    moved = collections.Counter()
    for name in chosen:
        files = sorted((staging / name).glob("*.jpg"))
        for i in range(0, len(files), 16):
            chunk = files[i:i + 16]
            preds = model.predict([str(f) for f in chunk], imgsz=640,
                                  conf=PERSON_CONF, classes=[0], verbose=False)
            for f, res in zip(chunk, preds):
                branch = "with_person" if len(res.boxes) else "solo"
                out = dst_root / branch / name
                out.mkdir(parents=True, exist_ok=True)
                f.rename(out / f.name)
                moved[branch] += 1
        try:
            (staging / name).rmdir()
        except OSError:
            pass
    try:
        staging.rmdir()
    except OSError:
        pass

    print(f"\n분류 완료 → {dst_root}")
    for branch, n in sorted(moved.items()):
        print(f"  {branch}: {n}장")
    print("\n다음 단계:")
    print("  python prepare_lookalike_dataset.py --dry-run --review")
    print("  → 검수 시트에서 목발 사진에 흰지팡이가 섞이지 않았는지 확인")


if __name__ == "__main__":
    main()
