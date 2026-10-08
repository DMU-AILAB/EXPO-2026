/** 엔드포인트별 얇은 래퍼. 화면은 여기만 부르고 URL을 직접 조립하지 않는다. */

import { qs, request, requestEnvelope, setToken } from './client'
import type {
  AudioFile, CalibrationFleetDevice, CalibrationRun, CalibrationSchedule,
  CalibrationStatus, CalibrationTargetMode, Camera, Device, DeviceDetail, DeviceStat, DetectionParams, EventRow,
  FpHotspot, MaskCandidate, MaskHit, NetworkStatus, RecordingClip, RecordingStatus, ReplayStatus,
  ReplayVideo, RfAudioItem, RfState, Roi, ScanResult, Schedule, StatsSummary, TimeSeriesPoint,
  WifiConnectResult, WifiNetwork, Esp32Overview,
} from '../types'

// ---------------------------------------------------------------- 인증

export async function login(username: string, password: string) {
  const res = await request<{ access_token: string; expires_in: number }>(
    '/api/auth/login', { method: 'POST', body: { username, password } })
  setToken(res.access_token)
  return res
}

export async function logout() {
  try {
    await request<void>('/api/auth/logout', { method: 'POST' })
  } finally {
    // 서버가 실패해도 클라이언트 토큰은 반드시 버린다.
    setToken(null)
  }
}

export const me = () =>
  request<{ id: number; username: string; display_name: string | null; team: string | null }>('/api/auth/me')

// ---------------------------------------------------------------- 디바이스

type DeviceListResponse = { data: Device[]; total: number }
const deviceListCache = new Map<string, DeviceListResponse>()

export const getCachedDeviceList = (search?: string) =>
  deviceListCache.get(search ?? '') ?? null

export const listDevices = async (search?: string) => {
  const result = await requestEnvelope<DeviceListResponse>(`/api/devices${qs({ search })}`)
  deviceListCache.set(search ?? '', result)
  return result
}

export const getDevice = (id: string) => request<DeviceDetail>(`/api/devices/${id}`)

export const createDevice = (body: {
  id: string; name: string; ip: string; location?: string
}) =>
  request<{ id: string; api_key: string; provisioned: boolean; provision_error: string | null; synced: boolean }>(
    '/api/devices', { method: 'POST', body })

export const updateDevice = (id: string, body: { name?: string; location?: string }) =>
  request<{ id: string; name: string }>(`/api/devices/${id}`, { method: 'PATCH', body })

export const deleteDevice = (id: string) =>
  request<void>(`/api/devices/${id}`, { method: 'DELETE' })

/** 신원 재주입 — 기기가 꺼져 있어 등록 시 실패했을 때. **새 api_key가 발급된다.** */
export const provisionDevice = (id: string) =>
  request<{ id: string; api_key: string }>(`/api/devices/${id}/provision`, {
    method: 'POST',
  })

export const restartDevice = (id: string) =>
  request<{ job_id: string; message: string }>(`/api/devices/${id}/restart`, { method: 'POST' })

export const rebootDevice = (id: string) =>
  request<{ job_id: string; message: string }>(`/api/devices/${id}/reboot`, { method: 'POST' })

// ---------------------------------------------------------------- 코드 업데이트

/** 기기별 업데이트 결과 — 한 대가 실패해도 나머지는 계속 진행되므로 배열로 온다. */
export interface UpdateResult {
  device_id: string
  ok: boolean
  bundle_id?: string
  applied?: number | null
  /** 재시작 뒤 새 번들로 돌아왔는가. false면 기기를 확인해야 한다. */
  came_back?: boolean
  error?: string
}

export interface DeviceUpdateStatus {
  bundle_id: string
  has_backup: boolean
  latest: string
  up_to_date: boolean
  error?: string
}

export const updateDevices = (deviceIds: string[], includeModels = false) =>
  request<{ bundle_id: string; results: UpdateResult[] }>('/api/devices/update', {
    method: 'POST',
    body: { device_ids: deviceIds, include_models: includeModels },
  })

export const getDeviceUpdateStatus = (id: string) =>
  request<DeviceUpdateStatus>(`/api/devices/${id}/update-status`)

export const rollbackDeviceUpdate = (id: string) =>
  request<unknown>(`/api/devices/${id}/update/rollback`, { method: 'POST' })

// ---------------------------------------------------------------- 카메라

/** `stale: true`면 기기가 꺼져 있어 캐시를 보여주는 중이다. */
export const listCameras = (deviceId: string) =>
  requestEnvelope<{ data: Camera[]; stale: boolean; etag: string }>(`/api/devices/${deviceId}/cameras`)

