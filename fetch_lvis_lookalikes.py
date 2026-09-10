"""fetch_lvis_lookalikes.py — LVIS를 색인으로 삼아 "흰지팡이 유사물" 이미지를 수집한다.

배경의 기둥·등산스틱·우산·목발처럼 흰 지팡이와 형태가 비슷한 물체가 지팡이로 오탐지되는
문제에 쓸 네거티브 데이터를 모은다. 데이터셋에 유사물이 "지팡이 아님"으로 라벨링된 사례가
하나도 없어서 모델이 "가늘고 긴 것 = 지팡이"만 배운 것이 근본 원인이다.

LVIS는 **어노테이션(JSON)만** 제공하고 이미지는 COCO 것을 그대로 쓴다. 각 이미지 레코드의
`coco_url`로 필요한 것만 개별 다운로드하면 COCO train2017 전체(18GB)를 받을 필요가 없다
(약 750장 ≈ 120MB).

**LVIS의 라벨은 쓰지 않는다.** 유사물에 박스를 그리지 않는 것이 이 데이터의 본체이고,
person 라벨은 prepare_lookalike_dataset.py가 COCO yolov8n으로 따로 붙인다. LVIS
어노테이션은 순전히 "유사물이 찍힌 이미지를 찾아내는 색인" 역할이다.

solo / with_person 분류도 COCO yolov8n으로 직접 판정한다 — LVIS의 person 라벨은 federated
어노테이션(카테고리별 positive/negative 이미지 집합에만 라벨을 붙임)이라 신뢰할 수 없다
(실측: val의 umbrella 397장 중 person 라벨이 붙은 건 10장뿐).

1회성 데이터 준비 스크립트라 Pi에 배포하지 않는다(Makefile의 DEPLOY_PY에 넣지 말 것).

사용법:
    # 어노테이션 준비 (약 334MB, 압축 해제 시 1.1GB)
    curl -LO https://dl.fbaipublicfiles.com/LVIS/lvis_v1_train.json.zip
    python -c "import zipfile;zipfile.ZipFile('lvis_v1_train.json.zip').extractall('.')"

    python fetch_lvis_lookalikes.py --lvis lvis_v1_train.json --dry-run
    python fetch_lvis_lookalikes.py --lvis lvis_v1_train.json
"""

from __future__ import annotations

import argparse
import collections
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).parent
DST_ROOT = ROOT / "datasets" / "raw" / "lookalike"
COCO_WEIGHTS = ROOT / "yolov8n.pt"
SEED = 0
PERSON_CONF = 0.40
# LVIS는 물체의 "존재"만 라벨링해서 부엌 구석의 빗자루처럼 부수적으로 작게 찍힌 것도 전부
# 포함된다. 그래서 **상한이 걸린 카테고리는 대상 물체가 크게 찍힌 순으로 상위 N장을 고른다**
# (무작위 표본보다 학습 가치가 높다). 상한이 없는 희소 카테고리는 고를 여유가 없으므로
# 크기와 무관하게 전량 받는다 — 필터의 목적은 "많은 것 중 좋은 것 고르기"지 "적은 것을 더
# 줄이기"가 아니다.
DOWNLOAD_WORKERS = 8

# LVIS 카테고리명(언더스코어 형식) → 가져올 상한.
# 고정 수직 구조물(pole, telephone_pole 등)은 일부러 뺐다 — camera_live_pi.py의 움직임
# 게이트가 "움직인 적 없는 지팡이 트랙"을 코드 수준에서 차단하므로, 촬영 시점도 다른
# COCO 기둥 사진을 수백 장 넣는 것보다 그쪽이 확실하고 저렴하다.
TARGETS: dict[str, int] = {
    # 사람이 들고 다니는 가늘고 긴 물체 — 런타임 방어선이 전부 통과시키므로 모델이 배워야 한다
    "ski_pole": 150,        # 등산스틱과 사실상 같은 형태
    "baseball_bat": 150,
    # umbrella는 뺐다 — LVIS의 우산 이미지는 대부분 "펼친 우산/파라솔"이라 캐노피가 지팡이와
    # 전혀 안 닮고 손잡이 축도 가려져 있다(육안 검수로 확인). 접은 우산만 고르는 건 LVIS
    # 라벨로 불가능하다.
    # 실제 실패 사례에 가장 가까운 것들 — 수가 적어 전량
    "broom": None,
    "walking_cane": None,   # ⚠️ 흰지팡이가 섞였을 수 있어 육안 검수 필수
    "shovel": None,
    "mop": None,
    "walking_stick": None,
    "crutch": None,
}

# 흰지팡이 오염 차단 — 이 카테고리가 함께 라벨된 이미지는 (그 카테고리 자신을 수집할 때를
# 제외하고) 받지 않는다. 흰지팡이가 네거티브로 섞여 들어오면 "흰지팡이는 지팡이가 아니다"를
# 가르치게 되어 정확히 반대 효과가 난다.
CANE_RISK = {"walking_cane", "walking_stick"}


