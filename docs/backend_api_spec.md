# VisionGuide 백엔드 기능 명세서 (visionguide-backend)

> **작성 목적**: 관리자 대시보드 프론트엔드(`dashboard/frontend/`)가 필요로 하는 모든 백엔드 기능을 구체적으로 기술한다.  
> **구현 상태**: `dashboard/backend/`에 FastAPI 서버가 구현되어 있고, `dashboard/frontend/`가 이 API를 호출한다.
> **분석 근거**: `dashboard/frontend/src/` 전체 코드 분석 (타입 정의, 목 데이터, UI 흐름)
> — 아래 스키마 중 Pi 런타임(`device/`, `apps/roi_editor/`)과 맞닿는 부분은 실제 코드
> 기준으로 교정돼 있다. 운영 전 설정과 보안 키는 `.env`에서 지정해야 한다.

---

## 1. 개요

### 1.1 기술 스택 (권장)

| 항목 | 선택 |
|------|------|
| 프레임워크 | FastAPI |
| 데이터베이스 | SQLite (`data/visionguide.db`) |
| ORM | SQLAlchemy (동기 또는 `asyncio` 버전) |
| 인증 | JWT (Bearer) + API Key (디바이스용) |
| 실시간 | WebSocket (FastAPI 내장) |
| 비밀번호 해싱 | `passlib[bcrypt]` |
| 설정 관리 | `pydantic-settings` |

### 1.2 서버 포트

- **백엔드 API**: `8000`
- **CORS 허용 Origin**: `http://localhost:5173` (Vite 개발 서버), 배포 도메인

### 1.3 아키텍처 제약 사항 (Constraints)

> [!WARNING]
> **Single Worker 전용 구조 (Scale-out 금지)**
> 본 백엔드 서버는 `_heartbeat_buffer` 인메모리 버퍼와 `APScheduler` 인스턴스를 통해 무결성을 유지하고 있습니다. `uvicorn --workers N` 옵션이나 다중 서버 인스턴스로 실행할 경우, 1) 버퍼 파편화로 인한 SQLite 쓰기 불일치 발생, 2) 동일한 재부팅 명령이 N번 중복 스케줄링되는 치명적 부작용이 발생합니다. 반드시 단일 워커(Single Worker) 환경에서 구동해야 합니다.

### 1.3.1 구현하며 확정된 사항 (2026-09-23)

명세와 구현이 어긋났던 항목을 여기서 정리한다. **구현이 정본**이며 아래가 그 기록이다.

| 항목 | 결정 | 이유 |
|---|---|---|
| `If-Match` 헤더 | **변경 API에서 필수** | 낙관적 잠금. 이 기기는 AP 모드·캡티브 포털로 **오프라인 현장 설정**을 전제하므로, 대시보드가 낡은 스냅샷으로 현장 편집을 덮는 상황이 실제로 생긴다. Pi의 변경 감지는 mtime뿐이라 병합도 버전 비교도 없이 나중에 쓴 쪽이 이긴다 |
| `Roi.color` | **서버 DB 전용 필드로 유지** | 캔버스에서 ROI를 구분해 그리려면 색이 필요하다. **Pi로는 보내지 않는다** — Pi의 ROI 로더는 자기 팔레트를 쓴다 |
| `Camera.fps` | **서버 보관만** (§4대로) | Pi의 `CameraProfile`에 대응 필드가 없다. 응답에 `fps_applied: false`를 함께 내려 UI가 "미적용"으로 표시한다. Pi 4의 병목은 프레임레이트가 아니라 추론이라 `capture_preset`·`model_variant`가 더 직접적인 손잡이다 |
| 오디오 길이 제한 | **10초 유지** | 안내 음성은 짧아야 한다. 근거가 약하면 `MAX_DURATION_SECONDS` 한 곳만 고치면 된다 |
| `config_etag` 초기값 | **빈 문자열 금지** (UUID로 생성) | `''`이면 클라이언트가 If-Match에 보낼 값이 없어 **첫 ROI 저장부터 400**이 난다 |
| 기기 제어 인증 | Pi의 identity 재주입·`POST /api/service/restart`·`/api/system/reboot`에 **`X-Device-Key` 필요** | 포트 5000의 검색·일반 설정 라우트는 LAN 호환성을 위해 무인증으로 남긴다. 미등록 Pi의 identity bootstrap은 네트워크 검색 후 바로 허용하고, 등록 후 재주입과 제어 요청은 기존 키를 요구한다. 제어 키는 서버가 Pi를 호출할 때 필요하므로 `devices.control_key`에 **평문**으로 보관한다 — 방향이 반대라서 생기는 비대칭이다(`api_key_hash`는 검증만 하면 되므로 해시) |

### 1.4 전역 응답 형식

```json
// 성공 (단건)
{ "data": { ... }, "ok": true }

// 성공 (목록)
{ "data": [...], "total": N, "ok": true }

// 실패
{ "error": "ERROR_CODE", "message": "설명", "ok": false }
```

---

## 2. 인증 (Authentication)

### 2.1 관리자 인증 — JWT

#### `POST /api/auth/login`
관리자 로그인. 성공 시 JWT Access Token 발급.

**Request Body**
```json
{ "username": "admin", "password": "string" }
```

**Response 200**
```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 86400
}
```

**Response 401**
```json
{ "error": "INVALID_CREDENTIALS", "message": "아이디 또는 비밀번호가 올바르지 않습니다." }
```

**Response 423**
```json
{ "error": "ACCOUNT_LOCKED", "message": "로그인 실패 5회 초과. 30분 후 다시 시도하세요." }
```

**비고**:
- 비밀번호 실패 횟수를 `users.login_fail_count`에 누적, 5회 초과 시 계정 잠금
- 잠금 해제는 30분 경과 또는 DB 수동 초기화
- JWT payload에 `jti` (UUID) 포함 — 로그아웃 시 블랙리스트 활용

#### `POST /api/auth/logout`
현재 토큰을 무효화.

**Headers**: `Authorization: Bearer <token>`

**Response 204** (No Content)

**비고**: `jti`를 `token_blacklist` 테이블에 저장, 만료 시각 기록

#### `GET /api/auth/me`
현재 로그인한 관리자 정보 반환.

**Headers**: `Authorization: Bearer <token>`

**Response 200**
```json
{
  "id": 1,
  "username": "admin",
  "display_name": "Admin_Ops",
  "team": "보행안전 통합팀"
}
```

### 2.2 디바이스 인증 — API Key

라즈베리 파이 디바이스가 이벤트 ingest 등에 사용하는 인증 방식.

- 헤더: `X-API-Key: <raw_key>`
- DB 저장 형식: `sha256(raw_key)` (평문 미저장)
- 디바이스 등록 시 1회만 평문 노출

---

## 3. 디바이스 관리 (Device)

### 데이터 모델

```
Device {
  id: string           // 예: "cam-entrance-01"
  name: string         // 표시명
  ip: string           // Pi IP 주소
  location: string     // 설치 위치 (예: "정문 입구")
  status: enum         // online | offline | warning | unknown
  api_key_hash: string // sha256 해시
  config_etag: string  // 설정 변경 감지용 ETag
  last_seen: datetime
  created_at: datetime
}
```

**비고 — Pi 런타임과의 대응**:
- `cameras[].id`는 **문자열**이다 (`"dev-cam0"` 등). 자세한 내용은 §4 참고.
- 응답에 포함되는 `cameras` · `rois`는 **Pi에서 읽어온 스냅샷**이다 — 설정의 원본은 Pi이며
  서버 DB는 캐시다(§13.0).
- 아래 응답 예시의 `cpu` · `temperature` · `memory` · `latency` · `npu_ms`는 대시보드
  표시용 형태다. Pi가 실제로 수집하는 원본 필드·단위는 §3의 마지막 절
  (`GET /api/devices/{device_id}/status`) 비고를 따른다 — 특히 **CPU 사용률(%)과
  `latency` · `npu_ms`는 현재 Pi가 산출하지 않는다.**

### `GET /api/devices`
모든 디바이스 목록 반환.

**Headers**: `Authorization: Bearer <token>`

**Query Parameters**
- `search?: string` — 이름·IP·위치 부분 검색