export const updateCamera = (
  deviceId: string, cameraId: string, etag: string,
  body: Partial<Pick<Camera, 'capture_preset' | 'fps' | 'model_variant' | 'rotation' | 'require_person' | 'privacy_mask'>>,
) => request<Camera>(`/api/devices/${deviceId}/cameras/${cameraId}`, { method: 'PATCH', body, etag })

/** 기기의 모든 카메라에 얼굴 모자이크를 켜고 끈다(디바이스 목록 토글). */
export const setPrivacyMask = (deviceId: string, enabled: boolean) =>
  request<{ ok: boolean; enabled: boolean; cameras: number }>(
    `/api/devices/${deviceId}/privacy-mask`, { method: 'PUT', body: { enabled } })

export const getDetectionParams = (deviceId: string, cameraId: string) =>
  request<DetectionParams>(`/api/devices/${deviceId}/cameras/${cameraId}/detection-params`)

export const updateDetectionParams = (
  deviceId: string, cameraId: string, etag: string, body: Partial<DetectionParams>,
) => request<DetectionParams>(`/api/devices/${deviceId}/cameras/${cameraId}/detection-params`,
  { method: 'PATCH', body, etag })

// ---------------------------------------------------------------- ROI

export const listRois = (deviceId: string, cameraId?: string) =>
  requestEnvelope<{ data: Roi[]; stale: boolean; etag: string }>(
    `/api/devices/${deviceId}/rois${qs({ camera_id: cameraId })}`)

export const createRoi = (deviceId: string, etag: string, body: Partial<Roi> & { camera_id: string; name: string; polygon: [number, number][] }) =>
  request<Roi>(`/api/devices/${deviceId}/rois`, { method: 'POST', body, etag })

export const updateRoi = (deviceId: string, roiId: number, etag: string, body: Partial<Roi>) =>
  request<Roi>(`/api/devices/${deviceId}/rois/${roiId}`, { method: 'PATCH', body, etag })

export const deleteRoi = (deviceId: string, roiId: number, etag: string) =>
  request<void>(`/api/devices/${deviceId}/rois/${roiId}`, { method: 'DELETE', etag })

// ---------------------------------------------------------------- 이벤트 · 통계

export const listEvents = (params: {
  device_id?: string; camera_id?: string; start?: string; end?: string
  limit?: number; offset?: number
} = {}) =>
  requestEnvelope<{ data: EventRow[]; total: number }>(`/api/events${qs(params)}`)

export const statsSummary = (date?: string) =>
  request<StatsSummary>(`/api/stats/summary${qs({ date })}`)

export const statsByDevice = (date?: string) =>
  request<DeviceStat[]>(`/api/stats/devices${qs({ date })}`)

/** `granularity`를 생략하면 서버가 period에 맞춰 고른다. */
export const statsTimeseries = (period: 'today' | '7d' | '30d', deviceId?: string,
                                granularity?: 'hourly' | 'daily') =>
  requestEnvelope<{ data: TimeSeriesPoint[]; period: string; granularity: string }>(
    `/api/stats/timeseries${qs({ period, device_id: deviceId, granularity })}`)

// ---------------------------------------------------------------- 오디오

export const listAudio = () => request<AudioFile[]>('/api/audio')

export const uploadAudio = (file: File, label?: string) => {
  const form = new FormData()
  form.append('file', file)
  if (label) form.append('label', label)
  return request<AudioFile>('/api/audio/upload', { method: 'POST', form })
}

export const generateTts = (body: {
  text: string
  label?: string
  voice?: string
  rate?: number
}) => request<AudioFile>('/api/audio/tts', { method: 'POST', body })

// ---------------------------------------------------------------- RF 리모컨

export const getRf = (deviceId: string) => request<RfState>(`/api/devices/${deviceId}/rf`)

/** 리모컨을 한 번 누르면 items 순서대로 이어서 재생한다. */
export const setRfAudio = (deviceId: string, items: RfAudioItem[]) =>
  request<{ audio_files: string[] }>(`/api/devices/${deviceId}/rf/audio`, {
    method: 'PUT', body: { items },
  })

/** 군집 제어 — 같은 누름을 들은 기기들이 priority 순(작을수록 먼저)으로 한 대씩 재생. */
export const setRfGroup = (deviceId: string, groupEnabled: boolean, groupPriority: number) =>
  request<{ group_enabled: boolean; group_priority: number }>(`/api/devices/${deviceId}/rf/group`, {
    method: 'PUT', body: { group_enabled: groupEnabled, group_priority: groupPriority },
  })

/** 리모컨 감지 임계값(RSSI 1~255). 낮을수록 먼 거리에서도 반응하지만 오반응 위험이 커진다. */
export const setRfDetection = (deviceId: string, rssiThreshold: number) =>
  request<{ rssi_threshold: number }>(`/api/devices/${deviceId}/rf/detection`, {
    method: 'PUT', body: { rssi_threshold: rssiThreshold },
  })

