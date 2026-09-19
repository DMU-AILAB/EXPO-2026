"""eval_video_recall.py — 실영상 기준 지팡이 탐지/트리거 성능 벤치마크.

이 시스템이 실제로 최적화해야 하는 것은 "정지 이미지에서 지팡이를 찾는 것"이
아니라 **"영상에서 지팡이를 끊기지 않고 따라가 ROI 트리거를 발동시키는 것"**이다.
그런데 지금까지 자동화된 평가는 `eval_background_fp.py`(정지 이미지 오탐지)와
`yolo val`(정지 이미지 mAP)뿐이었고, 실영상 성능은 1회성 수동 검증으로만 확인됐다
(`docs/model_evaluation_report_v2.md` §9).

이 스크립트는 그 축을 자동화한다. 핵심은 **배포와 완전히 같은 코드 경로로 재는 것**
이다 — `camera_live_pi.py`의 백엔드/게이트 상수/연관 로직을 그대로 import한다.
로직을 복붙하면 배포 코드가 바뀔 때 평가가 조용히 어긋난다.

사용 예:
    # 배포 경로 그대로 (TFLite INT8)
    python eval_video_recall.py --weights-dir runs/white_cane_v6_ft320/weights \
        --video docs/presentation/KakaoTalk_20260729_175616347.mp4 --imgsz 320

    # 전처리 A/B — 종횡비를 뭉개는 현행 방식 vs 레터박스
    python eval_video_recall.py ... --preprocess squash
    python eval_video_recall.py ... --preprocess letterbox

    # ROI 크롭 추론 (고정 카메라 + 기정의 ROI를 이용해 객체 픽셀 밀도를 높인다)
    python eval_video_recall.py ... --preprocess letterbox --roi-crop rois.dev-cam0.json

`eval_background_fp.py`와 같은 이유로 Pi 배포 대상이 아니다(`Makefile`의 `DEPLOY_PY`
에 넣지 말 것) — PC에서 모델을 채택 판정할 때만 쓴다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]   # 저장소 루트 (이 파일은 tools/<분류>/ 아래에 있다)
sys.path.insert(0, str(ROOT))
# 배포 런타임 모듈은 device/ 에 있다 — 복붙 금지 원칙상 그대로 import한다.
sys.path.insert(0, str(ROOT / "device"))

# 배포 코드에서 그대로 가져온다 — 복붙 금지. 게이트 순서·상수·연관 로직은 전부
# `gate_chain.GateChain`에 있고 배포(`camera_live_pi.py`)·재생검증(`replay_engine.py`)이
# 같은 것을 돌린다. 여기서 재현하지 않는다.
from camera_live_pi import build_backend  # noqa: E402
from audio_trigger import StandaloneDispatcher  # noqa: E402
from cane_person_assoc import CANE_CLASS_ID, PERSON_CLASS_ID  # noqa: E402
from gate_chain import GateChain  # noqa: E402
from pedestrian_entity import EntityTracker  # noqa: E402

# 배포의 안내 디스패처를 그대로 태울 때 쓰는 가상 ROI 이름. 평가 영상에는 ROI
# 정의가 없으므로 "게이트를 통과한 주체는 전부 이 구역 안에 있다"고 본다 —
# 쿨다운/최소간격이 실제로 몇 번의 안내를 만드는지 보려는 것이다.
_EVAL_ROI = "__eval__"
from simple_tracker import SimpleTracker  # noqa: E402

DEFAULT_THRESHOLDS = (0.25, 0.40, 0.55)
DEFAULT_DEBOUNCE_SEC = 0.5


# --------------------------------------------------------------------------
# 전처리 — 배포 경로의 결함을 A/B로 비교하기 위해 프레임을 미리 변환한다.
#
# `yolo_postprocess.set_input()`은 letterbox 없이 cv2.resize로 정사각 스쿼시를
# 하는데, 학습은 ultralytics letterbox(비율 보존 + 패딩)다. 16:9 영상이 1:1로
# 눌리면 가로가 1.78배 압축되어, 비스듬히 뻗은 가늘고 긴 지팡이가 학습 분포
# 밖으로 나간다. 어느 쪽이 맞는지를 수치로 보이기 위한 옵션이다.
# --------------------------------------------------------------------------
def _preprocess(frame: np.ndarray, mode: str, size: int) -> np.ndarray:
    if mode == "native":
        return frame
    if mode == "squash":
        return cv2.resize(frame, (size, size))
    if mode == "letterbox":
        h, w = frame.shape[:2]
        scale = size / max(h, w)
        nh, nw = max(1, int(round(h * scale))), max(1, int(round(w * scale)))
        canvas = np.full((size, size, 3), 114, np.uint8)
        top, left = (size - nh) // 2, (size - nw) // 2
        canvas[top:top + nh, left:left + nw] = cv2.resize(frame, (nw, nh))
        return canvas
    raise ValueError(f"알 수 없는 preprocess 모드: {mode}")


def _roi_crop_box(roi_path: Path, w: int, h: int, margin: float = 0.10):
    """trigger 구역들의 합집합 bbox를 여유 margin만큼 확장해 픽셀 좌표로 반환.

    카메라가 고정이고 ROI가 이미 정의돼 있다는 이 프로젝트의 구조를 이용한다 —
    ROI 밖은 어차피 트리거 대상이 아니므로, 그 영역만 잘라 추론하면 같은 입력
    해상도로 객체의 픽셀 밀도를 높일 수 있다(전체 프레임을 640으로 올리는 것과
    비슷한 효과를 320 연산량에 얻는다).

    제외구역(zone_type="exclude")은 크롭 범위 계산에 넣지 않는다 — 트리거가
    일어날 수 없는 영역이라 포함할 이유가 없다.
    """
    data = json.loads(roi_path.read_text(encoding="utf-8"))
    pts: list[list[float]] = []
    for roi in data.get("rois", []):
        if roi.get("zone_type", "trigger") != "trigger":
            continue
        pts.extend(roi.get("points", []))
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    mx, my = (x1 - x0) * margin, (y1 - y0) * margin
    x0, x1 = max(0.0, x0 - mx), min(1.0, x1 + mx)
    y0, y1 = max(0.0, y0 - my), min(1.0, y1 + my)

    px0, py0, px1, py1 = x0 * w, y0 * h, x1 * w, y1 * h
    # 모델 입력이 정사각(320x320)이라, 크롭이 가로로 길면 레터박스 패딩이 캔버스의
    # 절반 이상을 먹어 배율 이득이 사라진다(실측: 2.18:1 크롭은 크롭 안 한 것보다
    # 오히려 나빴다). 짧은 변을 긴 변에 맞춰 정사각으로 넓혀 캔버스를 채운다 —
    # 프레임 경계에 막히면 반대쪽으로 밀어서 최대한 정사각에 가깝게 만든다.
    side = max(px1 - px0, py1 - py0)
    def _expand(a: float, b: float, limit: float) -> tuple[float, float]:
        need = side - (b - a)
        if need <= 0:
            return a, b
        a -= need / 2
        b += need / 2
        if a < 0:
            b = min(limit, b - a)
            a = 0.0
        if b > limit:
            a = max(0.0, a - (b - limit))
            b = limit
        return a, b
    px0, px1 = _expand(px0, px1, float(w))
    py0, py1 = _expand(py0, py1, float(h))
    return int(px0), int(py0), int(px1), int(py1)


# --------------------------------------------------------------------------
# 1단계: 추론 — 가장 낮은 임계값으로 딱 한 번만 돌려 캐시한다.
#
# `eval_background_fp.py`가 쓰는 것과 같은 절약 패턴이다. 다만 트래커는 상태가
# 있어서 임계값마다 다시 돌려야 하므로, "추론 결과"만 캐시하고 트래킹/게이트는
# 임계값별로 재실행한다. 캐시를 남겨두면 게이트 파라미터 튜닝을 추론 없이
# 반복할 수 있다.
# --------------------------------------------------------------------------
def collect_detections(args, min_conf: float) -> tuple[list[list[dict]], tuple[int, int], float]:
    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        raise SystemExit(f"영상을 열 수 없습니다: {args.video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    backend = build_backend(
        conf=min_conf, prefer=args.backend,
        weights_dir=args.weights_dir, input_size=args.imgsz,
    )
    print(f"[INFO] 백엔드: {type(backend).__name__}  전처리: {args.preprocess}  imgsz={args.imgsz}")

    crop = None
    frames: list[list[dict]] = []
    shape = (0, 0)
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        idx += 1
        if args.stride > 1 and idx % args.stride:
            continue

        if args.roi_crop and crop is None:
            h0, w0 = frame.shape[:2]
            crop = _roi_crop_box(Path(args.roi_crop), w0, h0)
            if crop:
                print(f"[INFO] ROI 크롭: x{crop[0]}~{crop[2]}, y{crop[1]}~{crop[3]} "
                      f"(원본 {w0}x{h0})")
            else:
                print("[WARN] trigger 구역이 없어 ROI 크롭을 건너뜁니다")
        view = frame if not crop else frame[crop[1]:crop[3], crop[0]:crop[2]]

        dets = backend.predict(_preprocess(view, args.preprocess, args.imgsz))
        # 좌표계를 원본 프레임 기준으로 되돌린다 — 이후의 트래킹/움직임 게이트가
        # 프레임 대각선 비율을 쓰므로 좌표계가 섞이면 임계값 의미가 달라진다.
        vh, vw = view.shape[:2]
        if args.preprocess in ("squash", "letterbox"):
            dets = _unmap(dets, args.preprocess, args.imgsz, vw, vh)
        if crop:
            for d in dets:
                x1, y1, x2, y2 = d["bbox"]
                d["bbox"] = [x1 + crop[0], y1 + crop[1], x2 + crop[0], y2 + crop[1]]
        frames.append(dets)
        shape = frame.shape[:2]

    cap.release()
    backend.close()
    # stride로 프레임을 건너뛰었다면 실효 프레임레이트도 그만큼 내려간다. 이 값이
    # 디바운스(0.5초)를 몇 프레임으로 환산할지를 정하므로 반드시 보정해야 한다 —
    # Pi 실측은 8~9 FPS라 디바운스가 4~5프레임인데, 60fps 영상 기준 30프레임으로
    # 재면 실제보다 7배 엄격한 조건이 되어 트리거가 과소 집계된다.
    return frames, shape, fps / max(1, args.stride)


def _unmap(dets: list[dict], mode: str, size: int, w: int, h: int) -> list[dict]:
    """전처리된 이미지 좌표 → 원본(view) 좌표.

    백엔드는 자기가 받은 이미지 크기 기준으로 bbox를 돌려주므로, 우리가 미리
    변환해 넣었다면 그만큼 되돌려야 한다.
    """
    out = []
    if mode == "squash":
        sx, sy, px, py = w / size, h / size, 0.0, 0.0
    else:  # letterbox
        scale = size / max(h, w)
        nh, nw = int(round(h * scale)), int(round(w * scale))
        sx = sy = 1.0 / scale
        px, py = (size - nw) / 2.0, (size - nh) / 2.0
    for d in dets:
        x1, y1, x2, y2 = d["bbox"]
        out.append({**d, "bbox": [
            int(round((x1 - px) * sx)), int(round((y1 - py) * sy)),
            int(round((x2 - px) * sx)), int(round((y2 - py) * sy)),
        ]})
    return out


# --------------------------------------------------------------------------
# 2단계: 트래킹 + 3중 게이트 — `camera_live_pi.py`의 트리거 경로를 그대로 재현.
#
# 게이트 순서와 임계값은 배포 코드에서 import한 상수를 쓴다. 각 게이트가 서로
# 다른 오탐지 유형을 막으므로 순서를 바꾸면 의미가 달라진다:
#   정지 억제 → 움직임 게이트 → 사람 동반
# --------------------------------------------------------------------------
def run_gates(frames: list[list[dict]], shape: tuple[int, int],
              conf: float, fps: float, debounce: float, require_person: bool,
              gt: np.ndarray | None = None, use_entity: bool = True,
              entity_virtual_sec: float | None = None,
              on_frame=None, subject_aware: bool = True,
              audio_sec: float = 2.0, tracker_kwargs: dict | None = None) -> dict:
    # 게이트 체인은 배포와 **같은 것**을 쓴다. 트래커 파라미터는 지금껏 한 번도
    # 튜닝된 적이 없어(§10) 스윕할 수 있게 열어 두고, `use_entity=False`가 엔티티
    # 레이어의 A/B 기준선이다 — 같은 추론 결과 위에서 게이트만 바꿔 비교한다.
    chain = GateChain(
        require_person=require_person, use_entity=use_entity,
        tracker=SimpleTracker(**(tracker_kwargs or {})),
        entity_tracker=(EntityTracker(**({"virtual_max_sec": entity_virtual_sec}
                                         if entity_virtual_sec is not None else {}))
                        if use_entity else None),
    )
    latched_ever: set[int] = set()
    virtual_frames = 0

    # `on_frame(state)`는 프레임별 내부 상태를 그대로 넘겨주는 훅이다 —
    # `render_entity_overlay.py`가 게이트 로직을 복붙하지 않고 그리기 위해 쓴다.
    # 로직을 두 벌로 두면 배포 코드가 바뀔 때 그림이 조용히 어긋난다.

    # 배포와 같은 디스패처로 "실제 안내가 몇 번 나가는가"를 센다. `triggers`(아래
    # 연속 통과 구간 수)는 쿨다운을 전혀 모르므로 배포 횟수와 다르다.
    # `subject_aware=False`면 주체를 하나로 묶어 옛 ROI 단위 쿨다운을 재현한다.
    dispatcher = StandaloneDispatcher(debounce, 10.0)
    announcements: list[tuple[float, object]] = []
    release_at: float | None = None

    stage = {"raw": 0, "static": 0, "moved": 0, "person": 0}
    passing = []           # 프레임별 최종 게이트 통과 여부
    raw_hit = []           # 프레임별 raw 탐지 여부 (정답 대비 재현율/오탐지 계산용)
    seen: dict[int, dict] = {}

    for fi, dets in enumerate(frames):
        # 엔티티 레이어의 시간 상수는 초 단위라 프레임 번호를 시각으로 환산해 넘긴다
        # (배포는 time.time()). 이 환산이 있어야 평가에서 고른 값이 실기기로 전이된다.
        now = fi / fps if fps else float(fi)
        dets = [d for d in dets if d["conf"] >= conf]
        hit = any(d["label"] == "white_cane" for d in dets)
        raw_hit.append(hit)
        if hit:
            stage["raw"] += 1

        g = chain.step(dets, shape, now)
        tracks = g.tracks
        for t in tracks:
            rec = seen.setdefault(t["track_id"], {"cls": t["class"], "frames": 0, "disp": 0.0})
            rec["frames"] += 1
            rec["disp"] = max(rec["disp"], t.get("max_disp", 0.0))

        # 단계별 통과 프레임 — GateChain이 돌려준 결과를 세기만 한다(재현 금지).
        cane, virtual = g.cane_tracks, g.virtual
        after_static = [t for t in g.all_cane_tracks
                        if t.get("static_frames", 0) < chain.static_suppress_frames]
        if after_static:
            stage["static"] += 1
        if [t for t in after_static if t.get("max_disp", 0.0) >= g.moved_min]:
            stage["moved"] += 1
        if cane:
            stage["person"] += 1
        latched_ever |= {e.entity_id for e in g.entities if e.is_cane_user}
        # 가상 지팡이 박스도 ROI 판정 대상이다 — 배포 경로에서 실제 박스와 같은
        # 목록에 들어가므로, 여기서도 통과 프레임으로 센다.
        if not cane and virtual:
            virtual_frames += 1
        passing.append(g.passing)

        # --- 안내 디스패처 (배포 경로 그대로) ---------------------------------
        # 주체는 camera_live_pi.py와 같은 규칙으로 정한다: 지팡이를 쥔 엔티티,
        # 없으면 지팡이 트랙 id 폴백, 가상 박스는 그 엔티티.
        if subject_aware and use_entity:
            subjects = {sid for sid, _b in g.roi_targets}
        else:
            # 주체 구분 이전의 동작 재현 — ROI 하나에 주체도 하나뿐이라, 먼저 온
            # 사람의 쿨다운이 뒤에 오는 사람의 안내를 그대로 잡아먹는다.
            subjects = {"__any__"} if passing[-1] else set()

        if release_at is not None and now >= release_at:
            dispatcher.update_last_triggered(_EVAL_ROI, release_at)
            release_at = None
        said = dispatcher.update(_EVAL_ROI, subjects, now)
        if said is not None:
            announcements.append((now, said))
            release_at = now + audio_sec

        if on_frame is not None:
            on_frame({
                "index": fi, "now": now, "tracks": tracks,
                "passed_cane": cane, "virtual": virtual,
                "latched": g.latched_ids, "moved_min": g.moved_min,
                "entities": g.entities,
                "passing": passing[-1],
            })

    # 최장 연속 통과 구간 + 디바운스를 채운 트리거 횟수
    need = max(1, int(round(fps * debounce)))
    longest = run = triggers = 0
    fired = False
    for ok in passing:
        if ok:
            run += 1
            longest = max(longest, run)
            if run >= need and not fired:
                triggers += 1
                fired = True
        else:
            run = 0
            fired = False

    canes = [r for r in seen.values() if r["cls"] == CANE_CLASS_ID]
    persons = [r for r in seen.values() if r["cls"] == PERSON_CLASS_ID]

    # 정답 구간이 있으면 재현율(지팡이 있는 구간)과 오탐지(없는 구간)를 분리한다.
    # 없으면 "전 구간에 지팡이 존재"로 가정하는데, 지팡이가 3분의 1 구간에만 있는
    # 영상에서는 그 가정이 분모를 3배로 부풀려 재현율을 과소평가한다.
    gt_stats = None
    if gt is not None:
        rh = np.asarray(raw_hit, dtype=bool)
        pa = np.asarray(passing, dtype=bool)
        m = gt[:len(rh)]
        present, absent = int(m.sum()), int((~m).sum())
        # 헛트리거: 지팡이가 없는 구간에서만 이뤄진 연속 통과 구간
        false_runs = 0
        i = 0
        while i < len(pa):
            if pa[i]:
                j = i
                while j < len(pa) and pa[j]:
                    j += 1
                if (j - i) >= need and not m[i:j].any():
                    false_runs += 1
                i = j
            else:
                i += 1
        gt_stats = {
            "present": present, "absent": absent,
            "recall_raw": float(rh[m].mean()) if present else 0.0,
            "recall_gated": float(pa[m].mean()) if present else 0.0,
            "fp_raw": float(rh[~m].mean()) if absent else 0.0,
            "fp_gated": float(pa[~m].mean()) if absent else 0.0,
            "false_triggers": false_runs,
        }

    return {
        "gt": gt_stats,
        "entity": use_entity,
        "subject_aware": subject_aware,
        "announcements": len(announcements),
        "latched_entities": len(latched_ever),
        "virtual_frames": virtual_frames,
        "conf": conf, "total": len(frames), "stage": stage,
        "longest": longest, "need": need, "triggers": triggers,
        "cane_tracks": len(canes), "person_tracks": len(persons),
        "cane_max_life": max((r["frames"] for r in canes), default=0),
        "person_max_life": max((r["frames"] for r in persons), default=0),
        "cane_max_disp": max((r["disp"] for r in canes), default=0.0),
    }


def _report(results: list[dict], args) -> None:
    print()
    print(f"영상: {args.video}")
    print(f"모델: {args.weights_dir}  |  전처리: {args.preprocess}"
          f"{'  + ROI크롭' if args.roi_crop else ''}  |  imgsz={args.imgsz}")
    print(f"평가 프레임: {results[0]['total']}"
          f"{f' (stride={args.stride})' if args.stride > 1 else ''}")
    print()
    print("게이트 단계별 통과 프레임 (docs/model_evaluation_report_v2.md §9-1과 같은 형식)")
    print(f"{'conf':>6} | {'지팡이탐지':>10} {'정지억제후':>10} {'움직임후':>9} {'사람동반후':>10}"
          f" | {'최장연속':>8} {'트리거':>6}")
    print("-" * 78)
    for r in results:
        s = r["stage"]
        pct = s["raw"] / r["total"] * 100 if r["total"] else 0
        print(f"{r['conf']:>6.2f} | {s['raw']:>6} ({pct:4.1f}%) {s['static']:>10} "
              f"{s['moved']:>9} {s['person']:>10} | {r['longest']:>8} {r['triggers']:>6}")
    if results[0]["gt"]:
        g0 = results[0]["gt"]
        print()
        print(f"정답 구간 기준 (지팡이 있음 {g0['present']}프레임 / 없음 {g0['absent']}프레임)")
        print(f"{'conf':>6} | {'재현율(raw)':>12} {'재현율(게이트후)':>16}"
              f" | {'오탐율(raw)':>12} {'오탐율(게이트후)':>16} {'헛트리거':>9}")
        print("-" * 82)
        for r in results:
            g = r["gt"]
            print(f"{r['conf']:>6.2f} | {g['recall_raw']*100:>11.1f}% {g['recall_gated']*100:>15.1f}%"
                  f" | {g['fp_raw']*100:>11.1f}% {g['fp_gated']*100:>15.1f}% {g['false_triggers']:>9}")
    else:
        print()
        print("정답 구간 없음 — 위 수치는 '전 구간에 지팡이 존재' 가정이다"
              " (datasets/videos/video_gt.json에 항목을 추가하면 분리 집계된다).")

    print()
    r0 = results[0]
    print()
    print("실제 안내 횟수 (배포 디스패처 그대로 — 쿨다운·최소간격 반영)")
    print(f"  주체 구분: {'켬 (사람 단위)' if r0['subject_aware'] else '끔 (ROI 단위, 옛 동작)'}"
          f"  |  오디오 길이 가정 {args.audio_sec}s")
    print(f"{'conf':>6} | {'안내':>5} {'(참고) 연속통과 구간':>22}")
    print("-" * 38)
    for r in results:
        print(f"{r['conf']:>6.2f} | {r['announcements']:>5} {r['triggers']:>22}")
    print()
    print(f"트랙 통계 (conf={r0['conf']:.2f}) — 디바운스 {args.debounce}s = 연속 {r0['need']}프레임 필요")
    print(f"  지팡이: 트랙 {r0['cane_tracks']}개, 최장 생존 {r0['cane_max_life']}프레임, "
          f"최대 변위 {r0['cane_max_disp']:.0f}px")
    print(f"  사람  : 트랙 {r0['person_tracks']}개, 최장 생존 {r0['person_max_life']}프레임")
    print()
    if results[0]["entity"]:
        print("보행자 엔티티 (pedestrian_entity)")
        print(f"{'conf':>6} | {'래치된 엔티티':>14} {'가상박스 단독통과':>18}")
        print("-" * 44)
        for r in results:
            print(f"{r['conf']:>6.2f} | {r['latched_entities']:>14} "
                  f"{r['virtual_frames']:>18}")
        print("  '가상박스 단독통과' = 실제 지팡이 트랙이 없는데 가상 박스로 통과한"
              " 프레임 수.")
        print("  0이면 엔티티 레이어의 효과 경로가 안 열린 것이다"
              " (SimpleTracker의 max_age=10이 끊김을 이미 다 흡수했다는 뜻).")
    else:
        print("보행자 엔티티: 꺼짐 (--no-entity) — A/B 기준선")
    print()
    print("주의: 이 지표는 영상 표본 수가 적으면 그 영상에 과적합된다. 채택 판정 시"
          " 반드시 표본 수를 병기할 것.")


def main() -> None:
    p = argparse.ArgumentParser(
        description="실영상 기준 지팡이 탐지/트리거 벤치마크 (배포와 같은 코드 경로)")
    p.add_argument("--video", required=True, help="입력 영상 파일")
    p.add_argument("--weights-dir", default="runs/white_cane_v6_ft320/weights",
                   help="가중치 디렉터리 (camera_config.MODEL_VARIANTS의 weights_dir과 같은 형식)")
    p.add_argument("--backend", default="tflite", choices=("auto", "edgetpu", "tflite", "pytorch"),
                   help="추론 백엔드. 기본 tflite — Pi 배포 경로와 같게 재기 위함")
    p.add_argument("--imgsz", type=int, default=320)
    p.add_argument("--preprocess", default="native", choices=("native", "squash", "letterbox"),
                   help="native=백엔드 기본, squash=정사각 스쿼시(현행 Pi 동작), letterbox=비율 보존")
    p.add_argument("--roi-crop", metavar="ROIS_JSON",
                   help="trigger 구역 합집합 bbox로 크롭 후 추론 (고정 카메라 전제)")
    p.add_argument("--thresholds", type=float, nargs="+", default=list(DEFAULT_THRESHOLDS))
    p.add_argument("--debounce", type=float, default=DEFAULT_DEBOUNCE_SEC,
                   help="트리거로 인정할 연속 통과 시간(초). 배포 기본값 0.5")
    p.add_argument("--no-require-person", action="store_true",
                   help="사람 동반 게이트를 끄고 측정 (배포 기본값은 켜짐)")
    p.add_argument("--entity-virtual-sec", type=float, metavar="SEC",
                   help="가상 지팡이 박스 유지 상한(초). "
                        "기본 pedestrian_entity.VIRTUAL_MAX_SEC")
    p.add_argument("--no-subjects", action="store_true",
                   help="안내 디스패처를 주체 구분 이전(ROI 단위 쿨다운)으로 되돌려 측정")
    p.add_argument("--audio-sec", type=float, default=2.0,
                   help="안내 1회의 오디오 길이 가정(초) — 쿨다운 기산점 계산에 쓴다")
    p.add_argument("--no-entity", action="store_true",
                   help="보행자 엔티티 레이어(pedestrian_entity)를 끄고 측정 — A/B 기준선")
    p.add_argument("--stride", type=int, default=1, help="N프레임마다 1장만 평가 (빠른 확인용)")
    p.add_argument("--gt", metavar="JSON", default="datasets/videos/video_gt.json",
                   help="정답 구간 파일. 영상 파일명을 키로 [[시작초, 끝초], ...]를 담는다. "
                        "해당 영상의 항목이 없으면 전 구간에 지팡이가 있다고 가정한다.")
    p.add_argument("--cache", metavar="NPZ", help="추론 결과 캐시 경로 (있으면 재사용, 없으면 생성)")
    args = p.parse_args()

    thresholds = sorted(args.thresholds)
    cache = Path(args.cache) if args.cache else None

    if cache and cache.exists():
        blob = json.loads(cache.read_text(encoding="utf-8"))
        frames, shape, fps = blob["frames"], tuple(blob["shape"]), blob["fps"]
        print(f"[INFO] 캐시 재사용: {cache} ({len(frames)}프레임)")
    else:
        frames, shape, fps = collect_detections(args, thresholds[0])
        if cache:
            cache.write_text(json.dumps(
                {"frames": frames, "shape": list(shape), "fps": fps}), encoding="utf-8")
            print(f"[INFO] 캐시 저장: {cache}")

    gt = _load_gt(args, len(frames), fps)
    results = [run_gates(frames, shape, c, fps, args.debounce,
                         not args.no_require_person, gt=gt,
                         use_entity=not args.no_entity,
                         entity_virtual_sec=args.entity_virtual_sec,
                         subject_aware=not args.no_subjects,
                         audio_sec=args.audio_sec)
               for c in thresholds]
    _report(results, args)


def _load_gt(args, n_frames: int, fps: float) -> np.ndarray | None:
    """정답 구간(초) → 프레임 단위 불리언 마스크."""
    path = Path(args.gt) if args.gt else None
    if not path or not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    spans = data.get(Path(args.video).name)
    if not spans:
        return None
    mask = np.zeros(n_frames, dtype=bool)
    for a, b in spans:
        # stride로 건너뛴 경우 마스크 인덱스도 같은 간격으로 줄어든다.
        mask[int(round(a * fps)):int(round(b * fps)) + 1] = True
    return mask


if __name__ == "__main__":
    main()
