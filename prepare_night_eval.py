"""야간/저조도 합성 평가셋 생성 (로컬 1회성, 회귀 감시용).

현재 지팡이 학습·평가 이미지는 평균밝기 p1=64로 **사실상 전량 주간**이다
(평균밝기<60인 이미지 0.25%). KPI에 "야간 mAP@0.5 >= 0.75"가 있는데 측정할 데이터
자체가 없다.

신규 야간 촬영 없이 감마 보정 + 센서 노이즈로 저조도를 합성한다. 실측 결과 평균
밝기가 123 → 65로 떨어져 **황혼~야간 수준**이 된다(원본 데이터에서 가장 어두운
1퍼센타일이 64였다).

**이 수치는 KPI 달성 근거가 아니라 회귀 감시용이다.** 실제 야간은 조명 스펙트럼
(가로등 나트륨등), 모션블러, 센서 노이즈 특성이 달라 감마 보정으로 재현되지 않는다.
"저조도에서 성능이 무너지지 않는지" 정도만 본다.

사용:
    python prepare_night_eval.py                 # datasets/v2_night/ 생성
    yolo val model=<w> data=datasets/v2_night/data.yaml split=test imgsz=320

Pi 배포 대상이 아니다 (`Makefile`의 `DEPLOY_PY`에 넣지 말 것).
"""
import cv2, numpy as np, os, sys
from pathlib import Path

src = Path('datasets/v2/test')
dst = Path(sys.argv[1] if len(sys.argv) > 1 else 'datasets/v2_night/test')
gamma_lo, gamma_hi, noise_sigma = 0.35, 0.55, 6.0
rng = np.random.default_rng(0)

(dst / 'images').mkdir(parents=True, exist_ok=True)
(dst / 'labels').mkdir(parents=True, exist_ok=True)
n = 0
for f in sorted(os.listdir(src / 'images')):
    img = cv2.imread(str(src / 'images' / f))
    if img is None:
        continue
    g = rng.uniform(gamma_lo, gamma_hi)
    lut = (((np.arange(256) / 255.0) ** (1.0 / g)) * 255).astype(np.uint8)  # 어둡게
    out = cv2.LUT(img, lut).astype(np.float32)
    out += rng.normal(0, noise_sigma, out.shape)          # 센서 노이즈
    cv2.imwrite(str(dst / 'images' / f), np.clip(out, 0, 255).astype(np.uint8),
                [cv2.IMWRITE_JPEG_QUALITY, 92])
    stem = Path(f).stem
    (dst / 'labels' / f'{stem}.txt').write_bytes((src / 'labels' / f'{stem}.txt').read_bytes())
    n += 1
root = dst.parent.resolve()
(root / 'data.yaml').write_text(
    f"# 합성 야간 평가셋 — 감마 {gamma_lo}~{gamma_hi} + 가우시안 노이즈 sigma={noise_sigma}.\n"
    f"# 회귀 감시용이며 KPI 달성 근거로 쓰지 말 것(실제 야간과 광원/노이즈 특성이 다름).\n"
    f"path: {root}\ntrain: test/images\nval: test/images\ntest: test/images\n\n"
    f"nc: 2\nnames: ['white_cane', 'person']\n")
print(f'야간 합성 {n}장 → {dst}')