/** group_enabled인 모든 기기를 priority 오름차순으로 반환. 상대적 순위 계산용. */
export const getGroupOverview = () =>
  request<{ id: string; name: string; priority: number; group_enabled: boolean; online: boolean }[]>(
    '/api/rf/group'
  )

// ---------------------------------------------------------------- 예약 재부팅

export const listSchedules = (deviceId: string) =>
  request<Schedule[]>(`/api/devices/${deviceId}/schedules`)

export const createSchedule = (deviceId: string, body: { days: number[]; hour: number; is_enabled?: boolean }) =>
  request<Schedule>(`/api/devices/${deviceId}/schedules`, { method: 'POST', body })

export const updateSchedule = (deviceId: string, id: number, body: Partial<{ days: number[]; hour: number; is_enabled: boolean }>) =>
  request<Schedule>(`/api/devices/${deviceId}/schedules/${id}`, { method: 'PATCH', body })

export const deleteSchedule = (deviceId: string, id: number) =>
  request<void>(`/api/devices/${deviceId}/schedules/${id}`, { method: 'DELETE' })

// ---------------------------------------------------------------- 탐색

export const startScan = (subnet: string, port = 5000) =>
  request<{ scan_id: string; status: string }>('/api/scan/network', { method: 'POST', body: { subnet, port } })

/** 서버 주소(`PUBLIC_BASE_URL`)가 속한 /24 — 탐색 서브넷의 기본값. */
export const getSuggestedSubnet = () => request<{ subnet: string | null }>('/api/scan/suggest')

export const getScan = (scanId: string) => request<ScanResult>(`/api/scan/${scanId}`)

export const verifyDevice = (ip: string, port = 5000) =>
  request<{ reachable: boolean; version?: string | null; camera_count?: number | null; already_registered?: boolean }>(
    '/api/scan/verify', { method: 'POST', body: { ip, port } })

// ---------------------------------------------------------------- 녹화 (카메라 포트 중계)

const camBase = (deviceId: string, cameraId: string) => `/api/devices/${deviceId}/cameras/${cameraId}`

export const getRecordingStatus = (deviceId: string, cameraId: string) =>
  request<RecordingStatus>(`${camBase(deviceId, cameraId)}/recording`)

/** `raw`=학습용 촬영(오버레이 없는 원본). */
export const startRecording = (deviceId: string, cameraId: string, raw = false) =>
  request<{ ok: boolean; clip_id?: string }>(
    `${camBase(deviceId, cameraId)}/recording/start${qs({ raw: raw || undefined })}`, { method: 'POST' })

export const stopRecording = (deviceId: string, cameraId: string) =>
  request<{ ok: boolean; clip_id?: string; duration_sec?: number }>(
    `${camBase(deviceId, cameraId)}/recording/stop`, { method: 'POST' })

export const listRecordings = (deviceId: string, cameraId: string) =>
  request<RecordingClip[]>(`${camBase(deviceId, cameraId)}/recording/clips`)

// ---------------------------------------------------------------- 검증 재생

export const listReplayVideos = (deviceId: string) =>
  request<ReplayVideo[]>(`/api/devices/${deviceId}/replay/videos`)

export const startReplay = (deviceId: string, body: {
  video: string; conf?: number; model_variant?: string; require_person?: boolean
  speed?: number; loop?: boolean; debug_gates?: boolean
}) => request<{ ok: boolean; video: string; rois: number }>(
  `/api/devices/${deviceId}/replay/start`, { method: 'POST', body })

/** `paused`를 비우면 토글. */
export const pauseReplay = (deviceId: string, paused?: boolean) =>
  request<{ paused: boolean }>(`/api/devices/${deviceId}/replay/pause`,
    { method: 'POST', body: { paused: paused ?? null } })

export const stepReplay = (deviceId: string) =>
  request<{ ok: boolean }>(`/api/devices/${deviceId}/replay/step`, { method: 'POST' })

export const stopReplay = (deviceId: string) =>
  request<{ ok: boolean }>(`/api/devices/${deviceId}/replay/stop`, { method: 'POST' })

export const getReplayStatus = (deviceId: string) =>
  request<ReplayStatus>(`/api/devices/${deviceId}/replay/status`)

// ---------------------------------------------------------------- 오탐 관리

export const getCalibration = (deviceId: string, cameraId: string) =>
  request<CalibrationStatus>(`${camBase(deviceId, cameraId)}/calibration`)

