# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## 프로젝트 개요

**VisionGuide** — 컴퓨터 비전 기반 시각장애인 보조 공학용 자동 음성 안내 시스템 (EXPO-2026 캡스톤 프로젝트)

두 개의 독립된 서브시스템으로 구성됩니다.

1. **현장 디바이스 (Edge)** — Raspberry Pi 4에서 YOLOv8n으로 흰 지팡이를 탐지하고, ROI 위치 판별 후 GPIO 릴레이 또는 TTS로 음성 안내를 트리거
2. **관리자 대시보드** — FastAPI 백엔드 + React/TypeScript 프론트엔드로 ROI 설정, 음성 매핑, 실시간 모니터링, 통계 제공

---

## 현재 구현 상태

> 각 파일이 **Pi 배포 대상인지 / 커밋 대상인지**(런타임 코드 · 로컬 1회성 도구 ·
> 커밋 안 하는 산출물)는 [`docs/FILE_INVENTORY.md`](docs/FILE_INVENTORY.md)에
> 따로 정리돼 있다. 아래 표는 기능 설명이다.

### 구현 완료

| 파일/디렉토리 | 설명 |
|------|------|
| `camera_live_pi.py` | Pi 전용 추론 뷰어 — `CameraPipeline`(카메라별 독립 파이프라인) + 슈퍼바이저 `main()`. Coral EdgeTPU / TFLite INT8 / PyTorch 자동 선택, 카메라 회전, 감지 제외구역 필터링, MJPEG 스트리밍. `--camera-config` 미지정 시 기존 단일 카메라(`--source`/`--roi-config`/`--port`) 동작 그대로 |
| `camera_live.py` | PC용 추론 뷰어 (PyTorch) — 회전(`--rotation`)만 반영, ROI/듀얼카메라 파이프라인 없음 |
| `detect.py` | `WhiteCaneDetector` 클래스 |
| `edgetpu_infer.py` | Coral Edge TPU Python 3.9 서브프로세스 워커 |
| `camera_config.py` | `CameraProfile` 다중 카메라 프로필 (`camera_config.json`) — load/save/validate. 표준 라이브러리만 사용 |
| `audio_trigger.py` | `StandaloneDispatcher` (**사람 단위** 디바운스/쿨다운 + ROI 단위 최소간격) + `AudioPlayer` (큐+워커스레드 기반 순차 재생 — 여러 카메라가 공유해도 겹쳐 재생되지 않고 대기열에 쌓였다가 순서대로 나옴, mpg123/pygame) |
| `apps/simulator/app.py` | Streamlit PC 시뮬레이터 — ROI 폴리곤 편집, 실시간 탐지, 오디오 트리거 |
| `apps/simulator/detector.py` | 시뮬레이터용 탐지기 |
| `apps/simulator/roi_manager.py` | `ROIManager` (Shapely Point-in-Polygon) + `ROI` dataclass (audio_file, `zone_type`: "trigger"/"exclude" 포함) |
| `apps/simulator/trigger_dispatcher.py` | Streamlit 전용 디바운스/쿨다운 (시뮬레이터만 사용) |
| `apps/roi_editor/server.py` | Pi 로컬 FastAPI 서버(포트 5000) — ROI CRUD(폴리곤 Shapely 유효성 검증 포함) + 카메라 프로필 CRUD(`/api/cameras`, `model_variant` 포함) + `/api/model-variants`(모델 선택 드롭다운용) + `/api/device/status`(가동시간/CPU온도/부하/메모리, `/proc`·`/sys` 표준 파일만 사용) + 오디오 파일 업로드(`/api/audio/upload`) + 오디오 미리듣기(`/api/audio/file`, audio_dir 밖 경로 차단) + `/api/stats/timeseries`(기간별 유동인구 시계열) + `/api/events`(최근 감지 이벤트), `rois.json`/`camera_config.json` atomic write. `?camera=<id>` 쿼리로 카메라별 ROI 파일 분리 |
| `apps/roi_editor/static/index.html` | 브라우저 ROI 웹 에디터 — `dashboard/demo`의 디자인 토큰(accent/good/amber/danger, Pretendard)과 레이아웃(상단 탭 + 화면별 페이지)을 그대로 채용. 탭 4개: **모니터링**(카메라 상태 배지, 최근 감지 이벤트 표, 오늘 총 유동인구/지팡이 사용자 감지, ROI별 오디오 안내 테스트 재생) / **ROI 편집**("+ 새 구역 그리기" 명시적 토글로만 캔버스 클릭이 꼭짓점을 추가, 목록에서 기존 ROI 클릭 시 우측 폼에 로드되어 이름/안내텍스트/오디오/우선순위/구역유형 편집 및 삭제, 카메라 선택·설정·신뢰도 슬라이더 포함) / **통계**(기간 오늘/7일/30일, 순수 canvas 꺾은선 그래프) / **녹화**(수동 시작/중지 클립 목록·재생·다운로드) |
| `pedestrian_entity.py` | 사람+지팡이를 하나의 `PedestrianEntity`로 묶어 프레임 사이에 상태를 유지 — 1:1 배정(히스테리시스) · 지팡이 사용자 **래치** · 지팡이 트랙이 끊긴 구간의 **가상 지팡이 박스** · 안내 주체 매핑(`subject_for_canes`). 표준 라이브러리만 사용 |
| `gate_chain.py` | **게이트 체인의 단일 구현** — 정지 억제 → 움직임 → 엔티티 갱신 → 사람 동반(래치 완화) 순서와 상수(`STATIC_CANE_SUPPRESS_FRAMES`·`MOVED_MIN_DIAG_RATIO`)를 한 곳에 둔다. `camera_live_pi.py`·`eval_video_recall.py`·`replay_engine.py`가 **같은 것**을 돌린다 |
| `replay_engine.py` | 저장된 영상을 **배포와 같은 경로**로 재생하며 주석 프레임을 만든다 — roi_editor의 "검증" 탭이 MJPEG로 띄운다. 오디오는 재생하지 않고 발사 시점만 기록 |
| `device_identity.py` | 서버가 발급한 `device_id`·`api_key`·`server_url` 보관. **값의 주인은 서버** — 등록 시 `POST /api/identity`로 심긴다. `rois.json`과 같은 Pi 로컬 런타임 파일(rsync·git 대상 아님) |
| `event_logger.py` | Pi → 서버 아웃바운드 2종 — `EventSender`(감지 이벤트 outbox, 실패해도 보관) · `HeartbeatSender`(살아있음+상태, 실패하면 **버리고** 다음 주기에 최신값). 탐지 루프는 sqlite 한 줄만 쓰고 전송은 `roi_editor`의 스레드가 맡는다. `urllib`만 써서 Pi 의존성을 늘리지 않는다 |
| `device_status.py` | `/proc`·`/sys`만으로 읽는 가동시간·CPU온도·부하·메모리 + **CPU 사용률**(두 시점 차이). psutil 미사용 |
| `device_metrics.py` | 탐지 루프의 추론 시간·프레임 시간·스트리밍 여부를 sqlite로 `roi_editor`에 넘긴다 — 하트비트가 쓰는 값이 탐지 프로세스에만 있기 때문 |
| `foot_traffic_counter.py` | 유동인구 sqlite 집계 — `FootTrafficCounter`(트랙 소멸 기반 카운팅) + 조회 함수 `read_daily_totals`/`read_hourly_breakdown`(0~23시 0-채움)/`read_range_daily_totals`(N일 일별 합계, 0-채움). ROI별 집계는 스키마상 불가(카메라 단위 시간별 합계만 기록) |
| `detection_events.py` | 최근 감지/안내 이벤트 로그(카메라별 sqlite, `foot_traffic_counter.py`와 같은 db 파일에 별도 테이블) — `log_event()`(ROI 트리거 시점마다 1건 기록, 오래된 건 자동 정리) / `read_recent_events()`(최신순 N건) |
| `gpio_controls.py` | GPIO 재시작 버튼 — 라즈베리파이 재부팅이 아니라 `visionguide-device` 서비스만 재시작 |
| `rois_example.json` | ROI 설정 파일 예시 |
| `runs/white_cane_v2/`, `v3_320`, `v4_320`, `v5b_ft320`, `v6_ft320`, `v10_nolkc`, `v11_v26n` 의 `weights/` | 학습된 가중치 — 카메라 프로필의 `model_variant`로 선택 (`camera_config.MODEL_VARIANTS` 참고). **현행 권장은 `v10_320`** (= `runs/white_cane_v10_nolkc/weights`). v10은 **누수 없는 재분할(`datasets/v2`) 위에서 처음부터 학습한 계보**이고, 실영상 탐지율이 v9 계열 최고 수준이다(`docs/model_evaluation_report_v3.md`). `v11_yolo26n_320`은 백본 비교용으로 남겨둔 것이지 권장이 아니다(실영상 35.3% vs v10 73.2%). **v1~v6의 정지 이미지 지표(mAP50 0.98)는 누수된 split에서 나온 값이라 v10과 직접 비교하면 안 된다** |
| `prepare_background_dataset.py` | 로컬 전용(Pi 배포 대상 아님) 1회성 데이터 준비 — `datasets/sources/background_photos/`의 배경 사진을 EXIF 회전 반영·640 jpg 정규화·`bg_XXXX.jpg` 리네임 후 빈 라벨과 함께 `datasets/v1/train/`에 편입. FP 벤치용 홀드아웃을 v4 오탐지 여부로 층화 추출해 분리 |
| `eval_background_fp.py` | 배경(네거티브) 이미지에서 나오는 오탐지를 conf 임계값별로 집계하는 벤치마크. PT/TFLite 등 ultralytics가 읽는 형식이면 모두 같은 잣대로 비교 가능 |
| `fetch_lvis_lookalikes.py` / `fetch_openimages_lookalikes.py` | 로컬 전용 1회성 수집 — 공개 데이터셋(LVIS / Open Images V7)을 **색인으로만** 써서 유사물 사진을 내려받고 COCO yolov8n으로 solo/with_person 분류. LVIS는 어노테이션만 제공하므로 이미지는 각 레코드의 `coco_url`로 개별 다운로드(전체 18GB를 받을 필요 없음), Open Images는 공개 S3에서 id 단위로 받는다 |
| `lookalike_exclude.txt` | 유사물 네거티브에서 뺄 원본 파일명 + 근거 주석 (`--exclude-file`) — 흰지팡이가 찍힌 사진을 걸러내는 육안 검수 결과 |
| `prepare_lookalike_dataset.py` + `dataset_prep.py` | 로컬 전용 1회성 데이터 준비 — 흰지팡이 **유사물**(등산스틱·우산·목발·난간·나뭇가지) 사진을 네거티브로 편입. `datasets/sources/lookalike_lvis_oi/{solo,with_person}/<카테고리>/` 구조를 받아 solo는 빈 라벨, with_person은 COCO yolov8n으로 person만 자동 라벨링(`--review` 컨택트시트로 검수). `dataset_prep.py`는 `prepare_background_dataset.py`와 공유하는 정규화/층화 헬퍼 |
| `fp_hotspots.py` | 오탐지 다발 지점 누적(카메라별 sqlite, `detection_events.py`와 같은 db 파일에 별도 테이블) — 정지 억제로 걸러낸 지팡이 트랙 위치를 32×32 그리드 셀로 집계. `roi_editor`가 이걸 읽어 제외구역을 **제안**한다(자동 생성하지 않음) |
| `eval_video_recall.py` | 로컬 전용 — **실영상 기준 지팡이 탐지/트리거 벤치마크. 모델 채택의 1차 기준.** `camera_live_pi.py`의 백엔드·게이트 상수·연관 로직을 그대로 import해 배포와 같은 경로로 잰다(복붙 금지). `--gt`로 정답 구간을 주면 재현율과 오탐지를 분리 집계한다(`datasets/videos/video_gt.json`) |
| `resplit_dataset.py` + `tests/test_resplit_dataset.py` | 로컬 전용 1회성 — 누수 없는 **그룹 단위 재분할**(`datasets/v2/`, 하드링크). 증강 해시/AIHub 세션을 그룹으로 묶고 층별 md5로 배정한다. `--relabel-person`으로 cane_only의 누락 사람 라벨도 보완. 테스트가 split 쌍의 그룹키 교집합이 공집합인지 검증한다 |
| `prepare_night_eval.py` | 로컬 전용 1회성 — 합성 야간 평가셋(`datasets/v2_night/`, 감마 0.35~0.55 + 노이즈 σ=6). **KPI 달성 근거가 아니라 회귀 감시용** |
| `configs/train_*.yaml` | 학습 설정 — `yolo train cfg=<yaml>`로 재현 가능하게 고정. `project:`는 반드시 절대경로(상대경로면 `runs/detect/runs/<name>`으로 중첩된다) |
| `apps/label_tool/server.py` + `apps/label_tool/static/index.html` | 로컬 전용(Pi 배포 대상 아님) 데이터셋 라벨링 보완 툴 — `datasets/v1/{train,val,test}`에서 class 0(지팡이)만 있고 class 1(사람)이 없는 이미지("cane_only")만 골라 보여주고, 사람 바운딩박스를 그려 저장. 기존 지팡이 라벨은 읽기 전용으로 표시, 검토 진행상황은 `apps/label_tool/reviewed.json`(gitignore)에 저장돼 재시작해도 이어서 작업 가능 |