**Response 200**
```json
{
  "data": [
    {
      "id": "cam-entrance-01",
      "name": "정문 카메라",
      "ip": "192.168.1.101",
      "location": "정문 입구",
      "status": "online",
      "last_seen": "2026-09-16T14:32:00Z",
      "cameras": [ { "id": "dev-cam0", "port": 8080 }, { "id": "dev-cam1", "port": 8081 } ],
      "today_detections": 42,
      "cpu": 38.5,
      "temperature": 51.2,
      "memory": { "used": 1.2, "total": 4.0 },
      "uptime": "3일 14시간 22분",
      "latency": 11,
      "npu_ms": 48
    }
  ],
  "total": 6,
  "ok": true
}
```

### `POST /api/devices`
새 디바이스 등록.

**Headers**: `Authorization: Bearer <token>`

**Request Body**
```json
{
  "id": "cam-entrance-01",
  "name": "정문 카메라",
  "ip": "192.168.1.101",
  "location": "정문 입구"
}
```

**Response 201**
```json
{
  "data": {
    "id": "cam-entrance-01",
    "api_key": "vg_rawkey_xxxx"
  },
  "ok": true
}
```

**비고**: `api_key`는 등록 응답에서만 1회 평문 노출. 이후 재조회 불가.

### `GET /api/devices/{device_id}`
단일 디바이스 상세 정보. 카메라 목록, ROI 목록, 최근 이벤트 포함.

**Headers**: `Authorization: Bearer <token>`

**Response 200**
```json
{
  "data": {
    "id": "cam-entrance-01",
    "name": "정문 카메라",
    "ip": "192.168.1.101",
    "location": "정문 입구",
    "status": "online",
    "cpu": 38.5,
    "temperature": 51.2,
    "memory": { "used": 1.2, "total": 4.0 },
    "uptime": "3일 14시간 22분",
    "latency": 11,
    "npu_ms": 48,
    "last_seen": "2026-09-16T14:32:00Z",
    "cameras": [
      {
        "id": "dev-cam0",
        "port": 8080,
        "capture_preset": "640x480",
        "fps": 20,
        "roi_count": 3,
        "today_detections": 27,
        "is_streaming": true,
        "current_alert": null
      }
    ],
    "rois": [
      {
        "id": 1,
        "name": "정문 진입 구역",
        "zone_type": "trigger",
        "priority": 1,
        "announcement_text": "정문 입구입니다. 전방에 계단이 있습니다.",
        "audio_file": "entrance.mp3",
        "is_active": true
      }
    ],
    "recent_events": [
      {
        "id": 1,
        "time": "14:32:07",
        "camera": "카메라 0",
        "roi": "정문 진입 구역",
        "confidence": 0.94,
        "timestamp": "2026-09-16T14:32:07Z"
      }
    ]
  },
  "ok": true
}
```

### `PATCH /api/devices/{device_id}`
디바이스 메타데이터 수정.

**Headers**: `Authorization: Bearer <token>`

**Request Body** (부분 업데이트 가능)
```json
{ "name": "정문 카메라 (수정)", "location": "정문 우측 입구" }
```

**Response 200**: 수정된 Device 객체

### `DELETE /api/devices/{device_id}`
디바이스 삭제. 연관 카메라·ROI·이벤트 모두 CASCADE 삭제.

**Headers**: `Authorization: Bearer <token>`

**Response 204**

### `GET /api/devices/{device_id}/status`
디바이스 실시간 시스템 상태 (Pi의 `/proc`, `/sys`에서 수집된 값).

**Headers**: `Authorization: Bearer <token>`

**Response 200**
```json
{
  "data": {
    "status": "online",
    "load_avg": [0.82, 0.74, 0.69],
    "cpu_temp_c": 51.2,
    "memory": { "used_mb": 1228.8, "total_mb": 4096.0 },
    "uptime_seconds": 302542,
    "uptime_human": "3일 14시간 22분",
    "latency_ms": 11,
    "npu_ms": 48,
    "last_seen": "2026-09-16T14:32:00Z"
  },
  "ok": true
}
```

**비고**:
- Pi가 주기적으로 `PATCH /api/devices/me/heartbeat`로 밀어올린 값을 캐싱해서 반환.
- 필드명·단위는 Pi가 실제로 수집하는 값(`apps/roi_editor/server.py`의 `_read_device_status()`)에
  맞췄다 — `cpu_temp_c`(℃), `memory.used_mb`/`total_mb`(**MB 단위**, GB 아님),
  `load_avg`(1·5·15분 3-tuple).
- **CPU 사용률(%)은 이제 Pi가 산출한다** (`device/device_status.py`의 `CpuSampler`가
  `/proc/stat` 두 시점 차분을 낸다). psutil은 여전히 쓰지 않는다. 하트비트의 `cpu_percent`로
  올라오고 서버는 `device_status_cache.cpu_percent`에 보관한다.
  **첫 표본에서는 `null`이다** — 차분에 두 시점이 필요하기 때문이다.
- `latency_ms`(프레임 처리 시간) · `npu_ms`(추론 시간)도 Pi가 보낸다
  (`device/device_metrics.py` → 하트비트). 카메라가 여러 대면 **가장 느린 쪽**을 보고한다 —
  평균을 내면 한 대가 막혀 있어도 정상으로 보인다.
- **이 세 값은 모두 `null`일 수 있다.** 센서 읽기 실패, 살아있는 카메라 없음, 값이 0인 경우가
  전부 `null`로 온다. 백엔드 스키마는 반드시 Optional이어야 한다 — 필수로 두면 422가 나고,
  Pi는 하트비트를 버퍼링하지 않으므로 그 주기의 상태가 사라진다.
- Pi가 아닌 환경(개발 PC 등)에서 실행하면 해당 항목만 조용히 `null`로 빠진다.

---

## 4. 카메라 설정 (Camera)

### 데이터 모델

```
Camera {
  id: string             // 카메라 프로필 id — 예: "dev-cam0" (정수 아님)
  device_id: string      // FK
  port: int              // MJPEG 스트림 포트 (8080, 8081 등)
  capture_preset: string // "auto"(기본) | "320x180" | "320x240" | "480x270"
                         // | "480x360" | "480x480" | "640x360" | "640x480"
                         // | "848x480" | "800x600" | "960x540"
  fps: int               // 10 | 15 | 20 | 25 | 30
  model_variant: string  // "v2_640" | "v3_320" | "v4_320" | "v5b_320"
                         // | "v6_320" | "v10_320"(기본·권장) | "v11_yolo26n_320"
  rotation: int          // 0 | 90 | 180 | 270
  require_person: bool   // 보행자 동반 필수 조건 (기본 true)
  is_active: bool
}
```

**비고 — Pi 런타임(`device/camera_config.py`의 `CameraProfile`)과의 대응**:

- **이 절의 조회/변경은 Pi의 `roi_editor` API(포트 5000)를 중계해 구현한다**(§13.0).
  서버 테이블은 캐시다.
- **`id`는 문자열이다.** Pi의 `camera_config.json`, `roi_editor`의 `?camera=<id>` 쿼리,
  ROI 파일 분리(`rois.<camera_id>.json`)가 모두 문자열 id를 쓴다.
- **`id`의 문자셋은 백엔드가 `^[A-Za-z0-9_-]{1,32}$`로 제한한다.** id가 URL 경로 세그먼트
  (`/cameras/{camera_id}/stream`)와 파일명(`rois.<id>.json`)에 그대로 들어가는데, **Pi의
  `validate_camera_config()`는 id 중복만 검사하고 문자 구성은 검사하지 않는다** — 공백이나
  `/`가 섞인 id가 저장되면 경로가 깨진다. 검증 지점은 두 곳이다:
  - **경로 파라미터로 들어온 `camera_id`** — 패턴 불일치면 Pi에 전달하지 않고 400으로 끊는다.
    (Pi는 프로필 목록과 대조해 매칭하므로 경로 조작 위험 자체는 낮지만, URL 인코딩 문제를
    원천 차단한다.)
  - **Pi에 카메라 프로필을 쓸 때의 `id`**(§13.2) — 저장 전에 검증한다.
  - 현재 운용 중인 id(`dev-cam0` · `dev-cam1`)는 이 패턴을 만족하므로 마이그레이션은 없다.
