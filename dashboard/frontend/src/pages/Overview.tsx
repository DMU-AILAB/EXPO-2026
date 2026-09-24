import { useState } from 'react'
import { Wifi, Scan, AlertTriangle, Thermometer, Clock, RefreshCw, Search } from 'lucide-react'

import * as api from '../api'
import DeviceCard from '../components/DeviceCard'
import { useApi } from '../hooks/useApi'
import { useEventStream } from '../hooks/useEventStream'

/** 관제 화면이라 주기적으로 다시 읽는다. 5초면 배지·KPI가 충분히 최신이다. */
const REFRESH_MS = 5000

export default function Overview() {
  const [query, setQuery] = useState('')

  const devicesRes = useApi(() => api.listDevices(), [], REFRESH_MS)
  const summaryRes = useApi(() => api.statsSummary(), [], REFRESH_MS)
  const devices = devicesRes.data?.data ?? []
  const summary = summaryRes.data

  // 이벤트가 들어오면 폴링 주기를 기다리지 않고 바로 다시 읽는다.
  useEventStream({ onMessage: (m) => { if (m.type !== 'ping') summaryRes.reload() } })

  const totalStreams = summary?.total_streams ?? 0
  const activeStreams = summary?.active_streams ?? 0
  const onlineCount = summary?.online_device_count ?? 0
  const deviceCount = summary?.total_device_count ?? devices.length
  const totalDetections = summary?.total_detections_today ?? 0
  const avgTemp = summary?.avg_cpu_temperature ?? 0
  const alertCount = summary?.active_alert_count ?? 0

  const q = query.trim().toLowerCase()
  const filtered = q
    ? devices.filter((d) =>
        d.name.toLowerCase().includes(q) ||
        d.ip.includes(q) ||
        (d.location ?? '').toLowerCase().includes(q))
    : devices

  return (
    <div className="max-w-[1720px] mx-auto px-8 py-7 relative z-10">
      {/* Page title */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between pb-6 gap-4 border-b border-slate-200/60 mb-6">
        <div>
          <h1 className="text-2xl lg:text-[26px] font-extrabold tracking-tight text-slate-900 flex items-center gap-2.5">
            실시간 관제 현황
            <span className="text-xs px-2.5 py-0.5 rounded-lg bg-slate-200/70 text-slate-700 font-semibold border border-slate-300/60">
              Overview
            </span>
          </h1>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 px-3.5 py-2 rounded-xl bg-white/80 border border-slate-200/80 text-xs text-slate-600 shadow-sm">
            <Clock className="w-3.5 h-3.5 text-[#2c4be0]" />
            <span>
              자동 갱신: <strong className="text-slate-900 font-bold">{REFRESH_MS / 1000}초 주기</strong>
            </span>
          </div>

        </div>
      </div>

      {/* KPI Cards */}
      <section className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-5 mb-8">
        {/* Online Pi */}
        <div className="glass-panel p-5 flex flex-col justify-between group hover:border-emerald-300 transition-all duration-300">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-bold text-slate-600 tracking-tight">활성 스트림</span>
            <div className="w-9 h-9 rounded-xl bg-emerald-50 border border-emerald-200/80 flex items-center justify-center text-emerald-600 shadow-sm">
              <Wifi className="w-4 h-4" strokeWidth={2.2} />
            </div>
          </div>
          <div>
            <div className="text-[32px] font-black text-slate-900 tracking-tight leading-none">
              {activeStreams} / {totalStreams}
            </div>
            <div className="text-[11px] text-slate-500 mt-1 font-medium">
              Pi {onlineCount}/{deviceCount}대
            </div>
          </div>
        </div>

        {/* Total Detections */}
        <div className="glass-panel p-5 flex flex-col justify-between group hover:border-blue-300 transition-all duration-300">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-bold text-slate-600 tracking-tight">오늘 총 탐지</span>
            <div className="w-9 h-9 rounded-xl bg-blue-50 border border-blue-200/80 flex items-center justify-center text-[#2c4be0] shadow-sm">
              <Scan className="w-4 h-4" strokeWidth={2.2} />
            </div>
          </div>
          <div>
            <div className="text-[32px] font-black text-slate-900 tracking-tight leading-none">
              {totalDetections}건
            </div>
          </div>
        </div>

        {/* Active Alerts */}
        <div className="glass-panel p-5 flex flex-col justify-between group hover:border-amber-300 transition-all duration-300">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <span className="text-xs font-bold text-slate-600 tracking-tight">활성 알림</span>
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-500 opacity-75" />
                <span className="relative inline-flex rounded-full h-2 w-2 bg-amber-500" />
              </span>
            </div>
            <div className="w-9 h-9 rounded-xl bg-amber-50 border border-amber-200/80 flex items-center justify-center text-amber-600 shadow-sm">
              <AlertTriangle className="w-4 h-4" strokeWidth={2.2} />
            </div>
          </div>
          <div>
            <div className="text-[32px] font-black text-slate-900 tracking-tight leading-none">
              {alertCount}개
            </div>
          </div>
        </div>

        {/* Avg CPU Temp */}
        <div className="glass-panel p-5 flex flex-col justify-between group hover:border-slate-300 transition-all duration-300">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-bold text-slate-600 tracking-tight">평균 CPU 온도</span>
            <div className="w-9 h-9 rounded-xl bg-slate-100 border border-slate-200 flex items-center justify-center text-emerald-600 shadow-sm">
              <Thermometer className="w-4 h-4" strokeWidth={2.2} />
            </div>
          </div>
          <div>
            <div className="text-[32px] font-black text-slate-900 tracking-tight leading-none">
              {avgTemp ? `${avgTemp}°C` : '—'}
            </div>
          </div>
        </div>
      </section>

      {/* Device Grid Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between mb-5 gap-3">
        <div className="flex items-center gap-3">
          <h2 className="text-lg lg:text-xl font-bold text-slate-900 tracking-tight flex items-center gap-2.5">
            실시간 모니터링
          </h2>
          <span className="hidden md:inline-block text-slate-300">•</span>
          <div className="hidden md:flex items-center gap-3.5 text-xs font-semibold text-slate-600">
            <span className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-full bg-emerald-500" />
              온라인 {onlineCount}
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-full bg-red-500" />
              오프라인 {Math.max(deviceCount - onlineCount, 0)}
            </span>
          </div>
        </div>
        <div className="flex items-center gap-2.5">
          <div className="relative">
            <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input
              className="bg-white border border-slate-200 rounded-xl pl-9 pr-3.5 py-1.5 text-xs text-slate-800 placeholder-slate-400 focus:outline-none focus:border-[#2c4be0] focus:ring-2 focus:ring-[#2c4be0]/10 w-52 shadow-xs transition"
              placeholder="노드명 또는 IP 필터..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          <button
            onClick={() => { devicesRes.reload(); summaryRes.reload() }}
            className="glass-btn px-3 py-1.5 rounded-xl text-xs gap-1.5">
            <RefreshCw className="w-3.5 h-3.5" />
            새로고침
          </button>
        </div>
      </div>

      {/* Device Grid */}
      <section className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-5 relative">
        {filtered.map((device) => (
          <DeviceCard key={device.id} device={device} />
        ))}
      </section>

      {devicesRes.loading && filtered.length === 0 && (
        <div className="py-16 text-center text-sm text-slate-500">불러오는 중…</div>
      )}
      {!devicesRes.loading && devices.length === 0 && (
        <div className="py-16 text-center text-sm text-slate-500">
          등록된 디바이스가 없습니다 — "디바이스 탐색"에서 추가하세요.
        </div>
      )}
      {devicesRes.error && (
        <div className="mt-4 px-4 py-3 rounded-xl bg-red-50 border border-red-200 text-xs font-semibold text-red-700">
          {devicesRes.error.message}
        </div>
      )}

      {/* Bottom summary bar */}
      <div className="mt-8 p-4 rounded-2xl glass-panel-subtle flex flex-col md:flex-row items-center justify-between gap-4 text-xs font-medium text-slate-600">
        <div className="flex items-center gap-3">
          <span className="relative flex h-2.5 w-2.5">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-500 opacity-75" />
            <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-emerald-600" />
          </span>
          <span>
            디바이스 {deviceCount}대 ·{' '}
            <strong className="text-slate-800 font-semibold">
              오늘 유동인구 {summary?.total_foot_traffic_today ?? 0}명
            </strong>
          </span>
        </div>
        <div className="flex items-center gap-2 text-slate-500">
          <span className="relative flex h-2 w-2">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-60" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
          </span>
          <span className="font-semibold text-emerald-700">정상 가동 중</span>
        </div>
      </div>
    </div>
  )
}