### 미구현 (계획)

- 관리자 대시보드 백엔드 (`visionguide-backend/` — FastAPI)
- 관리자 대시보드 프론트엔드 (`visionguide-frontend/` — React)
- GPIO 릴레이 트리거 (`gpiozero`)
- 설정 폴링 (`config_syncer.py`)
- 이벤트 로거 / 서버 전송 (`event_logger.py`)
- 헬스 워치독 (`watchdog.py`)

---

## 디렉터리 구조 (★ 배치 규칙)

```
device/     Pi에서 실행되는 런타임 24개 — Makefile의 DEPLOY_PY와 정확히 일치한다
tools/      PC 전용 스크립트 (data/ 데이터준비 · eval/ 평가 · dev/ 개발보조)
apps/       사람이 띄워 쓰는 앱 (roi_editor · simulator · label_tool)
dashboard/  미구현 React 대시보드(frontend) + 디자인 자료(mockups · demo)
configs/ deploy/ tests/ docs/ datasets/ runs/ weights/
```

**새 파일을 어디에 둘지**는 "Pi에서 도는가"로 먼저 가른다. Pi에서 돌면 `device/`에
넣고 **반드시 `Makefile`의 `DEPLOY_PY`에도 추가**한다. 둘이 어긋나면 기기에서
ImportError가 나거나, 더 나쁘게는 구버전 파일이 조용히 남는다.

### ★ Pi는 평면 배치다 — 이 저장소 구조와 다르다

`make sync`는 `device/*.py`를 Pi의 `~/visionguide/`에 **평면으로** 풀어놓는다.
즉 기기에서는 `device/`라는 디렉터리가 존재하지 않고 모든 모듈이 한 곳에 있다.
systemd 유닛도 `~/visionguide/camera_live_pi.py`를 가리킨다.

그래서 **양쪽에서 동작해야 하는 경로 계산은 배치를 판별해야 한다.** `device/`의
모듈들은 이 관용구를 쓴다.

```python
_HERE = Path(__file__).parent
_BASE = _HERE if (_HERE / "runs").is_dir() else _HERE.parent   # 평면(Pi) vs 중첩(PC)
```

`simulator` 패키지도 같은 문제를 겪는다 — Pi는 `~/visionguide/simulator/`,
PC는 `apps/simulator/`다. `camera_live_pi.py`가 `sys.path`에 둘 다 시도하는 이유이며,
**`ROIManager` import가 `try/except ImportError`로 감싸여 있어 경로가 틀리면 ROI·오디오가
조용히 꺼지기 때문에** 여기서 확실히 잡아야 한다.

`apps/roi_editor/server.py`도 마찬가지다 — Pi에서는 `parent.parent`가 곧
`~/visionguide/`지만 PC에서는 `apps/`라서, 런타임 모듈을 찾으려면 `device/`를
따로 넣어야 한다.

테스트는 `tests/conftest.py`가 `device/`·`apps/`·`tools/*`를 한 번에 경로에 넣는다 —
개별 테스트에 `sys.path` 조작을 다시 넣지 말 것.

## 핵심 파일 관계

`device/camera_live_pi.py` (Pi 메인) ←→ `device/audio_trigger.py` + `apps/simulator/roi_manager.py` + `device/camera_config.py` + `device/pedestrian_entity.py`
`apps/simulator/app.py` (PC 시뮬레이터) ←→ `device/audio_trigger.py` + `apps/simulator/roi_manager.py` + `apps/simulator/trigger_dispatcher.py`
`apps/roi_editor/server.py` (ROI/카메라 웹 에디터) ←→ `apps/simulator/roi_manager.py`(간접, JSON 스키마 공유) + `device/camera_config.py` + `device/foot_traffic_counter.py`

새 기능을 추가할 때: `apps/simulator/roi_manager.py`는 Pi와 시뮬레이터가 공유하므로 변경 시 양쪽 동작을 확인하세요.
`device/camera_config.py`는 `device/camera_live_pi.py`(런타임 로더)와 `apps/roi_editor/server.py`(웹 UI 저장/검증) 양쪽이
동일 모듈을 import하므로, 검증 규칙(포트 중복, Coral 동글 1개 제약 등)은 한 곳(`validate_camera_config`)에만 있다.

## 카메라 프로필 / 회전 / 감지 제외구역 (설계 결정)

- **다중 카메라**: `camera_config.json`(루트, `rois.json`과 동일 원칙으로 Pi 로컬 전용·rsync 배포 안 함)이
  있으면 `camera_live_pi.py`가 카메라마다 독립된 `CameraPipeline`(자체 스레드, 자체 추론 백엔드/트래커/
  ROI매니저/MJPEG서버)을 동시에 구동한다. 파일이 없으면 기존 CLI 인자 기반 단일 카메라 동작 그대로.
- **동시성은 스레드** — 멀티프로세싱이 아니다. TFLite/EdgeTPU 호출이 네이티브 코드라 GIL을 상당 부분
  해제해 실질 병렬 처리되고, 오디오/GPIO LED 하트비트를 프로세스 내에서 손쉽게 공유할 수 있어서다.
  트레이드오프: 한 카메라 스레드의 네이티브 크래시가 프로세스 전체를 내릴 수 있음(Pi 실기기에서 재현되면
  멀티프로세싱으로 격상 검토).
- **Coral 동글은 물리적으로 1개** — `validate_camera_config()`가 `inference_backend="edgetpu"`를 활성
  카메라 2개 이상에 지정하는 걸 설정 저장 단계에서 차단한다.
- **회전**은 카메라 read() 직후, 추론 이전 단 한 곳(`_apply_rotation()`)에 적용 — 이후 모든 소비 지점이
  매 프레임 `frame.shape`를 다시 읽으므로 90/270도 가로세로 반전도 별도 수정 없이 전파된다. **회전값을
  바꾸면 그 카메라에 이미 그려진 ROI/제외구역 폴리곤 좌표계가 안 맞을 수 있다** — 자동 재배치는 하지
  않고 `roi_editor` UI가 경고 후 수동 재작도를 유도한다(90/270도는 가로세로비까지 바뀌어 단순 이동이
  아니라서 자동 변환의 버그 위험이 큼).
- **지팡이 트리거는 3중 게이트를 통과해야 한다.** 각 게이트가 서로 다른 오탐지 유형을 막으므로
  하나라도 빼면 그 유형이 통과한다. 순서대로 `camera_live_pi.py`의 `cane_tracks` 필터에 있다.

  | 게이트 | 막는 것 | 통과시키는 것 |
  |---|---|---|
  | 정지 억제 (`static_frames < 24`) | 오래 고정된 케이블/문틀 | 방금 생긴 트랙(아래 공백) |
  | **움직임 게이트** (`max_disp >= 대각선 2%`) | **사람 발치의 기둥/난간** | 흔들리는 나뭇가지 |
  | 사람 동반 (`require_person_for_trigger`) | **사람 없이 흔들리는 나뭇가지** | 사람이 든 유사물 |

  나머지(사람이 든 등산스틱·우산)는 런타임에서 못 막고 **모델이 구분해야** 한다 —
  `prepare_lookalike_dataset.py`/`fetch_lvis_lookalikes.py`의 유사물 네거티브가 그 몫이다.

  **세 번째 게이트만 `pedestrian_entity`의 래치로 완화된다** — 판정이
  `프레임 단위 연관 OR 래치`라서 지금 통과하던 것은 전부 계속 통과한다(단조 완화).
  앞의 두 게이트는 그대로이며, 오히려 **래치의 입력**으로 쓰인다(아래 절).

