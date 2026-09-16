# 파일 인벤토리 — 저장소 구조도

**갱신일:** 2026-09-16 (전수 분석 기준)
**대상 커밋:** `feat/motion-gate-and-lvis-lookalikes` (origin/main 병합 후)

이 문서는 저장소의 **모든 디렉터리와 루트 파일**이 어떤 역할인지, 그리고
**Pi 배포 대상인지 / 커밋 대상인지**를 한눈에 보여준다. 기능 설명은 `CLAUDE.md`에
있고, 이 문서는 "어디에 무엇이 있고 왜 거기 있는가"를 다룬다.

## 규모 요약

| | 파일 수 | 용량 | 비중 |
|---|---:|---:|---:|
| `datasets/` | 45,574 | 618.3 MB | 59% |
| `runs/` | 536 | 372.2 MB | 36% |
| `visionguide-frontend/` | 34 | 31.7 MB | 3% |
| 루트 파일 | 49 | 13.4 MB | 1% |
| 그 외 전부 | 105 | 10.2 MB | 1% |
| **합계** | **46,298** | **1,045.8 MB** | |

**추적 파일의 95%가 데이터셋과 학습 산출물이다.** 코드는 전체의 1% 남짓이다.

---

## 1. 전체 구조

```
expo/
├── [루트 34개 .py]        ← 디바이스 런타임과 PC 도구가 섞여 있다 (§2)
│
├── roi_editor/            Pi 로컬 웹 UI (포트 5000) — 실제 운영 대시보드
├── simulator/             PC Streamlit 시뮬레이터
├── label_tool/            라벨 보완 툴 (로컬 전용)
│
├── configs/               학습 설정 yaml (yolo train cfg=)
├── deploy/                systemd 유닛 · sudoers · AP 전환 스크립트
├── tests/                 pytest 20개 파일
│
├── datasets/              학습 데이터 (§4) ★저장소의 59%
├── runs/                  학습 산출물 (§5) ★저장소의 36%
│
├── docs/                  설계·평가 문서 + PDF
│
├── visionguide-frontend/  React 관리자 대시보드 — 미구현·미배포 (§6)
├── dash/                  Stitch 디자인 목업 (html+png)
└── EXPO-Dash-demo/        디자인 토큰 출처 (roi_editor가 채용)
```

> **`visionguide-backend/`는 존재하지 않는다.** `CLAUDE.md`에 미구현으로 기재돼 있다.

---

## 2. 루트 Python 34개 — 두 종류가 섞여 있다

루트가 평평해서 "Pi에서 도는 코드"와 "PC에서 한 번 돌리는 도구"가 구분되지 않는다.
판별 기준은 **`Makefile`의 `DEPLOY_PY`에 있는가**이다.

### 2-1. Pi 런타임 (`DEPLOY_PY` 17개) — rsync로 기기에 배포된다

| 파일 | 역할 |
|---|---|
| `camera_live_pi.py` | **Pi 메인.** 카메라·추론·트래킹·ROI·MJPEG·녹화 (88K, 최대 파일) |
| `yolo_postprocess.py` | 전처리(letterbox)·후처리 공유 모듈 — CPU/EdgeTPU 양쪽이 쓴다 |
| `edgetpu_infer.py` | Coral EdgeTPU Python 3.9 서브프로세스 워커 |
| `detect.py` | `WhiteCaneDetector` (PyTorch 폴백) |
| `simple_tracker.py` | 트래커 (`static_frames`/`max_disp` 포함) |
| `cane_person_assoc.py` | 지팡이–사람 짝짓기 |
| `camera_config.py` | 다중 카메라 프로필 + `MODEL_VARIANTS` |
| `audio_trigger.py` | 디바운스/쿨다운 + 큐 기반 순차 재생 |
| `announcement_router.py` | 카메라·RF 두 이벤트 소스의 안내 라우팅 |
| `foot_traffic_counter.py` | 유동인구 sqlite 집계 |
| `detection_events.py` | 감지 이벤트 로그 |
| `fp_hotspots.py` | 오탐지 핫스팟 누적 → 제외구역 제안 |
| `gpio_controls.py` | Wi-Fi 전환 버튼·LED·부저 |
| `fan_controller.py` | 냉각팬 GPIO 스위칭 |
| `si4432_radio.py` | Si4432 SPI 수신기 |
| `kics_protocol.py` | KICS.KO-06.0046/R3 358.5MHz 펄스 디코더 |
| `rf_audio_trigger.py` | RF 수신 → 오디오 트리거 |

### 2-2. PC 전용 도구 (배포 안 함) 17개

**데이터 준비 (1회성)**

