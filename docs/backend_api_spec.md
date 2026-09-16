# VisionGuide 백엔드 기능 명세서 (visionguide-backend)

> **작성 목적**: `visionguide-frontend` 대시보드가 필요로 하는 모든 백엔드 기능을 구체적으로 기술한다.  
> **구현 대상**: 담당 백엔드 개발자가 이 문서를 기준으로 FastAPI 서버를 독립적으로 구현한다.  
> **분석 근거**: `visionguide-frontend/src/` 전체 코드 분석 (타입 정의, 목 데이터, UI 흐름)

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

### 1.3 전역 응답 형식

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
      "cameras": [ { "id": 0, "port": 8080 }, { "id": 1, "port": 8081 } ],
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
        "id": 0,
        "port": 8080,
        "resolution": "640x480",
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
    "cpu": 38.5,
    "temperature": 51.2,
    "memory": { "used": 1.2, "total": 4.0 },
    "uptime_seconds": 302542,
    "uptime_human": "3일 14시간 22분",
    "latency_ms": 11,
    "npu_ms": 48,
    "last_seen": "2026-09-16T14:32:00Z"
  },
  "ok": true
}
```

**비고**: Pi가 주기적으로 `PATCH /api/devices/me/heartbeat`로 밀어올린 값을 캐싱해서 반환.

---

## 4. 카메라 설정 (Camera)

### 데이터 모델

```
Camera {
  id: int              // 0, 1, ...
  device_id: string    // FK
  port: int            // MJPEG 스트림 포트 (8080, 8081 등)
  resolution: string   // "640x480" | "1280x720" | "1920x1080"
  fps: int             // 10 | 15 | 20 | 25 | 30
  model_variant: string // "v1-2" | "v2" | "v3_320" | "v4_320" | "v5b_ft320"
  rotation: int        // 0 | 90 | 180 | 270
  require_person: bool // 보행자 동반 필수 조건
  is_active: bool
}
```

### `GET /api/devices/{device_id}/cameras`
해당 디바이스의 카메라 목록.

**Response 200**
```json
{
  "data": [
    {
      "id": 0,
      "port": 8080,
      "resolution": "640x480",
      "fps": 20,
      "model_variant": "v5b_ft320",
      "rotation": 0,
      "require_person": false,
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
  "resolution": "1280x720",
  "fps": 15,
  "model_variant": "v5b_ft320",
  "rotation": 90,
  "require_person": true
}
```

**Response 200**: 수정된 Camera 객체

**비고**: 변경 시 해당 Device의 `config_etag` 갱신 → Pi 폴링 시 설정 재로드

---

## 5. ROI 관리 (Region of Interest)

### 데이터 모델

```
Roi {
  id: int
  device_id: string      // FK
  camera_id: int         // 어느 카메라에 속하는지
  name: string
  zone_type: enum        // "trigger" | "exclude"
  priority: int          // 0~10
  announcement_text: string // TTS 안내 텍스트
  audio_file: string     // 예: "entrance.mp3"
  polygon: JSON          // 정규화 좌표 [[x,y], ...] (0.0~1.0)
  is_active: bool
  created_at: datetime
  updated_at: datetime
}
```

### `GET /api/devices/{device_id}/rois`
ROI 전체 목록.

**Query Parameters**
- `camera_id?: int` — 특정 카메라 필터

**Response 200**
```json
{
  "data": [
    {
      "id": 1,
      "device_id": "cam-entrance-01",
      "camera_id": 0,
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
  "camera_id": 0,
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

---

## 6. 감지 이벤트 (Detection Event)

### 데이터 모델

```
DetectionEvent {
  id: int
  device_id: string
  camera_id: int
  roi_id: int | null
  roi_name: string         // 비정규화 (ROI 삭제 후에도 이력 유지)
  class_name: string       // "white_cane"
  confidence: float        // 0.0~1.0
  event_type: enum         // "DETECTION" | "ANNOUNCEMENT" | "OFFLINE"
  timestamp: datetime
}
```

### `GET /api/events`
감지 이벤트 조회.

**Headers**: `Authorization: Bearer <token>`

**Query Parameters**
- `device_id?: string` — 특정 디바이스 필터
- `camera_id?: int`
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
      "camera_id": 0,
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

**비고**: 90일 초과 기간 조회 시 400 반환

### `POST /api/events/ingest`
Pi 디바이스가 감지 이벤트를 서버로 전송.

**Headers**: `X-API-Key: <device_api_key>`

**Request Body**
```json
{
  "camera_id": 0,
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

**비고**: 수신 즉시 WebSocket 구독자에게 실시간 푸시

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
    "camera_id": 0,
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
    "camera_id": 0,
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
디바이스별 탐지 수 분포 (통계 페이지 바 차트용).

**Headers**: `Authorization: Bearer <token>`

**Query Parameters**
- `date?: string` — YYYY-MM-DD, 기본: 오늘

**Response 200**
```json
{
  "data": [
    { "device_id": "cam-entrance-01", "name": "정문 카메라", "detections": 42 },
    { "device_id": "cam-gate-02", "name": "게이트 카메라", "detections": 38 }
  ],
  "ok": true
}
```

### `GET /api/stats/timeseries`
시간별 탐지 수 시계열 (hourly / daily).

**Headers**: `Authorization: Bearer <token>`

**Query Parameters**
- `device_id?: string` — 특정 디바이스 필터 (미지정 시 전체 합산)
- `period: enum` — `today` | `7d` | `30d`
- `granularity?: enum` — `hourly` (기본, today에만 유효) | `daily`

**Response 200 (period=today, granularity=hourly)**
```json
{
  "data": [
    { "hour": 0, "detections": 0 },
    { "hour": 14, "detections": 23 },
    { "hour": 23, "detections": 0 }
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
    { "date": "2026-09-10", "detections": 112 },
    { "date": "2026-09-16", "detections": 158 }
  ],
  "ok": true
}
```

**비고**: 데이터 없는 시간/날짜는 0으로 채워 반환 (0-패딩)

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
- 백엔드가 Pi에 직접 SSH 명령을 보내거나, Pi가 다음 폴링 시 `restart_pending` 플래그를 보고 실행
- SSH 방식 권장 (즉각 처리), 폴링 방식은 최대 60초 지연 가능

### `POST /api/devices/{device_id}/reboot`
Pi 전체 재부팅 요청.

**Headers**: `Authorization: Bearer <token>`

**Request Body** (확인용)
```json
{ "confirm": true }
```

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

**비고**: 진행 중일 때 `status: "running"`, `progress`는 현재 스캔된 IP 수

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

## 13. 디바이스 → 서버 API (Pi 측 호출)

Pi가 주기적으로 서버를 호출하는 API. 인증은 `X-API-Key`.

### `PATCH /api/devices/me/heartbeat`
Pi가 주기적(10~30초)으로 자신의 상태를 서버에 업로드.

**Headers**: `X-API-Key: <device_api_key>`

**Request Body**
```json
{
  "status": "online",
  "cpu": 38.5,
  "temperature": 51.2,
  "memory": { "used": 1.2, "total": 4.0 },
  "uptime_seconds": 302542,
  "latency_ms": 11,
  "npu_ms": 48,
  "cameras": [
    { "id": 0, "is_streaming": true, "current_alert": null, "today_detections": 27 }
  ]
}
```

**Response 200**
```json
{ "ok": true }
```

### `GET /api/devices/me/config`
Pi가 60초마다 자신의 설정을 폴링. ETag로 변경 여부 확인.

**Headers**: `X-API-Key: <device_api_key>`, `If-None-Match: <etag>`

**Response 304** (변경 없음, ETag 일치)

**Response 200** (변경됨)
```json
{
  "data": {
    "cameras": [
      {
        "id": 0,
        "resolution": "640x480",
        "fps": 20,
        "model_variant": "v5b_ft320",
        "rotation": 0,
        "require_person": false
      }
    ],
    "rois": [
      {
        "id": 1,
        "camera_id": 0,
        "name": "정문 진입 구역",
        "zone_type": "trigger",
        "priority": 1,
        "announcement_text": "정문 입구입니다.",
        "audio_file": "entrance.mp3",
        "polygon": [[0.1, 0.2], [0.5, 0.2], [0.5, 0.8], [0.1, 0.8]],
        "is_active": true
      }
    ]
  },
  "etag": "abc123def456",
  "ok": true
}
```

---

## 14. MJPEG 스트림 프록시

### `GET /api/devices/{device_id}/cameras/{camera_id}/stream`
Pi의 MJPEG 스트림을 백엔드가 프록시. 직접 접근 불가한 네트워크에서 사용.

**Headers**: `Authorization: Bearer <token>`

**Response**: `multipart/x-mixed-replace; boundary=frame` 스트리밍

**비고**:
- 동시 연결 수 **5개** 초과 시 신규 연결 거부 (503 반환)
- 프론트엔드의 `window.open(http://${ip}:${port}/stream.mjpg)` 방식은 Pi에 직접 접근하므로, 방화벽 환경에서는 이 프록시 API 사용

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

-- 카메라
CREATE TABLE cameras (
  id INTEGER NOT NULL,
  device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
  port INTEGER NOT NULL,
  resolution TEXT DEFAULT '640x480',
  fps INTEGER DEFAULT 20,
  model_variant TEXT DEFAULT 'v5b_ft320',
  rotation INTEGER DEFAULT 0,
  require_person BOOLEAN DEFAULT FALSE,
  is_active BOOLEAN DEFAULT TRUE,
  PRIMARY KEY (id, device_id)
);

-- ROI
CREATE TABLE rois (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
  camera_id INTEGER NOT NULL,
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
  camera_id INTEGER NOT NULL,
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
  hour DATETIME NOT NULL,  -- 해당 시간의 정각 (예: 2026-09-16 14:00:00)
  detection_count INTEGER DEFAULT 0,
  PRIMARY KEY (device_id, hour)
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
  cpu REAL,
  temperature REAL,
  memory_used REAL,
  memory_total REAL,
  uptime_seconds INTEGER,
  latency_ms INTEGER,
  npu_ms INTEGER,
  updated_at DATETIME
);
```

---

## 16. 오류 코드 정의

| HTTP | error | 설명 |
|------|-------|------|
| 400 | VALIDATION_ERROR | 요청 바디 유효성 오류 |
| 400 | INVALID_POLYGON | ROI 폴리곤 자기교차 등 유효하지 않은 도형 |
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
| 503 | STREAM_CAPACITY_FULL | MJPEG 동시 스트림 5개 초과 |

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
| 7 | 디바이스 설정 폴링 (`/me/config`) | Pi 핫리로드 |
| 8 | Heartbeat API | 실시간 상태 업데이트 |
| 9 | 디바이스 제어 (재시작/재부팅) | 운영 편의 |
| 10 | 예약 재부팅 | 운영 편의 |
| 11 | 오디오 파일 관리 | ROI 편집 보조 |
| 12 | MJPEG 프록시 | 방화벽 환경 지원 |
| 13 | Pi 네트워크 스캔 | 등록 편의 |
