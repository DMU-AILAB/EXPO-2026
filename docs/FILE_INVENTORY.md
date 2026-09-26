# 파일 인벤토리 — 저장소 구조도

**갱신일:** 2026-09-16 (전수 분석 기준)
**대상 브랜치:** `refactor/repo-structure` (기능별 디렉터리 재배치 후)

이 문서는 저장소의 **모든 디렉터리와 루트 파일**이 어떤 역할인지, 그리고
**Pi 배포 대상인지 / 커밋 대상인지**를 한눈에 보여준다. 기능 설명은 `CLAUDE.md`에
있고, 이 문서는 "어디에 무엇이 있고 왜 거기 있는가"를 다룬다.

## 규모 요약

| | 파일 수 | 용량 | 비중 |
|---|---:|---:|---:|
| `datasets/` | 45,574 | 618.3 MB | 59% |
| `runs/` | 536 | 372.2 MB | 36% |
| `dashboard/frontend/` | 34 | 31.7 MB | 3% |
| 루트 파일 | 49 | 13.4 MB | 1% |
| 그 외 전부 | 105 | 10.2 MB | 1% |
| **합계** | **46,298** | **1,045.8 MB** | |

**추적 파일의 95%가 데이터셋과 학습 산출물이다.** 코드는 전체의 1% 남짓이다.

---

## 1. 전체 구조

```
expo/
├── device/            Pi에서 실행되는 런타임 24개 ★ Makefile의 DEPLOY_PY와 정확히 일치
├── tools/             PC 전용 스크립트 17개
│   ├── data/            데이터 준비 9 + lookalike_exclude.txt
│   ├── eval/            평가 2 (eval_video_recall · eval_background_fp)
│   └── dev/             개발 보조 3 (camera_live · discover · seed_dummy_traffic)
├── apps/              사람이 띄워 쓰는 앱
│   ├── roi_editor/      Pi 로컬 웹 UI (:5000) — 실제 운영 대시보드
│   ├── simulator/       PC Streamlit 시뮬레이터
│   └── label_tool/      라벨 보완 툴
├── dashboard/         PC 중앙 관리자 대시보드(backend + frontend) + 디자인 자료
│   ├── frontend/        (구 visionguide-frontend)
│   ├── mockups/         (구 dash — Stitch 목업)
│   └── demo/            (구 EXPO-Dash-demo — 디자인 토큰 출처)
├── configs/           학습 설정 yaml 10개 + examples/ 설정 예시 3개
├── deploy/            systemd 유닛 · sudoers · auto_ap.sh · deploy.ps1
├── tests/             pytest 26개 + conftest.py
├── weights/           COCO 사전학습 .pt (gitignore)
├── datasets/          학습 데이터 ★저장소의 59%
├── runs/              학습 산출물 ★저장소의 36%
└── docs/              설계·평가 문서
```

루트에 남는 파일은 8개다 — `README.md` `CLAUDE.md` `AGENTS.md` `Makefile`
`Makefile` `environment.yml` `requirements.txt` `requirements-pi.txt` `.gitignore`.

## 2. ★ Pi는 평면 배치다 — 이 저장소 구조와 다르다

`make sync`는 `device/*.py`를 Pi의 `~/visionguide/`에 **평면으로** 풀어놓는다.
기기에는 `device/`라는 디렉터리가 없고 모든 모듈이 한 곳에 있으며, systemd 유닛도
`~/visionguide/camera_live_pi.py`를 가리킨다. **이 재배치로 기기 쪽은 전혀 바뀌지 않았다.**

그래서 양쪽에서 동작해야 하는 경로 계산은 배치를 판별한다.

| 파일 | 판별 방식 |
|---|---|
| `device/{camera_live_pi,detect,edgetpu_infer}.py` | `runs/`가 옆에 있으면 평면(Pi), 없으면 한 단계 위가 루트(PC) |
| `device/camera_live_pi.py`의 `simulator` import | `_BASE`와 `_BASE/apps` 둘 다 `sys.path`에 시도 |
| `apps/roi_editor/server.py` | `parent.parent`(Pi=루트, PC=apps) + PC일 때만 `device/` 추가 |
| `tests/` | `conftest.py`가 `device`·`apps`·`tools/*`를 한 번에 넣는다 |

**`simulator` import는 특히 주의해야 한다.** `camera_live_pi.py`에서 `ROIManager`
import가 `try/except ImportError`로 감싸여 있어, 경로가 틀리면 예외 없이
**ROI·오디오 기능이 조용히 꺼진다**. 그래서 import 전에 경로를 확정한다.

### 새 파일을 어디에 둘 것인가

"Pi에서 도는가"로 먼저 가른다. Pi에서 돌면 `device/`에 넣고 **반드시 `Makefile`의
`DEPLOY_PY`에도 추가**한다 — 둘이 어긋나면 기기에서 ImportError가 나거나, 더 나쁘게는
구버전 파일이 조용히 남는다. PC에서만 쓰면 `tools/` 아래 용도별 디렉터리에 넣는다.

