# 320 해상도 재학습 + INT8 양자화 리포트 (white_cane_v3_320)

**작성일:** 2026-07-21
**목적:** 프레임 드랍 완화를 위해 입력 해상도를 640→320으로 낮추고, 그로 인한 흰 지팡이(white_cane)
탐지 정확도 손실이 허용 범위인지 정량 검증. 최종적으로 Coral EdgeTPU 배포용 full-integer INT8 모델까지 생성.

---

## 1. 배경

- 기존 배포 모델 `runs/white_cane_v2`(2클래스 `white_cane`/`person`, 640)는 Pi CPU TFLite INT8에서
  프레임 드랍이 관찰됨. YOLOv8 연산량은 대략 입력 해상도 제곱에 비례하므로 640→320이면 이론상 ~4배 감소.
- 리스크: 흰 지팡이는 얇고 긴 물체라 다운샘플링 + INT8 양자화에서 recall이 크게 떨어질 수 있음
  (CLAUDE.md 양자화 손실 기준: mAP 손실 ≤ 5%).

## 2. 학습 설정

`white_cane_v2`와 동일 하이퍼파라미터, `imgsz`만 변경.

```bash
yolo train data=datasets/data.yaml model=yolov8n.pt \
  epochs=100 batch=32 patience=20 seed=0 imgsz=320 \
  project=runs name=white_cane_v3_320
```

- 데이터셋: `datasets/data.yaml` (train 10,888 / val 1,263 / test 1,099, 2클래스).
- 100 epoch 정상 종료.

## 3. FP32 정확도 (양자화 전, `yolo val` split=val)

| 모델 | imgsz | white_cane Recall | Precision | mAP50 | mAP50-95 |
|---|---|---|---|---|---|
| white_cane_v2 (기존 배포) | 640 | 0.981 | 0.988 | 0.993 | 0.757 |
| **white_cane_v3_320** | 320 | **0.975** | 0.988 | 0.987 | 0.735 |

→ **해상도를 절반으로 줄여도 FP32 recall 손실은 -0.6%p에 불과.** 320 재학습 자체는 안전.

## 4. INT8 양자화 — calibration 데이터에 따른 손실 (핵심 발견)

INT8 export 시 calibration 데이터셋에 따라 결과가 크게 달라진다. 각 변형을 `yolo val`(split=val, imgsz=320)로 측정.

| 변형 | 백엔드 / calibration | 입출력 dtype | white_cane Recall | mAP50 | EdgeTPU 호환 |
|---|---|---|---|---|---|
| FP32 기준 | — | float32 | 0.975 | 0.987 | — |
| A0 | LiteRT / val 전체(1263) | float32 I/O | 0.898 | 0.971 | ✗ |
| **A1 ⭐** | LiteRT / **train(~1088)** | float32 I/O | **0.966** | 0.973 | ✗ |
| A2 | LiteRT / 지팡이전용(~1116) | float32 I/O | 0.894 | 0.974 | ✗ |
| **full-integer ⭐** | **onnx2tf / train(~1088)** | **int8 I/O** | **0.971** | 0.982 | ✓ |

핵심:
- A0의 recall 급락(0.898)은 **양자화가 탐지 능력을 망친 게 아니라 operating point가 이동한 아티팩트**다.
  세 LiteRT 변형의 **mAP50은 0.971~0.974로 사실상 동일** — calibration 데이터가 confidence 스케일만 바꿔
  기본 threshold의 recall/precision 균형점을 이동시켰을 뿐이다.
- calibration에 **train split(다양한 조도/각도)**을 쓰면 고recall 지점으로 이동(A1: 0.966).
- **onnx2tf full-integer 경로**는 입출력까지 int8로 만들면서 정확도도 가장 우수(recall 0.971, mAP50 0.982,
  FP32 대비 -0.4%). LiteRT 경로는 입출력을 float32로 남겨 EdgeTPU 완전 매핑이 불가하다.

## 5. 실제 사용할 파일 (⭐ 중요)

`runs/white_cane_v3_320/weights/` 아래에 export 중간 산출물이 여러 개 생성되지만, **실제로 쓰는 파일은 다음뿐이다.**

| 용도 | 파일 | 설명 |
|---|---|---|
| **재학습/재export 원본** | `weights/best.pt` | 320 FP32 PyTorch 원본. 모든 export의 출발점. |
| **CPU TFLite 배포 (현행 방식)** | `weights/best_int8.tflite` | LiteRT INT8(A1, recall 0.966). 현재 `camera_live_pi.py` CPU 폴백 경로에 그대로 사용. |
| **Coral EdgeTPU 배포 (목표)** | `weights/best_saved_model/best_full_integer_quant.tflite` | int8 I/O full-integer(recall 0.971). `edgetpu_compiler`로 컴파일해 `*_edgetpu.tflite` 생성 예정. |

> 그 외 `best.onnx`, `best_saved_model/{best_float32,best_float16,best_int8,best_integer_quant}.tflite`,
> `saved_model.pb` 등은 onnx2tf 변환 과정의 **중간 산출물이라 배포에 쓰지 않는다**(참고용으로 남겨둠).

## 6. 배포 시 필수 변경 (아직 미적용 — 후속 작업)

이 모델을 실제 Pi에 올리려면 입력 해상도 상수를 320으로 맞춰야 한다.

- `yolo_postprocess.py`의 `INPUT_SIZE = 640` → `320` (CPU TFLite 백엔드 + EdgeTPU 워커가 공유하는 상수).
- `camera_live_pi.py`/`edgetpu_infer.py` 출력 형상 확인: 출력이 `[1,6,8400]`(640) → `[1,6,2100]`(320)으로 바뀐다.
- onnx2tf full-integer 모델은 입력 레이아웃이 **NHWC**(`[1,320,320,3]`)다. `yolo_postprocess.py:set_input`이
  `is_nchw` 자동감지로 처리하므로 추가 수정은 불필요.
- `Makefile`의 `DEPLOY_MODEL` 경로를 새 모델로 교체 후 `make sync`.

## 7. 남은 작업

- [ ] `edgetpu_compiler` 설치 후 `best_full_integer_quant.tflite` → EdgeTPU 컴파일, 매핑률(부분 위임 여부) 확인.
      (참고: `white_cane_v1-2`의 EdgeTPU 컴파일 로그는 CONCATENATION/TRANSPOSE 일부 미매핑 = partial delegation.)
- [ ] Pi + Coral 실기기에서 온디바이스 FPS 측정(측정 스크립트: `docs/coral_yolo_optimization.md` §6).
- [ ] 위 §6 배포 변경 적용 및 실기 프레임 드랍 개선 확인.

## 8. export 툴체인 참고

full-integer(int8 I/O) 경로는 `expo` conda env에 `tensorflow`/`onnx2tf`/`tf_keras`/`onnx_graphsurgeon`를
추가 설치해야 동작한다(`format=edgetpu`가 내부적으로 onnx2tf 경유). 기존 `format=tflite int8=True`(LiteRT)는
float32 I/O만 만든다. `requirements.txt`/`environment.yml`에는 아직 미반영.

```bash
# full-integer(int8 I/O) 생성 — best_saved_model/best_full_integer_quant.tflite 산출
yolo export model=runs/white_cane_v3_320/weights/best.pt \
  format=edgetpu imgsz=320 int8=True data=datasets/data.yaml split=train fraction=0.1
```