- **`capture_preset`은 자유로운 해상도 문자열이 아니라 `CAPTURE_PRESETS`의 키다.**
  Full HD 이상 프리셋은 Pi 4에서 캡처 단계가 병목이 되어 의도적으로 없다. 목록 밖의 값은
  `validate_camera_config()`가 저장 단계에서 거부한다.
- **`model_variant`도 `MODEL_VARIANTS`의 키여야 한다.** 목록의 단일 출처는 Pi이며
  `GET /api/model-variants`(roi_editor, 포트 5000)로 노출된다 — 백엔드가 목록을
  하드코딩하면 모델 추가 시 값이 어긋난다.
- **`require_person`의 Pi 측 필드명은 `require_person_for_trigger`이고 기본값은 `true`다.**
  Pi의 `camera_config.json`은 배포(rsync) 대상이 아니라 필드가 없으면 코드 기본값이
  그대로 적용되므로, 서버가 `false`를 기본으로 내려보내면 지팡이 트리거 게이트 하나가
  조용히 꺼진다.
- **`fps`는 구현 예정 필드다.** 현재 `CameraProfile`에 대응 필드가 없어 Pi는 FPS를 설정하지
  않고 측정만 한다(서버는 값을 보관만 한다). 적용하려면 `CameraProfile`에 필드 추가가
  선행돼야 한다. 참고로 Pi 4의 병목은 프레임레이트가 아니라 추론이라, 성능 조절 손잡이로는
  `capture_preset` · `model_variant`가 더 직접적이다.
- 저장 시 Pi가 추가로 검사하는 규칙: **포트 중복 금지**, **포트 5000 예약**(roi_editor),
  **Coral EdgeTPU는 활성 카메라 1대만**(`inference_backend`가 `"auto"`인 카메라도 집계됨).

### `GET /api/devices/{device_id}/cameras`
해당 디바이스의 카메라 목록.

**Response 200**
```json
{
  "data": [
    {
      "id": "dev-cam0",
      "port": 8080,
      "capture_preset": "640x480",
      "fps": 20,
      "model_variant": "v10_320",
      "rotation": 0,
      "require_person": true,
      "is_active": true,
      "roi_count": 3,
      "today_detections": 27,
      "is_streaming": true,
      "current_alert": null
    }
  ],
  "ok": true
}
```

### `PATCH /api/devices/{device_id}/cameras/{camera_id}`
카메라 설정 변경. 저장 후 Pi가 설정 폴링 시 반영.

**Headers**: `Authorization: Bearer <token>`

**Request Body** (부분 업데이트 가능)
```json
{
  "capture_preset": "640x480",
  "fps": 15,
  "model_variant": "v10_320",
  "rotation": 90,
  "require_person": true
}
```

**Response 200**: 수정된 Camera 객체

**비고**:
- 변경 시 해당 Device의 `config_etag` 갱신 → Pi 폴링 시 설정 재로드
- `capture_preset` · `model_variant` 값은 위 비고의 열거값이어야 한다. 목록 밖의 값을
  저장하면 Pi가 설정 전체를 무시하므로(유효성 오류 시 갱신 자체를 건너뜀) 백엔드에서도
  같은 검증을 해야 한다.
- **`rotation`을 바꾸면 그 카메라에 이미 그려진 ROI/제외구역 폴리곤 좌표계가 어긋난다.**
  Pi는 자동 재배치를 하지 않으므로(90/270도는 가로세로비까지 바뀜) UI가 경고 후 수동
  재작도를 유도해야 한다.

---

## 5. ROI 관리 (Region of Interest)

### 데이터 모델

```
Roi {
  id: int                // 서버 DB 전용 (Pi의 rois.json에는 없음)
  device_id: string      // FK
  camera_id: string      // 어느 카메라에 속하는지 — 예: "dev-cam0" (정수 아님)
  name: string
  zone_type: enum        // "trigger" | "exclude"
  priority: int          // 0~10
  announcement_text: string // TTS 안내 텍스트
  audio_file: string     // 예: "entrance.mp3"
  polygon: JSON          // 정규화 좌표 [[x,y], ...] (0.0~1.0)
                         // ※ Pi에 전달할 때의 키 이름은 points (§13 참고)
  is_active: bool
  created_at: datetime
  updated_at: datetime
}
```

**비고 — Pi의 `rois.json`과의 대응**:

- **이 절의 CRUD는 서버 DB가 아니라 Pi의 `roi_editor` API(포트 5000)를 중계해 구현한다**
  (§13.0). 서버 테이블은 캐시다.
- **좌표 키 이름이 다르다**: 서버 `polygon` ↔ Pi `points`. Pi의 ROI 로더
  (`apps/simulator/roi_manager.py`, `device/camera_live_pi.py`)는 `points`만 읽는다.
- **Pi 파일에는 `id`와 `is_active`가 없다.** ROI의 식별자는 **이름**이며
  (`DELETE /api/rois/{name}`), 비활성 ROI라는 개념 자체가 없다 — 서버가 설정을 쓸 때
  `is_active: false`인 ROI는 목록에서 빼서 보낸다. 두 필드는 **서버 DB 전용**으로 유지한다
  (Pi에 도입하려면 디스패처의 쿨다운 키잉까지 바뀌어 회귀 위험이 크다).
- **ROI 이름은 Pi의 식별자이자 이벤트 로그의 비정규화 키다**(`detection_events.roi_name`).
  이름을 바꾸면 과거 이벤트·통계와의 연결이 끊기므로, UI에서 이름 변경 시 경고해야 한다.
- **`audio_file`은 파일명이 아니라 Pi 로컬 절대경로다.** Pi의 업로드 API가 반환한 절대경로를
  그대로 저장하고 `camera_live_pi.py`가 그 경로로 재생한다 — §13에서 변환이 필요하다.
- **`zone_type: "exclude"`는 오디오 트리거 제외가 아니라 감지 제외구역이다.** raw detection
  단계에서 지팡이·사람 전체를 필터링한다(지형지물 오탐지 대응).
- Pi의 `rois.json` 최상위에는 ROI 배열 외에 **`conf`(클래스별 신뢰도 임계값) ·
  `cooldown` · `debounce`** 가 함께 저장된다 — 이 절 마지막의
  `.../detection-params` 엔드포인트가 담당한다. **ROI를 쓸 때 이 값들을 함께 보존**해야 한다.

### `GET /api/devices/{device_id}/rois`
ROI 전체 목록.

**Query Parameters**
- `camera_id?: string` — 특정 카메라 필터

**Response 200**
```json
{
  "data": [
    {
      "id": 1,
      "device_id": "cam-entrance-01",
      "camera_id": "dev-cam0",
      "name": "정문 진입 구역",
      "zone_type": "trigger",
      "priority": 1,
      "announcement_text": "정문 입구입니다. 전방에 계단이 있습니다.",
      "audio_file": "entrance.mp3",
      "polygon": [[0.1, 0.2], [0.5, 0.2], [0.5, 0.8], [0.1, 0.8]],
      "is_active": true
    }
  ],
  "ok": true
}
```

### `POST /api/devices/{device_id}/rois`
ROI 신규 생성.

**Headers**: `Authorization: Bearer <token>`

**Request Body**
```json
{
  "camera_id": "dev-cam0",
  "name": "정문 진입 구역",
  "zone_type": "trigger",
  "priority": 1,
  "announcement_text": "정문 입구입니다.",
  "audio_file": "entrance.mp3",
  "polygon": [[0.1, 0.2], [0.5, 0.2], [0.5, 0.8], [0.1, 0.8]],
  "is_active": true
}
```

**Validation**:
- `polygon` 꼭짓점 최소 3개
- Shapely로 폴리곤 유효성 검증 (self-intersection 불가)
- `priority` 범위: 0~10

**Response 201**: 생성된 Roi 객체

### `PATCH /api/devices/{device_id}/rois/{roi_id}`
ROI 수정.

**Headers**: `Authorization: Bearer <token>`

**Request Body** (부분 업데이트 가능)
```json
{
  "name": "수정된 구역명",
  "is_active": false,
  "priority": 2
}
```

**Response 200**: 수정된 Roi 객체

### `DELETE /api/devices/{device_id}/rois/{roi_id}`
ROI 삭제.

