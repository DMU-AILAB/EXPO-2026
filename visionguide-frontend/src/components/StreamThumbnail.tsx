import { Camera, Unlink, Volume2, AlertTriangle, CheckCircle2 } from 'lucide-react'
import type { DeviceStatus, Camera as CameraType } from '../types'

interface StreamThumbnailProps {
  status: DeviceStatus
  camera?: CameraType
  deviceName: string
}

function OnlineStream({ camera, deviceName }: { camera: CameraType; deviceName: string }) {
  const hasAlert = !!camera.currentAlert

  return (
    <div className="relative w-full h-48 rounded-xl bg-slate-900 border border-slate-800/80 overflow-hidden flex items-center justify-center stream-scanlines shadow-inner">
      {/* SVG Detection Overlay */}
      <svg className="absolute inset-0 w-full h-full opacity-70 pointer-events-none" fill="none" viewBox="0 0 320 180">
        <line stroke="rgba(255,255,255,0.2)" strokeDasharray="3 3" x1="0" y1="180" x2="160" y2="80" />
        <line stroke="rgba(255,255,255,0.2)" strokeDasharray="3 3" x1="320" y1="180" x2="160" y2="80" />
        {hasAlert ? (
          <>
            <polygon
              points="100,180 140,80 180,80 220,180"
              fill="rgba(245,166,35,0.22)"
              stroke="#f59e0b"
              strokeWidth="1.6"
            />
            <rect
              x="155" y="110" width="45" height="40" rx="2"
              fill="rgba(245,158,11,0.25)" stroke="#f59e0b" strokeDasharray="3 2" strokeWidth="1.8"
            />
            <text x="142" y="105" fill="#fcd34d" fontFamily="monospace" fontSize="8" fontWeight="bold">
              OBSTACLE DETECTED
            </text>
          </>
        ) : (
          <>
            <polygon
              points="120,180 145,100 175,100 200,180"
              fill="rgba(245,166,35,0.2)"
              stroke="rgba(245,166,35,0.5)"
              strokeWidth="1.2"
            />
            <rect
              x="140" y="70" width="70" height="85" rx="3"
              fill="rgba(96,165,250,0.2)" stroke="#60a5fa" strokeDasharray="4 2" strokeWidth="2"
            />
            <line stroke="#ffffff" strokeLinecap="round" strokeWidth="2.5" x1="150" y1="145" x2="195" y2="90" />
            <text x="142" y="66" fill="#93c5fd" fontFamily="monospace" fontSize="8.5" fontWeight="bold">
              WHITE_CANE 96.4%
            </text>
          </>
        )}
      </svg>

      {/* LIVE badge */}
      <div className="absolute top-2.5 right-2.5 px-2.5 py-0.5 rounded-full bg-red-600 text-white text-[10px] font-bold tracking-wider flex items-center gap-1 shadow-sm">
        <span className="w-1.5 h-1.5 rounded-full bg-white animate-pulse" />
        LIVE
      </div>

      {/* Resolution */}
      <div className="absolute top-2.5 left-2.5 text-[9.5px] font-mono text-slate-200 bg-black/60 backdrop-blur-sm px-2 py-0.5 rounded border border-white/15">
        {camera.resolution} · {camera.fps} FPS
      </div>

      {/* Camera icon center */}
      <div className="w-11 h-11 rounded-full bg-white/20 backdrop-blur-md border border-white/30 flex items-center justify-center text-white group-hover:scale-110 group-hover:bg-[#2c4be0] transition-all shadow-md">
        <Camera className="w-5 h-5" />
      </div>

      {/* Bottom bar */}
      <div
        className={`absolute bottom-2 left-2 right-2 flex items-center justify-between text-[10.5px] px-2.5 py-1 rounded-lg backdrop-blur-sm border ${
          hasAlert
            ? 'text-amber-300 bg-black/75 border-amber-400/40'
            : 'text-slate-200 bg-black/70 border-white/10'
        }`}
      >
        <span className="flex items-center gap-1.5 font-medium">
          {hasAlert ? (
            <>
              <AlertTriangle className="w-3.5 h-3.5 text-amber-400" />
              {camera.currentAlert}
            </>
          ) : (
            <>
              <Volume2 className="w-3.5 h-3.5 text-emerald-400" />
              음성유도 송출 대기
            </>
          )}
        </span>
        <span className="font-mono text-blue-300 font-bold">{deviceName.slice(0, 3).toUpperCase()} {camera.port}</span>
      </div>
    </div>
  )
}

function OfflineStream() {
  return (
    <div className="relative w-full h-48 rounded-xl bg-slate-900/95 border border-red-300/40 overflow-hidden flex flex-col items-center justify-center shadow-inner">
      <div className="w-12 h-12 rounded-full bg-red-500/15 border border-red-500/30 flex items-center justify-center text-red-400 mb-2 transition-transform shadow-md">
        <Unlink className="w-5 h-5" />
      </div>
      <span className="text-xs font-bold text-red-300 tracking-tight">연결 끊김</span>
      <span className="text-[10.5px] text-slate-400 mt-0.5">RTSP 소켓 타임아웃</span>
      <div className="absolute bottom-2 left-2 right-2 flex items-center justify-between text-[10.5px] text-red-300 bg-red-950/70 px-2.5 py-1 rounded-lg border border-red-500/30 backdrop-blur-sm">
        <span className="flex items-center gap-1.5 font-medium">
          <CheckCircle2 className="w-3.5 h-3.5 text-red-400" />
          네트워크 응답 없음
        </span>
        <button className="underline font-bold text-red-200 hover:text-white transition text-[10.5px]">
          재연결 시도
        </button>
      </div>
    </div>
  )
}

export default function StreamThumbnail({ status, camera, deviceName }: StreamThumbnailProps) {
  if (status === 'offline' || !camera) return <OfflineStream />
  return <OnlineStream camera={camera} deviceName={deviceName} />
}
