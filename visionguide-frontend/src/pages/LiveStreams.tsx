import { useState } from 'react'
import { Maximize2, Camera, Unlink, Volume2, AlertTriangle } from 'lucide-react'
import { mockDevices } from '../data/mockData'
import type { Device, Camera as CameraType } from '../types'

interface StreamItem {
  device: Device
  camera: CameraType
}

const LAYOUTS = [
  { label: '1×1', cols: 1 },
  { label: '2×2', cols: 2 },
  { label: '3×3', cols: 3 },
  { label: '4×4', cols: 4 },
] as const

function StreamCell({ item, fullHeight }: { item: StreamItem; fullHeight?: boolean }) {
  const { device, camera } = item
  const isOffline = device.status === 'offline'
  const hasAlert = !!camera.currentAlert

  if (isOffline) {
    return (
      <div className="relative bg-black/90 rounded-xl overflow-hidden border border-slate-800 flex flex-col items-center justify-center" style={{ minHeight: fullHeight ? '100%' : 240 }}>
        <div className="w-12 h-12 rounded-full bg-red-500/15 border border-red-500/30 flex items-center justify-center text-red-400 mb-2">
          <Unlink className="w-5 h-5" />
        </div>
        <span className="text-xs font-bold text-red-300">카메라 연결 끊김</span>
        <span className="text-[10px] text-slate-500 mt-0.5">{device.name}</span>
      </div>
    )
  }

  return (
    <div
      className="relative bg-slate-900 rounded-xl overflow-hidden border border-slate-800/60 group"
      style={{ minHeight: fullHeight ? '100%' : 240 }}
    >
      {/* Scanlines bg */}
      <div className="absolute inset-0 stream-scanlines opacity-60" />

      {/* SVG detection overlay */}
      <svg className="absolute inset-0 w-full h-full opacity-60 pointer-events-none" fill="none" viewBox="0 0 320 180" preserveAspectRatio="xMidYMid slice">
        <line stroke="rgba(255,255,255,0.15)" strokeDasharray="3 3" x1="0" y1="180" x2="160" y2="80" />
        <line stroke="rgba(255,255,255,0.15)" strokeDasharray="3 3" x1="320" y1="180" x2="160" y2="80" />
        {hasAlert ? (
          <>
            <polygon points="100,180 140,80 180,80 220,180" fill="rgba(245,158,11,0.2)" stroke="#f59e0b" strokeWidth="1.4" />
            <text x="130" y="72" fill="#fcd34d" fontFamily="monospace" fontSize="8" fontWeight="bold">OBSTACLE</text>
          </>
        ) : (
          <>
            <rect x="140" y="70" width="70" height="85" rx="3" fill="rgba(96,165,250,0.15)" stroke="#60a5fa" strokeDasharray="4 2" strokeWidth="1.8" />
            <line stroke="#ffffff" strokeLinecap="round" strokeWidth="2" x1="150" y1="145" x2="195" y2="90" />
            <text x="142" y="66" fill="#93c5fd" fontFamily="monospace" fontSize="7.5" fontWeight="bold">CANE 96%</text>
          </>
        )}
      </svg>

      {/* Top overlay */}
      <div className="absolute top-0 left-0 right-0 px-3 py-2 flex items-center justify-between"
        style={{ background: 'linear-gradient(to bottom, rgba(0,0,0,0.75), transparent)' }}>
        <span className="text-[10.5px] font-bold text-white">{device.name}</span>
        <span className="text-[10px] font-mono text-slate-300 bg-black/40 px-1.5 py-0.5 rounded">
          카메라 {camera.id}
        </span>
      </div>

      {/* Center icon */}
      <div className="absolute inset-0 flex items-center justify-center">
        <div className="w-10 h-10 rounded-full bg-white/15 backdrop-blur-sm border border-white/25 flex items-center justify-center text-white group-hover:scale-110 group-hover:bg-[#2c4be0] transition-all">
          <Camera className="w-4 h-4" />
        </div>
      </div>

      {/* Bottom overlay */}
      <div
        className={`absolute bottom-0 left-0 right-0 px-3 py-2 flex items-center justify-between text-[10px] ${hasAlert ? 'text-amber-300' : 'text-slate-200'}`}
        style={{ background: 'linear-gradient(to top, rgba(0,0,0,0.8), transparent)' }}
      >
        <span className="flex items-center gap-1.5 font-medium">
          {hasAlert ? (
            <><AlertTriangle className="w-3 h-3 text-amber-400" />{camera.currentAlert}</>
          ) : (
            <><Volume2 className="w-3 h-3 text-emerald-400" />음성유도 대기</>
          )}
        </span>
        <span className="flex items-center gap-2 font-mono">
          <span className="text-slate-400">{camera.fps} FPS</span>
          <span className="flex items-center gap-1 text-emerald-400">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
            탐지 중
          </span>
        </span>
      </div>

      {/* Hover glow border */}
      <div className="absolute inset-0 rounded-xl border-2 border-[#2c4be0] opacity-0 group-hover:opacity-80 transition-opacity pointer-events-none" />
    </div>
  )
}

export default function LiveStreams() {
  const [layout, setLayout] = useState<1 | 2 | 3 | 4>(2)

  const streams: StreamItem[] = mockDevices.flatMap((device) =>
    device.cameras.length > 0
      ? device.cameras.map((camera) => ({ device, camera }))
      : [{ device, camera: { id: 0, port: 0, resolution: '', fps: 0, roiCount: 0, todayDetections: 0, isStreaming: false } }]
  )

  const activeCount = streams.filter((s) => s.device.status !== 'offline').length

  return (
    <div className="max-w-[1720px] mx-auto px-8 py-7">
      {/* Toolbar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between mb-6 gap-4 pb-5 border-b border-slate-200/60">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-extrabold tracking-tight text-slate-900">실시간 스트림</h1>
          <span className="flex items-center gap-1.5 px-3 py-1 rounded-full bg-emerald-50 border border-emerald-200 text-xs font-bold text-emerald-700">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
            {activeCount}개 스트림 활성
          </span>
        </div>
        <div className="flex items-center gap-3">
          {/* Layout toggle */}
          <div className="flex items-center gap-1 p-1 rounded-xl bg-slate-200/50 border border-white/80">
            {LAYOUTS.map((l) => (
              <button
                key={l.label}
                onClick={() => setLayout(l.cols as 1 | 2 | 3 | 4)}
                className={`px-3 py-1 rounded-lg text-xs font-semibold transition-all ${
                  layout === l.cols
                    ? 'bg-[#2c4be0] text-white shadow-md shadow-[#2c4be0]/25'
                    : 'text-slate-600 hover:text-slate-900 hover:bg-white/80'
                }`}
              >
                {l.label}
              </button>
            ))}
          </div>
          <button className="px-3 py-1.5 rounded-xl bg-white border border-slate-200 text-xs font-semibold text-slate-700 hover:bg-slate-50 transition flex items-center gap-1.5 shadow-sm">
            <Maximize2 className="w-3.5 h-3.5" />
            전체화면
          </button>
        </div>
      </div>

      {/* Stream grid */}
      <div
        className="grid gap-3"
        style={{ gridTemplateColumns: `repeat(${layout}, minmax(0, 1fr))` }}
      >
        {streams.map((item, idx) => (
          <StreamCell key={`${item.device.id}-${item.camera.id}-${idx}`} item={item} />
        ))}
      </div>
    </div>
  )
}
