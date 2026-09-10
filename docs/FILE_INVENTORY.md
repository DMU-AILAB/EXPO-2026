# 파일 분류표 (FILE_INVENTORY)

이 저장소의 파일이 **어떤 성격인지**만 정리한 문서입니다. 각 파일이 *무엇을 하는지*(기능
설명)는 [`CLAUDE.md`](../CLAUDE.md)에 있으므로 여기서 반복하지 않습니다 — 같은 내용을 두
문서에 적으면 반드시 한쪽이 낡습니다.

## 읽는 법

| 배지 | 의미 |
|---|---|
| 🍓 | **Pi에 배포됨** — `make sync` / `make sync-roi-editor`가 rsync로 전송 |
| 💻 | **PC 전용** — Pi에 올라가지 않음 (개발·학습·데이터 준비용) |
| ✅ | git 추적 중 |
| 🚫 | `.gitignore`로 의도적 제외 |
| ⚠️ | 추적도 무시도 안 됨 / 실제 상태가 문서·설정과 어긋남 → 문서 끝 [알려진 불일치](#알려진-불일치) 참고 |

**권위 있는 출처**: 배포 여부는 `Makefile`의 `DEPLOY_PY` · `DEPLOY_MODEL_DIRS` ·
`sync-roi-editor` 타겟이, 커밋 여부는 `.gitignore`가 정합니다. 이 표와 어긋나면 **그쪽이
맞습니다** — 이 표를 고치세요.

---

## 1. Pi 런타임 코어 🍓✅

`Makefile`의 `DEPLOY_PY` 17개. **Pi에서 실제로 돌아가는 코드는 이것이 전부입니다.**

| 파일 | 계열 |
|---|---|
| `camera_live_pi.py` | 메인 진입점 (`visionguide-device.service`) |
| `detect.py` · `edgetpu_infer.py` · `yolo_postprocess.py` | 추론 백엔드 |
| `simple_tracker.py` · `cane_person_assoc.py` | 추적 / 지팡이–사람 짝짓기 |
| `camera_config.py` | 카메라 프로필 로드·검증 (`roi_editor`와 공유) |
| `audio_trigger.py` · `announcement_router.py` | 안내 트리거 / 출력 라우팅 |
| `kics_protocol.py` · `si4432_radio.py` · `rf_audio_trigger.py` | RF 무선 안내 |
| `gpio_controls.py` · `fan_controller.py` | 물리 버튼·LED·부저 / 냉각팬 |
| `foot_traffic_counter.py` · `detection_events.py` · `fp_hotspots.py` | sqlite 집계·이벤트·오탐지 핫스팟 |

여기에 `rf_config_example.json`도 `make sync`가 함께 전송합니다(RF 설정 템플릿).

> **새 Python 파일을 Pi에서 쓰려면** `Makefile`의 `DEPLOY_PY`에 추가해야 합니다.
> 추가하지 않으면 로컬 테스트는 통과하는데 Pi에서 `ImportError`가 납니다.

## 2. Pi 웹 도구 🍓✅

`make sync-roi-editor`(= `make deploy`에 포함)로 전송됩니다.

- `roi_editor/` — FastAPI ROI/카메라 웹 에디터 (포트 5000, `visionguide-roi-editor.service`)
- `simulator/roi_manager.py` — **예외**: `simulator/` 아래에 있지만 `roi_editor`와
  `camera_live_pi`가 모두 import하므로 이 타겟이 Pi로 함께 보냅니다. 즉 `simulator/`
  디렉터리 전체가 PC 전용인 것은 **아닙니다**.

## 3. PC 전용 실행 💻✅

| 파일 | 용도 |
|---|---|
| `camera_live.py` | PC용 추론 뷰어 (PyTorch). `camera_live_pi.py`와 쌍 — 한쪽에 기능을 넣으면 다른 쪽도 확인 |
| `simulator/app.py` · `detector.py` · `trigger_dispatcher.py` | Streamlit 시뮬레이터 (`simulator/roi_manager.py`만 예외적으로 Pi에도 감 — 위 2절) |
| `simulator/requirements.txt` · `simulator/영상 대본.txt` | 시뮬레이터 의존성 / 시연 대본 |
| `EXPO-Dash-demo/` | React 대시보드 **디자인 참조본**. 실제 운영 UI는 `roi_editor/static/index.html`이며 여기서 디자인 토큰·레이아웃만 가져왔습니다. 빌드해서 배포하는 대상이 아닙니다 |

## 4. 로컬 1회성 데이터 준비 도구 💻✅

**Pi 배포 대상이 아니고, 학습 데이터를 만들 때 한 번씩 돌리는 스크립트**입니다. 운영
경로와 무관하므로 여기 있는 파일을 고쳐도 기기 동작은 바뀌지 않습니다.

| 파일 | 단계 |
|---|---|
| `fetch_lvis_lookalikes.py` · `fetch_openimages_lookalikes.py` | 공개 데이터셋에서 유사물 원본 수집 |
| `lookalike_exclude.txt` | 육안 검수 결과 — 네거티브에서 뺄 파일명 + 근거 |
| `prepare_lookalike_dataset.py` · `prepare_background_dataset.py` | 원본 → 640 jpg 정규화 → `datasets/train/` 편입 |
| `dataset_prep.py` | 위 두 스크립트가 공유하는 정규화·층화 헬퍼 |
| `merge_person_dataset.py` | 사람 데이터셋 병합 |
| `label_tool/` | 누락된 person 라벨 보완용 웹 툴 (+ 동봉 OpenVINO 모델) |
| `seed_dummy_traffic.py` | 대시보드 시연용 더미 통계 주입 |

## 5. 평가 / 벤치 💻✅

- `eval_background_fp.py` — 배경·유사물 홀드아웃에서 conf별 오탐지 집계
- `docs/model_evaluation_report_v1.md` · `v2.md` — 모델 비교 결과
- `docs/int8_320_quantization_report.md` · `docs/coral_yolo_optimization.md` — 양자화·Coral 최적화

## 6. 배포 · 환경 설정 ✅

| 파일 | 비고 |
|---|---|
| `Makefile` | **정식 배포 경로.** 배포 대상 파일 목록의 단일 출처 |
| `deploy/visionguide-{device,roi-editor,controls,fan}.service` | systemd 유닛 (`make install-service`) |
| `deploy/visionguide-uhubctl.sudoers` | USB 오디오 전원 제어용 sudoers |
| `requirements.txt` / `requirements-pi.txt` / `environment.yml` | PC / Pi / conda 의존성 — **의존성 추가 시 PC·Pi 양쪽 갱신** |
| `deploy.ps1` | ⚠️ **낡음 — 쓰지 마세요.** 전송 파일이 3개(`camera_live_pi`/`detect`/`edgetpu_infer`)에서 멈춰 있고 모델도 v1-2를 가리킵니다. 실행하면 불완전 배포가 됩니다. Windows에서는 Git Bash + `make`를 쓰세요 |

## 7. 설정 파일 — 예시 vs 실물

**원칙: 예시만 커밋하고, 실물은 Pi 로컬에서 `roi_editor` 웹 UI가 만듭니다.** 기기마다
다른 런타임 설정이라 커밋도 rsync 배포도 하지 않습니다. 새 Pi는 파일이 없으면 CLI 인자
기반 단일 카메라 모드로 동작하므로 마이그레이션이 필요 없습니다.

| 예시 (✅ 커밋) | 실물 (🚫 무시) |
|---|---|
| `rois_example.json` | `rois.json`, `rois.*.json` (예: `rois.dev-cam0.json`) |
| `camera_config_example.json` | `camera_config.json` |
| `rf_config_example.json` (🍓 Pi에도 전송) | `rf_config.json` |

## 8. 로컬 런타임 산출물 🚫

실행하면 생기고, 지워도 다시 생깁니다. 커밋 대상이 아닙니다.

`foot_traffic.db`(+`-wal`/`-shm`) · `recordings/` · `label_tool/reviewed.json` ·
`__pycache__/` · `.pytest_cache/` · `runs/detect/`(YOLO val 산출물) ·
`calibration_image_sample_data_*.npy`(onnx2tf export 부산물)

## 9. 대용량 데이터 · 모델

### `datasets/` (2.7G)

모든 데이터는 `datasets/` 안에 모여 있습니다. 최상위에 있던 `extra_data/`와
`lookalike_data/`는 각각 `datasets/raw/background/`, `datasets/raw/lookalike/`로
옮겼습니다(경로를 참조하던 `prepare_*_dataset.py`·`fetch_*_lookalikes.py`의 기본값도
함께 갱신됨 — `--src`/`--dst`로 여전히 덮어쓸 수 있습니다).

3단계 파이프라인이고, **단계마다 같은 이미지의 다른 버전이 남습니다.**

```
datasets/raw/background/  (966M, 272장)   ┐ 수집 원본
datasets/raw/lookalike/   (123M, 684장)   ┘ HEIC/webp 혼재, 크기 제각각
        │  prepare_background_dataset.py / prepare_lookalike_dataset.py
        │  (EXIF회전 → RGB → 최대변 640 → jpg 재인코딩 → bg_/lk_ 리네임)
        ▼
datasets/background/  (22M, 268장)  bg_XXXX.jpg + holdout.txt(40) + manifest.json
datasets/lookalike/   (85M, 683장)  lk_XXXX.jpg + holdout.txt(136)/holdout_solo.txt(18)
        │  홀드아웃을 뺀 나머지를 그대로 복사
        ▼
datasets/train/images/  ← bg_* 228장 + lk_* 544장
```

원본과 스테이징은 **재인코딩되어 바이트가 다르므로** 중복 파일로 잡히지 않습니다.
스테이징과 `train/`은 **바이트 동일 복사본**입니다.

| 커밋됨 ✅ | 무시됨 🚫 |
|---|---|
| `datasets/{train,val,test}/{images,labels}` — 실제 학습에 쓰이는 세트 | `datasets/raw/background/` — 배경 원본 사진 (966M, HEIC/avif 혼재) |
| `datasets/{images,labels}` — 지팡이 전용 원본 풀 (스플릿 전) | `datasets/raw/lookalike/` — 유사물 수집 원본 (123M) |
| `datasets/data.yaml` | `datasets/background/`, `datasets/lookalike/` — 변환 스테이징 |
| | `datasets/data_local.yaml` — 로컬 절대경로가 박힌 임시 파일 |

무시되는 쪽은 대체로 **준비 스크립트를 재실행하면 동일하게 재생성**됩니다(seed 고정).
`lk_*.jpg`도 무시되는 쪽입니다 → 재생성 절차는 [알려진 불일치](#알려진-불일치).
**예외: `datasets/raw/background/`는 직접 촬영본이라 재생성이 불가능한 유일본입니다.**

#### 알려진 중복 (총 394MB)

내용이 완전히 같은 파일이 여러 곳에 있습니다. 전부 **의도된 파이프라인의 부산물**이라
지금 지워야 할 것은 없지만, 용량을 회수해야 할 때 참고하세요.

| 중복 구간 | 장수 | 용량 | 성격 |
|---|---|---|---|
| `datasets/images` ↔ `train,val,test/images` | 9,308 | 281M | 스플릿 전 지팡이 원본 풀. **풀 전체가 스플릿에 포함**돼 있어 순수 잉여 |
| `lookalike/images` ↔ `train/images` | 544 | 53M | 스테이징 → 학습 세트 복사본 |
| `Pedestrian Detection CCTV yolov8/` ↔ 스플릿 | 3,808 | 37M | Roboflow 원본 vs 병합 결과 |
| `background/images` ↔ `train/images` | 228 | 18M | 스테이징 → 학습 세트 복사본 |
| `raw/lookalike` 내부 | 5 | 1M | 같은 사진이 두 카테고리에 수집됨(빗자루/삽/대걸레는 LVIS에서 겹침) |

`datasets/images`·`datasets/labels`는 스플릿을 다시 나눌 때의 입력이므로 남겨둡니다 —
지우면 `train/val/test` 비율을 바꿀 수 없게 됩니다.

### `runs/` (355M)

| 커밋됨 ✅ | 무시됨 🚫 |
|---|---|
| 학습 지표 (`results.csv`, `*_curve.png`, `confusion_matrix*.png`, `args.yaml`) | `runs/detect/` — val/predict 실행 산출물 |
| `weights/best.pt` · `last.pt` — 재학습·재export의 기준 | `weights/best_saved_model/` — onnx2tf 중간 산출물 |
| `weights/best_int8.tflite` · `best_int8_edgetpu.tflite` — **Pi가 실제로 쓰는 것**(🍓) | `weights/*.onnx` — export 중간물 |

Pi에 배포되는 모델은 `DEPLOY_MODEL_DIRS`의 5개 디렉터리에서 `best_int8.tflite`와
`best_int8_edgetpu.tflite` **두 파일만**입니다(`best.pt`는 Pi에 torch를 설치하지 않으므로
배포 대상이 아님).

> **새 모델을 추가하려면 두 곳을 함께 고쳐야 합니다** — `camera_config.py`의
> `MODEL_VARIANTS`(UI 드롭다운·런타임 로더)와 `Makefile`의 `DEPLOY_MODEL_DIRS`(전송).
> 한쪽만 고치면 UI에는 보이는데 Pi에 파일이 없는 상태가 됩니다.

### 루트의 사전학습 가중치

| 파일 | 상태 |
|---|---|
| `yolov8n.pt` ✅ | **사용 중** — `prepare_lookalike_dataset.py` 등이 person 자동 라벨링에 씀 |
| `yolo26n.pt` ✅ | 코드·문서 어디서도 참조 없음. 삭제하지 않고 보존 |
| `train.log` ✅ | v1 학습 로그 1.5MB. 결과는 `docs/model_evaluation_report_v*.md`에 정리됨. 보존 |

### `docs/`

- ✅ 설계·운영 문서 (`WIRING.md`, `pi-deployment-guide.md`, `si4432-kics-integration.md`,
  `multi-roi-collision-ideas.md`, 기능명세서, 참고 논문 PDF, 팀원 개인 제출 PDF)
- 🚫 `docs/presentation/` (199MB) — 발표·전시 자료. 동영상만 110MB이고 단일 파일 최대
  66MB라 저장소에 넣으면 clone이 무거워집니다. **로컬/외부 공유로 보관**하며, git에는
  올라가지 않습니다.

---

## 알려진 불일치

1. **유사물 네거티브 `datasets/train/images/lk_*.jpg` 544장은 커밋하지 않습니다**(`.gitignore`).
   bg_*.jpg 228장은 커밋돼 있어 전례와 다른데, 이쪽을 뺀 이유는 원본이 공개 데이터셋이라
   **스크립트로 재생성이 가능**하기 때문입니다(55MB + 라벨 544개).
   → clone한 환경에서 그대로 학습하면 유사물 네거티브 없이 학습되어 **v6 재현이 안 됩니다.**
   재현하려면 순서대로 실행하세요:

   ```bash
   python fetch_lvis_lookalikes.py          # LVIS 599장 (coco_url로 개별 다운로드)
   python fetch_openimages_lookalikes.py    # Open Images Crutch 85장
   python prepare_lookalike_dataset.py --exclude-file lookalike_exclude.txt
   ```

   `datasets/raw/lookalike/`(수집 원본)와 `datasets/lookalike/`(변환 스테이징)도 미커밋이지만,
   위 세 스크립트가 seed 고정이라 동일하게 재생성됩니다.
