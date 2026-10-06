import { API_BASE, getToken } from '../api/client'

export function streamUrl(deviceId: string, cameraId: string): string {
  const token = getToken() ?? ''
  return `${API_BASE}/api/devices/${deviceId}/cameras/${cameraId}/stream?token=${encodeURIComponent(token)}`
}


/** `<img>`/`<video>`/다운로드 링크용 — 헤더를 못 붙이므로 쿼리 토큰을 단다. */
export function authedUrl(path: string, params: Record<string, string> = {}): string {
  const sp = new URLSearchParams({ ...params, token: getToken() ?? '' })
  return `${API_BASE}${path}?${sp.toString()}`
}

/** 검증 재생 MJPEG. `nonce`를 바꾸면 브라우저가 새 연결을 연다(재생을 새로 시작했을 때). */
export function replayStreamUrl(deviceId: string, nonce: number): string {
  return authedUrl(`/api/devices/${deviceId}/replay/stream`, { n: String(nonce) })
}
