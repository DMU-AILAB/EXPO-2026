import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Search, ChevronRight } from 'lucide-react'
import StatusBadge from '../components/StatusBadge'
import { mockDevices } from '../data/mockData'

export default function DeviceList() {
  const [query, setQuery] = useState('')
  const navigate = useNavigate()

  const filtered = mockDevices.filter(
    (d) =>
      d.name.toLowerCase().includes(query.toLowerCase()) ||
      d.ip.includes(query) ||
      d.location.includes(query)
  )

  const onlineCount = mockDevices.filter((d) => d.status !== 'offline').length

  return (
    <div className="max-w-[1720px] mx-auto px-8 py-7">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between pb-6 gap-4 border-b border-slate-200/60 mb-6">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-extrabold tracking-tight text-slate-900">디바이스 목록</h1>
          <span className="text-xs px-2.5 py-0.5 rounded-lg bg-slate-200/70 text-slate-700 font-semibold border border-slate-300/60">
            {mockDevices.length}대
          </span>
          <span className="text-xs px-2.5 py-0.5 rounded-full bg-emerald-50 text-emerald-700 font-semibold border border-emerald-200/70">
            온라인 {onlineCount}
          </span>
        </div>
        <div className="relative">
          <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            className="bg-white border border-slate-200 rounded-xl pl-9 pr-3.5 py-1.5 text-xs text-slate-800 placeholder-slate-400 focus:outline-none focus:border-[#2c4be0] focus:ring-2 focus:ring-[#2c4be0]/10 w-60 shadow-xs transition"
            placeholder="장치명, 위치, IP 검색..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
      </div>

      {/* Table */}
      <div className="glass-panel overflow-hidden p-0">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-slate-50/70 border-b border-slate-200/70">
              {['상태', '장치명', '위치', 'IP', '가동시간', 'CPU', '온도', '카메라', '오늘 탐지', '마지막 연결', ''].map((h) => (
                <th key={h} className="text-left px-4 py-3 font-semibold text-slate-500 whitespace-nowrap">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.map((device) => {
              const isOffline = device.status === 'offline'
              const cpuColor =
                device.cpu > 70 ? 'text-amber-600' : device.cpu > 0 ? 'text-slate-800' : 'text-slate-400'
              const tempColor =
                device.temperature > 60
                  ? 'text-red-500'
                  : device.temperature > 55
                  ? 'text-amber-500'
                  : device.temperature > 0
                  ? 'text-emerald-600'
                  : 'text-slate-400'

              return (
                <tr
                  key={device.id}
                  className="border-b border-slate-100 hover:bg-[#2c4be0]/5 cursor-pointer transition-colors group"
                  onClick={() => navigate(`/devices/${device.id}`)}
                >
                  <td className="px-4 py-3.5">
                    <StatusBadge status={device.status} size="sm" />
                  </td>
                  <td className="px-4 py-3.5 font-mono font-semibold text-slate-800 whitespace-nowrap">
                    {device.name}
                  </td>
                  <td className="px-4 py-3.5 text-slate-600">{device.location}</td>
                  <td className="px-4 py-3.5 font-mono text-slate-500">{device.ip}</td>
                  <td className="px-4 py-3.5 text-slate-600">{isOffline ? '—' : device.uptime}</td>
                  <td className={`px-4 py-3.5 font-mono font-bold ${cpuColor}`}>
                    {device.cpu > 0 ? `${device.cpu}%` : '—'}
                  </td>
                  <td className={`px-4 py-3.5 font-mono font-bold ${tempColor}`}>
                    {device.temperature > 0 ? `${device.temperature}°C` : '—'}
                  </td>
                  <td className="px-4 py-3.5 text-slate-600 text-center">{device.cameras.length}</td>
                  <td className="px-4 py-3.5 font-bold text-slate-800">
                    {device.todayDetections > 0 ? `${device.todayDetections}건` : '—'}
                  </td>
                  <td className="px-4 py-3.5 text-slate-500">{device.lastSeen}</td>
                  <td className="px-4 py-3.5">
                    <ChevronRight className="w-3.5 h-3.5 text-slate-300 group-hover:text-[#2c4be0] transition-colors" />
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {filtered.length === 0 && (
          <div className="py-16 text-center text-slate-400 text-sm">검색 결과 없음</div>
        )}
      </div>
    </div>
  )
}
