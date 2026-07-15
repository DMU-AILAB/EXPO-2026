"""simple_tracker.py — IoU 기반 단순 객체 트래커.

`camera_live_pi.py`(Pi)와 `simulator/app.py`(PC 시뮬레이터)가 동일한 트래킹
로직을 공유하기 위해 분리된 모듈. 표준 라이브러리만 사용한다.
"""

from __future__ import annotations


class SimpleTracker:
    """IoU 기반 단순 객체 트래커.

    매칭된 트랙: EMA 스무딩으로 bbox 떨림 제거, age 초기화
    미탐지 트랙: max_age 프레임 동안 마지막 bbox 유지 (coasting)
    새 탐지:     신규 트랙 생성
    """

    def __init__(self, max_age: int = 10, min_iou: float = 0.3,
                 ema_alpha: float = 0.6) -> None:
        self.max_age  = max_age
        self.min_iou  = min_iou
        self.alpha    = ema_alpha   # 높을수록 새 탐지에 빠르게 반응
        self._tracks: list[dict] = []
        self._next_id = 0

    @staticmethod
    def _iou(a: list, b: list) -> float:
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        ix1 = max(ax1, bx1); iy1 = max(ay1, by1)
        ix2 = min(ax2, bx2); iy2 = min(ay2, by2)
        inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
        if inter == 0:
            return 0.0
        return inter / ((ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter)

    def update(self, detections: list[dict]) -> list[dict]:
        """탐지 결과를 받아 트랙 목록을 갱신하고 반환."""
        matched_det: set[int] = set()
        matched_trk: set[int] = set()

        # 탐지-트랙 greedy IoU 매칭
        for di, det in enumerate(detections):
            best_iou, best_ti = self.min_iou, -1
            for ti, trk in enumerate(self._tracks):
                if ti in matched_trk:
                    continue
                iou = self._iou(det["bbox"], trk["bbox"])
                if iou > best_iou and det["class"] == trk["class"]:
                    best_iou, best_ti = iou, ti
            if best_ti >= 0:
                matched_det.add(di)
                matched_trk.add(best_ti)
                a = self.alpha
                old = self._tracks[best_ti]["bbox"]
                new = det["bbox"]
                self._tracks[best_ti]["bbox"] = [
                    round(a * new[i] + (1 - a) * old[i]) for i in range(4)
                ]
                self._tracks[best_ti]["age"]  = 0
                self._tracks[best_ti]["conf"] = det["conf"]

        # 미매칭 탐지 → 신규 트랙 생성
        for di, det in enumerate(detections):
            if di not in matched_det:
                self._tracks.append({
                    "track_id": self._next_id,
                    "bbox":     det["bbox"][:],
                    "conf":     det["conf"],
                    "class":    det["class"],
                    "label":    det["label"],
                    "age":      0,
                })
                self._next_id += 1

        # 미매칭 트랙 age 증가
        for ti in range(len(self._tracks)):
            if ti not in matched_trk:
                self._tracks[ti]["age"] += 1

        # max_age 초과 트랙 제거
        self._tracks = [t for t in self._tracks if t["age"] <= self.max_age]

        return [dict(t) for t in self._tracks]