- **움직임 게이트**(`MOVED_MIN_DIAG_RATIO = 0.02`)가 필요한 이유는 정지 억제에 **약 2초의 공백**이
  있어서다. 새 트랙은 `static_frames = 0`으로 시작하므로(`simple_tracker.py`) 억제가 걸리기까지
  24프레임(실측 8~9 FPS에서 약 2.7초)이 걸리는데 디바운스는 0.5초라, 배경 기둥에 새 트랙이
  잡히면 **억제 전에 이미 음성이 나간다**. `max_age = 10`이라 탐지가 11프레임만 끊겨도 트랙이
  죽고 재생성되며 리셋되므로 반복될 수 있다. "정지가 증명되기 전까지 통과"를 "움직임이
  증명되기 전까지 억제"로 뒤집어 고정 물체를 프레임 0부터 막는다.
  - `SimpleTracker`가 `max_disp`(트랙 생성 지점 대비 중심 이동 거리의 최댓값)를 추적한다.
    **누적 경로 길이가 아니라 원점 대비 최대 변위인 이유**: 누적 경로는 EMA 스무딩 후에도 남는
    미세 지터가 매 프레임 더해져 고정 물체도 결국 "움직였다"가 된다. 원점 대비 변위는 지터
    진폭에 bounded돼 고정 물체는 영원히 작다(실측: ±2px 지터로 300프레임 뒤에도 3.9px,
    이동 물체는 30프레임에 114px).
  - 임계값이 픽셀 절대값이 아니라 프레임 대각선 비율인 이유는 회전(90/270)으로 가로세로가
    바뀌어도 같은 기준이 유지되어야 하기 때문이다.
  - **사람이 동반돼도 면제하지 않는다.** 사람 발치의 기둥/난간이 정확히 그 유형이라(실측
    오탐지 사례) 면제하면 이 게이트의 존재 이유가 사라진다. 대가는 지팡이 사용자가 트랙
    생성 시점부터 멈춰 있으면(재시작·가림 해제 직후) 한 걸음 움직일 때까지 안내가 지연되는 것.

- **사람 동반 필수 조건**(`CameraProfile.require_person_for_trigger`, **기본 True**)은 흰 지팡이가
  항상 사람 손에 들려 있다는 점을 이용한다. `cane_person_assoc.associate_canes()`가 사람과
  짝지어진 지팡이만 통과시켜, 움직임 게이트가 못 막는
  "움직이지만 사람이 없는" 유사물(바람에 흔들리는 나뭇가지 등)을 담당한다. **기본값이 True인
  이유**: Pi의 `camera_config.json`은 rsync 배포 대상이 아니라 필드가 없으면 코드 기본값이
  그대로 적용되므로, 코드에서 켜는 것이 가장 확실하다(기본값은 `camera_config._DEFAULT_REQUIRE_PERSON`
  한 곳에만 두어 dataclass 기본값과 로더 폴백이 어긋나지 않게 한다). 트레이드오프는 사람 탐지
  실패 시 정상 안내를 놓치는 것인데(test person R=0.920), 디바운스 0.5초가 여러 프레임을 보므로
  단발 실패는 흡수된다. 카메라별로 roi_editor UI에서 끌 수 있고, 레거시 단일 카메라는
  `--require-person` / `--no-require-person`.
- **짝짓기 기준은 "두 bbox의 최단거리 ≤ 사람 폭 × 0.15 AND 지팡이 중심 y가 사람 y 범위 안"**
  이다(`cane_person_assoc._matched_pairs`). 이전에는 "사람 bbox를 좌우 15% 확장한 뒤 지팡이
  **중심점**이 그 안인가"였는데, **흰 지팡이는 몸 앞으로 비스듬히 뻗어 짚기 때문에 사람과
  명백히 함께 있어도 중심점이 자주 밖으로 나간다**. PC 실측(사람 1명 + 지팡이 탐지 60프레임)에서
  옛 기준은 19/60만 통과시켜 **ROI 트리거가 한 번도 발동하지 않았고**(게이트 통과 프레임이
  띄엄띄엄해 디바운스 0.5초를 못 채움) 유동인구의 지팡이 사용자도 0명으로 집계됐다. 같은
  표본을 최단거리로 재면 60/60이 거리 0(두 박스가 겹침)이고 중심 y도 60/60이 사람 범위
  안이다 — 판정 축이 아니라 "중심점 하나로 본다"는 방식이 문제였다. 수정 후 통과 프레임이
  20 → 78로 늘고 **연속 40프레임**으로 붙어 트리거가 실제로 발동한다(0회 → 7회).
  - **세로 조건을 남기는 이유**: 빼면 사람 위쪽의 나뭇가지나 아래쪽 난간이 거리만 가까우면
    통과해 이 게이트의 존재 이유가 없어진다. 실측에서 세로 초과는 한 건도 없었다.
  - `max_gap_ratio = 0.15`는 분포에서 맞춘 값이 **아니다**(전부 0이라 맞출 게 없다) — 박스가
    살짝 떨어지는 경우를 위한 여유값이다. 픽셀 절대값이 아니라 사람 폭 대비 비율인 이유는
    원근에 따라 같은 기준이 유지되어야 하기 때문이다.
  - 두 박스가 겹치면 거리가 0이라 **옛 기준으로 통과하던 것은 전부 계속 통과한다**(하위호환).
- **유동인구의 "지팡이 사용자" 판정은 비율에서 래치로 바뀌었다.** 옛 방식
  (`cane_ratio_threshold=0.3`, 사람 트랙 프레임 중 지팡이 동반 비율)은 **트래킹이
  좋아질수록 불리해지는** 구조였다 — 실측에서 v6는 사람 트랙이 805프레임 전체를 살아남아
  분모에 "사람이 멀어 지팡이가 안 잡히는 구간"까지 들어가 23.1%로 미달했고, 트랙이
  522프레임에서 끊긴 v5b는 30.7%로 통과했다. **탐지 품질이 아니라 트랙 길이가 판정을
  갈랐다.** 지금은 `camera_live_pi.py`가 `pedestrian_entity`의 래치를
  `FootTrafficCounter.update(..., cane_user_ids=...)`로 넘긴다. 그 인자를 생략하면 옛 비율
  경로로 떨어지므로 하위호환은 유지된다(단위 테스트가 두 경로를 모두 고정한다).
- **오탐지 핫스팟 → 제외구역 제안**(`fp_hotspots.py`): 카메라가 고정이라 같은 지형지물은 항상 같은
  화면 좌표에 나타난다. 정지 억제로 걸러낸(=배경 오탐지가 거의 확실한) 지팡이 트랙의 위치를
  32×32 그리드 셀로 누적해두고, `roi_editor`가 `/api/fp-hotspots`로 읽어 "여기에 제외구역을
  만드시겠습니까?"라고 제안한다. **자동으로 만들지 않고 사람 확인을 거치는 이유**는 지팡이
  사용자가 늘 같은 지점에서 멈춰 서면 그 위치도 핫스팟으로 잡힐 수 있기 때문이다. 기록은
  **트랙 id당 1회**만 한다 — 매 프레임 sqlite에 쓰면 탐지 루프가 I/O에 막힌다.
- **감지 제외구역**(`ROI.zone_type="exclude"`)은 지형지물(손잡이/점자블록/기둥 등) 오탐지 대응용. 트래킹
  이후가 아니라 **raw detection 단계**(`backend.predict()` 직후, `tracker.update()` 이전)에서 지팡이+사람
  전체 클래스에 필터링한다 — 트래킹 이후 필터링은 EMA 스무딩/coasting 때문에 구역 경계에서 트랙이
  깜빡이는 문제가 있다. 기존 `rois.json`에 `zone_type` 필드가 없으면 `"trigger"`로 기본 처리되어 하위호환.
- **안내 발사는 ROI가 아니라 사람(주체) 단위로 판정한다.** `StandaloneDispatcher`의
  상태 키가 ROI 이름 하나뿐이던 시절에는 반대 방향의 결함이 둘 있었다 — ① A가 안내를
  받은 뒤 쿨다운 안에 B가 ROI를 **통과해 나가면** 이탈 시 디바운스 기준점이 지워져
  B에게 아무것도 안 나갔고(계속 서 있으면 쿨다운이 풀릴 때 나가므로, 완전 누락은
  "쿨다운이 끝나기 전에 벗어나는 경우"다 — 걸어서 지나가는 보행자가 정확히 그 경우),
  ② 머무는 사람에게는 쿨다운마다 같은 안내가 반복됐다. 주체는
  `pedestrian_entity`의 `entity_id`(= 사람 한 명)이며 상태기계는
  `OUT → PENDING → ANNOUNCED`, 이탈은 `exit_grace`(1초) 히스테리시스다.
  - **시간 제한이 두 개이고 역할이 다르다.** `cooldown`(`rois.json`, 기본 10초)은
    **주체별** 재안내 금지, `min_gap`(기본 3초)은 **ROI별** 스팸 방지다. 쿨다운을
    ROI에 걸면 먼저 온 사람이 뒤에 오는 사람의 안내를 잡아먹는다. `min_gap`은
    `cooldown`보다 크지 않게 잘린다(쿨다운을 낮춘 설정이 오히려 둔해지는 역전 방지).
  - **PENDING은 최소간격에 걸려도 버리지 않는다** — 풀리는 즉시, 그 주체가 아직 ROI
    안에 있을 때만 발사한다. 나간 뒤 안내는 소용이 없다.
  - **발사되면 그 순간 ROI 안의 주체를 전부 ANNOUNCED로 표시한다.** 안내는 스피커로
    공간에 나가므로 한 번이면 그 자리의 모두가 듣는다 — 주체별로 쿨다운만 풀면
    3명 동시 진입에 같은 안내가 3번 큐에 쌓인다.
  - **`dispatcher.update()`는 ROI마다 프레임당 정확히 한 번** 불러야 한다. 이탈
    판정이 "이번 프레임에 없었다"에 달려 있어, 같은 ROI를 두 번 부르면 두 번째
    호출이 첫 번째의 주체들을 이탈로 오인한다.
