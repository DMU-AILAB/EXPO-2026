import { API_BASE, getToken } from '../api/client'

export function streamUrl(deviceId: string, cameraId: string): string {
  const token = getToken() ?? ''
  return `${API_BASE}/api/devices/${deviceId}/cameras/${cameraId}/stream?token=${encodeURIComponent(token)}`
}

