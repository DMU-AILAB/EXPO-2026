import { useNavigate } from 'react-router-dom'
import { Target, Activity, Thermometer, Video, VideoOff } from 'lucide-react'
import type { Device } from '../types'
import StatusBadge from './StatusBadge'
import StreamThumbnail from './StreamThumbnail'

interface DeviceCardProps {
  device: Device
}

export default function DeviceCard({ device }: DeviceCardProps) {
  const navigate = useNavigate()
  const primaryCamera = device.cameras[0]
  const isOffline = device.status === 'offline'

  return (
    <div
      className={`glass-device-card p-5 group flex flex-col justify-between cursor-pointer ${isOffline ? 'offline-card' : ''}`}
      onClick={() => navigate(`/devices/${device.id}`)}
    >
      <div>
        {/* Header */}
        <div className="flex items-center justify-between mb-1">
          <div className="flex items-center gap-2">
            <div
              className={`w-2.5 h-2.5 rounded-full shadow-sm ${
                device.status === 'online'
                  ? 'bg-emerald-500'
                  : device.status === 'warning'
                  ? 'bg-amber-500'
                  : device.status === 'offline'
                  ? 'bg-red-500'
                  : 'bg-slate-400'
              }`}
            />
            <h3 className="text-base font-bold text-slate-900 tracking-tight">{device.name}</h3>
          </div>
          <StatusBadge status={device.status} pulse />
        </div>

        {/* Location */}
        <p className="text-xs text-slate-500 mb-3.5 font-mono">
          {device.location} · {device.ip}
        </p>

        {/* Stream Thumbnail */}
        <StreamThumbnail status={device.status} camera={primaryCamera} deviceName={device.name} />
      </div>

      {/* Footer micro-pills */}
      <div className={`flex flex-wrap items-center gap-2 pt-3 border-t ${isOffline ? 'border-red-200/60' : 'border-slate-200/70'}`}>
        <span className={`micro-pill ${isOffline ? 'text-slate-400 bg-white/70 border-slate-200' : ''}`}>
          <Target className={`w-3.5 h-3.5 ${isOffline ? 'text-slate-400' : 'text-[#2c4be0]'}`} />
          탐지 {device.todayDetections}회
        </span>
        <span className={`micro-pill ${isOffline ? 'text-slate-400 bg-white/70 border-slate-200' : ''}`}>
          <Activity className={`w-3.5 h-3.5 ${isOffline ? 'text-slate-400' : device.cpu > 70 ? 'text-amber-500' : 'text-emerald-600'}`} />
          CPU {isOffline ? '--' : `${device.cpu}%`}
        </span>
        <span className={`micro-pill ${isOffline ? 'text-slate-400 bg-white/70 border-slate-200' : ''}`}>
          <Thermometer className={`w-3.5 h-3.5 ${isOffline ? 'text-slate-400' : device.temperature > 60 ? 'text-amber-500' : 'text-emerald-600'}`} />
          {isOffline ? '--°C' : `${device.temperature}°C`}
        </span>
        {isOffline ? (
          <span className="micro-pill text-red-600 bg-red-50 border-red-200 font-bold">
            <VideoOff className="w-3.5 h-3.5 text-red-500" />
            신호 두절
          </span>
        ) : (
          <span className="micro-pill">
            <Video className="w-3.5 h-3.5 text-slate-500" />
            카메라 {device.cameras.length}개
          </span>
        )}
      </div>
    </div>
  )
}
