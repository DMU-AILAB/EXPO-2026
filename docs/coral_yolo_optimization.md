# Coral Edge TPU × YOLO 최적화 방법론

> 작성 기준: `best_int8_edgetpu.tflite` (EdgeTPU Compiler v16.0), Raspberry Pi 4, Coral USB Accelerator

---

## 1. 현재 상태 진단

### 1-1. Partial Delegation 문제

현재 모델(`best_int8_edgetpu.tflite`)은 **일부 연산만 EdgeTPU에서 실행**된다.
컴파일 로그(`runs/white_cane_v1-2/weights/best_int8_edgetpu.log`) 요약:

| 원인 | 해당 연산 | 영향 |
|------|-----------|------|
| `More than one subgraph` | CONCATENATION, MUL, CONV_2D, RESHAPE, SUB, ADD | EdgeTPU 서브그래프가 분할되어 남은 ops를 CPU에서 재실행 |
| `Unsupported data type` | DEQUANTIZE (float32) | 첫 번째 서브그래프 이후 전체가 CPU로 이탈 |
| `Unspecified limitation` | TRANSPOSE × 3, CONCATENATION × 1 | EdgeTPU 버퍼 제약으로 CPU fallback |

**결과**: EdgeTPU → CPU → (데이터 전송) → Python 사이의 왕복이 발생해  
이론 성능(~10 FPS) 대비 실제 성능이 저하됨.

### 1-2. 현재 Python 3.9 서브프로세스 아키텍처의 병목

```
Pi Camera → [Python 3.13] → stdin pipe → [Python 3.9] → EdgeTPU → stdout pipe → [Python 3.13]
```

프레임당 pipe IPC 비용 + partial delegation CPU 비용이 이중으로 발생한다.

---

## 2. 단기 최적화 (1~2주, 코드 수준)

### 2-1. 입력 해상도 축소

해상도를 낮추면 EdgeTPU 버퍼 사용량이 줄어 더 많은 ops가 단일 서브그래프에 매핑될 가능성이 있다.

```bash
# 재학습 없이 export 해상도만 변경
yolo export model=best.pt format=tflite int8=True imgsz=416
yolo export model=best.pt format=tflite int8=True imgsz=320

# 재컴파일 후 매핑률 비교
edgetpu_compiler -s best_int8_416.tflite
edgetpu_compiler -s best_int8_320.tflite
```

| 해상도 | 예상 FPS 향상 | 예상 mAP 손실 |
|--------|--------------|--------------|
| 640    | 기준         | 기준         |
| 416    | ~1.5×        | ~2~3%        |
| 320    | ~2.5×        | ~5~8%        |

**주의**: `_set_input`, `_postprocess` 모두 `_INPUT_SIZE` 상수 하나로 제어되므로  
`edgetpu_infer.py`와 `camera_live_pi.py`의 `_INPUT_SIZE = 640` 값만 바꾸면 된다.

---

### 2-2. Export 옵션 조정 (`simplify=True`)

YOLOv8 export 시 ONNX simplifier를 거치면 불필요한 RESHAPE, TRANSPOSE를 병합해  
EdgeTPU 매핑 가능 ops가 늘어나는 경우가 있다.

```bash
yolo export model=best.pt format=tflite int8=True simplify=True
```

---

### 2-3. 비동기 파이프라인 (Double Buffering)

현재: `카메라 캡처 → 추론 → 렌더링` 순차 실행  
개선: 추론하는 동안 다음 프레임을 미리 캡처

```python
# camera_live_pi.py main() 개선 방향
import queue, threading

frame_q = queue.Queue(maxsize=2)

def capture_thread(camera, q, stop_event):
    while not stop_event.is_set():
        ok, frame = camera.read()
        if ok:
            if not q.full():
                q.put(frame)

# 메인 루프에서 frame_q.get()으로 프레임 수신
```

추론 시간이 카메라 캡처 시간보다 길 때 유효하다 (EdgeTPU 추론 ~80ms > 캡처 ~33ms).

---