**Headers**: `Authorization: Bearer <token>`

**Response 204**

### `GET /api/devices/{device_id}/cameras/{camera_id}/detection-params`
탐지 파라미터(신뢰도 임계값 · 쿨다운 · 디바운스) 조회.

**Headers**: `Authorization: Bearer <token>`

**Response 200**
```json
{
  "data": {
    "conf": { "white_cane": 0.55, "person": 0.55 },
    "cooldown": 10.0,
    "debounce": 0.5
  },
  "ok": true
}
```

### `PATCH /api/devices/{device_id}/cameras/{camera_id}/detection-params`
탐지 파라미터 변경. 현장에서 가장 자주 조정하는 값들이다.

**Headers**: `Authorization: Bearer <token>`

**Request Body** (부분 업데이트 가능)
```json
{
  "conf": { "white_cane": 0.6, "person": 0.45 },
  "cooldown": 8.0,
  "debounce": 0.5
}
```

**Validation**:
- `conf`의 각 클래스 값은 0.0~1.0. 클래스 키는 `white_cane` · `person` 두 개이며 둘 다 필수
- `cooldown` · `debounce`는 0 이상 (초 단위)

**Response 200**: 수정된 파라미터 객체

**비고**:
- 이 값들은 Pi의 **`rois.json` 최상위**에 ROI 배열과 함께 저장된다 — 즉 카메라 단위 설정이며,
  §13.0의 중계 규칙에 따라 **ROI 배열과 한 번에 전체 치환**으로 기록해야 한다. ROI만 쓰면서
  이 필드들을 빠뜨리면 기존 값이 사라진다.
- Pi는 `conf`를 스칼라(모든 클래스 동일)로도 허용하지만, **이 API는 클래스별 딕셔너리로
  통일한다.** 스칼라를 받았을 때 두 클래스에 같은 값을 채우는 변환은 백엔드가 담당한다.
- 변경은 재시작 없이 최대 2초 내 반영된다(`camera_live_pi.py`의 mtime 폴링). 신뢰도 변경은
  추론 백엔드의 임계값만 갱신하므로 스트림이 끊기지 않는다.
- 기본값: `conf` 0.55 / `cooldown` 10.0초 / `debounce` 0.5초.

---

## 6. 감지 이벤트 (Detection Event)

### 데이터 모델

```
DetectionEvent {
  id: int
  device_id: string
  camera_id: string        // 예: "dev-cam0" (정수 아님)
  roi_id: int | null
  roi_name: string         // 비정규화 (ROI 삭제 후에도 이력 유지)
  class_name: string       // "white_cane"
  confidence: float        // 0.0~1.0
  event_type: enum         // "DETECTION" | "ANNOUNCEMENT" | "OFFLINE"
  timestamp: datetime
}
```

**비고 — Pi가 현재 생산하는 것과의 차이**:

- Pi의 이벤트 로그(`device/detection_events.py`)가 기록하는 것은
  **`(ts, class_name, roi_name)` 세 가지뿐**이다. `confidence` · `camera_id` · `roi_id` ·
  `event_type`은 **아직 Pi가 만들지 않는다** — ingest 계약을 채우려면 Pi 측 구현이 필요하다.
- 기록 시점은 **ROI 트리거가 실제로 발동해 음성 안내가 나가는 순간**이다(raw 탐지 프레임마다가
  아니다). 즉 지금의 Pi 이벤트는 사실상 `ANNOUNCEMENT` 한 종류다.
- `class_name`은 대부분 `"white_cane"`이지만 **항상 그렇지는 않다** — RF 트리거 경로
  (`device/announcement_router.py`)는 같은 로그에 `"announcement"`으로 기록한다.
- **Pi 로컬 이벤트는 최근 200건만 유지되는 링버퍼다**(기록할 때마다 초과분 즉시 삭제).
  장기 이력은 전적으로 서버 보관에 의존하므로, ingest가 끊긴 구간은 복구할 수 없다.

### `GET /api/events`
감지 이벤트 조회.

**Headers**: `Authorization: Bearer <token>`

**Query Parameters**
- `device_id?: string` — 특정 디바이스 필터
- `camera_id?: string`
- `start?: datetime` — ISO 8601
- `end?: datetime` — ISO 8601
- `limit?: int` — 기본 50, 최대 200
- `offset?: int` — 기본 0

**Response 200**
```json
{
  "data": [
    {
      "id": 1,
      "device_id": "cam-entrance-01",
      "camera_id": "dev-cam0",
      "roi_name": "정문 진입 구역",
      "class_name": "white_cane",
      "confidence": 0.94,
      "event_type": "ANNOUNCEMENT",
      "timestamp": "2026-09-16T14:32:07Z",
      "time_display": "14:32:07"
    }
  ],
  "total": 158,
  "ok": true
}
```

**비고**: 90일 초과 기간 조회 시 400 반환. 이 보관 기간은 **서버 DB 기준**이다 — Pi 로컬에는 최근 200건만 남는다(위 모델 비고 참고).

### `POST /api/events/ingest`
Pi 디바이스가 감지 이벤트를 서버로 전송.

**Headers**: `X-API-Key: <device_api_key>`

**Request Body**
```json
{
  "camera_id": "dev-cam0",
  "roi_id": 1,
  "roi_name": "정문 진입 구역",
  "class_name": "white_cane",
  "confidence": 0.94,
  "event_type": "ANNOUNCEMENT",
  "timestamp": "2026-09-16T14:32:07Z"
}
```

**Response 201**
```json
{ "id": 123, "ok": true }
```

**Rate Limit**: 분당 600건 초과 시 429 반환

**비고**:
- 수신 즉시 WebSocket 구독자에게 실시간 푸시
- Pi 측 전송은 `device/event_logger.py`의 `EventSender`가 맡는다(표준 라이브러리 `urllib`만
  쓴다 — Pi에 의존성을 늘리지 않으려고).

> [!WARNING]
> **4xx는 이벤트를 영구히 버린다.** `EventSender`는 4xx(**429 제외**)를 "다시 보내도 같은
> 답이 온다"로 읽고 outbox에서 그 행을 **즉시 삭제**한다(`event_logger.py:193-197`).
> 그래서 이 엔드포인트의 스키마를 빡빡하게 만들면 안 된다.
>
> - `confidence`는 **키가 생략될 수 있다** — 가상 지팡이 박스로 발사된 안내에는 신뢰도가 없다.
> - `camera_id`·`roi_name`·`class_name`은 **빈 문자열**로 올 수 있다(outbox 컬럼 DEFAULT가 `''`).
> - `class_name`은 `"white_cane"`이 아닐 수 있다 — RF 트리거 경로는 `"announcement"`로 적는다.
> - **일시적 실패는 반드시 5xx 또는 429로 낸다.** DB 오류를 400으로 접으면 그 이벤트가 사라진다.
> - 429에는 `Retry-After`를 붙인다.

- `timestamp`는 Pi가 ISO8601 UTC `'Z'`로 보낸다(**aware**). 서버는 저장 직전에 naive UTC로
  정규화한다 — 한 컬럼에 aware/naive가 섞이면 통계 경계 비교가 조용히 어긋난다.

---

## 7. 실시간 통신 (WebSocket)

### `WS /ws/events`
감지 이벤트 실시간 스트림.

**연결 시 Auth**: Query parameter `?token=<jwt>` 또는 첫 메시지로 토큰 전달

**서버 → 클라이언트 메시지 형식**
```json
{
  "type": "detection_event",
  "data": {
    "id": 123,
    "device_id": "cam-entrance-01",
    "camera_id": "dev-cam0",
    "roi_name": "정문 진입 구역",
    "confidence": 0.94,
    "event_type": "ANNOUNCEMENT",
    "timestamp": "2026-09-16T14:32:07Z"
  }
}
```

```json
{
  "type": "device_status_change",
  "data": {
    "device_id": "cam-entrance-01",
    "status": "offline",
    "timestamp": "2026-09-16T14:35:00Z"
  }
}
```

```json
{
  "type": "alert",
  "data": {
    "device_id": "cam-braille-01",
    "camera_id": "dev-cam0",
    "message": "전방 1.5m 장애물 우회 안내",
    "timestamp": "2026-09-16T14:36:00Z"
  }
}
```

