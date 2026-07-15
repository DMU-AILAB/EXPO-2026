"""
detect.py — 흰 지팡이 탐지 추론 모듈

단독 실행:
    python detect.py image.jpg
    python detect.py image.jpg --model runs/white_cane_v2/weights/best.pt --conf 0.25
    python detect.py datasets/test/images/ --save-dir output/

외부 임포트:
    from detect import WhiteCaneDetector
    detector = WhiteCaneDetector()
    results  = detector.predict("image.jpg")
    annotated = detector.annotate("image.jpg", save_path="out.jpg")
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Union

import cv2
import numpy as np

# 기본 모델 경로 (이 파일 기준 상대 경로)
_DEFAULT_MODEL = Path(__file__).parent / "runs/white_cane_v2/weights/best.pt"


class WhiteCaneDetector:
    """YOLOv8n 기반 흰 지팡이 탐지기.

    Parameters
    ----------
    model_path : str | Path, optional
        .pt 가중치 파일 경로. 기본값은 best.pt.
    conf : float
        탐지 신뢰도 임계값 (0~1).
    device : str
        추론 장치. 'cuda', 'cpu', '0' 등.
    """

    def __init__(
        self,
        model_path: Union[str, Path] = _DEFAULT_MODEL,
        conf: float = 0.25,
        device: str = "cuda",
    ) -> None:
        from ultralytics import YOLO

        self.model = YOLO(str(model_path))
        self.conf = conf
        self.device = device

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def predict(self, source: Union[str, Path, np.ndarray]) -> list[dict]:
        """이미지에서 흰 지팡이를 탐지합니다.

        Parameters
        ----------
        source : str | Path | np.ndarray
            이미지 파일 경로 또는 BGR numpy 배열.

        Returns
        -------
        list[dict]
            탐지 결과 리스트. 각 항목:
            {
                "bbox":  [x1, y1, x2, y2],   # 픽셀 좌표
                "conf":  float,               # 신뢰도
                "class": int,                 # 클래스 ID (0 = white_cane)
                "label": str,                 # 클래스 이름
            }
        """
        results = self.model.predict(
            source,
            conf=self.conf,
            device=self.device,
            verbose=False,
        )
        detections = []
        for r in results:
            for box in r.boxes:
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                detections.append(
                    {
                        "bbox": [round(x1), round(y1), round(x2), round(y2)],
                        "conf": round(float(box.conf[0]), 4),
                        "class": int(box.cls[0]),
                        "label": r.names[int(box.cls[0])],
                    }
                )
        return detections

    def annotate(
        self,
        source: Union[str, Path, np.ndarray],
        save_path: Union[str, Path, None] = None,
        show: bool = False,
    ) -> np.ndarray:
        """탐지 결과를 이미지에 그려서 반환합니다.

        Parameters
        ----------
        source : str | Path | np.ndarray
            이미지 파일 경로 또는 BGR numpy 배열.
        save_path : str | Path, optional
            저장할 경로. None이면 저장하지 않음.
        show : bool
            True이면 cv2.imshow로 결과를 화면에 출력.

        Returns
        -------
        np.ndarray
            바운딩 박스가 그려진 BGR 이미지.
        """
        if isinstance(source, np.ndarray):
            img = source.copy()
        else:
            img = cv2.imread(str(source))
            if img is None:
                raise FileNotFoundError(f"이미지를 읽을 수 없습니다: {source}")

        detections = self.predict(source)

        for det in detections:
            x1, y1, x2, y2 = det["bbox"]
            conf = det["conf"]
            label = f"{det['label']} {conf:.2f}"

            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 2)
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
            cv2.rectangle(img, (x1, y1 - th - 6), (x1 + tw + 4, y1), (0, 255, 0), -1)
            cv2.putText(img, label, (x1 + 2, y1 - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 1, cv2.LINE_AA)

        if save_path is not None:
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(save_path), img)

        if show:
            cv2.imshow("White Cane Detection", img)
            cv2.waitKey(0)
            cv2.destroyAllWindows()

        return img

    def predict_batch(
        self,
        image_dir: Union[str, Path],
        save_dir: Union[str, Path, None] = None,
        extensions: tuple[str, ...] = (".jpg", ".jpeg", ".png"),
    ) -> list[dict]:
        """디렉토리 내 이미지를 일괄 탐지합니다.

        Parameters
        ----------
        image_dir : str | Path
            이미지가 담긴 폴더 경로.
        save_dir : str | Path, optional
            결과 이미지를 저장할 폴더. None이면 저장하지 않음.
        extensions : tuple[str]
            처리할 이미지 확장자.

        Returns
        -------
        list[dict]
            { "file": str, "detections": list[dict] } 형태의 리스트.
        """
        image_dir = Path(image_dir)
        paths = sorted(p for p in image_dir.iterdir() if p.suffix.lower() in extensions)
        if not paths:
            print(f"[경고] {image_dir} 에서 이미지를 찾을 수 없습니다.")
            return []

        all_results = []
        for path in paths:
            dets = self.predict(path)
            entry = {"file": str(path), "detections": dets}
            all_results.append(entry)

            if save_dir is not None:
                out_path = Path(save_dir) / path.name
                self.annotate(path, save_path=out_path)

        return all_results


# ------------------------------------------------------------------ #
# CLI                                                                 #
# ------------------------------------------------------------------ #

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="흰 지팡이 탐지 — 이미지 또는 폴더를 입력하세요."
    )
    parser.add_argument("source", help="이미지 파일 또는 폴더 경로")
    parser.add_argument(
        "--model",
        default=str(_DEFAULT_MODEL),
        help="가중치 파일 경로 (기본값: runs/white_cane_v2/weights/best.pt)",
    )
    parser.add_argument("--conf", type=float, default=0.25, help="신뢰도 임계값 (기본값: 0.25)")
    parser.add_argument("--device", default="cuda", help="추론 장치 (기본값: cuda)")
    parser.add_argument("--save-dir", default="output", help="결과 저장 폴더 (기본값: output/)")
    parser.add_argument("--show", action="store_true", help="결과 화면 출력 (단일 이미지만)")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    detector = WhiteCaneDetector(
        model_path=args.model,
        conf=args.conf,
        device=args.device,
    )

    source = Path(args.source)

    if source.is_dir():
        print(f"[배치 모드] {source} → {args.save_dir}/")
        results = detector.predict_batch(source, save_dir=args.save_dir)
        total_det = sum(len(r["detections"]) for r in results)
        print(f"처리 완료: {len(results)}장 / 총 탐지 {total_det}건")

    elif source.is_file():
        print(f"[단일 이미지] {source}")
        dets = detector.predict(source)

        if dets:
            for d in dets:
                print(f"  ✓ {d['label']}  conf={d['conf']:.4f}  bbox={d['bbox']}")
        else:
            print("  탐지 결과 없음")

        save_path = Path(args.save_dir) / source.name
        detector.annotate(source, save_path=save_path, show=args.show)
        print(f"결과 저장: {save_path}")

    else:
        print(f"[오류] 경로를 찾을 수 없습니다: {source}")


if __name__ == "__main__":
    main()