- **트랙 재식별(`SimpleTracker.update(dets, now)`)과 coasting(`max_age`)은 다른 것이다.**
  coasting은 공백 동안 **마지막 박스를 얼려서 계속 내보낸다.** 길게 잡으면 사람이 이미
  지나간 자리에 유령 박스가 남아 ROI를 오판한다 — 실측에서 `max_age`를 10 → 40으로
  올리자 정답 구간 밖 오탐율이 5.1% → 18.2%(test3 conf 0.25), 0.0% → 14.8%(test2 conf
  0.10)로 뛰었다. 재식별은 공백 동안 **아무것도 내보내지 않고** 다시 잡혔을 때 신원만
  잇는다. `now`를 안 넘기면 재식별이 꺼져 동작이 종전과 같다(시뮬레이터 경로).
  - 되살릴 때 `origin_center`·`max_disp`를 물려준다 — 이게 목적이다. 새 트랙으로
    시작하면 움직임 게이트를 처음부터 다시 벌어야 하고 래치·안내 주체도 끊긴다.
    `static_frames`는 0에서 시작한다(죽어 있는 동안 움직였을 수 있어 "정지"를 물려줄
    근거가 없다. 움직임 게이트는 `max_disp`가 그대로라 느슨해지지 않는다).
  - **`PERSON_GRACE_SEC`(2.0초)은 `revive_sec`(2.0초)과 반드시 같이 움직여야 한다.**
    트래커가 사람을 되살릴 수 있는 동안 엔티티가 신원을 기억하지 못하면, 되살아난
    사람에게 새 엔티티가 붙어 재식별로 얻으려던 연속성이 그 자리에서 끊긴다.
- **★ `max_age`는 프레임 단위라 카메라 프레임레이트에 묶인다.** 평가 영상은 24~60 fps인데
  Pi 실측은 12.76 fps라, 같은 `max_age=10`이 test1에서는 0.17초지만 **기기에서는 0.78초**다.
  스윕에서 "20~40이 훨씬 낫다"고 나오는 것은 평가 영상이 빨라서 생기는 착시이며,
  **기기 값은 이미 좋은 구간에 있다.** 이 상수를 만질 때는 반드시 Pi 환산을 먼저 할 것
  (`docs/model_evaluation_report_v3.md` §10-2).
- **래치는 엄격한 1:1, 안내 주체 식별은 느슨한 연관** — 목적이 다르다. 1:1 배정은
  옆 사람이 덩달아 지팡이 사용자로 래치되는 것을 막는 데 꼭 필요하지만, 주체 식별에
  그대로 쓰면 **탐지기가 같은 지팡이를 여러 박스로 내놓을 때** 밀린 박스가 트랙 id로
  폴백해 한 사람이 여러 주체로 쪼개진다(test1 실측: 밀린 1,078프레임이 **전부** 사람
  후보를 갖고 있었고, 한 사람이 15개 주체로 쪼개져 안내가 1회 → 3회). 그래서
  `subject_for_canes()`가 밀린 지팡이도 가까운 짝부터 같은 엔티티에 붙인다.
- **오디오는 큐 기반 순차 재생**(`audio_trigger.AudioPlayer`) — 카메라 여러 대가 하나의 `AudioPlayer`
  인스턴스를 공유해, 거의 동시에 트리거해도 겹쳐 재생(음성 뭉개짐)되지 않고 대기열에 쌓였다가 순서대로
  나온다. 이전 버전은 재생 중 새 요청을 무시(drop)했으나, 여러 카메라 동시 트리거 시 안내가 누락되지
  않도록 큐 방식으로 변경했다.

---

## 서버 연동 (다중 Pi) — 설계 결정

Pi가 여러 대로 흩어지면서 생긴 경로다. **백엔드 기능명세서가 정의하지 않은 Pi 측**이며
서버 구현과 겹치지 않는다 — 명세 §6이 "현재 Pi에는 아웃바운드 HTTP 클라이언트가 없다 …
서버만 구현해서는 동작하지 않는다"고 적어둔 자리다.

- **탐지 루프는 네트워크를 모른다.** `camera_live_pi.py`는 sqlite에 한 줄 쓸 뿐이고
  (`event_logger.queue_event`, `device_metrics.report`), 전송은 `roi_editor`의 백그라운드
  스레드가 맡는다. 안전 기능인 음성 안내가 서버 응답을 기다리는 일이 있어서는 안 된다.
  → **서버 연동을 쓰려면 `visionguide-roi-editor` 서비스를 켜 두어야 한다.**
- **신원의 주인은 서버다.** `device_id`·`api_key`는 서버가 발급해 `POST /api/identity`로
  심는다. 기기가 자기 id를 지어내면 서버 것과 두 체계가 생긴다.
- **이벤트는 보관하고 하트비트는 버린다.** 못 보낸 이벤트는 outbox에 남지만(나중에라도
  올라가야 의미가 있다), 5분 전의 CPU 온도는 쓸모가 없어 다음 주기에 최신값으로 대체한다.
- **`GET /api/device/status`의 응답 스키마를 늘리지 말 것.** 백엔드의 기기 탐색(명세 §12)이
  이 200 응답의 **바디 스키마**로 VisionGuide 여부를 판별한다(캡티브 포털 catch-all 때문에
  "404가 아니면 있음" 식으로는 판별할 수 없다). 카메라 지표는 `/api/metrics`로 따로 낸다.
- **신원이 바뀌면 백오프를 리셋한다.** 잘못된 주소로 실패해 백오프가 최대치까지 늘어난 뒤
  주소를 고쳐도 리셋하지 않으면 최대 2분을 더 기다린다 — 등록 직후 "왜 연동이 안 되지?"가
  되는 지점이라 실제 테스트에서 걸렸다.

## 파이프라인 루프를 건드리면 통합 테스트를 돌릴 것

`tests/test_pipeline_integration.py`가 **프레임 루프를 끝까지 실제로 돌리는 유일한
테스트**다. 탐지 결과·ROI·traffic_db를 모두 붙여 게이트 → 엔티티 → ROI → 안내 →
서버 전송 대기열까지 한 번에 지난다.

**핵심은 `thread_exceptions` 픽스처다.** 파이프라인은 자체 스레드에서 돌고, **스레드에서
터진 예외는 pytest를 실패시키지 않는다** — 콘솔에 Traceback만 찍히고 통과로 잡힌다.
실기기 검증에서 단위 테스트 265개가 전부 통과하는 상태인데도 탐지 루프가 매 프레임
죽고 있었던 것(NameError·UnboundLocalError 각 1건)이 정확히 이 구멍이다. 훅을 걸어
스레드 예외를 전부 실패로 만든다.

**빈 탐지(`predict() -> []`)만 흘려보내는 테스트로는 부족하다** — 게이트·엔티티·지표·
outbox 코드가 한 줄도 실행되지 않는다. 사람과 지팡이가 실제로 **움직이는** 좌표를 줘야
움직임 게이트를 통과해 그 경로가 열린다.

## 게이트 체인은 한 곳에만 있다 (`gate_chain.py`)

같은 판정을 돌려야 하는 곳이 셋이다 — 배포(`device/camera_live_pi.py`), 모델 채택
판정(`tools/eval/eval_video_recall.py`), 화면 검증(`device/replay_engine.py`).
각자 구현을 들고 있으면 **배포가 바뀔 때 나머지가 조용히 어긋나** 평가가 실제와 다른
것을 재고 화면이 실제와 다른 것을 보여준다. `GateChain.step()`이 유일한 구현이고,
세 곳 모두 이것을 호출한다. 게이트를 추가·수정할 때는 반드시 여기만 고칠 것.

- **순서에 의미가 있다.** `정지 억제 → 움직임 게이트 → 엔티티 갱신 → 사람 동반`.
  엔티티 갱신이 움직임 게이트 **뒤**, 사람 동반 게이트 **앞**에 오는 것이 핵심이다 —
  래치의 입력은 앞의 두 게이트를 통과한 지팡이이고 출력은 사람 동반 게이트의
  완화라, 순서를 바꾸면 순환이 생긴다.
- `STATIC_CANE_SUPPRESS_FRAMES`·`MOVED_MIN_DIAG_RATIO`는 여기 있다.
  `camera_live_pi.py`가 재수출하므로 기존 `from camera_live_pi import ...`는 그대로 쓴다.
- `GateResult.roi_targets`는 `(주체, bbox)` 목록이다 — 실제 지팡이 박스와 가상 지팡이
  박스가 같은 목록에 들어가므로, ROI 판정부는 둘을 구분할 필요가 없다.

## 영상 검증 재생 (`replay_engine.py` + roi_editor "검증" 탭)

게이트 동작을 터미널 숫자로만 볼 수 있던 것을 화면으로 옮긴 것이다. **가상 지팡이
박스가 맞는 자리에 그려졌는지 같은 것은 수치로 판단할 수 없고**, ROI를 그려 놓고
"이 영상이면 안내가 나갔을까"를 확인할 방법도 없었다.

- **ROI는 지금 편집 중인 `rois.json`을 그대로 쓴다** — 그려 놓고 바로 확인하는 것이
  이 기능의 목적이다.
- **시각은 영상 시간**(프레임 ÷ fps)이다. 벽시계를 쓰면 디코딩 속도에 따라 래치·
  재식별·쿨다운 판정이 달라져 같은 영상에서 결과가 재현되지 않는다.
- **오디오는 재생하지 않는다.** 기기에서는 실제 안내 서비스가 같은 사운드 장치를
  쓰고 있어 검증 재생이 끼어들면 안 된다. 발사 시점은 이벤트 목록으로 남는다.