| 파일 | 역할 |
|---|---|
| `dataset_prep.py` | 정규화·분할 공유 헬퍼 |
| `prepare_background_dataset.py` | 배경 네거티브 편입 (`bg_*.jpg`) |
| `prepare_lookalike_dataset.py` | 유사물 네거티브 편입 (`lk_*.jpg`) |
| `prepare_stick_cctv_dataset.py` | CCTV 각도 유사물 — **기각됨**(헤더에 근거) |
| `prepare_night_eval.py` | 합성 야간 평가셋 |
| `fetch_lvis_lookalikes.py` | LVIS에서 유사물 수집 |
| `fetch_openimages_lookalikes.py` | Open Images에서 유사물 수집 |
| `merge_person_dataset.py` | 사람 데이터셋 병합 (1회성, 실행 완료) |
| `resplit_dataset.py` | **누수 없는 그룹 단위 재분할** → `datasets/v2/` |

**평가**

| 파일 | 역할 |
|---|---|
| `eval_video_recall.py` | **실영상 탐지/트리거 벤치 — 모델 채택 1차 기준** |
| `eval_background_fp.py` | 네거티브 오탐지 벤치 |

**기타**

| 파일 | 역할 |
|---|---|
| `camera_live.py` | PC용 뷰어 (PyTorch, ROI 없음) |
| `discover.py` | 서브넷 스캔으로 Pi 찾기 — **어디서도 import되지 않음** |
| `seed_dummy_traffic.py` | 통계 화면 확인용 더미 데이터 생성 |

### 2-3. 루트 비-Python 파일

| 파일 | 상태 |
|---|---|
| `Makefile` | Pi 배포 자동화 — `DEPLOY_PY`/`DEPLOY_MODEL_DIRS`가 배포 목록의 단일 출처 |
| `deploy.ps1` | Windows PowerShell 배포 (Makefile과 기능 중복) |
| `CLAUDE.md` (607줄) · `README.md` (182줄) · `AGENTS.md` (26줄) · `인수인계.md` (250줄) | 문서 4종 |
| `environment.yml` · `requirements.txt` · `requirements-pi.txt` | 의존성 |
| `camera_config_example.json` · `rois_example.json` · `rf_config_example.json` | 설정 예시 |
| `lookalike_exclude.txt` | 유사물 제외 목록 + 근거 주석 |
| `yolo26n.pt` · `yolov8n.pt` | **COCO 사전학습 가중치 11.5MB — ultralytics가 자동 다운로드하므로 커밋 불필요** |
| `train.log` | **2026-07-06 학습 로그 1.5MB — `runs/*/results.csv`에 같은 내용이 있다** |

---

## 3. 애플리케이션 디렉터리

| 디렉터리 | 내용 | 배포 |
|---|---|---|
| `roi_editor/` | `server.py`(FastAPI), `network_manager.py`(nmcli 래퍼), `static/index.html` | ⭕ `make sync-roi-editor` |
| `simulator/` | `app.py`(Streamlit), `detector.py`, `roi_manager.py`, `trigger_dispatcher.py` | `roi_manager.py`만 ⭕ |
| `label_tool/` | `server.py`, `static/index.html`, OpenVINO 모델 3.6MB | ❌ 로컬 전용 |
| `configs/` | `train_v9_*.yaml`(증강 실험 6) · `train_v10_*`(데이터 2) · `train_v11_*`(백본 2) | ❌ |
| `deploy/` | systemd 유닛 7 + sudoers 2 + `auto_ap.sh` | `make install-service` |
| `tests/` | pytest 20개 파일 | ❌ |

> **`simulator/roi_manager.py`는 Pi와 시뮬레이터가 공유한다.** 변경 시 양쪽 확인 필요.

---

## 4. `datasets/` — 저장소의 59% (618 MB / 45,574 파일)

```
datasets/
├── train/{images,labels}   11,116장  ┐
├── val/{images,labels}      1,263장  ├─ 원본 풀 (337.6 MB / 26,956 파일)
├── test/{images,labels}     1,099장  ┘
├── sources/cane_pool/       9,308장  ← train/val/test와 100% 중복 (280.7 MB)
├── data.yaml                          ← train/val/test를 가리킴
│
├── v2/ v2_nolkc/ v2_night/            ← .gitignore (resplit_dataset.py가 생성)
├── data_v2*.yaml                      ← .gitignore
└── test2.mp4 test3.mp4                ← untracked
```

**주의해야 할 두 가지**

1. **`sources/cane_pool/`은 순수 중복이다.** 9,308장 전부가 `train/val/test`와
   **동일한 git blob 해시**를 가진다(전수 확인). 18,616개 경로가 이중으로 추적된다.
2. **현재 학습에 실제로 쓰는 데이터는 추적되지 않는다.** 채택 모델 `v10_320`은
   `datasets/data_v2_nolkc.yaml`로 학습했는데 그 경로는 전부 `.gitignore`다.
   다만 `resplit_dataset.py`가 `train/val/test`에서 **하드링크로 생성**하므로
   **원본 풀만 있으면 재현된다** — 원본 풀은 지워선 안 된다.

---

## 5. `runs/` — 저장소의 36% (372 MB / 536 파일)

