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

    1차로 IoU 매칭을 시도하고, IoU 매칭에 실패한 탐지는 중심점 거리 기반으로
    한 번 더 매칭을 시도한다(2차 폴백) — 지팡이처럼 얇고 작은 물체는 저FPS
    환경에서 프레임 간 이동량이 박스 크기보다 커지기 쉬워 IoU가 쉽게 0으로
    떨어지고, 그러면 매 프레임 새 track_id가 생성되어 추적이 끊긴 것처럼
    보인다. 거리 임계값을 트랙 박스의 대각선 길이에 비례시켜, 물체가 크면
    더 멀리 움직여도 같은 트랙으로 보고 작으면 엄격하게 본다.

    각 트랙은 "static_frames"(연속으로 거의 안 움직인 프레임 수)도 추적한다 —
    지팡이는 사람이 들고 움직이지만, 배경의 케이블/문틀 경계선 같은 고정된
    오탐지 대상은 항상 같은 자리에 그대로 있다. 호출부(camera_live_pi.py 등)가
    이 값을 이용해 "너무 오래 정지된 지팡이 트랙은 배경 오탐지로 간주하고
    ROI 트리거 대상에서 제외"하는 식으로 활용할 수 있다.
    """

    def __init__(self, max_age: int = 10, min_iou: float = 0.3,
                 ema_alpha: float = 0.6, max_center_dist_ratio: float = 1.5,
                 static_move_px: float = 3.0) -> None:
        self.max_age  = max_age
        self.min_iou  = min_iou
        self.alpha    = ema_alpha   # 높을수록 새 탐지에 빠르게 반응
        self.max_center_dist_ratio = max_center_dist_ratio
        self.static_move_px = static_move_px  # 이 픽셀 이하 이동은 "안 움직임"으로 간주
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

    @staticmethod
    def _center(bbox: list) -> tuple[float, float]:
        x1, y1, x2, y2 = bbox
        return (x1 + x2) / 2, (y1 + y2) / 2

    @staticmethod
    def _diag(bbox: list) -> float:
        x1, y1, x2, y2 = bbox
        return ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5

    def _apply_match(self, ti: int, det: dict) -> None:
        a = self.alpha
        old = self._tracks[ti]["bbox"]
        new = det["bbox"]

        old_cx, old_cy = self._center(old)
        new_cx, new_cy = self._center(new)
        moved = ((new_cx - old_cx) ** 2 + (new_cy - old_cy) ** 2) ** 0.5
        if moved > self.static_move_px:
            self._tracks[ti]["static_frames"] = 0
        else:
            self._tracks[ti]["static_frames"] = self._tracks[ti].get("static_frames", 0) + 1

        self._tracks[ti]["bbox"] = [round(a * new[i] + (1 - a) * old[i]) for i in range(4)]
        self._tracks[ti]["age"]  = 0
        self._tracks[ti]["conf"] = det["conf"]

    def update(self, detections: list[dict]) -> list[dict]:
        """탐지 결과를 받아 트랙 목록을 갱신하고 반환."""
        matched_det: set[int] = set()
        matched_trk: set[int] = set()

        # 1차: 탐지-트랙 greedy IoU 매칭
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
                self._apply_match(best_ti, det)

        # 2차: IoU 매칭에 실패한 탐지 → 중심점 거리 기반 폴백 매칭
        for di, det in enumerate(detections):
            if di in matched_det:
                continue
            dcx, dcy = self._center(det["bbox"])
            best_dist, best_ti = None, -1
            for ti, trk in enumerate(self._tracks):
                if ti in matched_trk or trk["class"] != det["class"]:
                    continue
                tcx, tcy = self._center(trk["bbox"])
                dist = ((dcx - tcx) ** 2 + (dcy - tcy) ** 2) ** 0.5
                limit = self._diag(trk["bbox"]) * self.max_center_dist_ratio
                if dist < limit and (best_dist is None or dist < best_dist):
                    best_dist, best_ti = dist, ti
            if best_ti >= 0:
                matched_det.add(di)
                matched_trk.add(best_ti)
                self._apply_match(best_ti, det)

        # 미매칭 탐지 → 신규 트랙 생성
        for di, det in enumerate(detections):
            if di not in matched_det:
                self._tracks.append({
                    "track_id":     self._next_id,
                    "bbox":         det["bbox"][:],
                    "conf":         det["conf"],
                    "class":        det["class"],
                    "label":        det["label"],
                    "age":          0,
                    "static_frames": 0,
                })
                self._next_id += 1

        # 미매칭 트랙 age 증가
        for ti in range(len(self._tracks)):
            if ti not in matched_trk:
                self._tracks[ti]["age"] += 1

        # max_age 초과 트랙 제거
        self._tracks = [t for t in self._tracks if t["age"] <= self.max_age]

        return [dict(t) for t in self._tracks]