**클라이언트 → 서버 메시지** (구독 필터)
```json
{ "type": "subscribe", "device_ids": ["cam-entrance-01"] }
```

---

## 8. 통계 (Statistics)

### `GET /api/stats/summary`
대시보드 KPI 카드 데이터.

**Headers**: `Authorization: Bearer <token>`

**Query Parameters**
- `date?: string` — YYYY-MM-DD, 기본: 오늘

**Response 200**
```json
{
  "data": {
    "total_foot_traffic_today": 1284,
    "cane_user_count_today": 37,
    "total_detections_today": 158,
    "avg_confidence": 0.916,
    "active_streams": 8,
    "total_streams": 8,
    "online_device_count": 5,
    "total_device_count": 6,
    "online_rate": 0.833,
    "active_alert_count": 3,
    "avg_cpu_temperature": 51.8,
    "most_active_device": {
      "id": "cam-entrance-01",
      "name": "정문 카메라",
      "location": "정문 입구",
      "today_detections": 42
    }
  },
  "ok": true
}
```

### `GET /api/stats/devices`
디바이스별 지표 분포 (통계 페이지 바 차트용).

**Headers**: `Authorization: Bearer <token>`

**Query Parameters**
- `date?: string` — YYYY-MM-DD, 기본: 오늘

**Response 200**
```json
{
  "data": [
    { "device_id": "cam-entrance-01", "name": "정문 카메라",
      "foot_traffic": 412, "cane_users": 11, "detections": 42 },
    { "device_id": "cam-gate-02", "name": "게이트 카메라",
      "foot_traffic": 355, "cane_users": 9, "detections": 38 }
  ],
  "ok": true
}
```

### `GET /api/stats/timeseries`
시간별 지표 시계열 (hourly / daily).

**Headers**: `Authorization: Bearer <token>`

**Query Parameters**
- `device_id?: string` — 특정 디바이스 필터 (미지정 시 전체 합산)
- `period: enum` — `today` | `7d` | `30d`
- `granularity?: enum` — `hourly` (기본, today에만 유효) | `daily`

**Response 200 (period=today, granularity=hourly)**
```json
{
  "data": [
    { "hour": 0,  "total_count": 0,  "cane_user_count": 0, "detections": 0 },
    { "hour": 14, "total_count": 96, "cane_user_count": 3, "detections": 23 },
    { "hour": 23, "total_count": 0,  "cane_user_count": 0, "detections": 0 }
  ],
  "period": "today",
  "granularity": "hourly",
  "ok": true
}
```

**Response 200 (period=7d, granularity=daily)**
```json
{
  "data": [
    { "date": "2026-09-10", "total_count": 902,  "cane_user_count": 21, "detections": 112 },
    { "date": "2026-09-16", "total_count": 1284, "cane_user_count": 37, "detections": 158 }
  ],
  "ok": true
}
```

**비고 — 세 지표는 서로 다른 것을 센다**:

| 필드 | 의미 | 원천 |
|------|------|------|
| `total_count` / `foot_traffic` | 지나간 사람 수(유동인구) | Pi의 `foot_traffic_hourly.total_count` |
| `cane_user_count` / `cane_users` | 그중 **지팡이 사용자** 수 | Pi의 `foot_traffic_hourly.cane_user_count` |
| `detections` | ROI 트리거(음성 안내) 발생 횟수 | 이벤트 ingest(§6) |

- 앞의 두 계열은 **Pi가 이미 집계해 sqlite에 쌓고 있다.** 시간별은 0~23시 24개,
  일별은 요청 기간 전체가 0-패딩된 채로 나온다.
- **서버가 끌어온다**(`app/services/foot_traffic_puller.py`, 5분 주기). 이벤트는 Pi가 밀어
  올리고 유동인구는 서버가 끌어오는데, 유동인구는 누적 집계라 언제 읽어도 같은 답이 나오고
  Pi에 스케줄러를 하나 더 두지 않아도 되기 때문이다.
- **Pi의 응답 키가 서버와 다르다** — 변환은 백엔드 몫이다:

  | Pi (`GET /api/stats/timeseries`, 포트 5000) | 서버 (§8) |
  |---|---|
  | `granularity: "hour" \| "day"` | `hourly` \| `daily` |
  | `points[]` | `data[]` |
  | `points[].total_count` | `hourly_stats.foot_traffic_count` |

- **`detections`는 수집기가 덮어쓰면 안 된다** — 그쪽은 이벤트 ingest가 세는 값이다.
- Pi의 시간별 API는 `date=YYYY-MM-DD` 쿼리를 받는다(2026-09-23 추가). 서버가 꺼져 있던
  구간을 나중에 메우기 위한 것으로, 수집기가 기본 2일치를 거슬러 확인한다.
- **ROI별 집계는 제공하지 않는다.** Pi의 스키마가 카메라 단위 시간별 합계만 기록하므로
  구조적으로 낼 수 없다.
- 유동인구 db는 **카메라마다 파일이 분리**돼 있다 — 디바이스 단위·전체 합산은 백엔드가
  카메라별 값을 더해서 만든다.
- 데이터 없는 시간/날짜는 0으로 채워 반환 (0-패딩)

---

## 9. 오디오 파일 관리 (Audio)

### `GET /api/audio`
등록된 오디오 파일 목록.

**Headers**: `Authorization: Bearer <token>`

**Response 200**
```json
{
  "data": [
    { "filename": "entrance.mp3", "label": "정문 안내", "size_bytes": 54321 },
    { "filename": "crosswalk.mp3", "label": "횡단보도 안내", "size_bytes": 48200 },
    { "filename": "warning.mp3", "label": "주의", "size_bytes": 32100 },
    { "filename": "stairs.mp3", "label": "계단 안내", "size_bytes": 61200 },
    { "filename": "elevator.mp3", "label": "엘리베이터 안내", "size_bytes": 58000 },
    { "filename": "braille_block.mp3", "label": "점자블록 안내", "size_bytes": 44000 }
  ],
  "ok": true
}
```

### `POST /api/audio/upload`
MP3 파일 업로드.

**Headers**: `Authorization: Bearer <token>`, `Content-Type: multipart/form-data`

**Form Fields**
- `file: File` — `.mp3` 파일 (최대 10MB)
- `label?: string` — 표시명

**Response 201**
```json
{ "data": { "filename": "new_audio.mp3", "label": "새 안내음" }, "ok": true }
```

### `GET /api/audio/{filename}`
오디오 파일 스트리밍 다운로드 (ROI 편집 화면의 미리듣기용).

**Response**: `audio/mpeg` 스트리밍

**보안**: `audio_dir` 외부 경로(`../` 등) 차단

---

## 10. 디바이스 제어 (Device Control)

### `POST /api/devices/{device_id}/restart`
Pi의 `visionguide-device` systemd 서비스 재시작 요청.

**Headers**: `Authorization: Bearer <token>`

**Response 202**
```json
{
  "data": {
    "job_id": "restart-abc123",
    "estimated_seconds": 10,
    "message": "서비스 재시작 요청이 전송되었습니다."
  },
  "ok": true
}
```

**비고**:
- 백엔드는 Pi의 **`POST /api/service/restart`**(포트 5000)를 부른다. Pi는 `systemctl restart
  visionguide-device`를 실행한다 — **반드시 systemctl로** 한다. 앱이 SIGTERM에 정상 종료
  (exit 0)하므로 `pkill`로는 `Restart=on-failure`가 걸리지 않아 되살아나지 않는다.
- **`X-Device-Key` 헤더가 필요하다**(서버가 등록 때 심어둔 api_key). 신원이 없는 기기는
  403으로 거부한다 — 현장 설치 중 오작동 방지.
- **실패를 삼키지 않는다.** 기기가 꺼져 있으면 503, 응답이 없으면 504, 엔드포인트가 없는
  구버전이면 502를 낸다. 이전 구현은 모든 예외를 무시하고 항상 202를 돌려줘서, 꺼진 기기에도
  "재시작 요청이 전송되었습니다"가 떴다.
- sudoers(`deploy/visionguide-systemctl.sudoers`)가 유닛별 start/stop/restart/status와
  `/usr/sbin/reboot`만 비밀번호 없이 허용한다.

