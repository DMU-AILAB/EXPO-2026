import { useState } from 'react'
import { Unlink, VideoOff } from 'lucide-react'

import { API_BASE, getToken } from '../api/client'
import type { DeviceStatus, Camera as CameraType, CameraBrief } from '../types'

interface StreamThumbnailProps {
  status: DeviceStatus
  deviceId: string
  camera?: CameraType | CameraBrief
  deviceName?: string
}

/**
 * 백엔드의 MJPEG 프록시 URL (명세 §14).
 *
 * `<img>`는 Authorization 헤더를 붙일 수 없어 **쿼리 토큰**으로 인증한다 —
 * 백엔드의 `get_current_user_or_query`가 그래서 두 경로를 모두 받는다.
 *
 * Pi에 직접 붙지 않고 프록시를 거치는 이유는 방화벽 환경 때문이다. 대신 동시 5개
 * 제한이 있어(§14) 카드가 많은 화면에서는 전부 열면 503이 난다.
 */
export function streamUrl(deviceId: string, cameraId: string): string {
  const token = getToken() ?? ''
  return `${API_BASE}/api/devices/${deviceId}/cameras/${cameraId}/stream?token=${encodeURIComponent(token)}`
}

function OnlineStream({ deviceId, camera }: { deviceId: string; camera: CameraType | CameraBrief }) {
  const [failed, setFailed] = useState(false)
  const alert = 'current_alert' in camera ? camera.current_alert : null
  const hasAlert = Boolean(alert)

  if (failed) {
    return (
      <div className="relative w-full aspect-video rounded-xl bg-slate-900/95 border border-slate-300/40 overflow-hidden flex flex-col items-center justify-center">
        <VideoOff className="w-5 h-5 text-slate-400 mb-1.5" />
        <span className="text-[11px] font-semibold text-slate-400">스트림을 불러오지 못했습니다</span>
      </div>
    )
  }

  return (
    <div className={`relative w-full aspect-video rounded-xl overflow-hidden transition-all duration-500 ${
      hasAlert
        ? 'border border-amber-500/60 shadow-[0_0_20px_rgba(245,158,11,0.20)]'
        : 'border border-emerald-500/55 shadow-[0_0_20px_rgba(16,185,129,0.20)]'
    }`}>
      <img
        src={streamUrl(deviceId, camera.id)}
        alt=""
        onError={() => setFailed(true)}
        className="absolute inset-0 w-full h-full object-cover bg-slate-900"
        draggable={false}
      />
      <div className="absolute inset-0 stream-scanlines opacity-25 pointer-events-none" />

      <div className="absolute top-2.5 right-2.5 px-2 py-0.5 rounded-full bg-red-600/90 backdrop-blur-sm text-white text-[10px] font-bold tracking-wider flex items-center gap-1 shadow-sm">
        <span className="w-1.5 h-1.5 rounded-full bg-white animate-pulse" />
        LIVE
      </div>

      {hasAlert && (
        <div className="absolute bottom-2.5 left-2.5 right-2.5 px-2 py-1 rounded-lg bg-amber-500/90 text-white text-[10.5px] font-bold truncate">
          {alert}
        </div>
      )}
    </div>
  )
}

function OfflineStream({ reason }: { reason: string }) {
  return (
    <div className="relative w-full aspect-video rounded-xl bg-slate-900/95 border border-red-300/40 overflow-hidden flex flex-col items-center justify-center shadow-inner">
      <div className="w-12 h-12 rounded-full bg-red-500/15 border border-red-500/30 flex items-center justify-center text-red-400 mb-2 shadow-md">
        <Unlink className="w-5 h-5" />
      </div>
      <span className="text-xs font-bold text-red-300 tracking-tight">{reason}</span>
    </div>
  )
}

export default function StreamThumbnail({ status, deviceId, camera }: StreamThumbnailProps) {
  if (status === 'offline') return <OfflineStream reason="연결 끊김" />
  if (!camera) return <OfflineStream reason="카메라 없음" />
  // 기기는 살아 있는데 파이프라인이 죽은 경우를 구분해서 보여준다.
  if (!camera.is_streaming) return <OfflineStream reason="스트리밍 중지" />
  return <OnlineStream deviceId={deviceId} camera={camera} />
}