## 3. 중기 최적화 (2~4주, 모델 수준)

### 3-1. Quantization Aware Training (QAT)

현재 방식인 PTQ(Post-Training Quantization)는 학습 후 양자화를 적용해  
정확도 손실이 크다. QAT는 양자화 오차를 학습 과정에 반영해 손실을 최소화한다.

```bash
# Ultralytics QAT (v8.3+부터 실험적 지원)
yolo train model=best.pt data=data.yaml epochs=30 \
    int8=True                    # QAT 모드
    optimizer=AdamW lr0=0.0001   # 미세조정용 낮은 학습률
```

QAT 후 재export:
```bash
yolo export model=best_qat.pt format=tflite int8=True
edgetpu_compiler -s best_qat_int8.tflite  # 매핑률 재확인
```

**기대 효과**: mAP 손실 5% → 2% 이하로 감소.

---

### 3-2. YOLOv8 헤드 구조 최적화

EdgeTPU에서 문제가 되는 ops(`CONCATENATION`, `TRANSPOSE`)는  
대부분 YOLOv8의 Detect 헤드에서 발생한다.  
헤드를 TFLite 외부(Python postprocess)로 완전히 분리하면 backbone+neck만 EdgeTPU에서 실행된다.

```python
# ultralytics export 시 헤드 제거 옵션 (실험적)
from ultralytics import YOLO
model = YOLO("best.pt")
model.export(format="tflite", int8=True, nms=False)  # NMS 비포함 export
```

`nms=False`로 export하면 CONCATENATION/RESHAPE 일부가 제거되어  
단일 서브그래프 매핑 가능성이 높아진다.

---

### 3-3. 모델 경량화 (Pruning + Distillation)

**채널 가지치기 (Channel Pruning)**
```bash
# 학습 중 L1 정규화로 불필요 채널 제거
yolo train model=yolov8n.pt data=data.yaml \
    epochs=100 \
    weight_decay=0.0005
```

**Knowledge Distillation**  
큰 모델(yolov8s/m)을 교사(teacher)로, yolov8n을 학생(student)으로 학습:
```python
# YOLOv8 distillation (Ultralytics 공식 지원)
yolo train model=yolov8n.pt data=data.yaml \
    teacher=yolov8s.pt epochs=100
```

기대 효과: 동일 크기에서 mAP +2~5% 향상.

---

## 4. 장기 최적화 (1개월+, 아키텍처 전환)

### 4-1. EfficientDet-Lite (권장)

Google이 EdgeTPU를 위해 설계한 탐지 모델로,  
YOLOv8과 달리 **ops 전체가 단일 EdgeTPU 서브그래프에 매핑**된다.

```bash
# TFLite Model Maker로 흰 지팡이 데이터 fine-tuning
pip install tflite-model-maker

python - <<'EOF'
import tflite_model_maker as mm
from tflite_model_maker import object_detector

# YOLO 라벨 → Pascal VOC 변환 필요
spec = object_detector.EfficientDetLite0Spec()  # 가장 빠른 변형
train_data = object_detector.DataLoader.from_pascal_voc('datasets/voc/', ...)
model = object_detector.create(train_data, model_spec=spec, epochs=50)
model.export(export_dir='models/', tflite_filename='white_cane_edgetpu.tflite')
EOF
```

| 모델 | mAP (COCO) | EdgeTPU FPS (예상) | 완전 매핑 |
|------|-----------|-------------------|----------|
| YOLOv8n INT8 (현재) | ~37 | ~10 | ✗ (partial) |
| EfficientDet-Lite0 INT8 | ~33 | ~25 | ✓ |
| EfficientDet-Lite1 INT8 | ~37 | ~15 | ✓ |

**주의**: YOLO 라벨(`.txt`) → Pascal VOC(`.xml`) 변환 스크립트가 필요하다.

---

### 4-2. YOLOv8 → YOLO-NAS / PP-YOLOE (EdgeTPU 친화적 변형)

YOLO 계열 중 EdgeTPU 매핑률이 높은 모델들:

