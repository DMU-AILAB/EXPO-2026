import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { CalendarClock, ChevronRight, Loader2, Search, ShieldAlert } from 'lucide-react'

import * as api from '../api'
import CalibrationScheduleDialog from '../components/CalibrationScheduleDialog'
import StatusBadge from '../components/StatusBadge'
import { useApi } from '../hooks/useApi'
import { formatLastSeen } from '../format'
import { tempTone } from '../utils/temperature'
import { formatSec } from './device/shared'

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
  const [scheduleOpen, setScheduleOpen] = useState(false)

  // 수집 중인 기기 — 서버가 **기기에 직접** 묻는다(Pi 자체 화면에서 시작한 수집도 보인다).
  // 관측이 몇 분 단위라 5초 주기면 충분하고, 서버가 3초 캐시로 기기를 보호한다.
  const { data: active } = useApi(() => api.getActiveCalibrations(), [], 5_000)

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
        <div className="flex items-center gap-2.5">
        <button
          onClick={() => setScheduleOpen(true)}
          className="glass-btn px-3.5 py-1.5 rounded-xl text-xs font-semibold flex items-center gap-1.5"
        >
          <CalendarClock className="w-3.5 h-3.5" />수집 예약
        </button>
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
      </div>

      {/* Table */}
      <div className="glass-panel overflow-hidden p-0">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-slate-50/70 border-b border-slate-200/70">
              {['상태', '장치명', '위치', 'IP', '가동시간', 'CPU', '온도', '오탐 관리', '오늘 탐지', '마지막 연결', ''].map((h) => (
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
              const tempColor = tempTone(temp)

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
                  <td className="px-4 py-3.5">
                    <FalsePositiveButton
                      running={active?.[device.id]}
                      onClick={() => navigate(`/devices/${device.id}?tab=calibration`)}
                    />
                  </td>
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

      {scheduleOpen && <CalibrationScheduleDialog onClose={() => setScheduleOpen(false)} />}
    </div>
  )
}

/** 행 전체가 상세로 가는 링크라, 버튼은 클릭이 행으로 번지지 않게 막는다. */
function FalsePositiveButton({ running, onClick }: {
  running?: { remaining_sec: number | null; cameras: number }
  onClick: () => void
}) {
  const go = (e: React.MouseEvent) => { e.stopPropagation(); onClick() }
  if (running) {
    return (
      <button onClick={go} title={`카메라 ${running.cameras}대에서 구조물 수집 중`}
              className="px-2.5 py-1 rounded-lg text-[11px] font-bold whitespace-nowrap flex items-center gap-1.5 border border-amber-200 bg-amber-50 text-amber-700 hover:bg-amber-100 transition">
        <Loader2 className="w-3 h-3 animate-spin" />
        오탐 관리 중{running.remaining_sec != null && ` · ${formatSec(running.remaining_sec)}`}
      </button>
    )
  }
  return (
    <button onClick={go}
            className="px-2.5 py-1 rounded-lg text-[11px] font-bold whitespace-nowrap flex items-center gap-1.5 border border-slate-200 bg-white text-slate-600 hover:border-[#2c4be0] hover:text-[#2c4be0] transition">
      <ShieldAlert className="w-3 h-3" />오탐 관리
    </button>
  )
}
