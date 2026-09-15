import { useState } from 'react'
import { Wifi, Scan, AlertTriangle, Thermometer, Clock, Radio, RefreshCw, Search, TrendingUp } from 'lucide-react'
import DeviceCard from '../components/DeviceCard'
import { mockDevices } from '../data/mockData'

export default function Overview() {
  const [query, setQuery] = useState('')

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
          <h1 className="text-2xl lg:text-[26px] font-extrabold tracking-tight text-slate-900 flex items-center gap-2.5 mb-1">
            실시간 엣지 관제 현황
            <span className="text-xs px-2.5 py-0.5 rounded-lg bg-slate-200/70 text-slate-700 font-semibold border border-slate-300/60">
              Overview
            </span>
          </h1>
          <p className="text-xs lg:text-[13px] font-medium text-slate-500">
            시각장애인 흰 지팡이(White Cane) 및 점자블록 동선 AI 실시간 엣지 비전 모니터링
          </p>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 px-3.5 py-2 rounded-xl bg-white/80 border border-slate-200/80 text-xs text-slate-600 shadow-sm">
            <Clock className="w-3.5 h-3.5 text-[#2c4be0]" />
            <span>
              자동 갱신: <strong className="text-slate-900 font-bold">1초 주기</strong>
            </span>
          </div>
          <button className="px-4 py-2 rounded-xl text-xs font-semibold bg-[#2c4be0]/10 hover:bg-[#2c4be0]/15 text-[#2c4be0] border border-[#2c4be0]/30 shadow-sm transition flex items-center gap-2">
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
            <span className="text-xs font-bold text-slate-600 tracking-tight">온라인 Pi</span>
            <div className="w-9 h-9 rounded-xl bg-emerald-50 border border-emerald-200/80 flex items-center justify-center text-emerald-600 shadow-sm">
              <Wifi className="w-4 h-4" strokeWidth={2.2} />
            </div>
          </div>
          <div>
            <div className="text-[32px] font-black text-slate-900 tracking-tight leading-none mb-2">
              {onlineCount} / {mockDevices.length}
            </div>
            <div className="flex items-center gap-2 text-xs text-emerald-700 font-semibold">
              <span className="inline-block w-2 h-2 rounded-full bg-emerald-500 ring-4 ring-emerald-100" />
              가동률 {Math.round((onlineCount / mockDevices.length) * 100)}% · {mockDevices.length - onlineCount}대 점검 권장
            </div>
          </div>
          <div className="mt-4 pt-3 border-t border-slate-200/70 flex items-center justify-between text-[11px] text-slate-500 font-medium">
            <span>Subnet: 192.168.1.0/24</span>
            <span className="text-slate-800 font-mono font-bold bg-slate-100 px-1.5 py-0.5 rounded">MQTT: 11ms</span>
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
            <div className="text-[32px] font-black text-slate-900 tracking-tight leading-none mb-2">
              {totalDetections}건
            </div>
            <div className="flex items-center gap-1.5 text-xs text-[#2c4be0] font-semibold">
              <TrendingUp className="w-3.5 h-3.5" strokeWidth={2.4} />
              전일 동시간 대비 +18.4%
            </div>
          </div>
          <div className="mt-4 pt-3 border-t border-slate-200/70 flex items-center justify-between text-[11px] text-slate-500 font-medium">
            <span>흰 지팡이 인식 정밀도</span>
            <span className="text-[#2c4be0] font-bold font-mono">96.2% mAP</span>
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
            <div className="text-[32px] font-black text-slate-900 tracking-tight leading-none mb-2 flex items-baseline gap-2.5">
              <span>3개</span>
              <span className="text-xs font-bold text-amber-700 px-2.5 py-0.5 rounded-full bg-amber-100/80 border border-amber-300/70">
                주의 2 · 경고 1
              </span>
            </div>
            <div className="flex items-center gap-1 text-xs text-amber-700 font-medium">
              점자블록 전방 적치물 감지 외
            </div>
          </div>
          <div className="mt-4 pt-3 border-t border-slate-200/70 flex items-center justify-between text-[11px] text-slate-500 font-medium">
            <span>자동 음성안내 송출</span>
            <span className="text-slate-800 font-semibold">3회 연동 완료</span>
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
            <div className="text-[32px] font-black text-slate-900 tracking-tight leading-none mb-2 flex items-baseline gap-2.5">
              <span>{avgTemp}°C</span>
              <span className="text-xs font-bold text-emerald-700 px-2.5 py-0.5 rounded-full bg-emerald-100/80 border border-emerald-300/70">
                정상 범위
              </span>
            </div>
            <div className="flex items-center gap-2 text-xs text-slate-500 font-medium">
              <div className="w-full bg-slate-200 rounded-full h-2 overflow-hidden flex-1">
                <div
                  className="bg-gradient-to-r from-emerald-500 to-[#2c4be0] h-2 rounded-full"
                  style={{ width: `${(avgTemp / 100) * 100}%` }}
                />
              </div>
              <span className="font-mono text-[11px] font-bold text-slate-600">Max 68°C</span>
            </div>
          </div>
          <div className="mt-4 pt-3 border-t border-slate-200/70 flex items-center justify-between text-[11px] text-slate-500 font-medium">
            <span>액티브 쿨러 팬 가동</span>
            <span className="text-slate-800 font-mono font-bold">2,400 RPM</span>
          </div>
        </div>
      </section>

      {/* Device Grid Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between mb-5 gap-3">
        <div className="flex items-center gap-3">
          <h2 className="text-lg lg:text-xl font-bold text-slate-900 tracking-tight flex items-center gap-2.5">
            라즈베리파이 엣지 노드 모니터링
            <span className="text-xs px-2.5 py-0.5 rounded-lg bg-white border border-slate-200/80 text-slate-700 font-mono font-semibold shadow-xs">
              {Math.ceil(filtered.length / 3)} × 3 GRID
            </span>
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
          <button className="px-3 py-1.5 rounded-xl bg-white hover:bg-slate-50 border border-slate-200 text-xs font-semibold text-slate-700 shadow-xs transition flex items-center gap-1.5">
            <RefreshCw className="w-3.5 h-3.5 text-slate-500" />
            새로고침
          </button>
        </div>
      </div>

      {/* Device Grid */}
      <section className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 relative">
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
            실시간 MQTT 브로커 스트림 연결:{' '}
            <strong className="text-slate-900 font-mono font-bold bg-white px-2 py-0.5 rounded border border-slate-200">
              broker.visionguide.internal:1883
            </strong>{' '}
            (지연시간 11ms)
          </span>
        </div>
        <div className="flex items-center gap-4 text-slate-500">
          <span className="font-semibold text-slate-700">YOLOv8n-Cane v2.4 엣지 모델 탑재 (Hailo-8 NPU)</span>
          <span className="text-slate-300">|</span>
          <span>
            보안 암호화: <span className="font-semibold text-slate-700">TLS 1.3</span> / eBPF 감시 활성
          </span>
        </div>
      </div>
    </div>
  )
}
