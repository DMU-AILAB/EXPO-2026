import { useState } from 'react'
import { Wifi, Scan, AlertTriangle, Thermometer, Clock, Radio, RefreshCw, Search } from 'lucide-react'
import DeviceCard from '../components/DeviceCard'
import { mockDevices } from '../data/mockData'

export default function Overview() {
  const [query, setQuery] = useState('')

  // 스트림 수 계산 — LiveStreams 페이지와 동일한 로직
  const totalStreams = mockDevices.reduce((s, d) => s + Math.max(d.cameras.length, 1), 0)
  const activeStreams = mockDevices.reduce(
    (s, d) => s + (d.status !== 'offline' ? Math.max(d.cameras.length, 1) : 0),
    0
  )
  const onlineCount = mockDevices.filter((d) => d.status !== 'offline').length
  const totalDetections = mockDevices.reduce((s, d) => s + d.todayDetections, 0)
  const avgTemp = Math.round(
    mockDevices.filter((d) => d.temperature > 0).reduce((s, d) => s + d.temperature, 0) /
      mockDevices.filter((d) => d.temperature > 0).length
  )

  const filtered = mockDevices.filter(
    (d) =>
      d.name.toLowerCase().includes(query.toLowerCase()) ||
      d.ip.includes(query) ||
      d.location.includes(query)
  )

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
              자동 갱신: <strong className="text-slate-900 font-bold">1초 주기</strong>
            </span>
          </div>
          <button className="glass-btn-brand px-4 py-2 rounded-xl text-xs gap-2">
            <Radio className="w-3.5 h-3.5" strokeWidth={2.2} />
            음성 유도기 일괄 테스트
          </button>
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
              Pi {onlineCount}/{mockDevices.length}대
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
              3개
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
              {avgTemp}°C
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
              오프라인 {mockDevices.length - onlineCount}
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
          <button className="glass-btn px-3 py-1.5 rounded-xl text-xs gap-1.5">
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

      {/* Bottom summary bar */}
      <div className="mt-8 p-4 rounded-2xl glass-panel-subtle flex flex-col md:flex-row items-center justify-between gap-4 text-xs font-medium text-slate-600">
        <div className="flex items-center gap-3">
          <span className="relative flex h-2.5 w-2.5">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-500 opacity-75" />
            <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-emerald-600" />
          </span>
          <span>
            실시간 연결 중 ·{' '}
            <strong className="text-slate-800 font-semibold">지연시간 11ms</strong>
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