- **YOLO-NAS-S**: Neural Architecture Search로 양자화 친화 구조, INT8 손실 최소
- **PP-YOLOE-S**: PaddleDetection, depthwise conv 기반으로 EdgeTPU ops 단순

두 모델 모두 Ultralytics Hub 또는 PaddlePaddle을 통해 학습 후  
TFLite INT8 export → EdgeTPU 컴파일 동일 프로세스로 적용 가능.

---

## 5. 데이터 관점 개선

### 5-1. 야간 / 저조도 데이터 확보

현재 KPI: 야간 mAP ≥ 0.75 (목표값, 미달성 여부 확인 필요)

```bash
# 오프라인 증강으로 야간 데이터 생성
python - <<'EOF'
import cv2, numpy as np, pathlib

for img_path in pathlib.Path('datasets/images').glob('*.jpg'):
    img = cv2.imread(str(img_path))
    # 감마 보정으로 저조도 시뮬레이션
    gamma = np.random.uniform(0.3, 0.6)
    lut = (np.arange(256) / 255.0) ** gamma * 255
    dark = cv2.LUT(img, lut.astype(np.uint8))
    cv2.imwrite(str(img_path.parent / f"dark_{img_path.name}"), dark)
EOF
```

### 5-2. 대표 Calibration 데이터셋

INT8 양자화 품질은 calibration 데이터의 대표성에 크게 좌우된다.  
현재 `yolo export int8=True`는 학습 데이터 일부를 자동 사용하는데,  
실제 Pi Camera 영상(다양한 조도, 각도)을 별도 calibration set으로 쓰면 향상된다.

```bash
# Pi에서 calibration 영상 수집 (100장 이상 권장)
python camera_live_pi.py --save-frames 200 --output datasets/calib/

# export 시 calib 지정
yolo export model=best.pt format=tflite int8=True data=calib.yaml
```

---

## 6. 측정 및 비교 방법

각 최적화 단계마다 아래 지표를 측정해 비교한다.

```bash
# Pi에서 FPS 측정
python - <<'EOF'
import time, numpy as np, tflite_runtime.interpreter as tflite

interp = tflite.Interpreter('best_int8_edgetpu.tflite',
    experimental_delegates=[tflite.load_delegate('libedgetpu.so.1')])
interp.allocate_tensors()
inp = interp.get_input_details()[0]

dummy = np.zeros(inp['shape'], dtype=inp['dtype'])
# 워밍업
for _ in range(10): interp.set_tensor(inp['index'], dummy); interp.invoke()

# 측정
N = 100
t0 = time.perf_counter()
for _ in range(N): interp.set_tensor(inp['index'], dummy); interp.invoke()
print(f"FPS: {N / (time.perf_counter() - t0):.1f}")
EOF
```

| 지표 | 측정 도구 | 목표 |
|------|-----------|------|
| EdgeTPU FPS | 위 스크립트 | ≥ 15 FPS |
| mAP@0.5 | `yolo val` | ≥ 0.85 (주간) |
| EdgeTPU 매핑률 | `edgetpu_compiler -s` 로그 | 전체 ops의 ≥ 90% |
| 모델 크기 | `ls -lh *.tflite` | ≤ 10 MB |
| Pi CPU 온도 | `vcgencmd measure_temp` | ≤ 75°C |

---

## 7. 권장 로드맵

```
현재 (partial delegation, ~10 FPS)
  │
  ├─ [즉시] 해상도 320/416 export 테스트 → FPS +50% 가능
  │
  ├─ [2주] simplify=True + nms=False export → 단일 서브그래프 도전
  │
  ├─ [4주] QAT 재학습 → mAP 손실 최소화
  │
  └─ [2개월] EfficientDet-Lite1 전환 → 완전 매핑, ~15 FPS, mAP 동급
```

가장 빠른 개선: **해상도 416 + `simplify=True` export** → 재학습 없이 1~2일 내 적용 가능.  
가장 큰 개선: **EfficientDet-Lite 전환** → 완전한 EdgeTPU 단일 서브그래프 매핑.
