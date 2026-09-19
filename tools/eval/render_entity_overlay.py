"""render_entity_overlay.py — 게이트 판정을 영상 위에 그려 눈으로 확인한다.

`pedestrian_entity`의 **가상 지팡이 박스가 실제 지팡이 위치와 맞는지는 수치로
확인할 방법이 없다** — 재현율이 올라도 박스가 엉뚱한 곳에 있으면 ROI 판정이 틀린
채로 좋아 보일 수 있다. 그래서 눈으로 보는 경로를 따로 둔다(배포 쪽의
`camera_live_pi.py --debug-gates`와 같은 목적).

게이트 로직은 **한 줄도 복사하지 않는다.** `eval_video_recall.run_gates()`에
`on_frame` 훅을 달아 내부 상태를 그대로 받아 그린다 — 복붙하면 배포 코드가 바뀔 때
그림이 조용히 어긋난다(`eval_video_recall.py` 헤더의 같은 원칙).

사용 예:
    # 주석 영상 + 대조 시트 (엔티티 ON/OFF를 한 프레임에 나란히)
    python tools/eval/render_entity_overlay.py \\
        --video datasets/videos/test1.mp4 --cache <추론캐시>.json \\
        --conf 0.55 --out-dir eval_out/

`eval_video_recall.py`와 같은 이유로 Pi 배포 대상이 아니다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_video_recall import run_gates  # noqa: E402

# BGR. 배포의 `_draw_gate_debug()`와 같은 색 규칙을 쓴다.
C_PERSON = (200, 160, 60)
C_CANE = (0, 220, 0)
C_VIRTUAL = (255, 0, 255)
C_BLOCKED = (0, 140, 255)
C_TRUTH = (255, 255, 255)
C_TEXT = (255, 255, 255)


def _collect(frames, shape, fps, conf, use_entity, virtual_sec):
    """run_gates를 한 번 돌리며 프레임별 상태를 모은다."""
    states: list[dict] = []
    summary = run_gates(frames, shape, conf, fps, 0.5, True,
                        use_entity=use_entity, entity_virtual_sec=virtual_sec,
                        on_frame=states.append)
    return states, summary


def _box(img, bbox, color, label=None, thick=2, scale=1.0):
    h, w = img.shape[:2]
    x1, y1, x2, y2 = (int(max(0, min(v, lim - 1)))
                      for v, lim in zip(bbox, (w, h, w, h)))
    cv2.rectangle(img, (x1, y1), (x2, y2), color, max(1, int(thick * scale)))
    if label:
        fs = 0.5 * scale
        cv2.putText(img, label, (x1, max(int(14 * scale), y1 - int(8 * scale))),
                    cv2.FONT_HERSHEY_SIMPLEX, fs, (0, 0, 0),
                    max(2, int(4 * scale)), cv2.LINE_AA)
        cv2.putText(img, label, (x1, max(int(14 * scale), y1 - int(8 * scale))),
                    cv2.FONT_HERSHEY_SIMPLEX, fs, color,
                    max(1, int(2 * scale)), cv2.LINE_AA)


def _focus_box(st, shape, margin=1.1, truth=None):
    """이번 프레임의 사람·지팡이·가상 박스를 모두 담는 크롭 영역.

    원본 그대로 시트에 넣으면 지팡이와 가상 박스가 몇 픽셀로 줄어 **맞는지 틀리는지
    눈으로 판단할 수 없다** — 이 시각화의 목적 자체가 그 판단이므로 확대가 기본이다.
    """
    boxes = ([t["bbox"] for t in st["tracks"]] + [b for _e, b in st["virtual"]]
             + list(truth or []))
    if not boxes:
        return None
    h, w = shape
    x1 = min(b[0] for b in boxes); y1 = min(b[1] for b in boxes)
    x2 = max(b[2] for b in boxes); y2 = max(b[3] for b in boxes)
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    side = max(x2 - x1, y2 - y1) * (1 + margin)
    # 16:9로 잡아 나란히 붙였을 때 세로가 지나치게 길어지지 않게 한다
    half_w, half_h = side * 0.9, side * 0.62
    return (int(max(0, cx - half_w)), int(max(0, cy - half_h)),
            int(min(w, cx + half_w)), int(min(h, cy + half_h)))


def _draw(frame, st, tag, crop=None, out_w=None, truth=None):
    """한 프레임에 트랙·가상 박스·게이트 결과를 그린다. cv2.putText는 한글을 못
    그리므로(ROI 이름이 화면에서 깨지는 것과 같은 이유) 라벨은 전부 ASCII."""
    img = frame
    if crop is not None:
        img = img[crop[1]:crop[3], crop[0]:crop[2]]
    img = img.copy()
    scale = 1.0
    if out_w and img.shape[1]:
        scale = out_w / img.shape[1]
        img = cv2.resize(img, (out_w, int(img.shape[0] * scale)))

    def _map(b):
        if crop is None:
            return [v * scale for v in b]
        return [(b[0] - crop[0]) * scale, (b[1] - crop[1]) * scale,
                (b[2] - crop[0]) * scale, (b[3] - crop[1]) * scale]

    passed_ids = {t["track_id"] for t in st["passed_cane"]}
    for t in st["tracks"]:
        if t["class"] == 1:
            _box(img, _map(t["bbox"]), C_PERSON, f"P{t['track_id']}", 2, scale)
        else:
            tid = t["track_id"]
            if tid in passed_ids:
                _box(img, _map(t["bbox"]), C_CANE, f"cane{tid} PASS", 3, scale)
            else:
                _box(img, _map(t["bbox"]), C_BLOCKED, f"cane{tid} BLOCKED", 3, scale)

    # 참조 박스 — 같은 프레임을 낮은 conf로 다시 본 raw 탐지. 배포 임계값에서는
    # 놓쳤지만 지팡이가 실제로 거기 있었다는 증거이며, 가상 박스가 맞는 자리에
    # 그려졌는지 눈으로 대조하는 기준이 된다(판정에는 쓰이지 않는다).
    for bbox in (truth or []):
        _box(img, _map(bbox), C_TRUTH, "actual cane (low-conf ref)", 1, scale)

    for eid, bbox in st["virtual"]:
        _box(img, _map(bbox), C_VIRTUAL, f"E{eid} VIRTUAL(estimated)", 3, scale)

    h, w = img.shape[:2]
    bar = max(28, int(34 * scale))
    cv2.rectangle(img, (0, 0), (w, bar), (0, 0, 0), -1)
    verdict = "TRIGGERABLE" if st["passing"] else "no trigger"
    cv2.putText(img, f"{tag} | f{st['index']} | {verdict}",
                (8, int(bar * 0.72)), cv2.FONT_HERSHEY_SIMPLEX,
                0.62 * max(1.0, scale), C_CANE if st["passing"] else C_TEXT,
                2, cv2.LINE_AA)
    return img


def _side_by_side(left, right):
    """엔티티 OFF/ON을 같은 프레임에서 나란히 — 차이가 어디서 나는지 보이게."""
    pad = np.full((left.shape[0], 6, 3), 40, np.uint8)
    return np.hstack([left, pad, right])


def main() -> None:
    p = argparse.ArgumentParser(description="게이트 판정 시각화 (엔티티 ON/OFF 대조)")
    p.add_argument("--video", required=True)
    p.add_argument("--cache", required=True,
                   help="eval_video_recall.py --cache 로 만든 추론 결과 JSON")
    p.add_argument("--conf", type=float, default=0.55)
    p.add_argument("--virtual-sec", type=float, default=None)
    p.add_argument("--out-dir", default="eval_out")
    p.add_argument("--sheet-cols", type=int, default=2)
    p.add_argument("--sheet-rows", type=int, default=3)
    p.add_argument("--no-video", action="store_true", help="주석 영상은 만들지 않는다")
    p.add_argument("--video-width", type=int, default=760,
                   help="주석 영상 한 패널의 가로 폭. 원본 그대로 쓰면 mp4v 코덱 특성상 "
                        "파일이 수백 MB로 불어난다")
    p.add_argument("--video-range", metavar="A:B",
                   help="주석 영상을 이 프레임 구간만 쓴다 (예: 340:790)")
    p.add_argument("--no-zoom", action="store_true",
                   help="시트에서 확대 크롭을 끄고 전체 프레임을 쓴다")
    p.add_argument("--cell-width", type=int, default=760, help="시트 한 칸의 가로 폭")
    p.add_argument("--frames", help="시트에 넣을 프레임 번호를 직접 지정 (쉼표 구분)")
    p.add_argument("--truth-conf", type=float, metavar="C",
                   help="이 임계값으로 다시 본 raw 지팡이 탐지를 참조 박스로 겹쳐 그린다 — "
                        "가상 박스가 맞는 자리인지 대조용 (배포 conf보다 낮게 줄 것)")
    args = p.parse_args()

    blob = json.loads(Path(args.cache).read_text(encoding="utf-8"))
    frames, shape, fps = blob["frames"], tuple(blob["shape"]), blob["fps"]

    off, off_sum = _collect(frames, shape, fps, args.conf, False, None)
    on, on_sum = _collect(frames, shape, fps, args.conf, True, args.virtual_sec)

    # 볼 가치가 있는 프레임 = 엔티티가 판정을 뒤집은 프레임.
    diff = [i for i, (a, b) in enumerate(zip(off, on))
            if a["passing"] != b["passing"]]
    print(f"[INFO] 총 {len(frames)}프레임 중 판정이 달라진 프레임: {len(diff)}")
    print(f"[INFO] 최장 연속 통과  OFF {off_sum['longest']} -> ON {on_sum['longest']}"
          f"  |  트리거 {off_sum['triggers']} -> {on_sum['triggers']}")
    if not diff:
        print("[WARN] 차이가 없다 — conf를 낮추거나 --virtual-sec을 늘려서 다시 볼 것")

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = Path(args.video).stem

    vr = None
    if args.video_range:
        a, b = args.video_range.split(":")
        vr = (int(a), int(b))

    cap = cv2.VideoCapture(args.video)
    writer = None
    picks = []
    if args.frames:
        picks = [int(x) for x in args.frames.split(",") if x.strip()]
    elif diff:
        # 차이 구간에서 고르게 뽑는다 — 앞쪽만 보면 한 장면에 과적합한 인상을 준다.
        want = args.sheet_cols * args.sheet_rows
        step = max(1, len(diff) // want)
        picks = diff[::step][:want]
    picked: dict[int, np.ndarray] = {}

    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok or idx >= len(on):
            break
        if not args.no_video and (vr is None or vr[0] <= idx <= vr[1]):
            vtruth = None
            if args.truth_conf is not None:
                vtruth = [d["bbox"] for d in frames[idx]
                          if d["label"] == "white_cane" and d["conf"] >= args.truth_conf]
            pair = _side_by_side(
                _draw(frame, off[idx], "entity OFF", None, args.video_width, vtruth),
                _draw(frame, on[idx], "entity ON", None, args.video_width, vtruth))
            if writer is None:
                h, w = pair.shape[:2]
                writer = cv2.VideoWriter(str(out / f"{stem}_entity_ab.mp4"),
                                         cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
            writer.write(pair)
        if idx in picks:
            truth = None
            if args.truth_conf is not None:
                truth = [d["bbox"] for d in frames[idx]
                         if d["label"] == "white_cane" and d["conf"] >= args.truth_conf]
            crop = None if args.no_zoom else _focus_box(on[idx], shape, truth=truth)
            cw = args.cell_width // 2
            picked[idx] = _side_by_side(
                _draw(frame, off[idx], "entity OFF", crop, cw, truth),
                _draw(frame, on[idx], "entity ON", crop, cw, truth))
        idx += 1
    cap.release()
    if writer is not None:
        writer.release()
        print(f"[OK] 주석 영상: {out / f'{stem}_entity_ab.mp4'}")

    if picked:
        cell_w = args.cell_width
        rows = []
        items = [picked[i] for i in picks if i in picked]
        for r in range(0, len(items), args.sheet_cols):
            chunk = items[r:r + args.sheet_cols]
            resized = [cv2.resize(c, (cell_w, int(c.shape[0] * cell_w / c.shape[1])))
                       for c in chunk]
            hgt = max(c.shape[0] for c in resized)
            padded = [np.vstack([c, np.full((hgt - c.shape[0], c.shape[1], 3), 30,
                                            np.uint8)]) if c.shape[0] < hgt else c
                      for c in resized]
            while len(padded) < args.sheet_cols:
                padded.append(np.full_like(padded[0], 30))
            rows.append(np.hstack(padded))
        sheet = np.vstack(rows)
        path = out / f"{stem}_entity_sheet.jpg"
        cv2.imwrite(str(path), sheet, [cv2.IMWRITE_JPEG_QUALITY, 88])
        print(f"[OK] 대조 시트: {path}  (프레임 {picks})")


if __name__ == "__main__":
    main()
