#!/usr/bin/env python3
"""build_pseudo_videos.py — AIHub 연속 촬영 프레임을 의사(pseudo) 영상으로 복원한다.

로컬 전용(Pi 배포 대상 아님) 1회성 도구.

## 왜 필요한가

모델 채택의 1차 기준은 실영상 지표인데(`docs/model_evaluation_report_v3.md` 채택 기준 §1),
**깨끗한 평가 영상이 `test1.mp4` 805프레임 1개뿐**이라 수십 프레임 차이를 신호로 읽을 수
없었다. 그래서 리포트는 `augC 재학습 검증`을 "평가 영상이 늘어난 뒤에"로 미뤄 두었고,
그 상태로 **측정상 더 나았던 증강 설정을 채택하지 못한 채** 배포 중이다.

`datasets/v2/test`의 AIHub 이미지가 **연속 촬영 프레임**이라는 것을 발견했다 —
`20210514_182547_001_saved`, `_002_saved`, … 처럼 세션(초 단위 타임스탬프) 안에서 프레임
인덱스가 이어진다. 결번 없이 49~100프레임짜리 시퀀스가 5개 있고, 그룹 단위 분할
(`resplit_dataset.py`) 덕분에 **세션 전체가 test split 전용**이라 학습 누수가 없다.

**새 촬영 없이 영상 표본이 1개 → 6개가 된다.**

## 이 스크립트의 본체는 "방향 정렬"이다

원본 데이터가 Roboflow export라 **무증강 원본이 한 장도 없고**, 같은 프레임 인덱스에
1~2장이 있다. 그리고 그 둘은 **거의 전부 서로의 좌우 반전본**이다 — 실측으로
세션별 73/73 · 70/70 · 61/61 · 37/37 · 77/79가 반전쌍이었다(같은 인덱스 두 장의
cx가 0.577 / 0.423 = 합 1.0). 원본과 반전본이 인덱스마다 무작위로 섞여 있어, 그냥
이어 붙이면 **영상이 프레임마다 좌우로 뒤집힌다.**

그래서 문제는 "어느 복사본을 고를까"가 아니라 **"세션 전체를 한 방향으로 맞출까"** 다.
복사본은 첫 번째로 고정하고 필요할 때 `cv2.flip`으로 되뒤집는다. 절대 방향은 알 수
없고 알 필요도 없다 — 거울상 영상도 유효한 평가 영상이다(모델이 `fliplr=0.5`로
학습됐다). 판정은 직전 정렬 프레임과의 **직접 vs 거울** 비교이고,
**히스테리시스**(`_FLIP_MARGIN`)로 애매한 전이에서는 직전 방향을 유지한다.

처음에는 프레임 간 차이의 총합을 최소화하는 전역 DP로 복사본을 골랐는데, 카메라가
빠르게 팬하는 구간에서 거울상이 우연히 더 싸져 **세션당 약 10%의 전이가 뒤집혔다**
(픽셀·라벨 두 독립 지표가 같은 값을 가리켰다). 방향 정렬 + 히스테리시스로 바꾼 뒤
실측(낮을수록 매끄러움, `잔여반전` = 아직 뒤집힌 것으로 의심되는 전이 수):

| 세션 | 전역 DP 중앙 / p90 / 잔여반전 | **방향 정렬 중앙 / p90 / 잔여반전** |
|---|---|---|
| 182540 | 0.0396 / 0.0915 / 10 | **0.0384 / 0.0588 / 0** |
| 182547 | 0.0423 / 0.1752 / 12 | **0.0409 / 0.0666 / 0** |
| 182615 | 0.0216 / 0.0382 / 6 | **0.0216 / 0.0344 / 0** |

## fps를 12.76으로 선언하는 이유

`pedestrian_entity`의 시간 상수(`LATCH_SEC` 0.4 / `VIRTUAL_MAX_SEC` 1.0 /
`revive_sec` 2.0)가 **초 단위**라 fps가 게이트 판정을 직접 바꾼다. 실제 촬영 간격은
알 수 없으므로, **Pi 실측 12.76 fps**로 선언해 배포와 같은 시간축에 놓는다.
(`SimpleTracker.max_age`는 프레임 단위라 자동으로 Pi와 같아진다.)

## 한계 — 리포트에 반드시 병기할 것

AIHub 촬영본이라 **실내 도메인 갭을 대표하지 않고**, 선언한 fps가 실제 촬영 간격이
아니며, 쓰는 프레임이 여전히 Roboflow 증강본이다(무증강 원본이 없다). **트랙 지속성의
상대 비교용**이며 절대 성능 근거로 쓰면 안 된다.

## 사용법

    python tools/eval/build_pseudo_videos.py --split test --fps 12.76

    # 생성물: datasets/videos/pseudo_<세션>.mp4 + video_gt.json에 항목 추가
"""