| 계보 | run | 비고 |
|---|---|---|
| v1~v2 | `white_cane_v1-2`, `v2` | 640 해상도. EdgeTPU 컴파일본 보유 |
| v3~v6 | `v3_320`, `v4_320`, `v5_320`, `v5b_ft320`, `v6_320`, `v6_ft320` | 320 계보. **누수된 split** |
| v7~v8 | `v7_yolo26n_*`, `v8_yolo11n_*` | 백본 탐색 (누수된 split) |
| v9 | `v9_base`, `v9_augA~augE` | 증강 실험 6종 (`datasets/v2`) |
| v10~v11 | `v10_v8n`, **`v10_nolkc`**, `v11_v11n`, `v11_v26n` | 데이터·백본 비교 |

**현행 권장은 `v10_nolkc`** (= `MODEL_VARIANTS["v10_320"]`).
근거는 `docs/model_evaluation_report_v3.md`.

`.gitignore`로 제외되는 것: `weights/best.onnx`, `weights/best_saved_model/`,
`weights/keep_*.tflite`, `weights/last.pt`, `runs/detect/` — 전부 `best.pt`에서
재생성 가능한 export 중간 산출물이다.

---

## 6. UI 관련 디렉터리 3종 — 혼동 주의

이름이 비슷하지만 **서로 다른 것**이고, 그중 하나만 실제로 동작한다.

| 디렉터리 | 정체 | 상태 | 접속 |
|---|---|---|---|
| **`roi_editor/`** | Pi 로컬 FastAPI + 정적 HTML | **구현·배포됨** | `http://<pi>:5000` |
| `visionguide-frontend/` | React + Vite 관리자 대시보드 | **미구현·미배포** | 로컬 `npm run dev` |
| `dash/` | Stitch 생성 디자인 목업(html+png) | 참고 자료 | — |
| `EXPO-Dash-demo/` | 디자인 토큰 출처 (roi_editor가 채용) | 참고 자료 | — |

**기기에서 보이는 화면은 `roi_editor/`뿐이다.** `visionguide-frontend/`를 고쳐도
Pi에는 아무 영향이 없다 — 배포 경로 자체가 없다.

`visionguide-frontend/public/streams/*.jpg` 6장이 **29.8 MB**를 차지한다
(`hall.jpg` 한 장이 14 MB). 데모용 목업 이미지다.

---

## 7. `docs/`

| 분류 | 파일 |
|---|---|
| 평가 리포트 | `model_evaluation_report_v1~v3.md`, `int8_320_quantization_report.md` |
| 기술 문서 | `coral_yolo_optimization.md`, `pi-deployment-guide.md`, `WIRING.md`, `si4432-kics-integration.md`, `wifi-onboarding-guide.md`, `multi-roi-collision-ideas.md` |
| 구조 | `FILE_INVENTORY.md` (이 문서) |
| 설계 명세 | `시각장애인_음성안내시스템_통합_기능명세서_v2.0.{md,xlsx}` |
| 작업 기록 | `작업일지_20260909.md` |
| 참고 논문 PDF | 2편 (3.9 MB) |
| 학생 제출 PDF | 5편 (`20242514_*.pdf` 등, 4.0 MB) — 코드와 무관한 개인 제출물 |

---

## 8. 커밋하지 않는 것 (`.gitignore`)

| 대상 | 이유 |
|---|---|
| `datasets/sources/background_photos/`, `staging/`, `v2*/` | 원본·중간 산출물. 스크립트로 재생성 |
| `datasets/train/images/lk_*.jpg` (544장) | 공개 데이터셋에서 재수집 가능 |
| `runs/*/weights/{best.onnx,best_saved_model/,keep_*,last.pt}` | export 중간 산출물 |
| `runs/detect/` | `yolo val` 부산물 |
| `eval_out/` | 평가 시각화 산출물 |
| `rois.json`, `camera_config.json`, `recordings/` | **Pi 로컬 런타임 파일** — rsync 대상도 아니다 |
| `*.Zone.Identifier` | Windows 복사 부산물 |

---

## 9. 배포 경로 요약

```
PC 작업본 ──┬── make sync            → DEPLOY_PY 17개 + DEPLOY_MODEL_DIRS의 tflite
            ├── make sync-roi-editor → roi_editor/ + simulator/roi_manager.py
            └── make install-service → deploy/ 의 systemd 유닛
                                          ↓
                              Pi:~/visionguide/ (평면 배치)
```

**Pi에는 git 저장소가 없다.** 배포는 체크아웃이 아니라 rsync이므로, "브랜치를
Pi에 적용"이라는 경로는 존재하지 않는다. `make deploy`가 위 세 타겟을 모두
포함하며, **부분 배포 시 `sync`와 `sync-roi-editor`를 모두 챙겨야 한다**
(`sync`만 하면 웹 UI가 구버전으로 남는다 — 실제로 발생한 사례가 있다).
