#!/usr/bin/env python3
"""autolabel_videos.py — 학습용 영상에서 프레임을 뽑고 **1차 오토 라벨**을 붙인다 (로컬 1회성).

흐름은 **오토 라벨 → 사람이 label_tool로 전수 검수하며 놓친 것을 직접 그림 →
add_source_variant.py로 학습셋 편입**이다. 이 스크립트는 첫 단계만 한다 — 여기서
나온 라벨은 검수 전이라 그대로 학습에 넣으면 안 된다(add_source_variant가 막는다).

## 왜 30fps 전 프레임을 추적하는가

실측(v10 best.pt, 영상당 약 40프레임 샘플): 이 촬영 조건에서 지팡이가 잡히는
프레임은 imgsz 320에서 0~14/41, **960에서 3~25/41**이다. 절반 이상을 teacher가
못 찾는다. 프레임 한 장씩 conf로만 자르면 흐린 지팡이는 전부 버려지는데,
**같은 지팡이를 앞뒤로 확실히 본 트랙**이 있으면 그 사이의 저신뢰 검출도 믿을 수
있다. 2fps 샘플만으로는 프레임 간격(15프레임)이 너무 벌어져 트랙이 성립하지 않으므로
추적은 원본 프레임레이트에서 하고, 저장만 2fps로 한다.

## 무엇을 라벨로 쓰고 무엇을 후보로 남기는가

| 종류 | 조건 | 처리 |
|---|---|---|
| 확정 트랙의 실제 검출 | 트랙 길이·최대 conf·움직임 모두 충족 | **라벨 파일에 씀** |
| 확정 트랙의 짧은 공백 | 앞뒤 검출 간격 ≤ 1초 | 선형 보간 → 후보 `interp` |
| 미확정 트랙의 검출 | 위 조건 미달 | 후보 `low_conf` |
| 거의 안 움직인 트랙 | 최대 변위 < 대각선 2% | 후보 `static` (구조물 의심) |

후보는 `autolabel.json`에만 들어가고, label_tool에서 **사람이 수락해야** 라벨이 된다.
보간 박스를 라벨로 바로 쓰지 않는 이유는 얇고 기울어진 지팡이는 선형 보간 오차가
커서다. 정지 트랙을 라벨로 쓰지 않는 이유는 배포 게이트(`gate_chain`)의 움직임
게이트와 같다 — 현장 구조물(문틀·기둥)이 지팡이로 잡히는 것이 이 시스템의 대표
오탐이고, 그것을 양성으로 학습시키면 정확히 반대 효과가 난다.

라벨 박스는 **트래커의 칼만 보정 박스가 아니라 검출기의 원 박스**다. ultralytics의
`model.track()`은 칼만 상태를 박스로 돌려주는데, 빠르게 휘두르는 얇은 지팡이에서는
그 보정이 실제 위치와 어긋난다. 그래서 `BYTETracker`를 직접 돌리고 반환된 검출
인덱스로 원 박스를 찾는다.

사람(class 1)은 샘플 프레임에서 COCO yolov8l로 붙인다 — `resplit_dataset`의 사람
라벨 보완과 같은 가중치·임계값(스윕으로 정한 값)을 import해 쓴다.

## 파일명과 그룹

`N.mp4` → `<prefix>_0NN`, 프레임은 `<클립>_f<원본 프레임번호 6자리>.jpg`
(`docs/data_collection_plan.md` §3). `resplit_dataset._CLIP_FRAME`이 이 형태를 클립
단위 그룹으로 묶으므로 같은 클립의 프레임이 split을 넘나들 수 없다.

## 사람이 검수한 라벨을 절대 덮어쓰지 않는다

출력 디렉터리에 라벨이 이미 있으면 기본은 중단한다. `--resume`은 없는 프레임만
채우고, `reviewed.json`(label_tool이 쓰는 검수 이력)에 검수 완료로 적힌 프레임은
어떤 옵션으로도 건드리지 않는다.

사용:
    python tools/data/autolabel_videos.py --videos datasets/videos/{1..14}.mp4 \\
        --clip-prefix tr_vid_20260930 --out datasets/sources/vid_20260930 --dry-run

Pi 배포 대상이 아니다(Makefile의 DEPLOY_PY에 넣지 말 것).
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # 저장소 루트 (이 파일은 tools/<분류>/ 아래에 있다)
for _p in (ROOT / "tools" / "data", ROOT / "device"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from dataset_prep import JPEG_QUALITY, MAX_SIDE  # noqa: E402
from gate_chain import MOVED_MIN_DIAG_RATIO      # noqa: E402  배포 움직임 게이트와 같은 기준
from resplit_dataset import RELABEL_CONF, RELABEL_WEIGHTS  # noqa: E402

TEACHER_WEIGHTS = ROOT / "runs" / "white_cane_v10_nolkc" / "weights" / "best.pt"
# 320(배포 해상도)이 아니라 960인 이유: 1080p 원본에서 지팡이가 가늘어 320으로 줄이면
# 몇 픽셀만 남는다. 실측 검출 프레임 320 → 960에서 영상에 따라 1.5~5배.
TEACHER_IMGSZ = 960
# 검출 하한. 이 아래는 트래커에도 넣지 않는다. ByteTrack의 2차 연결 하한
# (track_low_thresh)과 같은 값이라 여기서 더 낮춰도 트랙에는 붙지 않는다.
CANE_CONF_MIN = 0.10

# 확정 트랙 조건 — 초 단위로 두는 이유는 pedestrian_entity와 같다(프레임 단위면
# 영상 프레임레이트에 묶인다).
CONFIRM_MIN_SEC = 0.5      # 이보다 짧게 산 트랙은 순간 오탐과 구분되지 않는다
CONFIRM_MIN_CONF = 0.50    # 한 번이라도 이만큼 확신한 적이 있어야 한다
INTERP_MAX_GAP_SEC = 1.0   # 이보다 긴 공백은 가림/이탈일 수 있어 보간하지 않는다

SAMPLE_FPS = 2.0           # data_collection_plan §5 — 0.5초 간격이면 자세 변화가 보인다
DEDUP_IOU = 0.3            # label_tool.SUGGEST_DEDUP_IOU와 같은 기준

EVAL_PREFIXES = ("test", "pseudo_", "ev_")
GT_PATH = ROOT / "datasets" / "videos" / "video_gt.json"


# ---------------------------------------------------------------------------
# 순수 함수 (모델 불필요 — 테스트 대상)
# ---------------------------------------------------------------------------
def clip_name(prefix: str, idx: int) -> str:
    """`tr_vid_20260930`, 1 → `tr_vid_20260930_001`."""
    return f"{prefix}_{idx:03d}"


def frame_name(clip: str, frame_idx: int) -> str:
    """원본 프레임 번호를 그대로 쓴다 — 영상의 몇 초인지 역산할 수 있게."""
    return f"{clip}_f{frame_idx:06d}.jpg"


def sample_step(fps: float, sample_fps: float = SAMPLE_FPS) -> int:
    return max(1, round(fps / sample_fps))


def sample_indices(n_frames: int, step: int) -> list[int]:
    return list(range(0, n_frames, step))


def is_eval_video(path: Path, gt_keys: set[str]) -> bool:
    """평가 영상이면 True — 학습에 들어가는 순간 지금까지의 판정이 전부 무효가 된다."""
    return path.name in gt_keys or path.name.startswith(EVAL_PREFIXES)


def video_index(path: Path) -> int:
    """`7.mp4` → 7. 숫자 이름만 받는다 — 클립 번호를 파일명에서 결정적으로 정하기 위해."""
    m = re.fullmatch(r"(\d+)\.mp4", path.name)
    if not m:
        raise ValueError(f"숫자 파일명(N.mp4)만 받는다: {path.name}")
    return int(m.group(1))


def xyxy_to_cxcywh_norm(box, w: int, h: int) -> list[float]:
    """픽셀 xyxy → 정규화 cxcywh. 화면 밖으로 넘친 부분은 잘라낸다."""
    x1, y1, x2, y2 = (max(0.0, min(float(v), lim)) for v, lim in zip(box, (w, h, w, h)))
    return [round((x1 + x2) / 2 / w, 6), round((y1 + y2) / 2 / h, 6),
            round((x2 - x1) / w, 6), round((y2 - y1) / h, 6)]


def iou_cxcywh(a, b) -> float:
    ax1, ay1, ax2, ay2 = a[0] - a[2] / 2, a[1] - a[3] / 2, a[0] + a[2] / 2, a[1] + a[3] / 2
    bx1, by1, bx2, by2 = b[0] - b[2] / 2, b[1] - b[3] / 2, b[0] + b[2] / 2, b[1] + b[3] / 2
    iw, ih = min(ax2, bx2) - max(ax1, bx1), min(ay2, by2) - max(ay1, by1)
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    return inter / (a[2] * a[3] + b[2] * b[3] - inter)


@dataclass
class Obs:
    """트랙의 한 프레임 관측 — 검출기의 원 박스(픽셀 xyxy)."""
    frame: int
    box: tuple[float, float, float, float]
    conf: float


@dataclass
class Track:
    tid: int
    obs: list[Obs] = field(default_factory=list)

    def max_disp(self) -> float:
        """첫 관측 중심 대비 최대 변위(px) — simple_tracker.max_disp와 같은 정의.

        누적 경로가 아니라 원점 대비 최대 변위인 이유도 같다: 고정 물체의 지터는
        누적하면 결국 커지지만 원점 대비로는 지터 진폭에 묶인다.
        """
        if not self.obs:
            return 0.0
        b0 = self.obs[0].box
        ox, oy = (b0[0] + b0[2]) / 2, (b0[1] + b0[3]) / 2
        return max(math.hypot((o.box[0] + o.box[2]) / 2 - ox, (o.box[1] + o.box[3]) / 2 - oy)
                   for o in self.obs)


def classify_track(track: Track, fps: float, diag: float) -> str:
    """`confirmed` / `static` / `weak`.

    정지 판정을 먼저 한다 — conf가 아무리 높아도 움직이지 않은 트랙은 라벨이 되지
    않는다(실측: 문짝 모서리를 conf 0.729로 지팡이라고 부른 사례, CLAUDE.md 구조물 마스크 절).
    """
    if track.max_disp() < MOVED_MIN_DIAG_RATIO * diag:
        return "static"
    if (len(track.obs) >= CONFIRM_MIN_SEC * fps
            and max(o.conf for o in track.obs) >= CONFIRM_MIN_CONF):
        return "confirmed"
    return "weak"


def interpolate_gaps(track: Track, targets: list[int], max_gap: int) -> dict[int, tuple]:
    """관측이 없는 target 프레임에 앞뒤 관측을 선형 보간한 박스(픽셀 xyxy).

    앞뒤 관측이 모두 있고 그 간격이 `max_gap` 프레임 이하일 때만 만든다 —
    트랙의 시작 전/끝 뒤로는 외삽하지 않는다.
    """
    seen = {o.frame for o in track.obs}
    frames = [o.frame for o in track.obs]      # 시간순
    out: dict[int, tuple] = {}
    j = 0
    for t in sorted(targets):
        if t in seen or not frames or t < frames[0] or t > frames[-1]:
            continue
        while j + 1 < len(frames) and frames[j + 1] < t:
            j += 1
        a, b = track.obs[j], track.obs[j + 1]
        if b.frame - a.frame > max_gap:
            continue
        r = (t - a.frame) / (b.frame - a.frame)
        out[t] = tuple(pa + (pb - pa) * r for pa, pb in zip(a.box, b.box))
    return out


def plan_frame_labels(tracks: list[Track], untracked: dict[int, list[Obs]],
                      targets: list[int], fps: float, w: int, h: int
                      ) -> tuple[dict[int, list[list[float]]], dict[int, list[dict]]]:
    """샘플 프레임별 지팡이 라벨과 후보를 정한다.

    반환: (labels {frame: [[cx,cy,w,h], ...]}, candidates {frame: [{cls, box, conf, why}]})
    """
    diag = math.hypot(w, h)
    target_set = set(targets)
    max_gap = round(INTERP_MAX_GAP_SEC * fps)
    labels: dict[int, list[list[float]]] = defaultdict(list)
    cands: dict[int, list[dict]] = defaultdict(list)

    def cand(frame, box, conf, why):
        cands[frame].append({"cls": 0, "box": xyxy_to_cxcywh_norm(box, w, h),
                             "conf": round(float(conf), 3), "why": why})

    for tr in tracks:
        kind = classify_track(tr, fps, diag)
        for o in tr.obs:
            if o.frame not in target_set:
                continue
            if kind == "confirmed":
                labels[o.frame].append(xyxy_to_cxcywh_norm(o.box, w, h))
            else:
                cand(o.frame, o.box, o.conf, "static" if kind == "static" else "low_conf")
        if kind == "confirmed":
            for f, box in interpolate_gaps(tr, targets, max_gap).items():
                cand(f, box, 0.0, "interp")
    for f, obs in untracked.items():
        if f in target_set:
            for o in obs:
                cand(f, o.box, o.conf, "low_conf")

    # 같은 지팡이가 라벨과 후보로 겹쳐 나오면 후보를 버린다 — 검수자가 같은 것을 두 번 보지 않게.
    for f in list(cands):
        kept: list[dict] = []
        for c in sorted(cands[f], key=lambda c: -c["conf"]):
            if all(iou_cxcywh(c["box"], l) < DEDUP_IOU for l in labels.get(f, [])) and \
               all(iou_cxcywh(c["box"], k["box"]) < DEDUP_IOU for k in kept):
                kept.append(c)
        cands[f] = kept
    return dict(labels), {f: c for f, c in cands.items() if c}


def load_reviewed(out_dir: Path) -> set[str]:
    """label_tool의 reviewed.json → 검수 완료 파일명 집합. 키 형식은 `train/<파일명>`."""
    try:
        data = json.loads((out_dir / "reviewed.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()
    return {k.split("/", 1)[1] for k, v in data.items() if v and "/" in k}


def label_lines(canes: list[list[float]], people: list) -> list[str]:
    return ([f"0 {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}" for cx, cy, bw, bh in canes]
            + [f"1 {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}" for cx, cy, bw, bh in people])


# ---------------------------------------------------------------------------
# 모델 구동
# ---------------------------------------------------------------------------
def _new_tracker(fps: float):
    from ultralytics.trackers.byte_tracker import BYTETracker
    from ultralytics.utils import YAML, IterableSimpleNamespace
    from ultralytics.utils.checks import check_yaml

    cfg = IterableSimpleNamespace(**YAML.load(check_yaml("bytetrack.yaml")))
    # track_buffer는 프레임 단위라 30fps 기준 1초. 보간 상한(INTERP_MAX_GAP_SEC)과 맞춘다 —
    # 그보다 오래 끊긴 트랙을 이어 붙여도 어차피 보간하지 않는다.
    cfg.track_buffer = max(1, round(INTERP_MAX_GAP_SEC * fps))
    return BYTETracker(cfg)


def track_canes(model, video: Path, targets: set[int], save_frame) -> tuple[list[Track], dict, dict]:
    """영상 전체를 추적한다. 샘플 프레임은 `save_frame(idx, frame)`로 넘긴다."""
    import cv2

    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    tracker = _new_tracker(fps)
    tracks: dict[int, Track] = {}
    untracked: dict[int, list[Obs]] = defaultdict(list)
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx in targets:
            save_frame(idx, frame)
        res = model.predict(frame, imgsz=TEACHER_IMGSZ, conf=CANE_CONF_MIN, classes=[0],
                            verbose=False)[0]
        det = res.boxes.cpu().numpy()
        out = tracker.update(det, frame)
        used = set()
        for row in out:
            tid, di = int(row[4]), int(row[-1])
            if di >= len(det):
                continue
            used.add(di)
            tracks.setdefault(tid, Track(tid)).obs.append(
                Obs(idx, tuple(det.xyxy[di].tolist()), float(det.conf[di])))
        for di in range(len(det)):
            if di not in used:
                untracked[idx].append(Obs(idx, tuple(det.xyxy[di].tolist()), float(det.conf[di])))
        idx += 1
    cap.release()
    return list(tracks.values()), dict(untracked), {"fps": fps, "frames": idx}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--videos", nargs="+", required=True, help="학습용 영상 (N.mp4)")
    ap.add_argument("--clip-prefix", required=True,
                    help="클립명 접두사 — 반드시 tr_<장소>_<YYYYMMDD> (예: tr_vid_20260930)")
    ap.add_argument("--out", required=True, help="출력 루트 (예: datasets/sources/vid_20260930)")
    ap.add_argument("--fps", type=float, default=SAMPLE_FPS, help="저장 프레임레이트 (기본 2)")
    ap.add_argument("--teacher", default=str(TEACHER_WEIGHTS), help="지팡이 teacher 가중치")
    ap.add_argument("--dry-run", action="store_true", help="클립별 예상 장수만 출력")
    ap.add_argument("--resume", action="store_true",
                    help="이미 라벨이 있는 프레임은 건너뛰고 없는 프레임만 채운다")
    args = ap.parse_args()

    import cv2

    if not re.fullmatch(r"tr_[A-Za-z0-9]+_\d{8}", args.clip_prefix):
        raise SystemExit("[ERR] --clip-prefix는 tr_<장소>_<YYYYMMDD> 형식이어야 한다 "
                         "(학습용 접두사 tr_ — 평가용 ev_와 섞이지 않게)")

    gt_keys = set(json.loads(GT_PATH.read_text(encoding="utf-8"))) if GT_PATH.exists() else set()
    bad = [Path(v).name for v in args.videos if is_eval_video(Path(v), gt_keys)]
    if bad:
        raise SystemExit(f"[ERR] 평가 영상은 학습에 넣을 수 없다: {bad}")
    try:
        videos = sorted((Path(v) for v in args.videos), key=video_index)
    except ValueError as e:
        raise SystemExit(f"[ERR] {e}")

    out = (ROOT / args.out) if not Path(args.out).is_absolute() else Path(args.out)
    img_dir, lbl_dir = out / "train" / "images", out / "train" / "labels"
    reviewed = load_reviewed(out)
    existing = {p.stem for p in lbl_dir.glob("*.txt")} if lbl_dir.is_dir() else set()
    if existing and not args.resume and not args.dry_run:
        raise SystemExit(f"[ERR] {lbl_dir}에 라벨 {len(existing)}개가 이미 있다. "
                         "없는 프레임만 채우려면 --resume (검수 완료 프레임은 어떤 경우에도 보존)")

    plan = []
    for v in videos:
        cap = cv2.VideoCapture(str(v))
        n, fps = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), cap.get(cv2.CAP_PROP_FPS) or 30.0
        w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()
        clip = clip_name(args.clip_prefix, video_index(v))
        plan.append((v, clip, n, fps, w, h, sample_indices(n, sample_step(fps, args.fps))))

    total = sum(len(p[6]) for p in plan)
    print(f"{'영상':>8} {'클립':<24} {'프레임':>6} {'fps':>5} {'해상도':>10} {'저장':>5}")
    for v, clip, n, fps, w, h, tg in plan:
        print(f"{v.name:>8} {clip:<24} {n:>6} {fps:>5.1f} {w:>4}x{h:<5} {len(tg):>5}"
              f"  ({len(tg) / total:.0%})")
    print(f"{'합계':>8} {'':<24} {sum(p[2] for p in plan):>6} {'':>5} {'':>10} {total:>5}")
    if args.dry_run:
        print("\n--dry-run: 파일을 만들지 않았습니다.")
        return

    from ultralytics import YOLO
    from prepare_lookalike_dataset import label_people

    img_dir.mkdir(parents=True, exist_ok=True)
    lbl_dir.mkdir(parents=True, exist_ok=True)
    side_path = out / "autolabel.json"
    try:
        sidecar = json.loads(side_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        sidecar = {}
    items: dict = sidecar.get("items", {})
    clips_meta: dict = sidecar.get("clips", {})

    teacher = YOLO(args.teacher)
    report = []
    for v, clip, n, fps, w, h, targets in plan:
        t0 = time.time()
        todo = {f for f in targets
                if frame_name(clip, f) not in reviewed
                and not (args.resume and Path(frame_name(clip, f)).stem in existing)}
        if not todo:
            print(f"[SKIP] {clip}: 채울 프레임 없음")
            continue

        def save_frame(fi, frame, _clip=clip):
            if fi not in todo:
                return
            s = MAX_SIDE / max(frame.shape[:2])
            small = cv2.resize(frame, (round(frame.shape[1] * s), round(frame.shape[0] * s)),
                               interpolation=cv2.INTER_AREA) if s < 1 else frame
            cv2.imwrite(str(img_dir / frame_name(_clip, fi)), small,
                        [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])

        tracks, untracked, meta = track_canes(teacher, v, todo, save_frame)
        labels, cands = plan_frame_labels(tracks, untracked, sorted(todo), meta["fps"], w, h)

        names = [frame_name(clip, f) for f in sorted(todo)]
        people = label_people(names, img_dir, weights=RELABEL_WEIGHTS, conf=RELABEL_CONF)

        why = Counter()
        n_cane = n_person = n_nocane = 0
        for f in sorted(todo):
            name = frame_name(clip, f)
            canes, ppl = labels.get(f, []), people.get(name, [])
            lines = label_lines(canes, ppl)
            (lbl_dir / (Path(name).stem + ".txt")).write_text(
                "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
            items[name] = {"candidates": cands.get(f, []),
                           "auto": {"cane": len(canes), "person": len(ppl)}}
            why.update(c["why"] for c in cands.get(f, []))
            n_cane += len(canes)
            n_person += len(ppl)
            n_nocane += not canes
        clips_meta[clip] = {"source": str(v.relative_to(ROOT) if v.is_absolute() else v),
                            "frames": meta["frames"], "fps": meta["fps"], "size": [w, h],
                            "sample_step": sample_step(fps, args.fps)}
        kinds = Counter(classify_track(t, meta["fps"], math.hypot(w, h)) for t in tracks)
        report.append((clip, len(todo), n_cane, n_person, n_nocane, why, kinds))
        print(f"[DONE] {clip}: {len(todo)}장 · 지팡이 {n_cane} · 사람 {n_person} · "
              f"후보 {dict(why)} · 트랙 {dict(kinds)} · {time.time() - t0:.0f}s")

        # 영상마다 저장한다 — 긴 작업이 중간에 죽어도 --resume으로 이어갈 수 있게.
        sidecar = {"meta": {"teacher": str(Path(args.teacher).resolve().relative_to(ROOT))
                            if Path(args.teacher).resolve().is_relative_to(ROOT) else args.teacher,
                            "teacher_imgsz": TEACHER_IMGSZ, "cane_conf_min": CANE_CONF_MIN,
                            "confirm_min_sec": CONFIRM_MIN_SEC,
                            "confirm_min_conf": CONFIRM_MIN_CONF,
                            "interp_max_gap_sec": INTERP_MAX_GAP_SEC,
                            "moved_min_diag_ratio": MOVED_MIN_DIAG_RATIO,
                            "person_weights": RELABEL_WEIGHTS.name, "person_conf": RELABEL_CONF,
                            "sample_fps": args.fps,
                            "updated": time.strftime("%Y-%m-%d %H:%M:%S")},
                   "clips": clips_meta, "items": items}
        tmp = side_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(sidecar, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(side_path)
        (out / "clips.json").write_text(json.dumps(clips_meta, ensure_ascii=False, indent=1),
                                        encoding="utf-8")

    if report:
        print(f"\n{'클립':<24} {'장수':>5} {'지팡이':>6} {'사람':>6} {'지팡이0장':>9}  후보")
        for clip, k, c, p, z, why, _ in report:
            print(f"{clip:<24} {k:>5} {c:>6} {p:>6} {z / k:>9.0%}  {dict(why)}")
        print(f"\n→ {out}\n다음: label_tool로 전수 검수 (docstring 참고). "
              "검수 전 라벨은 add_source_variant.py가 거부한다.")


if __name__ == "__main__":
    main()