from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path

import cv2
import numpy as np

# `20210514_182547_001_saved_jpg.rf.<hash>.jpg`
#   group(1) = 세션(초 단위 타임스탬프), group(2) = 세션 안의 프레임 인덱스
_FRAME = re.compile(r"^(\d{8}_\d{6})_(\d+)_saved")

CANE_CLASS_ID = 0

_HERE = Path(__file__).resolve().parent
_BASE = _HERE.parent.parent          # tools/eval/ → 저장소 루트

# 썸네일 한 변. 반전을 가르기에 충분하면서, 세션당 수백 장을 메모리에 올려도
# 부담이 없는 크기다.
_THUMB = 64

# "뒤집는 편이 낫다"고 판정하기 위해 필요한 비용 차이(0~1 정규화 L1 평균).
# 이보다 작으면 직전 방향을 유지한다 — 위 `_build_chain` docstring의 히스테리시스.
# 실측에서 진짜 반전은 0.05~0.2대로 벌어지고, 팬으로 인한 애매한 전이는 0.01 안팎이다.
_FLIP_MARGIN = 0.03


def _collect(split_dir: Path) -> dict[str, dict[int, list[Path]]]:
    """이미지 디렉터리를 훑어 {세션: {프레임인덱스: [복사본 경로, ...]}}로 묶는다."""
    sessions: dict[str, dict[int, list[Path]]] = collections.defaultdict(
        lambda: collections.defaultdict(list))
    for f in sorted((split_dir / "images").iterdir()):
        m = _FRAME.match(f.name)
        if m:
            sessions[m.group(1)][int(m.group(2))].append(f)
    return sessions


def _thumbs(paths: list[Path]) -> dict[Path, np.ndarray]:
    out = {}
    for p in paths:
        g = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
        if g is None:
            continue
        out[p] = cv2.resize(g, (_THUMB, _THUMB)).astype(np.float32) / 255.0
    return out


def _build_chain(byidx: dict[int, list[Path]],
                 margin: float = _FLIP_MARGIN) -> list[tuple[Path, bool]]:
    """인덱스마다 (파일, 좌우반전을 되돌릴지)를 정한다.

    **복사본을 고르는 문제가 아니라 방향을 정렬하는 문제다.** 실측으로 확인했듯
    한 인덱스의 복사본 2장은 **거의 항상 서로의 좌우 반전본**이다(세션별 73/73,
    70/70, 61/61, 37/37, 77/79). 즉 어느 쪽을 고르든 정보량은 같고, 문제는 **세션
    전체가 한 방향으로 정렬되어 있는가** 하나뿐이다.

    그래서 복사본은 첫 번째로 고정하고, 직전에 정렬된 프레임과 비교해 그대로 둘지
    뒤집을지만 정한다. 절대 방향(원본이냐 거울상이냐)은 알 수 없고 알 필요도 없다 —
    거울상 영상도 유효한 평가 영상이다(모델이 `fliplr=0.5`로 학습됐다). 중요한 것은
    **프레임 간 일관성**뿐이다.

    **히스테리시스가 핵심이다.** 카메라가 빠르게 팬하는 구간에서는 올바른 방향도
    차이가 커져 거울상이 우연히 더 싸게 나온다 — 앞선 전역 DP가 세션당 약 10%의
    전이를 뒤집었던 원인이다. 두 비용의 차이가 `margin` 미만이면 **판단을 보류하고
    직전 방향을 유지**한다. 진짜 반전은 차이가 크게 벌어지므로 이 편향으로 놓치지 않는다.
    """
    idxs = sorted(byidx)
    picks = [byidx[i][0] for i in idxs]
    th = _thumbs(picks)

    out: list[tuple[Path, bool]] = [(picks[0], False)]
    prev = th.get(picks[0])
    for p in picks[1:]:
        cur = th.get(p)
        if cur is None or prev is None:
            out.append((p, out[-1][1]))
            continue
        direct = float(np.abs(prev - cur).mean())
        mirror = float(np.abs(prev - np.fliplr(cur)).mean())
        flip = (direct - mirror) > margin        # 확실할 때만 뒤집는다
        out.append((p, flip))
        prev = np.fliplr(cur) if flip else cur
    return out