## 2-1. `device/` — Pi 런타임 24개 (= `DEPLOY_PY`)

| 파일 | 역할 |
|---|---|
| `camera_live_pi.py` | **Pi 메인.** 카메라·추론·트래킹·ROI·MJPEG·녹화 (88K, 최대 파일) |
| `yolo_postprocess.py` | 전처리(letterbox)·후처리 공유 — CPU/EdgeTPU 양쪽이 쓴다 |
| `edgetpu_infer.py` | Coral EdgeTPU Python 3.9 서브프로세스 워커 |
| `detect.py` | `WhiteCaneDetector` (PyTorch 폴백) |
| `simple_tracker.py` · `cane_person_assoc.py` | 트래킹(IoU + 거리 폴백 + **재식별**) · 지팡이–사람 짝짓기 |
| `gate_chain.py` | **게이트 체인 단일 구현** — 배포·평가·재생검증이 같은 순서·같은 상수를 쓴다 |
| `replay_engine.py` | 저장된 영상을 배포 경로로 재생해 주석 프레임 생성 (roi_editor 검증 탭) |
| `device_identity.py` | 서버가 발급한 `device_id`·`api_key` 보관 (다중 Pi 운용) |
| `event_logger.py` | 이벤트 outbox 전송 + 하트비트 (표준 라이브러리만) |
| `device_status.py` | `/proc`·`/sys` 상태 읽기 + CPU 사용률 |
| `device_metrics.py` | 탐지 루프 지표를 sqlite로 roi_editor에 전달 |
| `pedestrian_entity.py` | 사람+지팡이를 하나의 보행자 엔티티로 묶어 추적 (래치 · 가상 지팡이 박스) |
| `camera_config.py` | 다중 카메라 프로필 + `MODEL_VARIANTS` |
| `audio_trigger.py` · `announcement_router.py` | 디바운스·쿨다운·순차 재생 · 안내 라우팅 |
| `foot_traffic_counter.py` · `detection_events.py` · `fp_hotspots.py` | 유동인구 · 이벤트 로그 · 오탐지 핫스팟 |
| `gpio_controls.py` · `fan_controller.py` | Wi-Fi 버튼·LED·부저 · 냉각팬 |
| `si4432_radio.py` · `kics_protocol.py` · `rf_audio_trigger.py` | Si4432 수신 · KICS 디코더 · RF 트리거 |

## 2-2. `tools/` — PC 전용 17개

| 하위 | 파일 |
|---|---|
| `data/` | `resplit_dataset.py`(누수 없는 재분할) · `dataset_prep.py` · `merge_person_dataset.py` · `prepare_{background,lookalike,stick_cctv,night_eval}_*.py` · `fetch_{lvis,openimages}_lookalikes.py` · `lookalike_exclude.txt` |
| `eval/` | `eval_video_recall.py`(**모델 채택 1차 기준**) · `eval_background_fp.py` |
| `dev/` | `camera_live.py`(PC 뷰어) · `discover.py`(Pi 탐색) · `seed_dummy_traffic.py` |

## 3. 애플리케이션 디렉터리 (`apps/`)

| 디렉터리 | 내용 | 배포 |
|---|---|---|
| `apps/roi_editor/` | `server.py`(FastAPI), `network_manager.py`(nmcli 래퍼), `static/index.html` | ⭕ `make sync-roi-editor` |
| `apps/simulator/` | `app.py`(Streamlit), `detector.py`, `roi_manager.py`, `trigger_dispatcher.py` | `roi_manager.py`만 ⭕ |
| `apps/label_tool/` | `server.py`, `static/index.html`, OpenVINO 모델 3.6MB | ❌ 로컬 전용 |
| `configs/` | `train_v9_*.yaml`(증강 실험 6) · `train_v10_*`(데이터 2) · `train_v11_*`(백본 2) | ❌ |
| `deploy/` | systemd 유닛 7 + sudoers 2 + `auto_ap.sh` | `make install-service` |
| `tests/` | pytest 26개 파일 | ❌ |

> **`apps/simulator/roi_manager.py`는 Pi와 시뮬레이터가 공유한다.** 변경 시 양쪽 확인 필요.

---

## 4. `datasets/` — 저장소의 59% (618 MB / 45,574 파일)

```
datasets/
├── v1/                     ← 원본 풀. data.yaml이 가리킨다
│   ├── train/{images,labels}  11,116장  ┐
│   ├── val/{images,labels}     1,263장  ├─ 337.6 MB / 26,956 파일
│   └── test/{images,labels}    1,099장  ┘
├── videos/                 ← 평가 영상 3개 + video_gt.json (미추적)
├── sources/cane_pool/
│   ├── images/              9,308장  ← train/val/test와 blob 동일 (280.4 MB)
│   └── labels/              9,308개  ← 사람 라벨 병합 전 원본 (0.3 MB, 고유)
├── data.yaml                          ← train/val/test를 가리킴
│
├── v2/ v2_nolkc/ v2_night/            ← .gitignore (resplit_dataset.py가 생성)
├── data_v2*.yaml                      ← .gitignore
└── staging/                          ← .gitignore (변환 중간 산출물)
```

