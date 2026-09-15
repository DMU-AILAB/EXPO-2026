import { useNavigate } from 'react-router-dom'
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
            <h3 className="text-base font-bold text-slate-900 tracking-tight">{device.location}</h3>
          </div>
          <StatusBadge status={device.status} pulse />
        </div>

        {/* IP */}
        <p className="text-xs text-slate-500 mb-3.5 font-mono">
          {device.ip}
        </p>

        {/* Stream Thumbnail */}
        <StreamThumbnail status={device.status} camera={primaryCamera} deviceName={device.name} />
      </div>

    </div>
  )
}
