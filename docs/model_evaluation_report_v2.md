# 배경 네거티브 학습 효과 평가 리포트 (v4 → v5)

**평가 일시:** 2026-08-26
**평가 환경:** NVIDIA GeForce RTX 3060, Ultralytics 8.4.90, PyTorch 2.12.1+cu130 (`expo` conda env)
**평가 대상:** `white_cane_v4_320`(기존) / `white_cane_v5_320`(신규 학습) / `white_cane_v5b_ft320`(파인튜닝)

---

## 1. 배경 — 왜 했나

`extra_data/`에는 흰 지팡이도 사람도 없는 일반 배경 사진이 모여 있다. 이 사진들에 기존
`white_cane_v4_320`를 돌려보니(imgsz=320, conf=0.25) **272장 중 78장(28.7%)에서 오탐지**가 났다.

- `white_cane` 오탐지: 32장 / 37박스 (최고 conf 0.77)
- `person` 오탐지: 55장 / 80박스 (최고 conf 0.83)

즉 이 폴더는 현행 모델이 실제로 틀리는 **하드 네거티브 모음**이다. YOLO는 라벨 파일이 빈
이미지를 배경(negative)으로 학습하므로, 이를 train에 넣어 오탐지(KPI: FPR ≤ 5%)를 줄이는 것이
이번 작업의 목표다.

## 2. 데이터 준비 (`prepare_background_dataset.py`)

| 단계 | 내용 |
|---|---|
| 수동 검수 | 289장 → 272장 (작업자가 사람 찍힌 사진 17장 제거) |
| 자동 스크리닝 | COCO yolov8n으로 272장 전수 스캔 → 22장에서 person 감지. 육안 확인 결과 대부분 COCO 오탐지(마네킹·볼라드·의자·장난감·유모차)였으나 **실제 사람이 있는 4장이 검수에서 누락**됨 |
| 최종 제외 | `263.jpg`(보행자 5~6명), `39.jpeg`, `270.jpg`, `228.HEIC`(인물 등신대) → **268장** |
| 정규화 | HEIC/avif/webp 혼재 + 폰 EXIF 회전 → `exif_transpose` → RGB → 최대변 640 jpg(q92). 1.08GB → 21.9MB |
| 리네임 | `bg_%04d.jpg` — 원본에 `1.jpg`/`1.JPG`처럼 확장자만 다른 같은 stem이 있어 YOLO의 stem 기반 이미지↔라벨 매칭이 충돌한다 |
| 분할 | train 228장 / FP 벤치 홀드아웃 40장. 홀드아웃은 v4 오탐지 여부(`cane_fp`/`person_fp`/`clean`)로 **층화 추출** — 단순 무작위로는 오탐지 이미지가 거의 안 들어가 지표가 둔감해진다 |

학습셋 최종: train 11,116장 중 **231 backgrounds**(신규 228 + 기존 3), 0 corrupt.
val/test는 건드리지 않아 v4 지표와 직접 비교 가능하다.

**사람이 찍힌 배경을 반드시 빼야 하는 이유**: 라벨 없이 배경으로 넣으면 YOLO가 그 사람 영역을
"배경(사람 아님)"으로 학습해 person 재현율이 떨어진다. `merge_person_dataset.py` 병합 때 발생한
라벨 누락 문제와 정확히 같은 종류의 오염이다.

## 3. 학습 조건

| 모델 | 시작점 | 조건 | 소요 |
|---|---|---|---|
| v5_320 | `yolov8n.pt` | v4와 동일 하이퍼파라미터 (100ep, imgsz=320, batch=64, seed=0) | 54분 |
| v5b_ft320 | `white_cane_v4_320/best.pt` | 40ep, lr0=0.002, warmup_epochs=1, 나머지 동일 | 16분 |

## 4. 결과

### 4-1. test split (1,099장 / 3,102 인스턴스, imgsz=320)

| 모델 | mAP50 | mAP50-95 | cane P | **cane R** | cane mAP50 | person P | person R |
|---|---|---|---|---|---|---|---|
| v4_320 (기존) | 0.971 | 0.793 | 0.989 | 0.978 | 0.982 | 0.940 | 0.923 |
| v5_320 (신규) | 0.973 | 0.792 | 0.988 | 0.977 | 0.987 | 0.946 | 0.915 |
| **v5b_ft320 ⭐** | **0.974** | **0.793** | 0.982 | **0.979** | **0.990** | 0.936 | 0.920 |