### `POST /api/devices/{device_id}/reboot`
Pi 전체 재부팅 요청.

**Headers**: `Authorization: Bearer <token>`

**Request Body** (확인용)
```json
{ "confirm": true }
```

**비고**: 백엔드는 Pi의 **`POST /api/system/reboot?confirm=true`**를 부른다.
⚠ 이 Pi는 PoE 어댑터가 GPIO3를 점유해 **버튼으로 다시 켤 수 없다** — 재부팅이 실패해 꺼진
채로 남으면 현장에 가야 한다. 그래서 Pi 측에서도 `confirm`을 다시 요구한다.

**Response 202**
```json
{
  "data": {
    "job_id": "reboot-def456",
    "estimated_seconds": 60,
    "message": "디바이스 재부팅 요청이 전송되었습니다. 약 60초 후 온라인 상태가 됩니다."
  },
  "ok": true
}
```

---

## 11. 예약 재부팅 (Scheduled Reboot)

### 데이터 모델

```
ScheduledReboot {
  id: int
  device_id: string
  days: List[int]     // 0=일, 1=월, ..., 6=토
  hour: int           // 0~23 (정각 실행)
  is_enabled: bool
  created_at: datetime
}
```

### `GET /api/devices/{device_id}/schedules`
예약 재부팅 목록.

**Headers**: `Authorization: Bearer <token>`

**Response 200**
```json
{
  "data": [
    {
      "id": 1,
      "days": [1, 3, 5],
      "hour": 3,
      "is_enabled": true,
      "display": "월·수·금 03:00"
    }
  ],
  "ok": true
}
```

### `POST /api/devices/{device_id}/schedules`
예약 재부팅 추가.

**Request Body**
```json
{ "days": [1, 3, 5], "hour": 3, "is_enabled": true }
```

**Validation**:
- `days`: 0~6 범위의 정수 배열, 최소 1개
- `hour`: 0~23

**Response 201**: 생성된 ScheduledReboot 객체

### `PATCH /api/devices/{device_id}/schedules/{schedule_id}`
예약 수정 (활성화/비활성화 포함).

**Request Body** (부분 업데이트)
```json
{ "is_enabled": false }
```

**Response 200**: 수정된 ScheduledReboot 객체

### `DELETE /api/devices/{device_id}/schedules/{schedule_id}`
예약 삭제.

**Response 204**

---

## 12. 디바이스 탐색 / 등록 (Pi Scan)

### `POST /api/scan/network`
로컬 네트워크(`192.168.1.0/24` 등)에서 VisionGuide Pi 디바이스 자동 탐색 시작.

**Headers**: `Authorization: Bearer <token>`

**Request Body**
```json
{ "subnet": "192.168.1.0/24", "port": 5000 }
```

**Response 202**
```json
{
  "data": { "scan_id": "scan-xyz789", "status": "running" },
  "ok": true
}
```

### `GET /api/scan/{scan_id}`
스캔 진행 상황 및 결과 조회.

**Response 200**
```json
{
  "data": {
    "scan_id": "scan-xyz789",
    "status": "completed",
    "progress": 254,
    "total": 254,
    "discovered": [
      {
        "ip": "192.168.1.101",
        "hostname": "cam-entrance-01.local",
        "port": 5000,
        "version": "VisionGuide v1.2",
        "already_registered": false
      }
    ]
  },
  "ok": true
}
```

**비고**:
- 진행 중일 때 `status: "running"`, `progress`는 현재 스캔된 IP 수
- **`version`의 소스는 `GET /api/version`이다** (`apps/roi_editor/server.py`).
  응답은 `{version, product: "VisionGuide", registered, device_id}`이며 **인증 없이, 등록 전에도**
  답한다(등록 자체가 이 응답을 보고 이뤄지므로).
- **기기 판별은 이 응답의 `product == "VisionGuide"`로 한다.** 리다이렉트를 따라가면 안 된다
  (`follow_redirects=False`) — 캡티브 포털 catch-all이 302를 주기 때문이다.
- **VisionGuide 여부 판별은 "404가 아니면 있음" 식으로 하면 안 된다.** `roi_editor`에는
  Wi-Fi 온보딩용 캡티브 포털 catch-all 라우트가 있어, 존재하지 않는 경로도 조건에 따라
  302를 반환한다. 판별은 `GET /api/device/status`(포트 5000)의 **200 응답 + 바디 스키마**로
  해야 한다.

### `POST /api/scan/verify`
수동 입력한 IP/포트의 Pi 연결 확인.

**Request Body**
```json
{ "ip": "192.168.1.101", "port": 5000 }
```

**Response 200** (성공)
```json
{
  "data": {
    "reachable": true,
    "hostname": "cam-entrance-01.local",
    "version": "VisionGuide v1.2",
    "camera_count": 2
  },
  "ok": true
}
```

**Response 200** (실패)
```json
{ "data": { "reachable": false }, "ok": true }
```

---

## 13. 디바이스 ↔ 서버 동기화

### 기기 최초 등록과 신원 주입

Pi가 네트워크에 연결되면 대시보드가 `POST /api/devices`를 호출해 다음 본문을 Pi의
`POST /api/identity`로 전달한다.

```json
{
  "device_id": "pi-01",
  "api_key": "vg_<issued-key>",
  "server_url": "http://192.168.0.50:8000",
  "name": "현관 Pi",
  "location": "1층 현관",
  "registered_at": "2026-09-26T12:00:00Z"
}
```

미등록 Pi는 검색 후 identity를 저장한다. 이미 등록된 Pi를 재주입할 때는 기존
`X-Device-Key` 헤더를 사용한다. 백엔드는
Pi가 수락한 키를 `control_key`로 보관해 이후 제어 요청에 `X-Device-Key`로 사용한다.

### 13.0 설정 소유권 — **Pi가 원본, 서버 DB는 캐시**

`camera_config.json`과 `rois.json`의 최종 권한은 **Pi에 있다.** 두 파일은 rsync 배포 대상이
아니며, 쓰는 주체는 Pi 로컬의 `roi_editor`(포트 5000) 하나뿐이다. `camera_live_pi.py`는 파일
mtime을 폴링해 재시작 없이 반영한다(ROI 2초, 카메라 프로필은 슈퍼바이저 루프).

**서버가 원본을 가져가지 않는 이유**: 작성자가 둘이 되면 Pi의 변경 감지가 mtime뿐이라 병합도
버전 비교도 없이 **나중에 쓴 쪽이 무조건 이긴다.** 현장에서 방금 한 편집이 서버 폴링에 덮여
사라지거나, 반대로 대시보드 화면의 값과 기기의 실제 동작이 어긋나도 아무도 모르는 상태가
된다. 또 이 기기는 AP 모드·캡티브 포털·Wi-Fi 전환 버튼으로 **오프라인 현장 설정을 전제**로
만들어져 있어, 서버가 원본이 되면 그 흐름이 무력화된다.

> [!WARNING]
> **포트 5000은 LAN 중심의 설정 API다.** 일반 설정·조회 라우트는 기존 호환성을 위해
> 같은 네트워크에서 접근할 수 있다. `POST /api/identity`는 미등록 상태에서 허용되고,
> 등록 후 재설정에는 기존 `X-Device-Key`를 요구한다.
> `/api/service/restart`와 `/api/system/reboot` 같은 제어 라우트도 `X-Device-Key`가
> 필수다(§10). 신뢰할 수 없는 망에서는 Pi API를 직접 노출하지 말고 VPN이나 방화벽을 사용한다.

**따라서 백엔드는 설정의 저장소가 아니라 중계자다.**

| 동작 | 백엔드가 하는 일 |
|------|------------------|
| 조회 | Pi의 `GET /api/cameras` · `GET /api/rois?camera=<id>`(포트 5000)를 호출해 캐시를 갱신한 뒤 반환 |
| 변경 | 검증 후 Pi의 `POST /api/cameras` · `POST /api/rois?camera=<id>`로 **전체 문서 치환**. 성공 응답을 받은 뒤에만 캐시 갱신 |
| 삭제 | Pi의 `DELETE /api/rois/{name}?camera=<id>` |

