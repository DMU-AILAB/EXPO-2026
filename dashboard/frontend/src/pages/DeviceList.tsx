import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Search, ChevronRight } from 'lucide-react'

import * as api from '../api'
import StatusBadge from '../components/StatusBadge'
import { useApi } from '../hooks/useApi'
import { formatLastSeen } from '../format'

export default function DeviceList() {
  const [query, setQuery] = useState('')
  const [search, setSearch] = useState('')
  const navigate = useNavigate()

  useEffect(() => {
    const timer = window.setTimeout(() => setSearch(query), 250)
    return () => window.clearTimeout(timer)
  }, [query])

  // 검색은 **서버가** 한다(명세 §3의 `search` 파라미터) — 이름·IP·위치를 함께 본다.
  const { data, loading, error } = useApi(
    () => api.listDevices(search || undefined),
    [search],
    30_000,
    api.getCachedDeviceList(search || undefined),
  )
  const devices = data?.data ?? []
  const total = data?.total ?? 0
  const onlineCount = devices.filter((d) => d.status === 'online').length
  const filtered = devices

  return (
    <div className="max-w-[1720px] mx-auto px-8 py-7">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between pb-6 gap-4 border-b border-slate-200/60 mb-6">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-extrabold tracking-tight text-slate-900">디바이스 목록</h1>
          <span className="text-xs px-2.5 py-0.5 rounded-lg bg-slate-200/70 text-slate-700 font-semibold border border-slate-300/60">
            {total}대
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
              const cpu = device.cpu
              const temp = device.temperature
              const cpuColor =
                cpu == null ? 'text-slate-400' : cpu > 70 ? 'text-amber-600' : 'text-slate-800'
              const tempColor =
                temp == null
                  ? 'text-slate-400'
                  : temp > 60
                  ? 'text-red-500'
                  : temp > 55
                  ? 'text-amber-500'
                  : 'text-emerald-600'

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
                  <td className="px-4 py-3.5 text-slate-600">{device.location ?? '—'}</td>
                  <td className="px-4 py-3.5 font-mono text-slate-500">{device.ip}</td>
                  <td className="px-4 py-3.5 text-slate-600">{isOffline ? '—' : device.uptime ?? '—'}</td>
                  <td className={`px-4 py-3.5 font-mono font-bold ${cpuColor}`}>
                    {cpu != null ? `${cpu.toFixed(0)}%` : '—'}
                  </td>
                  <td className={`px-4 py-3.5 font-mono font-bold ${tempColor}`}>
                    {temp != null ? `${temp.toFixed(1)}°C` : '—'}
                  </td>
                  <td className="px-4 py-3.5 text-slate-600 text-center">{device.cameras.length}</td>
                  <td className="px-4 py-3.5 font-bold text-slate-800">
                    {device.today_detections > 0 ? `${device.today_detections}건` : '—'}
                  </td>
                  <td className="px-4 py-3.5 text-slate-500">{formatLastSeen(device.last_seen)}</td>
                  <td className="px-4 py-3.5">
                    <ChevronRight className="w-3.5 h-3.5 text-slate-300 group-hover:text-[#2c4be0] transition-colors" />
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {loading && filtered.length === 0 && (
          <div className="py-16 text-center text-slate-400 text-sm">불러오는 중…</div>
        )}
        {!loading && filtered.length === 0 && (
          <div className="py-16 text-center text-slate-400 text-sm">
            {query ? '검색 결과 없음' : '등록된 디바이스가 없습니다'}
          </div>
        )}
        {error && (
          <div className="px-4 py-3 text-xs font-semibold text-red-700 bg-red-50 border-t border-red-200">
            {error.message}
          </div>
        )}
      </div>
    </div>
  )
}
