"""eval_background_fp.py — 배경(네거티브) 이미지에서 나오는 오탐지를 세는 벤치마크.

지팡이도 사람도 없는 배경 사진 목록을 주면, 모델이 그 위에서 만들어낸 박스는 전부
false positive다. 가중치가 달라져도 같은 잣대로 비교할 수 있게 conf 임계값별로
"오탐지 이미지 수 / 박스 수 / 클래스별 분해 / 최고 conf"를 출력한다.

PT / ONNX / TFLite 등 ultralytics가 로드할 수 있는 형식이면 무엇이든 받는다 —
int8 양자화 후 오탐지가 되돌아오지 않았는지 확인하는 데도 그대로 쓴다.

사용법:
    python eval_background_fp.py --weights runs/white_cane_v4_320/weights/best.pt \
           --images datasets/background/holdout.txt --imgsz 320
"""

from __future__ import annotations

import argparse
from pathlib import Path

CLASS_NAMES = {0: "white_cane", 1: "person"}
DEFAULT_THRESHOLDS = (0.25, 0.40, 0.50)


def load_image_list(spec: str) -> list[str]:
    """목록 파일(.txt, 한 줄에 한 경로) 또는 디렉토리를 받아 이미지 경로 목록을 반환."""
    path = Path(spec)
    if path.is_dir():
        return sorted(str(p) for p in path.iterdir()
                      if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp"})
    return [line.strip() for line in path.read_text().splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weights", required=True)
    parser.add_argument("--images", default="datasets/background/holdout.txt",
                        help="이미지 목록 txt 또는 디렉토리")
    parser.add_argument("--imgsz", type=int, default=320)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--thresholds", type=float, nargs="+", default=list(DEFAULT_THRESHOLDS))
    args = parser.parse_args()

    from ultralytics import YOLO

    images = load_image_list(args.images)
    model = YOLO(args.weights)

    # TFLite/ONNX 등 export 산출물은 배치 1로 고정 export되어 다중 이미지를 한 번에 못 받는다.
    batch = args.batch if Path(args.weights).suffix == ".pt" else 1

    # 가장 낮은 임계값으로 한 번만 추론하고, 나머지 임계값은 결과를 필터링해서 재사용한다.
    floor = min(args.thresholds)
    detections: list[list[tuple[int, float]]] = []
    for i in range(0, len(images), batch):
        results = model.predict(images[i:i + batch], imgsz=args.imgsz,
                                conf=floor, verbose=False)
        for res in results:
            detections.append([(int(c), float(s))
                               for c, s in zip(res.boxes.cls.tolist(), res.boxes.conf.tolist())])

    print(f"가중치: {args.weights}")
    print(f"배경 이미지: {len(images)}장 (imgsz={args.imgsz})\n")
    header = f"{'conf':>6} {'FP 이미지':>10} {'FP 박스':>8} {'cane':>6} {'person':>7} {'박스/장':>8} {'최고 conf':>10}"
    print(header)
    print("-" * len(header))
    for thr in sorted(args.thresholds):
        kept = [[d for d in dets if d[1] >= thr] for dets in detections]
        boxes = [d for dets in kept for d in dets]
        n_images = sum(1 for dets in kept if dets)
        cane = sum(1 for c, _ in boxes if c == 0)
        person = sum(1 for c, _ in boxes if c == 1)
        top = max((s for _, s in boxes), default=0.0)
        print(f"{thr:>6.2f} {n_images:>6}/{len(images):<3} {len(boxes):>8} {cane:>6} {person:>7}"
              f" {len(boxes)/len(images):>8.2f} {top:>10.3f}")

    worst = sorted(((max((s for _, s in dets), default=0.0), Path(p).name, dets)
                    for p, dets in zip(images, detections) if dets), reverse=True)[:10]
    if worst:
        print("\n오탐지 상위 이미지:")
        for score, name, dets in worst:
            detail = ", ".join(f"{CLASS_NAMES.get(c, c)} {s:.2f}" for c, s in sorted(dets, key=lambda d: -d[1])[:4])
            print(f"  {name:16s} {detail}")


if __name__ == "__main__":
    main()