- 영상은 `datasets/videos/` 또는 기기의 `~/visionguide/videos/`에서 찾는다.
  경로 탈출을 막으려고 **파일명만** 받는다.
- 추론 백엔드 import는 재생을 시작할 때 한다 — 검증 탭을 쓰지 않는 기기에서
  roi_editor 기동이 무거워지지 않게.

## 보행자 엔티티 (`pedestrian_entity.py`) — 설계 결정

`cane_person_assoc.py`는 스스로 밝히듯 **"이번 한 프레임"만** 본다. 지팡이 트랙과 사람
트랙이 끝까지 별개라서 네 가지가 파생됐다: ① 지팡이가 한 프레임만 가려져도 사람 동반
게이트가 막혀 디바운스가 리셋 ② 밀착한 두 사람 사이에서 지팡이 하나가 양쪽 모두와 짝지어짐
③ 유동인구 비율 판정의 역설(위 절) ④ 지팡이를 놓치면 ROI 판정 자체가 사라짐.

`PedestrianEntity`는 사람+지팡이를 하나로 묶고 상태를 프레임 사이에 유지해 넷을 한꺼번에
닫는다. 근거는 **사람 트랙이 지팡이보다 훨씬 오래 산다**는 비대칭이다(실측 최장 생존
360 vs 121프레임).

- **순환을 피하는 게이트 배치가 핵심이다.** 래치의 **입력**은 정지 억제 + 움직임 게이트를
  통과한 지팡이(`gated_cane_ids`)이고, **출력**은 사람 동반 게이트의 완화다. 사람 동반
  게이트의 결과를 입력으로 쓰면 순환이 생기므로 쓰지 않는다. 부수 효과로 래치가 "이미 두
  게이트를 통과한 지팡이"의 연장이 되어, 배경 오탐지가 래치되는 경로가 닫힌다.
- **사람 트랙이 죽으면 엔티티도 죽는다.** `SimpleTracker`는 `max_age` 뒤 트랙을 제거하고
  **같은 track_id를 부활시키지 않으므로**, 사람 소멸은 곧 영구 소멸이라 유예 기간을 두는
  것이 의미가 없다 — 낡은 사람 bbox에 가상 지팡이를 계속 투영하는 위험만 남는다.
  트래커의 coasting 자체가 이미 유예 기간이다.
- **가상 지팡이 박스는 ROI 판정 코드를 바꾸지 않고 입력만 바꾼다.** 래치 시점의
  "지팡이 ↔ 사람" 상대 오프셋(사람 bbox 크기로 정규화, 지수평활)을 현재 사람 bbox에
  투영한다. 사람 발치를 그냥 쓰지 않는 이유는 흰 지팡이가 몸 앞으로 뻗어 짚어 접지점이
  발보다 앞서기 때문이다 — 발치로 재면 트리거 타이밍이 뒤로 밀린다.
- **★ 이 모듈의 시간 상수는 프레임이 아니라 `초`다.** 기존 게이트 상수
  (`STATIC_CANE_SUPPRESS_FRAMES=24`, `SimpleTracker.max_age=10`)는 프레임 단위라
  촬영 프레임레이트에 묶인다 — 평가 영상은 24~60 fps인데 **Pi 실측은 12.76 fps**라
  같은 "30프레임"이 0.5초에서 2.4초까지 벌어져, **평가에서 고른 값이 실기기로 전이되지
  않는다.** 이 함정은 실제로 값 선택을 바꿨다(`docs/model_evaluation_report_v3.md` §8-5).
- **`VIRTUAL_MAX_SEC`(가상 박스 유지 상한)에는 반드시 상한이 있어야 한다.** 없으면 래치된
  엔티티가 사람 트랙이 사는 내내 가상 박스를 뿜는다 — 실측에서 정답 구간 밖 오탐율이
  0.0% → 39.2%, 헛트리거가 0 → 3으로 뛰었다. 값을 1.0초로 잡은 이유는 **트래커의
  coasting(Pi에서 10프레임 = 0.78초)보다 길어야 기기에서 하는 일이 생기기** 때문이다.
  늘리면 재현율과 오탐율이 **함께** 오른다(스윕 표는 리포트 §8-4, `--entity-virtual-sec`).
- **효과는 전부 가상 박스에서 나온다** — `virtual_max_sec=0`(가상 박스만 끔)이 엔티티를
  완전히 끈 것과 모든 영상·모든 임계값에서 같았다. 즉 ①(프레임 단위 연관 끊김)은 이론상
  존재하지만 실측에서는 거의 발생하지 않는다. 지팡이가 탐지되고 두 게이트를 통과한
  프레임에서는 사람 연관이 거의 항상 성립한다.

## 영상 녹화 (설계 결정)

- **개인정보 정책 예외**: `docs/시각장애인_음성안내시스템_통합_기능명세서_v2.0.md`(대시보드
  설계 문서, 미구현)의 리스크 항목은 "공공장소 영상의 개인정보 보호를 위해 원본 영상 저장 금지,
  BBox/통계만 저장"을 명시하고 있다. `camera_live_pi.py`의 `ClipRecorder`는 이 정책과 정면으로
  상충하지만, **이 저장소의 데모/전시 범위에서는 사용자 승인으로 예외 적용**한다. 실제 상용 배치
  전에는 이 정책을 반드시 재검토해야 한다.
- **녹화는 `camera_live_pi.py`(실제 프레임을 쥐고 있는 프로세스)에서만 구현** — `apps/roi_editor/
  server.py`는 완전히 별도 프로세스이며 카메라 프레임에 직접 접근할 방법이 없다(공유하는 건
  `rois.json`/`camera_config.json`/`foot_traffic.db` 파일뿐). 브라우저는 이미 각 카메라의
  MJPEG 포트에 직접 접속하는 구조(`streamUrlFor(port)` → `http://<host>:<port>/stream.mjpg`,
  `roi_editor`를 거치지 않음)이므로, 녹화 제어(`/recording/start|stop|status|list|clips/*`)도
  `MJPEGServer`의 같은 포트에 라우트를 추가해 브라우저가 직접 호출한다 — 별도 프록시나 HTTP
  클라이언트 의존성이 필요 없다.
- **녹화 대상 프레임은 기본적으로 오버레이가 그려진 최종 프레임**이다(`MJPEGServer.push()`가
  받는 그것). 데모·검토용으로는 그쪽이 쓸모 있다.
  - **학습 데이터로 쓰려면 `?raw=1`로 녹화해야 한다.** 오버레이가 그려진 프레임으로
    학습하면 모델이 **그려진 박스를 단서로 배우는** 오염이 생긴다. raw 모드는 그리기
    이전 프레임을 저장하고, 사이드카에 `"raw": true`를 남긴다(파일만 보고는 구분할 수 없다).
  - **복사 비용은 raw 녹화 중에만 낸다**(`ClipRecorder.wants_raw`) — 1080p 한 장 복사가
    Pi에서 1~2ms다. 화면·스트리밍은 항상 오버레이본이다.
- **저장 위치**: `recordings/<camera_id>/`(레거시 단일카메라는 `recordings/legacy/`).
  `rois.json`/`camera_config.json`과 같은 원칙으로 **git 추적 대상도 rsync 배포 대상도 아니다**
  (`.gitignore`의 `recordings/`) — Pi 로컬에만 쌓이는 산출물이다.
- **저장공간 관리**: `ClipRecorder._enforce_quota()`가 클립 개수(기본 30개)/총 용량(기본 2GB)
  상한 초과 시 카메라별로 가장 오래된 클립(mp4+jpg+json)부터 자동 삭제한다. 실사용 환경에서
  상한값이 적절한지는 Pi 실기기에서 재검토 필요.
- **`cv2.VideoWriter`(`mp4v` fourcc)를 새 의존성 없이 그대로 사용** — `requirements-pi.txt`에
  ffmpeg 파이썬 바인딩이 없어 추가하지 않았다. Pi 실기기(`opencv-python-headless`, ffmpeg 내장
  빌드)에서 `mp4v` 인코딩 확인 완료.
- **`ClipRecorder.write()`/`stop()`은 반드시 같은 락으로 감싸야 한다** — `write()`는 카메라
  파이프라인 스레드에서, `stop()`은 MJPEGServer의 HTTP 핸들러 스레드에서 각각 호출되는데,
  `cv2.VideoWriter.write()`를 락 밖에서 호출하면 `stop()`의 `release()`가 동시에 같은 writer
  객체를 해제해버리는 use-after-release 경쟁으로 세그폴트가 난다(Pi 실기기에서 실제로
  재현되어 `visionguide-device` 프로세스 전체가 죽는 것을 확인 — 두 호출 모두 락 안에서
  수행하도록 수정 후 재검증 완료).

## 최근 감지 이벤트 로그 (설계 결정)

- `detection_events.py`는 ROI 트리거가 실제로 발동한 순간(오디오 안내가 나가는 시점)만
  기록한다 — raw 탐지 프레임마다 기록하면 디바운스/쿨다운 이전 상태까지 전부 쌓여 노이즈가
  된다. `camera_live_pi.py`의 트리거 지점(`dispatcher.on_detected()`가 `True`를 반환하는 곳,
  `audio_player.play()` 호출과 같은 자리)에서만 `log_event()`를 호출한다.
- 기록되는 `class_name`은 항상 `"white_cane"`이다 — ROI 트리거 루프가 `cane_tracks`(지팡이
  클래스만 필터링된 트랙)만 순회하기 때문에, 이 시스템의 실제 동작상 사람 단독 감지로는
  트리거가 발생하지 않는다(의도된 동작).

---

## 데이터셋

- 학습에 실제로 쓰이는 건 `datasets/v1/{train,val,test}/{images,labels}` (`data.yaml`이 참조하는 경로).
  파생 스플릿(`v2`·`v2_nolkc`·`v2_night`)과 나란히 놓이도록 `v1/`로 묶었다.
  `datasets/sources/cane_pool/{images,labels}`는 지팡이 전용 원본 풀(스플릿 전)로, `v1/{train,val,test}`의
  cane_only 이미지 합계와 장수가 일치한다.
