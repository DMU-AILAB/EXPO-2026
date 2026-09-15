import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import type { Device } from '../types'
import StatusBadge from './StatusBadge'
import StreamThumbnail from './StreamThumbnail'

interface DeviceCardProps {
  device: Device
}

export default function DeviceCard({ device }: DeviceCardProps) {
  const navigate = useNavigate()
  const [selectedCam, setSelectedCam] = useState(0)
  const isOffline = device.status === 'offline'
  const activeCamera = device.cameras[selectedCam]
  const multiCam = device.cameras.length > 1

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
        <StreamThumbnail status={device.status} camera={activeCamera} deviceName={device.name} />

        {/* Camera Toggle */}
        {multiCam && (
          <div className="flex items-center gap-1.5 mt-2.5" onClick={(e) => e.stopPropagation()}>
            {device.cameras.map((cam, idx) => (
              <button
                key={cam.id}
                onClick={() => setSelectedCam(idx)}
                className={`flex-1 py-1 rounded-lg text-[11px] font-semibold transition-all ${
                  selectedCam === idx
                    ? 'bg-[#2c4be0] text-white shadow-sm shadow-[#2c4be0]/30'
                    : 'bg-slate-100/80 text-slate-500 hover:bg-slate-200/80 hover:text-slate-700'
                }`}
              >
                CAM {cam.id}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