- **§4·§5의 카메라/ROI 엔드포인트는 이 중계로 구현한다.** 서버 DB의 `cameras` · `rois` 테이블은
  진실이 아니라 마지막으로 읽어온 **스냅샷**이다.
- **쓰기는 항상 전체 문서 치환이다.** Pi의 저장 API는 배열을 통째로 받으므로, 백엔드 스키마에
  없는 필드를 빠뜨리면 그 값이 조용히 코드 기본값으로 되돌아간다. 쓰기 전에 현재 파일을 읽어
  모르는 필드를 보존해야 한다.
- **기기가 오프라인이면 설정 변경은 실패시킨다**(503). 큐에 쌓아 나중에 적용하지 않는다 —
  그 순간 사실상 서버가 원본이 되어 위의 문제가 그대로 발생한다.
- **조회는 Pi를 먼저 읽어 캐시를 갱신한 뒤 반환한다.** 기기가 꺼져 있으면 캐시를 주되
  응답에 `stale: true`를 실어 UI가 구분하게 한다.
- ⚠ **Pi의 `rois.json`이 사라지면 서버 캐시도 따라서 비워진다.** 원본이 Pi라는 결정의
  직접적인 결과다 — SD 카드를 다시 굽거나 기기를 교체하면 다음 조회에서 그 카메라의
  활성 ROI가 캐시에서 삭제되고, **서버에서 되돌릴 방법이 없다.** 비활성 ROI는 서버 전용
  상태라 남는다. 기기 교체 절차에 ROI 재설정을 반드시 포함할 것.
- 기기 대수가 늘어 중앙 관리 비중이 커지면 서버 소유 방식으로 옮길 수 있다. 지금 구조는 그
  전환을 막지 않는다 (현재 운용 대수: 1~2대).

### 13.1 Pi → 서버 (인증: `X-API-Key`)

### `PATCH /api/devices/me/heartbeat`
Pi가 주기적(10~30초)으로 자신의 상태를 서버에 업로드.

**Headers**: `X-API-Key: <device_api_key>`

**Request Body**
```json
{
  "status": "online",
  "load_avg": [0.82, 0.74, 0.69],
  "cpu_temp_c": 51.2,
  "memory": { "used_mb": 1228.8, "total_mb": 4096.0 },
  "uptime_seconds": 302542,
  "latency_ms": 11,
  "npu_ms": 48,
  "cameras": [
    { "id": "dev-cam0", "is_streaming": true, "current_alert": null, "today_detections": 27 }
  ]
}
```

**Response 200**
```json
{ "ok": true }
```

### 13.2 서버 → Pi 설정 쓰기 — 페이로드 변환 규칙

> **구 `GET /api/devices/me/config`는 구현하지 않는다.** Pi가 서버에서 설정을 내려받는 방향은
> §13.0의 소유권 결정과 충돌한다(작성자 이중화). 대신 아래 스키마를 **백엔드가 Pi에 설정을
> 쓸 때의 변환 규칙**으로 사용한다 — 포트 5000의 `POST /api/cameras` ·
> `POST /api/rois?camera=<id>` 바디가 이 형태다. `config_etag`는 Pi에서 읽어온 스냅샷의
> 버전으로 계속 활용한다(캐시 무효화·충돌 감지용).

**Pi에 쓰는 페이로드 형태**
```json
{
  "data": {
    "cameras": [
      {
        "id": "dev-cam0",
        "capture_preset": "640x480",
        "fps": 20,
        "model_variant": "v10_320",
        "rotation": 0,
        "require_person": true
      }
    ],
    "rois": [
      {
        "id": 1,
        "camera_id": "dev-cam0",
        "name": "정문 진입 구역",
        "zone_type": "trigger",
        "priority": 1,
        "announcement_text": "정문 입구입니다.",
        "audio_file": "/home/ailab/visionguide/audio/entrance.mp3",
        "points": [[0.1, 0.2], [0.5, 0.2], [0.5, 0.8], [0.1, 0.8]],
        "is_active": true
      }
    ]
  },
  "etag": "abc123def456",
  "ok": true
}
```

**비고 — Pi가 그대로 소비하는 값이므로 서버 DB 표현과 다르다**:

- **`camera_id`/`cameras[].id`는 문자열**이다 (`"dev-cam0"`).
- **ROI 좌표 키는 `polygon`이 아니라 `points`**다. Pi의 ROI 로더는 `points`만 읽으며,
  없으면 그 ROI는 판정에서 빠진다.
- **`audio_file`은 Pi 로컬 절대경로**여야 한다. 파일명(`"entrance.mp3"`)을 그대로 내려주면
  재생에 실패한다 — 서버가 보관한 오디오를 Pi에 먼저 배치하고 그 경로를 넣어야 한다.
- **`is_active: false`인 ROI는 목록에서 제외해 보낸다.** Pi의 `rois.json`에는 비활성 개념이
  없어 내려보내면 곧바로 활성 ROI가 된다.
- **`capture_preset` · `model_variant`가 열거값 밖이거나 포트/Coral 제약을 어기면 Pi는 갱신
  전체를 무시한다**(일부만 적용하지 않는다). 서버에서 같은 검증을 선행해야 한다.
- **`rois` 배열을 쓸 때는 같은 파일 최상위의 `conf` · `cooldown` · `debounce`도 함께 보낸다**
  (§5의 `.../detection-params`). 빠뜨리면 기존 값이 사라진다.
- **Pi 측 신규 구현은 필요 없다.** 백엔드가 기존 `roi_editor` API(포트 5000)를 호출하는
  방향이므로 Pi에 HTTP 클라이언트를 추가하지 않는다.
- 쓰기 직후 Pi가 mtime을 보고 자동 반영한다 — ROI는 최대 2초, 카메라 프로필 변경은 해당
  카메라 파이프라인 재시작(수 초간 스트림 끊김)을 동반한다.

---

## 14. MJPEG 스트림 프록시

### `GET /api/devices/{device_id}/cameras/{camera_id}/stream`
Pi의 MJPEG 스트림을 백엔드가 프록시. 직접 접근 불가한 네트워크에서 사용.

**Headers**: `Authorization: Bearer <token>`

**Response**: `multipart/x-mixed-replace; boundary=frame` 스트리밍

**비고**:
- 같은 카메라를 보는 브라우저는 Pi에 대한 **하나의 업스트림 연결을 공유**한다.
- 업스트림이 끊기면 백엔드가 자동 재연결하고, 느린 브라우저에는 최신 프레임만 전달한다.
- 프론트엔드의 `window.open(http://${ip}:${port}/stream.mjpg)` 방식은 Pi에 직접 접근하므로, 방화벽 환경에서는 이 프록시 API 사용
- 경로의 `{camera_id}`는 **문자열**이다 (`/api/devices/cam-entrance-01/cameras/dev-cam0/stream`)
- Pi 측 스트림 포트에는 `/stream.mjpg` 외에 녹화 제어 라우트(`/recording/*`)도 함께 붙어 있다 — 대시보드에서 녹화를 다루려면 별도 절이 필요하다

---

## 15. DB 스키마 요약