- 라벨 형식: `<class_id> <cx> <cy> <w> <h>` (정규화 0~1), class 0 = 흰 지팡이, class 1 = 사람
- **원본 해상도 상한: 지팡이 416×416 / 사람 224×224** (전수 조사). 따라서 **`imgsz > 416`
  학습은 순수 업샘플링**이라 정보 이득 없이 연산만 늘어난다 — 640 학습을 하지 않는 이유다.
  배포 추론에서 해상도를 올리는 것은 효과가 있지만(실측 640에서 탐지율 2.4배), 그건
  "고해상도 학습"이 아니라 **추론 시 객체 픽셀 밀도 확보**(ROI 크롭)로 풀어야 한다.
- **데이터를 추가할 때는 반드시 그룹 단위로 분할할 것** (`resplit_dataset.py`). 파일 단위로
  나누면 (a) Roboflow 증강본(`<stem>_<EXT>.rf.<hash>.jpg`)이 원본과 흩어지고 (b) AIHub
  연속 촬영(`20210514_HHMMSS_*`)의 같은 순간 프레임이 train/test에 걸친다. 실측 누수율은
  각각 **69.4% / 99.7%**였고, 그 상태의 mAP50 0.98은 일반화가 아니라 train에서 본 사진의
  증강본을 다시 맞힌 값이었다. 배정은 층별 md5 해시로 하므로(RNG 체이닝 금지) 층을
  추가·제거해도 나머지 그룹의 split이 바뀌지 않는다.
- **지팡이 데이터셋과 사람 데이터셋은 서로 다른 소스에서 각각 라벨링된 뒤 합쳐졌다**
  (`merge_person_dataset.py` 참고) — 원래는 한 이미지에 지팡이+사람이 동시에 라벨링된 경우가
  거의 없었다(cane_only 이미지에 사람이 찍혀 있어도 사람 라벨 없음). 이 누락은 YOLO 학습 시
  라벨 안 된 사람 영역을 "배경(사람 아님)"으로 잘못 가르치는 오염이다.
  **`resplit_dataset.py --relabel-person`으로 보완 완료**: `datasets/v2` 기준 지팡이+사람이
  동시에 라벨링된 이미지가 **9,228장**이고 cane_only는 426 → **75장**으로 줄었다
  (`docs/model_evaluation_report_v3.md` §1). `apps/label_tool/`은 남은 75장을 수동 검토할 때 쓴다.
- **배경(네거티브) 이미지 228장**이 `datasets/v1/train/`에 `bg_XXXX.jpg` + 빈 라벨로 들어가 있다
  (`prepare_background_dataset.py`가 `datasets/sources/background_photos/`에서 생성). YOLO는 빈 라벨 이미지를 배경으로
  학습해 오탐지를 억제한다. 원본 `datasets/sources/background_photos/`와 변환 스테이징 `datasets/staging/background/`는
  Roboflow 원본과 같은 원칙으로 **커밋 대상이 아니다**(`.gitignore`) — 실제 학습에 쓰이는
  변환본만 `datasets/v1/train/`에 커밋된다. 이 중 40장은 학습에서 제외하고 오탐지 측정 전용
  홀드아웃(`datasets/staging/background/holdout.txt`)으로 쓴다. 배경에 사람이 찍힌 4장은
  `prepare_background_dataset.EXCLUDE`로 제외했다 — 라벨 없이 넣으면 "사람 = 배경"을 가르치게 된다.
- **유사물 네거티브**는 `prepare_lookalike_dataset.py`가 `lk_XXXX.jpg`로 편입한다.
  bg_*.jpg와 달리 **lk_*.jpg 544장은 저장소에 커밋하지 않는다**(`.gitignore`) — 원본이
  공개 데이터셋이라 `fetch_lvis_lookalikes.py`/`fetch_openimages_lookalikes.py`로 언제든
  재수집할 수 있어서다. 대신 clone한 환경에서 그냥 학습하면 유사물 네거티브 없이 학습돼
  v6가 재현되지 않으니, 수집 절차를 `docs/FILE_INVENTORY.md`에서 먼저 확인할 것. 지팡이
  유사물이 "지팡이 아님"으로 라벨링된 사례가 데이터셋에 하나도 없어서 모델이 "가늘고 긴 것 =
  지팡이"만 배웠던 문제를 겨냥한 것이다. `with_person/`(사람이 유사물을 든 사진에 person만
  라벨링)이 `solo/`(빈 라벨)보다 강한 신호다 — "사람 옆의 이 막대는 지팡이가 아니다"를 직접
  대비시키기 때문. **유사물 자체에는 어떤 박스도 그리지 않는다.** person 자동 검출이 실패한
  `with_person` 이미지는 solo로 강등하지 않고 **버린다**(강등하면 라벨 없는 사람을 배경으로
  가르치게 되어, 이 스크립트가 막으려는 오염이 그대로 발생한다).
- **유사물 원본 684장은 공개 데이터셋에서 가져왔다** — LVIS 599장(`fetch_lvis_lookalikes.py`)
  + Open Images `Crutch` 85장(`fetch_openimages_lookalikes.py`). 둘 다 **어노테이션은 색인으로만
  쓰고 박스는 버린다** — 유사물에 박스를 그리지 않는 것이 이 데이터의 본체이고, person 라벨은
  COCO yolov8n으로 새로 붙이기 때문이다(LVIS의 person 라벨은 federated 어노테이션이라 신뢰 불가).
  카테고리 선정에서 뺀 것과 그 이유:
  - **umbrella** — LVIS 우산은 대부분 펼친 우산/파라솔이라 캐노피가 지팡이와 안 닮고 손잡이 축도
    가려져 있다(육안 검수). 접힌 것만 고르는 건 LVIS 라벨로 불가능하다
  - **고정 수직 구조물(pole 등)** — 움직임 게이트가 코드 수준에서 확실히 막으므로, 촬영 시점도
    다른 COCO 기둥 사진 수백 장보다 그쪽이 저렴하고 확실하다
  - **나뭇가지·난간** — LVIS 1,203개·Open Images 601개 어디에도 해당 클래스가 없다. 이 둘은
    학습이 아니라 런타임 게이트(움직임/사람 동반)에 의존한다
- **흰지팡이 혼입 차단**은 두 겹이다. `fetch_lvis_lookalikes.CANE_RISK`가 `walking_cane`/
  `walking_stick`이 함께 라벨된 이미지를 다른 카테고리 수집에서 배제하고, 그 두 카테고리 자체는
  육안 검수해 `lookalike_exclude.txt`(`--exclude-file`)로 3장을 뺐다. 흰지팡이 사진이 네거티브로
  섞이면 "흰지팡이는 지팡이가 아니다"를 가르쳐 정확히 반대 효과가 난다.
- **유사물 홀드아웃 136장은 `--classes 0`과 함께 쓴다** (`datasets/staging/lookalike/holdout.txt`).
  `with_person` 이미지에는 사람이 실제로 찍혀 있어 person 탐지는 오탐지가 아니므로, 지팡이
  클래스만 세면 홀드아웃 전량을 벤치에 쓸 수 있다 — solo만 쓰면 18장으로 줄어 지표가 둔감해진다.
  클래스 필터 없이 쓰려면 `holdout_solo.txt`(18장)가 따로 있다.
- `*.Zone.Identifier` 파일은 Windows에서 복사된 부산물이며 무시하면 됩니다 (`.gitignore`에 등록됨)

---

## 디바이스 개발 명령어

```bash
# PC 의존성 (Python 3.10, Anaconda 가상환경)
conda env create -f environment.yml   # 최초 1회
conda activate visionguide
# 또는 기존 환경에 직접 설치:
# pip install -r requirements.txt

# PC 시뮬레이터 실행
cd apps/simulator && streamlit run app.py

# Pi 전용 카메라 뷰어 (ROI + 오디오 없음)
python device/camera_live_pi.py --source 0 --headless

# Pi 전용 카메라 뷰어 (ROI + MP3 음성 안내)
python device/camera_live_pi.py --roi-config rois.json --headless

# Pi 전용 카메라 뷰어 (다중 카메라 — camera_config.json에 정의된 카메라들을 동시 구동)
python device/camera_live_pi.py --camera-config camera_config.json --headless

# YOLOv8 학습 (PC/GPU 환경)
yolo train cfg=configs/train_v10_nolkc.yaml   # 설정은 yaml로 고정 — imgsz=320(원본 상한 416)

# PT → TFLite INT8 변환
yolo export model=best.pt format=tflite int8=True

# 개별 모듈 단위 테스트
python -m pytest tests/ -v

# systemd 데몬 등록 (PC에서: make install-service). 등록 후 Pi에서:
sudo systemctl status visionguide-device
sudo journalctl -u visionguide-device -f
```

---

## Pi 배포 자동화 (Makefile)

`Makefile`로 PC → Pi 파일 전송과 의존성 설치를 자동화합니다.

**필요**: Git Bash (rsync + ssh 포함) 또는 WSL.
Windows에서 `make` 미설치 시: `scoop install make` 또는 `choco install make`.

```bash
# Pi Python 3.10 환경 일회성 설치 (최초 1회 — pyenv 이용)
make setup-pi-python310

# 전체 배포 (파일 전송 + 의존성 설치)
make deploy

# 코드만 변경된 경우 — 파일만 빠르게 재전송
make sync

# Pi에서 headless 스트리밍 시작 (수동 실행/테스트용)
make run-headless   # 브라우저: http://raspberrypi.local:8080/stream.mjpg

# systemd 등록 — 부팅 시 카메라 앱 + ROI 에디터 완전 자동/headless 구동 (최초 1회)
make install-service

# Pi 연결 및 환경 확인
make ping
```

**서비스 재시작은 반드시 `systemctl`로** — `pkill`로는 되살아나지 않는다. 앱이
SIGTERM을 받아 **정상 종료(exit 0)** 하므로 `Restart=on-failure`가 걸리지 않는다.
`make install-service`가 유닛 4개에 대한 `systemctl start/stop/restart/status`만
비밀번호 없이 허용하는 sudoers(`deploy/visionguide-systemctl.sudoers`)를 함께 설치하므로,
그 뒤로는 `make restart PI="ip1 ip2 ip3"`로 여러 대를 한 번에 재시작할 수 있다.
**`enable`/`disable`은 일부러 넣지 않았다** — 실수로 자동 시작을 꺼버리면 현장에 가야
복구된다.