### 4-2. 배경 홀드아웃 40장 오탐지 (`eval_background_fp.py`)

| 모델 | conf 0.25 이미지/박스 | cane / person | conf 0.50 박스 | 최고 conf |
|---|---|---|---|---|
| v4_320 (기존) | 12/40 · **14박스** | 6 / 8 | 7 | 0.723 |
| v5_320 (신규) | 4/40 · 4박스 | 1 / 3 | 1 | 0.570 |
| **v5b_ft320 ⭐** | 3/40 · **3박스** | 1 / 2 | 2 | 0.559 |

### 4-3. 채택 판정

사전에 정한 기준을 순서대로 적용한다.

1. **cane recall이 v4 대비 1%p 이상 하락하지 않을 것** — 안전 기능이라 재현율 손실이 오탐지보다
   치명적이다. v5 −0.1%p, v5b **+0.1%p** → 둘 다 통과
2. **배경 홀드아웃 FP 박스 수 최소** — v5b 3박스 < v5 4박스 → **v5b 채택**

v5b는 부차 지표(test mAP50 0.974, mAP50-95 0.793, cane mAP50 0.990)에서도 모두 최고이거나
v4와 동등하다. 파인튜닝의 우려였던 배경 과적합에 의한 재현율 저하는 나타나지 않았다.

**요약: 배경 오탐지 박스 14 → 3 (−79%), 지팡이 재현율은 0.978 → 0.979로 유지.**

## 5. INT8 양자화 (Pi 배포용)

`docs/int8_320_quantization_report.md`에서 검증된 calibration 설정(`split=train fraction=0.1`,
변형 A1)을 그대로 적용했다. calibration에 val split을 쓰면 recall이 급락하는 아티팩트가 있다.

```bash
yolo export model=runs/white_cane_v5b_ft320/weights/best.pt \
  format=tflite int8=True imgsz=320 data=datasets/data.yaml split=train fraction=0.1
```

test split(1,099장, imgsz=320) 기준 두 경로를 모두 측정했다.

| 형식 | 파일 | I/O | cane R | mAP50 | mAP 손실 | 배경 FP (conf 0.25) |
|---|---|---|---|---|---|---|
| FP32 (PT) | `weights/best.pt` | float32 | 0.979 | 0.990 | — | 3박스 |
| **INT8 LiteRT (A1) ⭐** | `weights/best_int8.tflite` (3.3MB) | float32 NCHW | **0.960** | **0.971** | **-1.9%** | **3박스** |
| full-integer (onnx2tf) | `weights/best_saved_model/best_full_integer_quant.tflite` (3.2MB) | int8 NHWC | 0.924 | 0.950 | -4.0% | 3박스 |

- 두 경로 모두 mAP 손실이 KPI(≤5%)를 만족하지만, **이번 모델에서는 LiteRT A1 경로가 더 정확했다**
  (cane recall 0.960 vs 0.924). `docs/int8_320_quantization_report.md`의 v3 측정에서는 반대로
  full-integer가 우세했으므로, 모델이 바뀌면 두 경로를 다시 비교해야 한다 — 어느 한쪽이 항상
  낫다고 가정하지 말 것.
- **양자화 후에도 배경 오탐지가 되돌아오지 않았다**(세 형식 모두 3박스) — 이번 작업의 효과가
  int8 배포 경로까지 살아남는다는 뜻이다.
- 출력 형상은 세 형식 모두 `[1,6,2100]`으로 v4와 동일 — `yolo_postprocess.py` 수정 불필요.
- 입력 레이아웃은 LiteRT가 NCHW `[1,3,320,320]` float32, full-integer가 NHWC `[1,320,320,3]` int8이다.
  `yolo_postprocess.set_input`이 `is_nchw`를 자동감지하므로 양쪽 모두 추가 수정 없이 동작한다.

### EdgeTPU 컴파일 (미완 — 후속 작업)

`format=edgetpu` export는 `best_full_integer_quant.tflite`까지 정상 생성했으나, 마지막
`edgetpu_compiler` 호출 단계에서 실패했다. 이 머신에 컴파일러가 없고 ultralytics가 자동으로
`sudo apt`로 설치하려다 비대화형 셸에서 권한을 얻지 못했기 때문이다
(`CalledProcessError: 'sudo mkdir -p /etc/apt/keyrings'`).