```sql
-- 사용자 (관리자)
CREATE TABLE users (
  id INTEGER PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  display_name TEXT,
  team TEXT,
  password_hash TEXT NOT NULL,
  login_fail_count INTEGER DEFAULT 0,
  locked_until DATETIME
);

-- JWT 블랙리스트
CREATE TABLE token_blacklist (
  jti TEXT PRIMARY KEY,
  expires_at DATETIME NOT NULL
);

-- 디바이스
CREATE TABLE devices (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  ip TEXT NOT NULL,
  location TEXT,
  status TEXT DEFAULT 'unknown',
  api_key_hash TEXT NOT NULL,
  config_etag TEXT DEFAULT '',
  last_seen DATETIME,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 카메라 — Pi의 camera_config.json 캐시 (원본은 Pi, §13.0)
CREATE TABLE cameras (
  id TEXT NOT NULL,              -- 카메라 프로필 id ("dev-cam0") — 정수 아님
  device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
  port INTEGER NOT NULL,
  capture_preset TEXT DEFAULT 'auto',   -- CAPTURE_PRESETS의 키
  fps INTEGER DEFAULT 20,               -- 구현 예정 (현재 Pi 미적용, 서버 보관만)
  model_variant TEXT DEFAULT 'v10_320', -- MODEL_VARIANTS의 키
  rotation INTEGER DEFAULT 0,
  require_person BOOLEAN DEFAULT TRUE,  -- Pi: require_person_for_trigger (기본 true)
  is_active BOOLEAN DEFAULT TRUE,
  -- 탐지 파라미터 — Pi에서는 rois.json 최상위에 저장된다 (§5)
  conf_white_cane REAL DEFAULT 0.55,
  conf_person REAL DEFAULT 0.55,
  cooldown REAL DEFAULT 10.0,           -- 초
  debounce REAL DEFAULT 0.5,            -- 초
  PRIMARY KEY (id, device_id)
);

-- ROI — Pi의 rois.json 캐시 (원본은 Pi, §13.0)
CREATE TABLE rois (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
  camera_id TEXT NOT NULL,
  name TEXT NOT NULL,
  zone_type TEXT DEFAULT 'trigger',
  priority INTEGER DEFAULT 0,
  announcement_text TEXT DEFAULT '',
  audio_file TEXT DEFAULT '',
  polygon TEXT NOT NULL,  -- JSON 배열
  is_active BOOLEAN DEFAULT TRUE,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 감지 이벤트
CREATE TABLE detection_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
  camera_id TEXT NOT NULL,
  roi_id INTEGER,
  roi_name TEXT,
  class_name TEXT DEFAULT 'white_cane',
  confidence REAL,
  event_type TEXT DEFAULT 'DETECTION',
  timestamp DATETIME NOT NULL
);

-- 시간별 통계 (집계 테이블)
CREATE TABLE hourly_stats (
  device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
  camera_id TEXT NOT NULL,        -- Pi는 카메라 단위로 집계한다 (db 파일 분리)
  hour DATETIME NOT NULL,         -- 해당 시간의 정각 (예: 2026-09-16 14:00:00)
  foot_traffic_count INTEGER DEFAULT 0,  -- 유동인구 (Pi 집계)
  cane_user_count INTEGER DEFAULT 0,     -- 그중 지팡이 사용자 (Pi 집계)
  detection_count INTEGER DEFAULT 0,     -- ROI 트리거 횟수 (이벤트 ingest 기반)
  PRIMARY KEY (device_id, camera_id, hour)
);

-- 예약 재부팅
CREATE TABLE scheduled_reboots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
  days TEXT NOT NULL,      -- JSON 배열 [0,1,2,...]
  hour INTEGER NOT NULL,
  is_enabled BOOLEAN DEFAULT TRUE,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 디바이스 실시간 상태 캐시
CREATE TABLE device_status_cache (
  device_id TEXT PRIMARY KEY REFERENCES devices(id) ON DELETE CASCADE,
  load_avg_1m REAL,       -- Pi는 CPU 사용률(%)을 산출하지 않는다
  load_avg_5m REAL,
  load_avg_15m REAL,
  cpu_temp_c REAL,
  memory_used_mb REAL,    -- MB 단위 (GB 아님)
  memory_total_mb REAL,
  uptime_seconds INTEGER,
  latency_ms INTEGER,     -- Pi 미산출 (구현 예정)
  npu_ms INTEGER,         -- Pi 미산출 (구현 예정)
  updated_at DATETIME
);
```

---

## 16. 오류 코드 정의

| HTTP | error | 설명 |
|------|-------|------|
| 400 | VALIDATION_ERROR | 요청 바디 유효성 오류 |
| 400 | INVALID_POLYGON | ROI 폴리곤 자기교차 등 유효하지 않은 도형 |
| 400 | INVALID_CAMERA_ID | `camera_id`가 `^[A-Za-z0-9_-]{1,32}$` 패턴에 맞지 않음 |
| 400 | DATE_RANGE_TOO_LARGE | 통계 조회 기간 90일 초과 |
| 401 | UNAUTHORIZED | 인증 정보 없음 또는 만료 |
| 401 | INVALID_CREDENTIALS | 로그인 실패 |
| 403 | FORBIDDEN | 권한 없음 |
| 404 | DEVICE_NOT_FOUND | 디바이스 없음 |
| 404 | ROI_NOT_FOUND | ROI 없음 |
| 404 | AUDIO_NOT_FOUND | 오디오 파일 없음 |
| 409 | DEVICE_ALREADY_EXISTS | 동일 ID 디바이스 중복 |
| 423 | ACCOUNT_LOCKED | 계정 잠금 |
| 429 | RATE_LIMIT_EXCEEDED | ingest 분당 600건 초과 |
| 500 | INTERNAL_ERROR | 서버 내부 오류 |

---

## 17. 환경 변수 (.env)

```
# 서버
HOST=0.0.0.0
PORT=8000
DEBUG=false

# JWT
JWT_SECRET_KEY=<강력한 랜덤 시크릿>
JWT_ALGORITHM=HS256
JWT_EXPIRE_HOURS=24

# 데이터베이스
DATABASE_URL=sqlite:///./data/visionguide.db

# 파일 저장 경로
AUDIO_DIR=./data/audio

# CORS
CORS_ORIGINS=http://localhost:5173,https://your-dashboard-domain.com

# 관리자 초기 계정 (초기화 시 1회 사용)
INITIAL_ADMIN_USERNAME=admin
INITIAL_ADMIN_PASSWORD=<초기 비밀번호>
```

---

## 18. 프로젝트 구조 (권장)

```
visionguide-backend/
├── app/
│   ├── main.py              # FastAPI 앱 생성, 라우터 등록, CORS
│   ├── config.py            # pydantic-settings 환경 변수
│   ├── database.py          # SQLAlchemy 엔진/세션
│   ├── models/              # SQLAlchemy ORM 모델
│   │   ├── user.py
│   │   ├── device.py
│   │   ├── camera.py
│   │   ├── roi.py
│   │   ├── event.py
│   │   └── schedule.py
│   ├── schemas/             # Pydantic Request/Response 스키마
│   │   ├── auth.py
│   │   ├── device.py
│   │   ├── roi.py
│   │   ├── event.py
│   │   └── stats.py
│   ├── routers/             # 엔드포인트 라우터
│   │   ├── auth.py
│   │   ├── devices.py
│   │   ├── cameras.py
│   │   ├── rois.py
│   │   ├── events.py
│   │   ├── stats.py
│   │   ├── audio.py
│   │   ├── scan.py
│   │   └── ws.py            # WebSocket
│   ├── services/            # 비즈니스 로직
│   │   ├── auth_service.py
│   │   ├── device_service.py
│   │   └── stats_service.py
│   ├── deps.py              # 공통 의존성 (get_db, get_current_user)
│   └── db/
│       └── init_db.py       # 초기 DB 생성 + 관리자 시드
├── data/
│   ├── visionguide.db       # SQLite (gitignore)
│   └── audio/               # 업로드된 MP3 (gitignore)
├── tests/
│   ├── test_auth.py
│   ├── test_devices.py
│   └── test_events.py
├── pyproject.toml
├── requirements.txt
└── .env.example
```

---

## 19. 구현 우선순위

| 우선순위 | 기능 | 이유 |
|---------|------|------|
| 1 | 인증 (JWT 로그인/로그아웃) | 모든 API의 선결 조건 |
| 2 | 디바이스 CRUD + 상태 | 프론트엔드 목 데이터 교체의 핵심 |
| 3 | 감지 이벤트 ingest + 조회 | Pi → 서버 핵심 흐름 |
| 4 | WebSocket 실시간 이벤트 | "자동 갱신: 1초 주기" UI 동작 |
| 5 | ROI CRUD | 디바이스 설정 관리 |
| 6 | 통계 API | 통계 페이지 |
| 7 | Pi 설정 중계 (§13.0·§13.2) | Pi가 원본 — 백엔드는 중계자 |
| 8 | Heartbeat API | 실시간 상태 업데이트 |
| 9 | 디바이스 제어 (재시작/재부팅) | 운영 편의 |
| 10 | 예약 재부팅 | 운영 편의 |
| 11 | 오디오 파일 관리 | ROI 편집 보조 |
| 12 | MJPEG 프록시 | 방화벽 환경 지원 |
| 13 | Pi 네트워크 스캔 | 등록 편의 |