**임베디드 headless 운영 흐름**: `make install-service` 이후로는 Pi IP 접속이 최초 ROI/오디오 설정(또는 재설정) 시에만 필요합니다.
탐지·음성 안내(`visionguide-device.service`)는 네트워크 연결 여부와 무관하게 기기 단독으로 부팅 시 자동 시작되며,
`roi_editor`(포트 5000, `visionguide-roi-editor.service`)에서 저장한 `rois.json` 변경은 최대 2초 내 재시작 없이 자동 반영됩니다
(`camera_live_pi.py`가 mtime을 폴링). ROI 에디터를 계속 켜두고 싶지 않으면
`ssh <user>@<pi> sudo systemctl disable --now visionguide-roi-editor` 로 끌 수 있습니다.

**IP 주소 지정** (Pi IP가 동적으로 바뀌는 경우):

```bash
# mDNS 호스트명이 작동할 때 (기본값, 대부분의 경우)
make deploy                          # PI=raspberrypi.local 기본값 사용

# IP를 직접 지정할 때
make deploy PI=192.168.0.89
make sync   PI=192.168.0.89

# 현재 Pi IP 확인 (라우터 DHCP 테이블 또는 Pi에서 실행)
#   Pi에서: hostname -I
#   PC에서: arp -a | findstr raspberry  (Windows)

# ★ 기기가 여러 대면 공백으로 나열한다 — 한 대씩 차례로 실행된다
make deploy     PI="192.168.0.101 192.168.0.102 192.168.0.103"
make check-time PI="192.168.0.101 192.168.0.102 192.168.0.103"
```

**다중 호스트는 재귀 호출이다** — 개별 레시피는 한 대만 상대하고, `PI`에 주소가 둘
이상이면 `MULTI_TARGETS`가 한 대씩 다시 `make`를 부른다. 병렬로 돌리지 않는 이유는
한 대가 실패했을 때 출력이 뒤섞여 원인을 못 찾기 때문이고, **한 대가 실패해도 나머지는
계속한다** — 3대 중 1대만 꺼져 있을 때 나머지 배포까지 막을 이유가 없다. 실패한 기기는
마지막에 모아서 보여주고 종료코드로 알린다.

**기기가 여러 대면 시계를 맞춰야 한다.** 이벤트 타임스탬프는 Pi가 찍어 서버로 보내므로
(`event_logger.py`), 시계가 어긋나면 서버에서 순서가 뒤바뀌고 시간대별 통계가 엉킨다.
나중에 카메라 간 핸드오프를 넣으면 "몇 초 전에 저쪽에서 사라졌다"를 비교하게 되는데,
그때는 어긋남이 곧 오작동이다. `make setup-ntp`로 켜고 `make check-time`으로 확인한다
(Raspberry Pi OS는 `systemd-timesyncd`를 내장하므로 보통 이미 켜져 있다).

**배포 대상 파일** — `Makefile` 상단 `DEPLOY_PY` 변수로 관리:

| 변수 | 파일 | 설명 |
|------|------|------|
| `DEPLOY_PY` | `camera_live_pi.py`, `detect.py`, `edgetpu_infer.py`, `audio_trigger.py`, `gpio_controls.py`, `fan_controller.py`, `yolo_postprocess.py`, `simple_tracker.py`, `cane_person_assoc.py`, `pedestrian_entity.py`, `foot_traffic_counter.py`, `camera_config.py` | Pi에 배포할 Python 소스 |
| `DEPLOY_MODEL` | `best_int8.tflite` | TFLite INT8 추론 모델 |

`camera_config.json`(다중 카메라 프로필)과 `rois.json`(ROI/제외구역)은 `rsync` 배포 대상이 아니다 —
Pi 로컬에서 `roi_editor` 웹 UI로 생성/수정하는 런타임 설정 파일이기 때문이다. 새 Pi는
`camera_config.json`이 없으면 기존처럼 `--source`/`--roi-config`/`--port` 단일 카메라 모드로
동작한다 (마이그레이션 불필요).

`apps/roi_editor/` 디렉토리는 별도로 `make sync-roi-editor` (deploy에 포함됨)로 전송됩니다.

새 Python 파일을 Pi에 배포해야 할 때는 `Makefile`의 `DEPLOY_PY`에 추가하세요.

---

## 물리 버튼 / LED (GPIO)

| 기능 | GPIO(BCM) | 물리 핀 | 배선 | 구현 위치 |
|------|-----------|---------|------|-----------|
| 전원(종료) 버튼 | GPIO4 | 7번 (GND: 9번) | 버튼 양단을 GPIO4–GND에 연결 | 커널 기능, 코드 없음 |
| Wi-Fi 전환 버튼 | GPIO17 | 11번 (GND: 9번) | 버튼 양단을 GPIO17–GND, 내부 풀업 사용(외부 저항 불필요) | `gpio_controls.py` |
| Wi-Fi 모드 상태 LED | GPIO24 | 18번 (GND: 아무 GND 핀) | GPIO24 → 저항(220~330Ω) → LED → GND | `gpio_controls.py` |
| Wi-Fi 전환 부저 | GPIO25 | 22번 (GND: 아무 GND 핀) | GPIO25 → 부저(+), 부저(-) → GND (액티브 부저 가정) | `gpio_controls.py` |
| 동작 확인 LED | GPIO27 | 13번 (GND: 아무 GND 핀) | GPIO27 → 저항(220~330Ω) → LED → GND | `camera_live_pi.py --status-led 27` |
| 냉각팬 | GPIO22 | 15번 | GPIO22 → 트랜지스터/MOSFET 베이스·게이트(1kΩ 저항) → 팬(+: 5V, 플라이백 다이오드 필수) | `fan_controller.py` |

**전원 버튼**은 라즈베리파이 OS 공식 기능이라 코드가 필요 없습니다. `/boot/config.txt`(Bookworm 이후는 `/boot/firmware/config.txt`)에
`dtoverlay=gpio-shutdown,gpio_pin=4` 한 줄을 추가하고 재부팅하면 됩니다 — 기본 GPIO(GPIO3, 물리 5번 핀)는 이
Pi에서 **PoE 어댑터가 물리 핀 1~6번을 점유**하고 있어 쓸 수 없으므로, `gpio_pin=4` 파라미터로 GPIO4(물리 7번 핀)를
대신 사용하도록 지정했다. **부팅 설정 파일을 건드리는 작업이라 원격에서 잘못 적용하면 복구가 번거로울 수 있으므로
Makefile로 자동화하지 않고 수동으로 적용하는 것을 권장합니다.** 짧게 누르면 안전 종료됩니다.
**단, 완전히 꺼진 상태에서 버튼을 다시 눌러도 재부팅되지 않습니다** — `gpio-shutdown` 오버레이의
"halt 상태에서 깨우기" 기능은 GPIO3(물리 5번 핀)에만 있는 하드웨어 특성이라(SoC의 상시 전원 도메인이
그 핀만 감시함), `gpio_pin=4`처럼 다른 핀을 지정하면 종료 감지는 되지만 깨우기는 불가능해진다(공식
오버레이 문서에 명시된 제약). 이 Pi는 PoE 어댑터가 GPIO3를 막고 있어 애초에 선택지가 없었다 — 즉 종료
기능을 얻는 대신 버튼으로 다시 켜는 기능은 포기한 것이다. 완전 종료 후 다시 켜려면 전원 자체를
껐다 켜야 한다(PoE 인젝터 스위치 또는 케이블 재연결).

**Wi-Fi 전환 버튼**은 이 Pi의 무선 칩/드라이버가 진짜 동시(AP+STA) 모드를 지원하지 않는 것으로 실측 확인되어
(홈 Wi-Fi `204_WIFI`와 자체 핫스팟 `VisionGuide-AP`가 wlan0 하나를 두고 경합, 항상 한쪽만 활성화됨) 만든
기능입니다. 버튼을 누르면 `gpio_controls.py`가 로컬에서 `nmcli connection up`으로 두 프로파일을 번갈아
전환합니다 — SSH 등 원격에서 같은 작업을 하면 전환 도중 그 연결 자체가 끊길 위험이 있지만, 이 방식은 Pi
로컬에서 D-Bus로 NetworkManager를 직접 호출하므로 그런 위험이 없습니다. 상태 LED(GPIO24)가 켜지면
핫스팟, 꺼지면 홈 Wi-Fi 모드이며, 부저(GPIO25)가 전환 시 1회(홈 Wi-Fi)/2회(핫스팟)/3회(실패)로 소리를 냅니다.
이전에 있던 "SW 재시작 버튼"(5초 홀드로 `visionguide-device` 재시작) 기능은 이 버튼에서 제거되었습니다 —
물리 버튼으로 SW를 재시작할 방법이 다시 필요하면 별도 GPIO 핀에 추가해야 합니다.

**냉각팬**은 신호선 없는 순수 2선(+/-) DC 모터(에듀이노 스마트홈 키트 팬, 정격 12V이나 5V 구동 확인됨)라
GPIO에 직접 연결할 수 없어 트랜지스터/MOSFET 스위치로 on/off만 한다. 온도 로직 없이 부팅과 함께 켜져서 상시
가동되며, `systemctl stop`(종료 시 자동 호출)에서 SIGTERM을 받아 팬을 끄고 종료한다 — 그래서 전원 버튼으로
종료해도 팬이 같이 꺼진다. 팬을 5V/GND에 직결하면 보드 대기전력 때문에 종료해도 안 꺼지므로 반드시 이 GPIO
스위칭 방식을 거쳐야 한다.

**동작 확인 LED**는 `camera_live_pi.py`의 탐지 루프가 프레임을 처리할 때마다 토글되는 하트비트입니다. 정상 동작 중엔
빠르게 깜빡이고, 루프가 멈추면(예: 추론 행/크래시) LED도 같이 멈추므로 모니터 없이도 "탐지 SW가 살아있는지"를
눈으로 확인할 수 있습니다.

`make install-service` 한 번으로 세 systemd 유닛(`visionguide-device`, `visionguide-roi-editor`, `visionguide-controls`)이
모두 설치/활성화됩니다.

