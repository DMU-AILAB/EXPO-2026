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
