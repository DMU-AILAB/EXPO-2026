import { Scan, Target, Trophy, Wifi } from 'lucide-react'
import { mockDevices, mockEvents } from '../data/mockData'

export default function Stats() {
  const totalDetections = mockDevices.reduce((s, d) => s + d.todayDetections, 0)
  const avgConfidence =
    mockEvents.length > 0
      ? mockEvents.reduce((s, e) => s + e.confidence, 0) / mockEvents.length
      : 0
  const mostActive = [...mockDevices].sort((a, b) => b.todayDetections - a.todayDetections)[0]!
  const onlineCount = mockDevices.filter((d) => d.status !== 'offline').length
  const onlineRate = Math.round((onlineCount / mockDevices.length) * 100)
  const maxDetections = Math.max(...mockDevices.map((d) => d.todayDetections), 1)
  const sortedDevices = [...mockDevices].sort((a, b) => b.todayDetections - a.todayDetections)

  return (
    <div className="max-w-[1720px] mx-auto px-8 py-7">
      {/* Header */}
      <div className="flex items-center gap-3 pb-6 border-b border-slate-200/60 mb-6">
        <h1 className="text-2xl font-extrabold tracking-tight text-slate-900">통계</h1>
        <span className="text-xs px-2.5 py-0.5 rounded-lg bg-slate-200/70 text-slate-700 font-semibold border border-slate-300/60">
          오늘
        </span>
      </div>

      {/* KPI Cards */}
      <section className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-5 mb-8">
        <div className="glass-panel p-5 flex flex-col justify-between group hover:border-blue-300 transition-all duration-300">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-bold text-slate-600 tracking-tight">오늘 총 탐지</span>
            <div className="w-9 h-9 rounded-xl bg-blue-50 border border-blue-200/80 flex items-center justify-center text-[#2c4be0] shadow-sm">
              <Scan className="w-4 h-4" strokeWidth={2.2} />
            </div>
          </div>
          <div className="text-[32px] font-black text-slate-900 tracking-tight leading-none">
            {totalDetections}건
          </div>
        </div>

        <div className="glass-panel p-5 flex flex-col justify-between group hover:border-emerald-300 transition-all duration-300">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-bold text-slate-600 tracking-tight">평균 신뢰도</span>
            <div className="w-9 h-9 rounded-xl bg-emerald-50 border border-emerald-200/80 flex items-center justify-center text-emerald-600 shadow-sm">
              <Target className="w-4 h-4" strokeWidth={2.2} />
            </div>
          </div>
          <div className="text-[32px] font-black text-slate-900 tracking-tight leading-none">
            {(avgConfidence * 100).toFixed(1)}%
          </div>
        </div>

        <div className="glass-panel p-5 flex flex-col justify-between group hover:border-amber-300 transition-all duration-300">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-bold text-slate-600 tracking-tight">최다 탐지 디바이스</span>
            <div className="w-9 h-9 rounded-xl bg-amber-50 border border-amber-200/80 flex items-center justify-center text-amber-600 shadow-sm">
              <Trophy className="w-4 h-4" strokeWidth={2.2} />
            </div>
          </div>
          <div>
            <div className="text-base font-black text-slate-900 tracking-tight leading-tight font-mono">
              {mostActive.name}
            </div>
            <div className="text-[11px] text-slate-500 mt-0.5">
              {mostActive.todayDetections}건 · {mostActive.location}
            </div>
          </div>
        </div>

        <div className="glass-panel p-5 flex flex-col justify-between group hover:border-emerald-300 transition-all duration-300">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-bold text-slate-600 tracking-tight">온라인 비율</span>
            <div className="w-9 h-9 rounded-xl bg-emerald-50 border border-emerald-200/80 flex items-center justify-center text-emerald-600 shadow-sm">
              <Wifi className="w-4 h-4" strokeWidth={2.2} />
            </div>
          </div>
          <div>
            <div className="text-[32px] font-black text-slate-900 tracking-tight leading-none">
              {onlineRate}%
            </div>
            <div className="text-[11px] text-slate-500 mt-0.5">
              {onlineCount} / {mockDevices.length}대
            </div>
          </div>
        </div>
      </section>

      {/* Bottom grid */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
        {/* Detection distribution */}
        <div className="lg:col-span-2 glass-panel p-5">
          <h2 className="text-sm font-bold text-slate-700 mb-4 pb-3 border-b border-slate-200/70">
            디바이스별 탐지 현황
          </h2>
          <div className="space-y-4">
            {sortedDevices.map((device) => (
              <div key={device.id} className="flex items-center gap-3">
                <span className="w-36 text-xs font-mono text-slate-600 truncate shrink-0">
                  {device.name}
                </span>
                <div className="flex-1 h-2 bg-slate-200/70 rounded-full overflow-hidden">
                  <div
                    className="h-2 rounded-full bg-[#2c4be0] transition-all duration-500"
                    style={{ width: `${(device.todayDetections / maxDetections) * 100}%` }}
                  />
                </div>
                <span className="w-10 text-xs font-bold text-slate-700 text-right shrink-0">
                  {device.todayDetections > 0 ? `${device.todayDetections}건` : '—'}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* Recent events */}
        <div className="lg:col-span-3 glass-panel p-5">
          <h2 className="text-sm font-bold text-slate-700 mb-4 pb-3 border-b border-slate-200/70">
            최근 감지 이벤트
          </h2>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-slate-200/70">
                <th className="text-left pb-2 font-semibold text-slate-500">시각</th>
                <th className="text-left pb-2 font-semibold text-slate-500">디바이스</th>
                <th className="text-left pb-2 font-semibold text-slate-500">카메라</th>
                <th className="text-left pb-2 font-semibold text-slate-500">ROI</th>
                <th className="text-right pb-2 font-semibold text-slate-500">신뢰도</th>
              </tr>
            </thead>
            <tbody>
              {mockEvents.map((ev, idx) => (
                <tr
                  key={idx}
                  className={`border-b border-slate-100 ${
                    idx === 0
                      ? 'bg-[#2c4be0]/5 border-l-2 border-l-[#2c4be0]'
                      : 'hover:bg-slate-50'
                  }`}
                >
                  <td className="py-2 pr-3 font-mono text-slate-500">{ev.time}</td>
                  <td className="py-2 pr-3 font-mono text-slate-700 text-[10.5px]">{ev.deviceId}</td>
                  <td className="py-2 pr-3 text-slate-600">{ev.camera}</td>
                  <td className="py-2 pr-3 text-slate-600">{ev.roi}</td>
                  <td className="py-2 text-right font-bold text-emerald-600">
                    {(ev.confidence * 100).toFixed(0)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