def load_index(lvis_path: Path):
    """(카테고리 -> {이미지 id: 면적 비율}), (이미지 id -> coco_url)을 반환."""
    data = json.loads(lvis_path.read_text())
    wanted = set(TARGETS) | CANE_RISK
    cat_ids = {c["id"]: c["name"] for c in data["categories"] if c["name"] in wanted}
    missing = wanted - set(cat_ids.values())
    if missing:
        raise SystemExit(f"LVIS에 없는 카테고리명: {sorted(missing)} — 이름 형식을 확인하세요")

    dims = {i["id"]: (i["width"], i["height"]) for i in data["images"]}
    # (카테고리, 이미지) -> 그 카테고리 물체가 차지하는 최대 면적 비율
    area: dict[str, dict[int, float]] = collections.defaultdict(dict)
    for a in data["annotations"]:
        name = cat_ids.get(a["category_id"])
        if not name:
            continue
        img_id = a["image_id"]
        w, h = dims.get(img_id, (0, 0))
        ratio = (a["bbox"][2] * a["bbox"][3]) / (w * h) if w and h else 0.0
        if ratio > area[name].get(img_id, 0.0):
            area[name][img_id] = ratio
    urls = {i["id"]: i["coco_url"] for i in data["images"]}
    return area, urls


def select(area: dict[str, dict[int, float]]) -> dict[str, list[int]]:
    """카테고리별로 상한만큼 고른다. 상한이 있으면 물체가 큰 순, 없으면 전량.

    흰지팡이 위험 이미지(walking_cane/walking_stick이 함께 라벨된 것)는 그 카테고리
    자신을 수집할 때를 빼고 전부 제외한다 — 흰지팡이가 네거티브로 섞이면 "흰지팡이는
    지팡이가 아니다"를 가르치게 되어 정확히 반대 효과가 난다.
    """
    risk_imgs: set[int] = set()
    for name in CANE_RISK:
        risk_imgs |= set(area.get(name, {}))

    chosen: dict[str, list[int]] = {}
    for name, cap in TARGETS.items():
        per_img = dict(area.get(name, {}))
        if name not in CANE_RISK:
            for img_id in risk_imgs:
                per_img.pop(img_id, None)
        if cap is not None and len(per_img) > cap:
            # 면적 내림차순, 동률은 id 오름차순으로 결정적 고정
            top = sorted(per_img.items(), key=lambda kv: (-kv[1], kv[0]))[:cap]
            chosen[name] = sorted(k for k, _ in top)
        else:
            chosen[name] = sorted(per_img)
    return chosen


def download(url: str, dst: Path) -> bool:
    if dst.exists():
        return True
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            dst.write_bytes(r.read())
        return True
    except Exception:                    # noqa: BLE001 - 개별 실패는 건너뛰고 계속
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lvis", default="lvis_v1_train.json",
                        help="LVIS 어노테이션 JSON 경로 (기본: lvis_v1_train.json)")
    parser.add_argument("--dry-run", action="store_true",
                        help="다운로드 없이 카테고리 매칭과 장수만 출력")
    parser.add_argument("--dst", default=None, help=f"출력 루트 (기본: {DST_ROOT.relative_to(ROOT)})")
    args = parser.parse_args()

    lvis_path = Path(args.lvis)
    if not lvis_path.is_file():
        raise SystemExit(
            f"LVIS 어노테이션이 없습니다: {lvis_path}\n"
            "  curl -LO https://dl.fbaipublicfiles.com/LVIS/lvis_v1_train.json.zip\n"
            "  python -c \"import zipfile;zipfile.ZipFile('lvis_v1_train.json.zip').extractall('.')\"")

    dst_root = Path(args.dst) if args.dst else DST_ROOT
    print(f"LVIS 어노테이션 로드 중… ({lvis_path})")
    area, urls = load_index(lvis_path)
    chosen = select(area)

    print(f"\n{'카테고리':<18} {'LVIS 보유':>9} {'선택':>6}  {'선택분 면적 중앙값':>16}")
    print("-" * 50)
    for name, cap in TARGETS.items():
        ratios = sorted(area.get(name, {}).get(i, 0.0) for i in chosen[name])
        med = ratios[len(ratios)//2] if ratios else 0.0
        print(f"  {name:<16} {len(area.get(name, {})):>9} {len(chosen[name]):>6}"
              f"  {med*100:>14.1f}%{'  (상한 %d)' % cap if cap else ''}")
    total = sum(len(v) for v in chosen.values())
    print("-" * 50)
    print(f"  {'합계':<16} {'':>9} {total:>6}")

    if args.dry_run:
        print("\n--dry-run: 다운로드를 건너뜁니다.")
        return

    # 1) 이미지 다운로드 (카테고리별 임시 폴더)
    staging = dst_root / "_incoming"
    jobs = []
    for name, ids in chosen.items():
        (staging / name).mkdir(parents=True, exist_ok=True)
        for img_id in ids:
            if img_id in urls:
                jobs.append((urls[img_id], staging / name / f"{img_id}.jpg"))
    print(f"\n이미지 {len(jobs)}장 다운로드 중…")
    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as ex:
        results = list(ex.map(lambda j: download(*j), jobs))
    ok = sum(results)
    print(f"  성공 {ok} / 실패 {len(jobs) - ok}")

    # 2) COCO yolov8n으로 사람 유무 판정 → solo / with_person 분류
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
    print("  → 검수 시트에서 walking_cane에 흰지팡이가 섞였는지, umbrella가 펼친 우산인지 확인")


if __name__ == "__main__":
    main()
