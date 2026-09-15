import { useState } from 'react'
import { Maximize2, Camera, Unlink, AlertTriangle } from 'lucide-react'

const STREAM_IMAGES = [
  '/streams/entrance.jpg',
  '/streams/hall.jpg',
  '/streams/street.jpg',
  '/streams/campus.jpg',
  '/streams/night.jpg',
  '/streams/elevator.jpg',
]
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

function StreamCell({ item }: { item: StreamItem }) {
  const { device, camera } = item
  const isOffline = device.status === 'offline'
  const hasAlert = !!camera.currentAlert

  if (isOffline) {
    return (
      <div className="relative bg-black/90 rounded-xl overflow-hidden border border-slate-800 flex flex-col items-center justify-center aspect-video">
        <div className="w-12 h-12 rounded-full bg-red-500/15 border border-red-500/30 flex items-center justify-center text-red-400 mb-2">
          <Unlink className="w-5 h-5" />
        </div>
        <span className="text-xs font-bold text-red-300">카메라 연결 끊김</span>
        <span className="text-[10px] text-slate-500 mt-0.5">{device.name}</span>
      </div>
    )
  }

  const imgSrc = STREAM_IMAGES[(camera.imageIndex ?? camera.id) % STREAM_IMAGES.length]

  return (
    <div className={`relative rounded-xl overflow-hidden group aspect-video transition-all duration-500 ${
      hasAlert
        ? 'border border-amber-500/60 shadow-[0_0_16px_rgba(245,158,11,0.18)]'
        : 'border border-emerald-500/50 shadow-[0_0_16px_rgba(16,185,129,0.16)]'
    }`}>
      {/* 실사 배경 이미지 */}
      <img
        src={imgSrc}
        alt=""
        className="absolute inset-0 w-full h-full object-cover"
        draggable={false}
      />

      {/* 상단 그라데이션 바 */}
      <div className="absolute top-0 left-0 right-0 px-3 py-2 flex items-center justify-between"
        style={{ background: 'linear-gradient(to bottom, rgba(0,0,0,0.70), transparent)' }}>
        <span className="text-[10.5px] font-bold text-white">{device.name}</span>
        <div className="flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />
          <span className="text-[10px] font-bold text-white/90 tracking-wider">LIVE</span>
        </div>
      </div>

      {/* 중앙 카메라 아이콘 */}
      <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity duration-300">
        <div className="w-10 h-10 rounded-full bg-white/20 backdrop-blur-sm border border-white/30 flex items-center justify-center text-white group-hover:bg-[#2c4be0] transition-colors">
          <Camera className="w-4 h-4" />
        </div>
      </div>

      {/* 경고 바 */}
      {hasAlert && (
        <div className="absolute bottom-0 left-0 right-0 px-3 py-2 flex items-center gap-1.5 text-[10px] text-amber-300"
          style={{ background: 'linear-gradient(to top, rgba(0,0,0,0.80), transparent)' }}>
          <AlertTriangle className="w-3 h-3 text-amber-400 flex-shrink-0" />
          {camera.currentAlert}
        </div>
      )}

      {/* 호버 테두리 */}
      <div className="absolute inset-0 rounded-xl border-2 border-[#2c4be0] opacity-0 group-hover:opacity-70 transition-opacity pointer-events-none" />
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
          <span className="glass-badge gap-1.5 px-3 py-1 rounded-full bg-emerald-50/80 border border-emerald-200/70 text-xs font-bold text-emerald-700">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
            {activeCount}개 스트림 활성
          </span>
        </div>
        <div className="flex items-center gap-3">
          {/* Layout toggle */}
          <div className="glass-toggle flex items-center gap-1 p-1 rounded-xl">
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
          <button className="glass-btn px-3 py-1.5 rounded-xl text-xs gap-1.5">
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
