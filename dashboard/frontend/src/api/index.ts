/** 엔드포인트별 얇은 래퍼. 화면은 여기만 부르고 URL을 직접 조립하지 않는다. */

import { qs, request, requestEnvelope, setToken } from './client'
import type {
  AudioFile, Camera, Device, DeviceDetail, DeviceStat, DetectionParams, EventRow,
  Roi, ScanResult, Schedule, StatsSummary, TimeSeriesPoint,
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

export const listDevices = (search?: string) =>
  requestEnvelope<{ data: Device[]; total: number }>(`/api/devices${qs({ search })}`)

export const getDevice = (id: string) => request<DeviceDetail>(`/api/devices/${id}`)

export const createDevice = (body: { id: string; name: string; ip: string; location?: string }) =>
  request<{ id: string; api_key: string; provisioned: boolean; provision_error: string | null; synced: boolean }>(
    '/api/devices', { method: 'POST', body })

export const updateDevice = (id: string, body: { name?: string; location?: string }) =>
  request<{ id: string; name: string }>(`/api/devices/${id}`, { method: 'PATCH', body })

export const deleteDevice = (id: string) =>
  request<void>(`/api/devices/${id}`, { method: 'DELETE' })

/** 신원 재주입 — 기기가 꺼져 있어 등록 시 실패했을 때. **새 api_key가 발급된다.** */
export const provisionDevice = (id: string) =>
  request<{ id: string; api_key: string }>(`/api/devices/${id}/provision`, { method: 'POST' })

export const restartDevice = (id: string) =>
  request<{ job_id: string; message: string }>(`/api/devices/${id}/restart`, { method: 'POST' })

export const rebootDevice = (id: string) =>
  request<{ job_id: string; message: string }>(`/api/devices/${id}/reboot`, { method: 'POST' })

// ---------------------------------------------------------------- 카메라

/** `stale: true`면 기기가 꺼져 있어 캐시를 보여주는 중이다. */
export const listCameras = (deviceId: string) =>
  requestEnvelope<{ data: Camera[]; stale: boolean; etag: string }>(`/api/devices/${deviceId}/cameras`)

export const updateCamera = (
  deviceId: string, cameraId: string, etag: string,
  body: Partial<Pick<Camera, 'capture_preset' | 'fps' | 'model_variant' | 'rotation' | 'require_person'>>,
) => request<Camera>(`/api/devices/${deviceId}/cameras/${cameraId}`, { method: 'PATCH', body, etag })

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

export const getScan = (scanId: string) => request<ScanResult>(`/api/scan/${scanId}`)

export const verifyDevice = (ip: string, port = 5000) =>
  request<{ reachable: boolean; version?: string | null; camera_count?: number | null; already_registered?: boolean }>(
    '/api/scan/verify', { method: 'POST', body: { ip, port } })