def _smoothness(chain: list[tuple[Path, bool]],
                margin: float = _FLIP_MARGIN) -> tuple[float, float, int]:
    """정렬된 체인의 프레임 간 픽셀 차이 (중앙값, p90)와 **잔여 반전 의심 전이 수**.

    마지막 값이 품질의 핵심 지표다 — 0에 가까워야 한다. 0이 아니면 그 세션의
    영상에는 아직 프레임이 뒤집히는 지점이 남아 있다는 뜻이다.
    """
    th = _thumbs([p for p, _ in chain])
    seq = [np.fliplr(th[p]) if f else th[p] for p, f in chain if p in th]
    d, bad = [], 0
    for a, b in zip(seq, seq[1:]):
        direct = float(np.abs(a - b).mean())
        d.append(direct)
        if float(np.abs(a - np.fliplr(b)).mean()) < direct - margin:
            bad += 1
    if not d:
        return float("nan"), float("nan"), 0
    return float(np.median(d)), float(np.percentile(d, 90)), bad


def _has_cane(label_path: Path) -> bool:
    try:
        text = label_path.read_text(encoding="utf-8")
    except OSError:
        return False
    return any(line.split()[0] == str(CANE_CLASS_ID)
               for line in text.splitlines() if line.strip())


def _spans(flags: list[bool], fps: float) -> list[list[float]]:
    """프레임별 지팡이 유무 → `video_gt.json`의 [[시작초, 끝초], ...] 구간.

    `eval_video_recall._load_gt`가 `mask[round(a*fps) : round(b*fps)+1] = True`로
    되읽으므로, 프레임 인덱스를 그대로 fps로 나눈 값을 쓰면 **양 끝이 포함되어**
    정확히 같은 프레임 집합이 복원된다.
    """
    spans, start = [], None
    for i, f in enumerate(flags):
        if f and start is None:
            start = i
        elif not f and start is not None:
            spans.append([round(start / fps, 4), round((i - 1) / fps, 4)])
            start = None
    if start is not None:
        spans.append([round(start / fps, 4), round((len(flags) - 1) / fps, 4)])
    return spans


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default="datasets/v2",
                    help="원본 데이터셋 루트 (기본 datasets/v2)")
    ap.add_argument("--split", default="test",
                    help="어느 split에서 뽑을지. **test 이외를 쓰면 학습 누수가 된다**")
    ap.add_argument("--fps", type=float, default=12.76,
                    help="선언할 프레임레이트. 기본 12.76 = Pi 실측값")
    ap.add_argument("--min-frames", type=int, default=20,
                    help="이 길이 미만의 세션은 건너뛴다 (트랙이 설 시간이 없다)")
    ap.add_argument("--out", default="datasets/videos",
                    help="mp4 출력 디렉터리")
    ap.add_argument("--gt", default="datasets/videos/video_gt.json",
                    help="정답 구간을 추가할 파일. 기존 항목은 보존한다")
    ap.add_argument("--flip-margin", type=float, default=_FLIP_MARGIN,
                    help="좌우 반전으로 판정할 최소 비용 차이. 작을수록 민감하지만 "
                         "카메라 팬 구간에서 오판이 늘어난다")
    ap.add_argument("--dry-run", action="store_true",
                    help="파일을 만들지 않고 선택 결과·품질만 출력")
    args = ap.parse_args()

    split_dir = _BASE / args.dataset / args.split
    if not (split_dir / "images").is_dir():
        raise SystemExit(f"[ERR] 없는 경로: {split_dir/'images'}")
    if args.split != "test":
        print(f"[WARN] split={args.split} — test가 아니면 학습에 쓰인 이미지라 "
              f"평가 결과가 낙관적으로 오염된다.")

    sessions = _collect(split_dir)
    targets = {s: b for s, b in sessions.items() if len(b) >= args.min_frames}
    if not targets:
        raise SystemExit(f"[ERR] {args.min_frames}프레임 이상인 세션이 없다.")

    out_dir = _BASE / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    label_dir = split_dir / "labels"

    new_gt: dict[str, list[list[float]]] = {}
    print(f"세션 {len(targets)}개 (≥{args.min_frames}프레임), fps={args.fps}\n")
    print(f"{'세션':<18}{'프레임':>6}{'결번':>5}{'지팡이':>7}{'되뒤집음':>8}"
          f"{'중앙':>9}{'p90':>9}{'잔여반전':>9}  출력")

    worst_bad = 0
    for sess in sorted(targets):
        byidx = targets[sess]
        idxs = sorted(byidx)
        gaps = (max(idxs) - min(idxs) + 1) - len(idxs)
        chain = _build_chain(byidx, args.flip_margin)
        med, p90, bad = _smoothness(chain, args.flip_margin)
        worst_bad = max(worst_bad, bad)

        flags = [_has_cane(label_dir / (p.stem + ".txt")) for p, _ in chain]
        name = f"pseudo_{sess.split('_', 1)[1]}.mp4"       # pseudo_182540.mp4
        spans = _spans(flags, args.fps)
        new_gt[name] = spans

        if not args.dry_run:
            first = cv2.imread(str(chain[0][0]))
            h, w = first.shape[:2]
            vw = cv2.VideoWriter(str(out_dir / name),
                                 cv2.VideoWriter_fourcc(*"mp4v"), args.fps, (w, h))
            if not vw.isOpened():
                raise SystemExit(f"[ERR] VideoWriter 열기 실패: {name}")
            for p, flip in chain:
                im = cv2.imread(str(p))
                if im is None:
                    continue
                if im.shape[:2] != (h, w):
                    im = cv2.resize(im, (w, h))
                vw.write(cv2.flip(im, 1) if flip else im)
            vw.release()

        print(f"{sess:<18}{len(idxs):>6}{gaps:>5}{sum(flags):>7}"
              f"{sum(f for _, f in chain):>8}{med:>9.4f}{p90:>9.4f}{bad:>9}  {name}")

    if args.dry_run:
        print("\n[dry-run] 파일을 만들지 않았다.")
        return

    gt_path = _BASE / args.gt
    data = {}
    if gt_path.exists():
        data = json.loads(gt_path.read_text(encoding="utf-8"))
    data.update(new_gt)
    gt_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")

    total = sum(len(targets[s]) for s in targets)
    print(f"\n영상 {len(new_gt)}개 / {total}프레임 → {out_dir}")
    print(f"정답 구간 {gt_path} 갱신 (기존 항목 보존)")
    if worst_bad:
        print(f"\n[WARN] 잔여 반전 의심 전이가 최대 {worst_bad}건 남았다 — "
              f"해당 영상을 눈으로 확인하고, 필요하면 --flip-margin을 조정할 것.")
    else:
        print("\n잔여 반전 의심 전이 0건 — 모든 세션이 한 방향으로 정렬됐다.")


if __name__ == "__main__":
    main()
