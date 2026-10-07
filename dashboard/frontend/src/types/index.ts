/**
 * 백엔드 명세(docs/backend_api_spec.md)의 응답 형태를 그대로 옮긴 타입.
 *
 * 목 데이터 시절의 타입과 세 군데가 달랐고, 전부 조용히 어긋나는 종류였다.
 *
 * - `Camera.id`는 **문자열**이다 (`"dev-cam0"`). Pi의 camera_config.json,
 *   `?camera=<id>` 쿼리, ROI 파일명(`rois.<id>.json`)이 전부 문자열 id를 쓴다.
 * - 해상도 필드 이름은 `capturePreset`이고 자유 문자열이 아니라 **프리셋 키**다.
 * - 메모리 단위는 **MB**다(GB 아님). Pi가 /proc에서 MB로 읽어 그대로 올린다.
 */

export type DeviceStatus = 'online' | 'offline' | 'warning' | 'unknown'

export type Memory = {
  used_mb: number | null
  total_mb: number | null
}

export type Camera = {
  id: string
  port: number
  capture_preset: string
  /** Pi에 대응 필드가 없어 서버가 보관만 한다 (명세 §4). */
  fps: number
  fps_applied: boolean
  model_variant: string
  rotation: number
  require_person: boolean
  /** 얼굴 모자이크 — 켜면 화면에 나가는 영상에서 사람 얼굴을 가린다(탐지에는 영향 없음). */
  privacy_mask: boolean
  is_active: boolean
  roi_count: number
  today_detections: number
  is_streaming: boolean
  current_alert: string | null
}

/** 목록 응답의 카메라 — 상세보다 필드가 적다. */
export type CameraBrief = {
  id: string
  port: number
  is_streaming: boolean
  privacy_mask: boolean
}

export type Roi = {
  id: number
  camera_id: string
  name: string
  zone_type: 'trigger' | 'exclude'
  priority: number
  announcement_text: string
  audio_file: string
  /** 서버 DB 전용 — Pi로는 보내지 않는다. */
  color: string
  is_active: boolean
  /** 정규화 0~1 좌표. Pi에서는 `points`라는 이름이다. */
  polygon: [number, number][]
}

/**
 * 이벤트는 **두 가지 모양**으로 온다 — 화면이 다르기 때문이다.
 *
 * - 기기 상세(`GET /api/devices/{id}`)의 `recent_events`: 그 기기 안의 표라서
 *   카메라·ROI를 짧은 이름으로 준다.
 * - 이벤트 목록(`GET /api/events`): 여러 기기를 가로지르므로 정규화된 id를 준다.
 */
export type RecentEvent = {
  id: number
  time: string
  camera: string
  roi: string | null
  confidence: number | null
  event_type: string
  timestamp: string
}

export type EventRow = {
  id: number
  device_id: string
  camera_id: string
  roi_id: number | null
  roi_name: string | null
  class_name: string | null
  /** 가상 지팡이 박스로 발사된 안내에는 신뢰도가 없다. */
  confidence: number | null
  event_type: string
  timestamp: string
  time_display: string
}

export type Device = {
  id: string
  name: string
  ip: string
  location: string | null
  status: DeviceStatus
  last_seen: string | null
  cpu: number | null
  temperature: number | null
  memory: Memory
  uptime: string | null
  uptime_seconds: number | null
  latency: number | null
  npu_ms: number | null
  today_detections: number
  cameras: CameraBrief[]
}

export type DeviceDetail = Omit<Device, 'cameras'> & {
  cameras: Camera[]
  rois: Roi[]
  recent_events: RecentEvent[]
  etag: string
}

export type DetectionParams = {
  conf: { white_cane: number; person: number }
  cooldown: number
  debounce: number
}

export type StatsSummary = {
  total_foot_traffic_today: number
  cane_user_count_today: number
  total_detections_today: number
  avg_confidence: number
  active_streams: number
  total_streams: number
  online_device_count: number
  total_device_count: number
  online_rate: number
  active_alert_count: number
  avg_cpu_temperature: number
  most_active_device: {
    id: string
    name: string
    location: string | null
    today_detections: number
  } | null
}

export type DeviceStat = {
  device_id: string
  name: string
  foot_traffic: number
  cane_users: number
  detections: number
}

export type TimeSeriesPoint = {
  hour?: number | null
  date?: string | null
  total_count: number
  cane_user_count: number
  detections: number
}

export type AudioFile = {
  filename: string
  label: string | null
  size_bytes: number
}

/** 기기(Pi) audio_dir에 이미 있는 파일. `path`는 Pi 로컬 절대경로. */
export type PiAudioFile = {
  name: string
  path: string
  size: number
}

/** 리모컨 음성 목록 항목 — 서버 라이브러리 파일명 또는 Pi 절대경로. */
export type RfAudioItem =
  | { source: 'library'; filename: string }
  | { source: 'pi'; path: string }