---

## Pi 호환성 코딩 지침

Pi에서 실행될 코드를 작성하거나 수정할 때 반드시 지켜야 할 규칙입니다.

### 파일 쌍 유지

| PC 버전 | Pi 버전 | 관계 |
|---------|---------|------|
| `camera_live.py` | `camera_live_pi.py` | 동일 기능, 추론 백엔드·카메라 소스만 다름 |
| `detect.py` | — | Pi에서는 TFLite가 우선; `detect.py`는 PyTorch fallback 전용 |

`camera_live.py`에 새 기능(ROI 오버레이, 통계 표시 등)을 추가하면 `camera_live_pi.py`에도 반영하세요.

### Pi 코드 경로에서 금지

- `torch` / `torchvision` / `ultralytics` 직접 import → TFLite 백엔드 우선, PyTorch는 마지막 fallback
- `cv2.imshow` 단독 사용 → `--headless` MJPEG 경로도 항상 함께 지원
- `cuda` 하드코딩 → `device` 파라미터로 추상화

### 모델 가중치 변경 시 체크리스트

1. `yolo export model=best.pt format=tflite int8=True` 로 TFLite 재생성
2. `runs/white_cane_v1-2/weights/best_int8.tflite` 교체
3. `camera_live_pi.py`의 `_postprocess` 출력 형상 확인 (`[1,5,8400]` vs `[1,8400,5]`)
4. `make sync` 로 Pi에 재배포

### 의존성 추가 시

- `requirements.txt` (PC용) 와 `requirements-pi.txt` (Pi용) 모두 업데이트
- Pi에 설치하지 않는 패키지: `torch`, `torchvision`, `ultralytics` (무거움)
- Pi 전용 패키지: `tflite-runtime`, `picamera2` (apt), `gpiozero`, `RPi.GPIO`

---

## 대시보드 백엔드 개발 명령어

```bash
cd visionguide-backend

# 의존성 설치
pip install -e ".[dev]"          # pyproject.toml 기준
# 또는
pip install fastapi uvicorn sqlalchemy pydantic-settings passlib[bcrypt] \
            python-jose httpx loguru shapely

# 개발 서버 실행
uvicorn app.main:app --reload --port 8000

# DB 초기화 (관리자 계정 시드)
python -m app.db.init_db

# 테스트 실행
pytest tests/ -v
pytest tests/test_auth.py -v      # 특정 파일만

# Docker Compose
docker-compose up --build
```

---

## 대시보드 프론트엔드 개발 명령어

```bash
cd visionguide-frontend

npm install
npm run dev          # Vite 개발 서버
npm run build        # 프로덕션 빌드
npm run lint         # ESLint
npm run typecheck    # tsc --noEmit
```

---

## 시스템 아키텍처 핵심 흐름

```
Pi Camera → YOLOv8n(TFLite INT8) → SORT 추적 → ROI Point-in-Polygon
  → 디바운싱(0.5s) + 쿨다운(10s) → GPIO 릴레이 / TTS 재생
  → POST /api/events/ingest (API Key 인증) → FastAPI → SQLite + WebSocket 푸시
  → React 대시보드 (실시간 이벤트 + 통계 + MJPEG 영상)
```

**설정 동기화**: 디바이스가 60초마다 `GET /api/devices/me/config` (ETag 비교)를 폴링하여 ROI·음성 매핑·쿨다운을 핫리로드합니다.

**영상 전송**: 디바이스 `:8080/stream.mjpg` → FastAPI MJPEG 프록시 → 브라우저 `<img>` 태그 (동시 5명 제한).

---

## 인증 구조

- **관리자**: JWT Bearer (24h 유효, jti 블랙리스트 로그아웃)
- **디바이스**: `X-API-Key` 헤더 (sha256 해시를 DB에 저장, 등록 시 1회만 평문 노출)

---

## 디바이스 핵심 모듈

| 파일 | 역할 | 상태 |
|------|------|------|
| `camera_live_pi.py` | Pi Camera/OpenCV 추상화 + 추론 + 추적 + MJPEG 송출 (`CameraPipeline` 다중 카메라 지원) | ✅ 구현됨 |
| `camera_config.py` | 다중 카메라 프로필 로드/저장/검증 (`camera_config.json`) | ✅ 구현됨 |
| `audio_trigger.py` | `StandaloneDispatcher` (디바운싱/쿨다운) + `AudioPlayer` (큐 기반 순차 재생 MP3) | ✅ 구현됨 |
| `apps/simulator/roi_manager.py` | Shapely 기반 ROI Point-in-Polygon 판별 | ✅ 구현됨 |
| `apps/simulator/trigger_dispatcher.py` | Streamlit 전용 디바운싱/쿨다운 (시뮬레이터용) | ✅ 구현됨 |
| `preprocess.py` | Letterbox 리사이즈 + CLAHE 야간 보정 | 미구현 (예정) |
| `priority_policy.py` | 다중 ROI 동시 점유 시 heapq 우선순위 | 미구현 (예정) |
| `config_syncer.py` | 60초 폴링, atomic config 교체, 핫리로드 | 미구현 (예정) |
| `event_logger.py` | 로컬 SQLite 버퍼 → 비동기 서버 전송 | 미구현 (예정) |
| `watchdog.py` | psutil CPU/온도/디스크, 픽셀 분산으로 렌즈 오염 탐지 | 미구현 (예정) |

---

## 모델 채택 기준 (v3에서 개정)

1. **1차 기준은 실영상 지표**(`eval_video_recall.py`)다. 이 시스템이 최적화해야 하는 것은
   "정지 이미지에서 지팡이를 찾기"가 아니라 **"영상에서 지팡이를 끊기지 않고 따라가 ROI
   트리거를 발동시키기"**이고, 이 지표만이 **split 변경과 무관한 공통 잣대**이기 때문이다.
   배포와 같은 코드 경로(같은 백엔드·게이트 상수·연관 로직)로 재므로 배포 성능을 직접 반영한다.

2. **정지 이미지 지표는 같은 split에서 잰 값끼리만 비교한다.** v1~v8(누수된 split)의
   mAP50 0.98과 v9 이후(`datasets/v2`)의 0.94를 나란히 놓으면 안 된다 — 모델의 우열이
   아니라 데이터셋의 차이다. 리포트에는 어느 split에서 잰 값인지 반드시 병기할 것.

3. **정지 이미지 1위가 배포 1위가 아니다 — 실측.** 백본 3종 공정 비교에서 yolo11n이
   정지 이미지 cane R·mAP50·야간 mAP 전부 1위(0.929 / 0.959 / 0.918)였는데 실영상은
   yolov8n의 68%였다(399 vs 589). 정지 이미지 지표만 봤다면 배포에서 더 나쁜 모델을
   골랐을 것이다. 차이는 **트랙 지속성**에서 온다(최장 생존 360 vs 121프레임) — 트리거는
   프레임 단위 정확도가 아니라 끊기지 않는 연속성이 가르는데, mAP는 그 축을 재지 않는다.

4. **양자화는 confidence 스케일을 옮기므로 conf 한 점으로 모델을 비교하지 말 것.**
   실측에서 yolo26n은 INT8 후 실영상 탐지가 284 → 432로 "올랐지만" mAP50은 오히려
   0.944 → 0.938로 내려갔다 — operating point 이동 아티팩트다. `eval_video_recall.py`는
   추론을 한 번만 하고 임계값별로 게이트만 재실행하므로 `--thresholds 0.10 0.25 0.40 0.55`
   스윕이 거의 공짜다. 백본 비교에는 스윕을 쓸 것.

5. **실영상 지표의 표본 수를 반드시 병기한다.** 현재 깨끗한 평가 영상은 `test1.mp4`
   805프레임 1개뿐이라, 수십 프레임 규모의 차이는 신호가 아니라 노이즈로 취급한다
   (`docs/model_evaluation_report_v3.md` §3-2에 부호가 뒤집힌 실례가 있다).

6. **INT8 두 경로(LiteRT / onnx2tf full-integer)의 우열은 모델마다 뒤집힌다** —
   v3는 full-integer, v5b는 LiteRT, v10은 다시 full-integer가 우세했다. 어느 한쪽이
   항상 낫다고 가정하지 말고 모델마다 다시 잴 것. calibration은 반드시
   `split=train fraction=0.1`(val을 쓰면 recall이 급락하는 아티팩트가 있다).

## AI 모델 KPI

| 지표 | 목표값 |
|------|--------|
| mAP@0.5 (낮) | ≥ 0.85 |
| mAP@0.5 (야간) | ≥ 0.75 |
| FPR | ≤ 5% |
| TFLite INT8 FPS (라파) | ≥ 10 |
| 모델 크기 | ≤ 10 MB |
| mAP 손실 (양자화) | ≤ 5% |

---

## DB 스키마 요약

SQLite (`data/visionguide.db`). 주요 테이블:

- `users` — 단일 관리자, bcrypt 해싱, 로그인 실패 카운터 + 잠금
- `devices` — 시리얼 UNIQUE, `api_key_hash`, `config_etag`, `last_seen`
- `rois` — `polygon` JSON (정규화 0~1 좌표), `priority`, `is_active`
- `announcement_mappings` — ROI당 1행, `audio_url` 또는 `text` 중 하나 필수
- `detection_events` — 타입: `DETECTION / ANNOUNCEMENT / OFFLINE`, 90일 초과 조회 불가
- `hourly_stats` — UNIQUE(device_id, roi_id, hour), 통계 집계용 사전 집계 테이블

---

## 중요 설계 결정 사항

- ROI 폴리곤 좌표는 항상 **정규화 0~1** 범위로 저장/전송합니다. 렌더링 시 캔버스 크기에 맞게 스케일링하세요.
- 폴리곤 유효성 최종 검증은 **백엔드 Shapely**에서 수행합니다 (프론트에서는 점 3개 미만만 차단).
- 디바이스 이벤트 ingest는 **분당 600건** 초과 시 429 응답합니다.
- 통계 API 조회 범위: hourly ≤ 90일, daily ≤ 1년, summary ≤ 31일.
- MJPEG 프록시는 **동시 5명** 초과 시 신규 연결을 거부합니다.