```bash
# 컴파일러 설치 후 수동 컴파일 (Coral 공식 저장소)
echo "deb https://packages.cloud.google.com/apt coral-edgetpu-stable main"   | sudo tee /etc/apt/sources.list.d/coral-edgetpu.list
curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg | sudo apt-key add -
sudo apt update && sudo apt install -y edgetpu-compiler

edgetpu_compiler -s runs/white_cane_v5b_ft320/weights/best_saved_model/best_full_integer_quant.tflite
```

산출된 `*_edgetpu.tflite`는 `camera_live_pi.py`가 찾는 경로 규칙(`_model_paths()`)에 맞춰
`runs/white_cane_v5b_ft320/weights/best_int8_edgetpu.tflite`로 배치해야 Coral 백엔드가 잡는다.
컴파일 전까지 `v5b_320` 프로필은 CPU TFLite 경로(`best_int8.tflite`)로 동작한다.

## 6. 반영된 코드 변경

| 파일 | 변경 |
|---|---|
| `prepare_background_dataset.py` | 신규 — 배경 변환·편입·층화 홀드아웃 |
| `eval_background_fp.py` | 신규 — 배경 오탐지 벤치 (PT/TFLite 공통) |
| `camera_config.py` | `MODEL_VARIANTS`에 `v5b_320` 추가 → ROI 에디터 드롭다운에 자동 노출 |
| `Makefile` | `DEPLOY_MODEL_DIRS`에 `runs/white_cane_v5b_ft320/weights` 추가 |
| `.gitignore` | `extra_data/`, `datasets/background/` 제외 (변환본만 `datasets/train/`에 커밋) |

## 7. 한계 / 후속 작업

- **도메인 불일치**: extra_data는 오페라하우스·거실·해변 같은 일반 사진이라 실제 배치 환경
  (지하철 역사, 횡단보도)과 다르다. 일반적 오탐지 억제엔 효과가 확인됐지만, 현장 특유의
  오탐지원(손잡이·점자블록·기둥·우산·청소도구)에는 제한적이다. **가장 가치 높은 네거티브는
  실제 설치 현장을 찍은 배경 사진**이므로, 데모 전 현장 촬영본을 같은 파이프라인
  (`prepare_background_dataset.py`)으로 추가 편입할 것을 권장한다.
  (현장 오탐지의 즉시 대응 수단으로는 `zone_type="exclude"` 감지 제외구역이 이미 있다.)
- **person 재현율 감시**: 배경 268장 중 COCO가 사람으로 오인한 22장 대부분은 마네킹·인쇄물 등
  "사람 비슷한 것"이다. 이를 배경으로 학습시키는 건 의도된 효과지만, 향후 배경을 더 추가할 때마다
  test person recall을 함께 확인해야 한다(현재 0.920, v4 0.923).
- **유사물 구분은 이 작업의 범위 밖이었다** — 후속 작업으로 진행 중.
  extra_data는 일반 배경이라 "없는 것을 있다고 하지 않는" 능력만 가르쳤고, 등산스틱·우산·
  난간처럼 **지팡이와 형태가 유사한 물체를 구분하는 능력**은 다루지 못했다. 실제로 v5b가 남긴
  유일한 지팡이 오탐지(`136.JPG`, conf 0.52)도 사람 발치의 가는 금속 기둥이다. 데이터셋에
  유사물이 "지팡이 아님"으로 라벨링된 사례가 하나도 없는 것이 근본 원인이며,
  `prepare_lookalike_dataset.py`와 런타임 방어선(사람 동반 필수 조건, 오탐지 핫스팟 제안)이
  이를 겨냥한다.
- **Pi 실기기 검증 미완**: 온디바이스 FPS와 실제 현장 오탐지 빈도는 `make sync` 후 확인 필요.
- **EdgeTPU 컴파일 미완**: §5 참고. 컴파일 전까지 `v5b_320`은 CPU TFLite로만 동작한다.
- `pi-heif`는 HEIC 원본을 읽기 위한 `prepare_background_dataset.py` 전용 의존성이다.
  1회성 스크립트라 `requirements.txt`에는 넣지 않고 스크립트 docstring에 설치 명령을 적어두었다.
