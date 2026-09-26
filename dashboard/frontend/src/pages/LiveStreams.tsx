import { useEffect, useRef, useState } from 'react'
import { Maximize2, Minimize2, Camera, Unlink, AlertTriangle } from 'lucide-react'

import * as api from '../api'
import { useMjpegStream } from '../hooks/useMjpegStream'
import { useApi } from '../hooks/useApi'
import { cameraDisplayName } from '../utils/cameraLabel'
import type { Device, CameraBrief } from '../types'

interface StreamItem {
  device: Device
  camera: CameraBrief
}

/**
 * 백엔드는 카메라별 Pi 연결 하나를 여러 브라우저 카드에 공유한다.
 */
const LAYOUTS = [
  { label: '1열', cols: 1 },
  { label: '2열', cols: 2 },
  { label: '3열', cols: 3 },
  { label: '4열', cols: 4 },
] as const

function StreamCell({ item }: { item: StreamItem }) {
  const { device, camera } = item
  const stream = useMjpegStream(device.id, camera.id)
  const cellRef = useRef<HTMLDivElement>(null)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const isOffline = device.status === 'offline'
  const hasAlert = false

  useEffect(() => {
    const handleFullscreenChange = () => {
      setIsFullscreen(document.fullscreenElement === cellRef.current)
    }
    document.addEventListener('fullscreenchange', handleFullscreenChange)
    return () => document.removeEventListener('fullscreenchange', handleFullscreenChange)
  }, [])

  const toggleFullscreen = async () => {
    if (!cellRef.current) return
    try {
      if (document.fullscreenElement === cellRef.current) {
        await document.exitFullscreen()
      } else {
        await cellRef.current.requestFullscreen()
      }
    } catch {
      // Browsers can reject fullscreen when the gesture is no longer active.
    }
  }

  if (isOffline) {
    return (
      <div className="relative bg-black/90 rounded-xl overflow-hidden border border-slate-800 flex flex-col items-center justify-center aspect-video">
        <div className="w-12 h-12 rounded-full bg-red-500/15 border border-red-500/30 flex items-center justify-center text-red-400 mb-2">
          <Unlink className="w-5 h-5" />
        </div>
        <span className="text-xs font-bold text-red-300">
          {device.status === 'offline' ? '카메라 연결 끊김' : '스트리밍 중지'}
        </span>
        <span className="text-[10px] text-slate-500 mt-0.5">{device.name}</span>
      </div>
    )
  }

  if (stream.failed) {
    return (
      <div className="relative bg-black/90 rounded-xl overflow-hidden border border-slate-800 flex flex-col items-center justify-center aspect-video">
        <Camera className="w-5 h-5 text-slate-500 mb-1.5" />
        <span className="text-[11px] font-semibold text-slate-400">스트림 연결을 재시도하는 중입니다</span>
        <span className="text-[10px] text-slate-600 mt-0.5">카메라 연결이 회복되면 자동으로 다시 표시됩니다</span>
      </div>
    )
  }

  return (
    <div ref={cellRef} className={`stream-cell relative rounded-xl overflow-hidden group aspect-video transition-all duration-500 ${
      hasAlert
        ? 'border border-amber-500/60 shadow-[0_0_16px_rgba(245,158,11,0.18)]'
        : 'border border-emerald-500/50 shadow-[0_0_16px_rgba(16,185,129,0.16)]'
    }`}>
      {/* 실사 배경 이미지 */}
      <img
        src={stream.src}
        alt=""
        onError={stream.onError}
        onLoad={stream.onLoad}
        className="absolute inset-0 w-full h-full object-cover bg-slate-900"
        draggable={false}
      />

      {/* 상단 그라데이션 바 */}
      <div className="absolute top-0 left-0 right-0 px-3 py-2 flex items-center justify-between"
        style={{ background: 'linear-gradient(to bottom, rgba(0,0,0,0.70), transparent)' }}>
        <span className="text-[10.5px] font-bold text-white">{device.name} · {cameraDisplayName(camera.id)}</span>
        <div className="flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />
          <span className="text-[10px] font-bold text-white/90 tracking-wider">LIVE</span>
        </div>
      </div>

      {/* 중앙 카메라 아이콘 */}
      <button
        type="button"
        onClick={() => void toggleFullscreen()}
        aria-label={isFullscreen ? '전체화면 닫기' : '카메라 전체화면'}
        title={isFullscreen ? '전체화면 닫기' : '카메라 전체화면'}
        className="absolute bottom-2.5 right-2.5 z-10 w-8 h-8 rounded-lg bg-black/55 backdrop-blur-sm border border-white/25 text-white flex items-center justify-center hover:bg-[#2c4be0] transition-colors"
      >
        {isFullscreen ? <Minimize2 className="w-4 h-4" /> : <Maximize2 className="w-4 h-4" />}
      </button>

      {/* 경고 바 */}
      {hasAlert && (
        <div className="absolute bottom-0 left-0 right-0 px-3 py-2 flex items-center gap-1.5 text-[10px] text-amber-300"
          style={{ background: 'linear-gradient(to top, rgba(0,0,0,0.80), transparent)' }}>
          <AlertTriangle className="w-3 h-3 text-amber-400 flex-shrink-0" />
          경보
        </div>
      )}

      {/* 호버 테두리 */}
    </div>
  )
}

export default function LiveStreams() {
  const [layout, setLayout] = useState<1 | 2 | 3 | 4>(2)

  const { data, loading } = useApi(() => api.listDevices(), [], 15_000)
  const devices = data?.data ?? []

  const streams: StreamItem[] = devices.flatMap((device) =>
    device.cameras.map((camera) => ({ device, camera })))

  const activeCount = streams.filter(
    (s) => s.device.status !== 'offline' && s.camera.is_streaming).length

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
          <button
            onClick={() => document.documentElement.requestFullscreen?.()}
            className="glass-btn px-3 py-1.5 rounded-xl text-xs gap-1.5">
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

      {!loading && streams.length === 0 && (
        <div className="py-20 text-center text-sm text-slate-500">표시할 카메라가 없습니다</div>
      )}
    </div>
  )
}
