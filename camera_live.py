"""
camera_live.py — 실시간 카메라 피드 흰 지팡이 탐지 뷰어

사용법:
    python camera_live.py                          # 기본 웹캠 (인덱스 0)
    python camera_live.py --source 1              # 두 번째 웹캠
    python camera_live.py --source video.mp4      # 영상 파일
    python camera_live.py --conf 0.4              # 신뢰도 임계값 조정
    python camera_live.py --device cpu            # CPU 강제 사용
"""

from __future__ import annotations

import argparse
import time

import cv2

from detect import WhiteCaneDetector


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="실시간 흰 지팡이 탐지 뷰어")
    parser.add_argument(
        "--source",
        default="0",
        help="웹캠 인덱스(0, 1, ...) 또는 영상 파일 경로 (기본값: 0)",
    )
    parser.add_argument(
        "--model",
        default="runs/white_cane_v2/weights/best.pt",
        help="가중치 파일 경로 (기본값: runs/white_cane_v2/weights/best.pt)",
    )
    parser.add_argument("--conf", type=float, default=0.25, help="신뢰도 임계값 (기본값: 0.25)")
    parser.add_argument(
        "--device",
        default="auto",
        help="추론 장치: auto, cuda, cpu (기본값: auto — CUDA 사용 가능 시 자동 선택)",
    )
    return parser.parse_args()


def _resolve_device(device: str) -> str:
    if device != "auto":
        return device
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def _open_capture(source: str) -> cv2.VideoCapture:
    src = int(source) if source.isdigit() else source
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        raise RuntimeError(f"카메라/영상을 열 수 없습니다: {source}")
    return cap


def main() -> None:
    args = _parse_args()
    device = _resolve_device(args.device)
    print(f"[INFO] 추론 장치: {device}")

    detector = WhiteCaneDetector(model_path=args.model, conf=args.conf, device=device)
    cap = _open_capture(args.source)

    print("[INFO] 실시간 탐지 시작 — 'q' 키를 누르면 종료합니다")
    prev_time = time.time()

    while True:
        ret, frame = cap.read()
        if not ret:
            print("[INFO] 영상 종료 또는 카메라 연결 끊김")
            break

        annotated = detector.annotate(frame)

        now = time.time()
        fps = 1.0 / (now - prev_time) if (now - prev_time) > 0 else 0.0
        prev_time = now

        cv2.putText(
            annotated,
            f"FPS: {fps:.1f}",
            (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            (0, 255, 255),
            2,
            cv2.LINE_AA,
        )

        cv2.imshow("VisionGuide — White Cane Detection", annotated)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()
    print("[INFO] 종료")


if __name__ == "__main__":
    main()