export type RfState = {
  config: Record<string, unknown>
  /** 현재 적용된 재생 순서 (Pi 절대경로). */
  audio_files: string[]
  pi_audio: PiAudioFile[]
}

export type Schedule = {
  id: number
  /** 0=일 … 6=토 (명세 §11). APScheduler와 하루 어긋나므로 서버가 변환한다. */
  days: number[]
  hour: number
  is_enabled: boolean
  display: string
}

export type ScanResult = {
  scan_id: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  progress: number
  total: number
  discovered: DiscoveredDevice[]
  error?: unknown
}

export type DiscoveredDevice = {
  ip: string
  hostname: string | null
  port: number
  version: string | null
  registered: boolean
  device_id: string | null
  already_registered: boolean
}

/** WebSocket 서버 → 클라이언트 메시지 (명세 §7). */
export type WsMessage =
  | { type: 'detection_event'; data: EventRow }
  | { type: 'device_status_change'; data: { device_id: string; status: DeviceStatus; timestamp: string } }
  | { type: 'alert'; data: { device_id: string; camera_id: string; message: string; timestamp: string } }
  | { type: 'ping' }

// ---------------------------------------------------------------- Pi 기능 중계
// 아래 타입은 Pi 응답을 **그대로** 옮긴 것이다(백엔드는 봉투만 씌운다).

/** 카메라 MJPEG 포트의 `/recording/status`. */
export type RecordingStatus =
  | { recording: false }
  | { recording: true; clip_id: string; elapsed_sec: number }

export type RecordingClip = {
  id: string
  started_at: string | null
  duration_sec: number | null
  size_bytes: number
  has_thumb: boolean
}

export type ReplayVideo = { name: string; size_mb: number; dir: string }

/** `device/replay_engine.py`의 `ReplaySession.status()`. 세션이 없으면 `{running:false, done:false}`만 온다. */
export type ReplayStatus = {
  running: boolean
  done: boolean
  video?: string
  paused?: boolean
  error?: string | null
  frame?: number
  total?: number
  fps?: number
  conf?: number
  counters?: Record<string, number>
  events?: { t: number; frame: number; roi: string; subject: string; audio: string }[]
}

/** 폐장 시간 구조물 수집 (`/calibrate/status`). */
export type CalibrationStatus = {
  running: boolean
  remaining_sec?: number
  frames?: number
  result?: Record<string, unknown> | null
}

/** 구조물 마스크 후보 (`static_mask.read_candidates`). bbox는 정규화 0~1. */
export type MaskCandidate = {
  id: number
  cls: number
  bbox: [number, number, number, number]
  hits: number
  frames: number
  max_disp: number
  max_conf: number
  thumb: string | null
  collected_at: number
  applied: boolean
  /** 지팡이=기본 선택, 사람=기본 해제 — 사람 마스크는 그 자리의 진짜 사람을 가릴 수 있다. */
  recommend: boolean
}

export type MaskHit = { cls: number; count: number; last_ts: number }

/** 오탐 지점 (`fp_hotspots.read_hotspots`). bbox는 정규화 0~1 평균 박스. */
export type FpHotspot = { cell: [number, number]; count: number; bbox: [number, number, number, number] }

export type NetworkStatus = {
  mode: 'ap' | 'station' | 'disconnected'
  ssid: string | null
  ip: string | null
  hostname: string
}

export type WifiNetwork = { ssid: string; signal_pct: number; security: string }

export type WifiConnectResult = {
  in_progress: boolean
  /** `network_manager._do_connect`가 남긴 값. */
  result: { status: 'ok'; ip: string | null } | { status: 'error'; error: string } | null
}

// ---------------------------------------------------------------- 구조물 수집 (일괄·예약)

/** 수집 대상 — 전체 / 모델별 / 선택한 기기. */
export type CalibrationTargetMode = 'all' | 'model' | 'devices'

/** 카메라 한 대에 수집을 시킨 기록. `ok`는 **시작 요청**의 결과다. */
export type CalibrationRun = {
  id?: number
  batch_id?: string
  schedule_id?: number | null
  device_id: string
  device_name: string
  camera_id: string
  model_variant: string | null
  seconds?: number
  started_at?: string
  ok: boolean
  error: string | null
}

export type CalibrationFleetDevice = {
  id: string
  name: string
  location: string | null
  status: DeviceStatus
  cameras: {
    id: string
    port: number
    model_variant: string | null
    is_active: boolean
    last_run: CalibrationRun | null
  }[]
}

export type CalibrationSchedule = {
  id: number
  name: string
  days: number[]
  hour: number
  minute: number
  seconds: number
  target_mode: CalibrationTargetMode
  targets: string[]
  is_enabled: boolean
  display: string
}
