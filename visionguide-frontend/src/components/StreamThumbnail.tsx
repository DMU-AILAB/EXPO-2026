import { Unlink } from 'lucide-react'
import type { DeviceStatus, Camera as CameraType } from '../types'

interface StreamThumbnailProps {
  status: DeviceStatus
  camera?: CameraType
  deviceName: string
}

const STREAM_IMAGES = [
  '/streams/entrance.jpg',
  '/streams/hall.jpg',
  '/streams/street.jpg',
  '/streams/campus.jpg',
  '/streams/night.jpg',
  '/streams/elevator.jpg',
]

function OnlineStream({ camera }: { camera: CameraType }) {
  const hasAlert = !!camera.currentAlert
  const imgSrc = STREAM_IMAGES[(camera.imageIndex ?? camera.id) % STREAM_IMAGES.length]

  return (
    <div className={`relative w-full aspect-video rounded-xl overflow-hidden transition-all duration-500 ${
      hasAlert
        ? 'border border-amber-500/60 shadow-[0_0_20px_rgba(245,158,11,0.20)]'
        : 'border border-emerald-500/55 shadow-[0_0_20px_rgba(16,185,129,0.20)]'
    }`}>
      {/* 실사 배경 이미지 */}
      <img
        src={imgSrc}
        alt=""
        className="absolute inset-0 w-full h-full object-cover"
        draggable={false}
      />

      {/* CCTV 분위기 다크 오버레이 */}
      <div className="absolute inset-0 bg-slate-900/35" />

      {/* 스캔라인 */}
      <div className="absolute inset-0 stream-scanlines opacity-40" />

      {/* LIVE 배지 */}
      <div className="absolute top-2.5 right-2.5 px-2 py-0.5 rounded-full bg-red-600/90 backdrop-blur-sm text-white text-[10px] font-bold tracking-wider flex items-center gap-1 shadow-sm">
        <span className="w-1.5 h-1.5 rounded-full bg-white animate-pulse" />
        LIVE
      </div>

    </div>
  )
}

function OfflineStream() {
  return (
    <div className="relative w-full aspect-video rounded-xl bg-slate-900/95 border border-red-300/40 overflow-hidden flex flex-col items-center justify-center shadow-inner cursor-pointer group/off hover:border-red-400/60 transition-colors">
      <div className="w-12 h-12 rounded-full bg-red-500/15 border border-red-500/30 flex items-center justify-center text-red-400 mb-2 shadow-md group-hover/off:bg-red-500/25 transition-colors">
        <Unlink className="w-5 h-5" />
      </div>
      <span className="text-xs font-bold text-red-300 tracking-tight">연결 끊김</span>
      <span className="text-[10.5px] text-slate-500 mt-0.5">클릭하여 재연결</span>
    </div>
  )
}

export default function StreamThumbnail({ status, camera }: StreamThumbnailProps) {
  if (status === 'offline' || !camera) return <OfflineStream />
  return <OnlineStream camera={camera} />
}
