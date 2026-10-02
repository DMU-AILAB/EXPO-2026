import { useNavigate } from 'react-router-dom'
import type { Device } from '../types'
import StatusBadge from './StatusBadge'
import StreamThumbnail from './StreamThumbnail'
import { cameraDisplayName } from '../utils/cameraLabel'

interface DeviceCardProps {
  device: Device
}

export default function DeviceCard({ device }: DeviceCardProps) {
  const navigate = useNavigate()
  const isOffline = device.status === 'offline'
  const multiCam = device.cameras.length > 1

  return (
    <div
      className={`glass-device-card p-5 group flex flex-col justify-between cursor-pointer ${
        multiCam ? 'md:col-span-2' : ''
      } ${isOffline ? 'offline-card' : ''}`}
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
            <h3 className="text-base font-bold text-slate-900 tracking-tight">
              {device.location || device.name}
            </h3>
          </div>
          <StatusBadge status={device.status} pulse />
        </div>

        {/* IP */}
        <p className="text-xs text-slate-500 mb-3.5 font-mono">
          {device.ip}
        </p>

        {/* 카메라가 여러 대면 탭으로 하나씩 보는 대신 나란히 전부 보여준다 — 관제 화면에서
            숨은 카메라는 안 보는 카메라가 된다.

            썸네일 폭을 1대짜리 카드와 똑같이 맞춘다. 그리드 칸 폭을 W, 칸 간격(gap-5)과
            카드 패딩(p-5)을 각 20px이라 하면 1대짜리 썸네일은 W-40이고, 2칸을 차지하는
            카드의 썸네일은 W-10-(안쪽 간격)/2 다. 둘이 같으려면 안쪽 간격이 60px
            (= 칸 간격 + 패딩×2)이어야 한다. 그리드 gap이나 카드 p를 바꾸면 이 값도 같이 바꿀 것.
            모든 카드에 카메라 이름 줄을 둬서 카드 높이도 같다 — 1대짜리가 옆 카드 높이에
            맞춰 늘어나며 아래가 비던 문제가 없어진다. */}
        <div className={multiCam ? 'grid grid-cols-1 md:grid-cols-2 gap-y-2.5 md:gap-x-[60px]' : ''}>
          {device.cameras.map((cam) => (
            <div key={cam.id}>
              <StreamThumbnail status={device.status} deviceId={device.id} camera={cam}
                               deviceName={device.name} />
              <p className="mt-1.5 text-[11px] font-semibold text-slate-500 text-center">
                {cameraDisplayName(cam.id)}
              </p>
            </div>
          ))}
          {device.cameras.length === 0 && (
            <div>
              <StreamThumbnail status={device.status} deviceId={device.id} camera={undefined}
                               deviceName={device.name} />
              <p className="mt-1.5 text-[11px] font-semibold text-slate-500 text-center">&nbsp;</p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