export const startCalibration = (deviceId: string, cameraId: string, seconds: number) =>
  request<{ ok: boolean; error?: string }>(`${camBase(deviceId, cameraId)}/calibration/start`,
    { method: 'POST', body: { seconds } })

export const cancelCalibration = (deviceId: string, cameraId: string) =>
  request<{ ok: boolean }>(`${camBase(deviceId, cameraId)}/calibration/cancel`, { method: 'POST' })

export const listMaskCandidates = (deviceId: string, cameraId: string) =>
  request<MaskCandidate[]>(`${camBase(deviceId, cameraId)}/static-mask`)

/** **선택한 것만 남는다** — 빈 목록이면 마스크를 모두 끈다. */
export const applyMask = (deviceId: string, cameraId: string, ids: number[]) =>
  request<{ ok: boolean; applied: number }>(`${camBase(deviceId, cameraId)}/static-mask`,
    { method: 'PUT', body: { ids } })

export const clearMask = (deviceId: string, cameraId: string) =>
  request<{ ok: boolean }>(`${camBase(deviceId, cameraId)}/static-mask`, { method: 'DELETE' })

export const getMaskHits = (deviceId: string, cameraId: string) =>
  request<MaskHit[]>(`${camBase(deviceId, cameraId)}/static-mask/hits`)

export const listFpHotspots = (deviceId: string, cameraId: string, minCount = 30) =>
  request<FpHotspot[]>(`${camBase(deviceId, cameraId)}/fp-hotspots${qs({ min_count: minCount })}`)

export const clearFpHotspots = (deviceId: string, cameraId: string) =>
  request<{ ok: boolean }>(`${camBase(deviceId, cameraId)}/fp-hotspots`, { method: 'DELETE' })

// ---------------------------------------------------------------- 네트워크

export const getNetwork = (deviceId: string) =>
  request<NetworkStatus>(`/api/devices/${deviceId}/network`)

export const scanWifi = (deviceId: string) =>
  request<WifiNetwork[]>(`/api/devices/${deviceId}/network/scan`)

export const connectWifi = (deviceId: string, ssid: string, password: string) =>
  request<{ ok: boolean; delay_seconds: number }>(`/api/devices/${deviceId}/network/connect`,
    { method: 'POST', body: { ssid, password } })

export const getWifiConnectResult = (deviceId: string) =>
  request<WifiConnectResult>(`/api/devices/${deviceId}/network/connect-result`)

// ---------------------------------------------------------------- ESP32 BLE 출력 장치

export const getEsp32 = (deviceId: string) =>
  request<Esp32Overview>(`/api/devices/${deviceId}/esp32`)

export const bindEsp32 = (deviceId: string, esp32Id: string) =>
  request<{ binding: string }>(`/api/devices/${deviceId}/esp32/binding`, {
    method: 'POST', body: { esp32_id: esp32Id },
  })

export const unbindEsp32 = (deviceId: string) =>
  request<{ binding: null }>(`/api/devices/${deviceId}/esp32/binding`, { method: 'DELETE' })

export const configureEsp32Wifi = (deviceId: string, ssid: string, password: string) =>
  request<Esp32Overview['command']>(`/api/devices/${deviceId}/esp32/wifi`, {
    method: 'POST', body: { ssid, password },
  })

// ---------------------------------------------------------------- 구조물 수집 (일괄·예약)

type CalibrationTarget = { target_mode: CalibrationTargetMode; targets: string[] }
type CalibrationScheduleBody = CalibrationTarget & {
  name: string; days: number[]; hour: number; minute: number; seconds: number; is_enabled: boolean
}

export const getCalibrationTargets = () =>
  request<{ devices: CalibrationFleetDevice[]; model_variants: string[] }>('/api/calibration/targets')

/** 지금 수집 중인 기기 → {남은 시간, 카메라 수}. 서버가 기기에 직접 묻는다. */
export const getActiveCalibrations = () =>
  request<Record<string, { remaining_sec: number | null; cameras: number }>>('/api/calibration/active')

export const listCalibrationRuns = (limit = 100) =>
  request<CalibrationRun[]>(`/api/calibration/runs${qs({ limit })}`)

export const listCalibrationSchedules = () =>
  request<CalibrationSchedule[]>('/api/calibration/schedules')

export const createCalibrationSchedule = (body: CalibrationScheduleBody) =>
  request<CalibrationSchedule>('/api/calibration/schedules', { method: 'POST', body })

export const updateCalibrationSchedule = (id: number, body: Partial<CalibrationScheduleBody>) =>
  request<CalibrationSchedule>(`/api/calibration/schedules/${id}`, { method: 'PATCH', body })

export const deleteCalibrationSchedule = (id: number) =>
  request<void>(`/api/calibration/schedules/${id}`, { method: 'DELETE' })