**주의해야 할 두 가지**

1. **`sources/cane_pool/`은 이미지만 중복이다.** 이미지 9,308장은 `train/val/test`와
   **동일한 git blob 해시**를 갖지만, **라벨은 9,303개가 다르다** — `merge_person_dataset.py`와
   `resplit_dataset.py --relabel-person`이 split 쪽 라벨에 사람을 추가했고, cane_pool은
   그 이전 상태를 보존한다. 정리할 때 **이미지만 빼고 라벨은 남겨야 한다.**
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

## 6. UI 관련 디렉터리 4종 — 혼동 주의

이름이 비슷하지만 **서로 다른 것**이고, 그중 하나만 실제로 동작한다.

| 디렉터리 | 정체 | 상태 | 접속 |
|---|---|---|---|
| **`apps/roi_editor/`** | Pi 로컬 FastAPI + 정적 HTML | **구현·배포됨** | `http://<pi>:5000` |
| `dashboard/backend/` | FastAPI 중앙 백엔드 | **구현됨·PC 실행** | `uvicorn app.main:app --port 8000 --workers 1` |
| `dashboard/frontend/` | React + Vite 관리자 대시보드 | **구현됨·PC 실행** | 로컬 `npm run dev` |
| `dashboard/mockups/` | Stitch 생성 디자인 목업(html+png) | 참고 자료 | — |
| `dashboard/demo/` | 디자인 토큰 출처 (roi_editor가 채용) | 참고 자료 | — |

**기기에서 직접 실행되는 API는 `apps/roi_editor/`이고, 관리 화면은 PC의 `dashboard/frontend/`다.**
`dashboard/frontend/`를 고쳐도
Pi에는 아무 영향이 없다 — 배포 경로 자체가 없다.

`dashboard/frontend/public/streams/*.jpg` 6장이 **29.8 MB**를 차지한다
(`hall.jpg` 한 장이 14 MB). 데모용 목업 이미지다.

---

## 7. `docs/`

| 분류 | 파일 |
|---|---|
| 평가 리포트 | `model_evaluation_report_v1~v3.md`, `int8_320_quantization_report.md` |
| 기술 문서 | `coral_yolo_optimization.md`, `pi-deployment-guide.md`, `WIRING.md`, `si4432-kics-integration.md`, `wifi-onboarding-guide.md`, `multi-roi-collision-ideas.md` |
| 구조 | `FILE_INVENTORY.md` (이 문서) |
| 설계 명세 | `시각장애인_음성안내시스템_통합_기능명세서_v2.0.{md,xlsx}` |
| 작업 계획 | `data_collection_plan.md` (실내 데이터 촬영 — 촬영 전 코드 준비 2건 포함) |
| 작업 기록 | `작업일지_20260909.md` |
| 참고 논문 PDF | 2편 (3.9 MB) |
| 학생 제출 PDF | 5편 (`20242514_*.pdf` 등, 4.0 MB) — 코드와 무관한 개인 제출물 |

---

## 8. 커밋하지 않는 것 (`.gitignore`)

| 대상 | 이유 |
|---|---|
| `datasets/sources/background_photos/`, `staging/`, `v2*/` | 원본·중간 산출물. 스크립트로 재생성 |
| `datasets/v1/train/images/lk_*.jpg` (544장) | 공개 데이터셋에서 재수집 가능 |
| `runs/*/weights/{best.onnx,best_saved_model/,keep_*,last.pt}` | export 중간 산출물 |
| `runs/detect/` | `yolo val` 부산물 |
| `eval_out/` | 평가 시각화 산출물 |
| `rois.json`, `camera_config.json`, `recordings/` | **Pi 로컬 런타임 파일** — rsync 대상도 아니다 |
| `*.Zone.Identifier` | Windows 복사 부산물 |

---

## 9. 배포 경로 요약

```
PC 작업본 ──┬── make sync            → DEPLOY_PY 24개 + DEPLOY_MODEL_DIRS의 tflite
            ├── make sync-roi-editor → roi_editor/ + simulator/roi_manager.py
            └── make install-service → deploy/ 의 systemd 유닛
                                          ↓
                              Pi:~/visionguide/ (평면 배치)
```

**Pi에는 git 저장소가 없다.** 배포는 체크아웃이 아니라 rsync이므로, "브랜치를
Pi에 적용"이라는 경로는 존재하지 않는다. `make deploy`가 위 세 타겟을 모두
포함하며, **부분 배포 시 `sync`와 `sync-roi-editor`를 모두 챙겨야 한다**
(`sync`만 하면 웹 UI가 구버전으로 남는다 — 실제로 발생한 사례가 있다).
