import { useState } from 'react'
import { Scan, Target, Trophy, Wifi, Users } from 'lucide-react'

import * as api from '../api'
import { useApi } from '../hooks/useApi'
import { formatNumber } from '../format'
import type { TimeSeriesPoint } from '../types'

type Period = 'today' | '7d' | '30d'

const PERIOD_LABEL: Record<Period, string> = {
  today: '오늘',
  '7d': '최근 7일',
  '30d': '최근 30일',
}

export default function Stats() {
  const [period, setPeriod] = useState<Period>('today')

  const summaryRes = useApi(() => api.statsSummary(), [], 15_000)
  const byDeviceRes = useApi(() => api.statsByDevice(), [], 15_000)
  // `granularity`는 넘기지 않는다 — 서버가 period에 맞춰 고른다.
  const seriesRes = useApi(() => api.statsTimeseries(period), [period], 30_000)
  const eventsRes = useApi(() => api.listEvents({ limit: 12 }), [], 10_000)

  const summary = summaryRes.data
  const devices = byDeviceRes.data ?? []
  const series = seriesRes.data?.data ?? []
  const events = eventsRes.data?.data ?? []

  const maxDetections = Math.max(...devices.map((d) => d.detections), 1)
  const sorted = [...devices].sort((a, b) => b.detections - a.detections)
  const mostActive = summary?.most_active_device

  return (
    <div className="max-w-[1720px] mx-auto px-8 py-7">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between pb-6 border-b border-slate-200/60 mb-6 gap-3">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-extrabold tracking-tight text-slate-900">통계</h1>
          <span className="text-xs px-2.5 py-0.5 rounded-lg bg-slate-200/70 text-slate-700 font-semibold border border-slate-300/60">
            {PERIOD_LABEL[period]}
          </span>
        </div>
        <div className="flex items-center gap-1.5 p-1 rounded-xl bg-slate-200/50 border border-white/80">
          {(Object.keys(PERIOD_LABEL) as Period[]).map((p) => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              className={`px-3.5 py-1.5 rounded-lg text-xs font-semibold transition-all ${
                period === p
                  ? 'bg-[#2c4be0] text-white shadow-md shadow-[#2c4be0]/25'
                  : 'text-slate-600 hover:text-slate-900 hover:bg-white/80'
              }`}
            >
              {PERIOD_LABEL[p]}
            </button>
          ))}
        </div>
      </div>

      {/* KPI Cards */}
      <section className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-5 mb-8">
        <Kpi
          label="오늘 유동인구"
          icon={<Users className="w-4 h-4" strokeWidth={2.2} />}
          tone="blue"
          value={`${formatNumber(summary?.total_foot_traffic_today)}명`}
          sub={`지팡이 사용자 ${formatNumber(summary?.cane_user_count_today)}명`}
        />
        <Kpi
          label="오늘 음성 안내"
          icon={<Scan className="w-4 h-4" strokeWidth={2.2} />}
          tone="blue"
          value={`${formatNumber(summary?.total_detections_today)}건`}
        />
        <Kpi
          label="평균 신뢰도"
          icon={<Target className="w-4 h-4" strokeWidth={2.2} />}
          tone="emerald"
          value={summary ? `${(summary.avg_confidence * 100).toFixed(1)}%` : '—'}
        />
        <Kpi
          label="온라인 비율"
          icon={<Wifi className="w-4 h-4" strokeWidth={2.2} />}
          tone="emerald"
          value={summary ? `${Math.round(summary.online_rate * 100)}%` : '—'}
          sub={summary ? `${summary.online_device_count} / ${summary.total_device_count}대` : undefined}
        />
      </section>

      {/* 시계열 */}
      <div className="glass-panel p-5 mb-6">
        <div className="flex items-center justify-between mb-4 pb-3 border-b border-slate-200/70">
          <h2 className="text-sm font-bold text-slate-700">
            {PERIOD_LABEL[period]} 유동인구 추이
          </h2>
          <div className="flex items-center gap-3 text-[11px] font-semibold">
            <span className="flex items-center gap-1.5 text-slate-600">
              <span className="w-2.5 h-2.5 rounded-sm bg-[#2c4be0]" /> 전체
            </span>
            <span className="flex items-center gap-1.5 text-slate-600">
              <span className="w-2.5 h-2.5 rounded-sm bg-amber-500" /> 지팡이 사용자
            </span>
          </div>
        </div>
        <TimeSeriesChart points={series} />
      </div>

      {/* Bottom grid */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
        <div className="lg:col-span-2 glass-panel p-5">
          <h2 className="text-sm font-bold text-slate-700 mb-4 pb-3 border-b border-slate-200/70">
            디바이스별 음성 안내
          </h2>
          {mostActive && (
            <div className="mb-4 flex items-center gap-2.5 px-3 py-2 rounded-xl bg-amber-50 border border-amber-200/70">
              <Trophy className="w-3.5 h-3.5 text-amber-600 shrink-0" />
              <span className="text-[11px] font-bold text-amber-800 truncate">
                {mostActive.name} · {mostActive.today_detections}건
              </span>
            </div>
          )}
          <div className="space-y-4">
            {sorted.map((device) => (
              <div key={device.device_id} className="flex items-center gap-3">
                <span className="w-36 text-xs font-mono text-slate-600 truncate shrink-0">
                  {device.name}
                </span>
                <div className="flex-1 h-2 bg-slate-200/70 rounded-full overflow-hidden">
                  <div
                    className="h-2 rounded-full bg-[#2c4be0] transition-all duration-500"
                    style={{ width: `${(device.detections / maxDetections) * 100}%` }}
                  />
                </div>
                <span className="w-10 text-xs font-bold text-slate-700 text-right shrink-0">
                  {device.detections > 0 ? `${device.detections}건` : '—'}
                </span>
              </div>
            ))}
            {sorted.length === 0 && (
              <p className="text-xs text-slate-400 py-6 text-center">집계된 데이터가 없습니다</p>
            )}
          </div>
        </div>

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
              {events.map((ev, idx) => (
                <tr
                  key={ev.id}
                  className={`border-b border-slate-100 ${
                    idx === 0 ? 'bg-[#2c4be0]/5 border-l-2 border-l-[#2c4be0]' : 'hover:bg-slate-50'
                  }`}
                >
                  <td className="py-2 pr-3 font-mono text-slate-500">{ev.time_display}</td>
                  <td className="py-2 pr-3 font-mono text-slate-700 text-[10.5px]">{ev.device_id}</td>
                  <td className="py-2 pr-3 text-slate-600">{ev.camera_id}</td>
                  <td className="py-2 pr-3 text-slate-600">{ev.roi_name ?? '—'}</td>
                  <td className="py-2 text-right font-bold text-emerald-600">
                    {/* 가상 지팡이 박스로 발사된 안내는 confidence가 없다. */}
                    {ev.confidence != null ? `${(ev.confidence * 100).toFixed(0)}%` : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {events.length === 0 && (
            <p className="text-xs text-slate-400 py-10 text-center">수신된 이벤트가 없습니다</p>
          )}
        </div>
      </div>
    </div>
  )
}

function Kpi({ label, icon, value, sub, tone }: {
  label: string
  icon: React.ReactNode
  value: string
  sub?: string
  tone: 'blue' | 'emerald'
}) {
  const box = tone === 'blue'
    ? 'bg-blue-50 border-blue-200/80 text-[#2c4be0]'
    : 'bg-emerald-50 border-emerald-200/80 text-emerald-600'
  return (
    <div className="glass-panel p-5 flex flex-col justify-between">
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs font-bold text-slate-600 tracking-tight">{label}</span>
        <div className={`w-9 h-9 rounded-xl border flex items-center justify-center shadow-sm ${box}`}>
          {icon}
        </div>
      </div>
      <div>
        <div className="text-[32px] font-black text-slate-900 tracking-tight leading-none">{value}</div>
        {sub && <div className="text-[11px] text-slate-500 mt-1 font-medium">{sub}</div>}
      </div>
    </div>
  )
}

/**
 * 순수 SVG 막대 그래프 — 차트 라이브러리를 새로 들이지 않는다.
 *
 * roi_editor의 통계 탭도 같은 이유로 canvas 직접 그리기를 쓴다. 지표가 두 계열뿐이라
 * 의존성을 추가할 만큼의 복잡도가 아니다.
 */
function TimeSeriesChart({ points }: { points: TimeSeriesPoint[] }) {
  if (points.length === 0) {
    return <p className="text-xs text-slate-400 py-12 text-center">집계된 데이터가 없습니다</p>
  }

  const max = Math.max(...points.map((p) => p.total_count), 1)
  const label = (p: TimeSeriesPoint) =>
    p.hour != null ? `${p.hour}시` : (p.date ?? '').slice(5)

  return (
    <div className="flex items-end gap-[3px] h-44">
      {points.map((p, i) => {
        const totalH = (p.total_count / max) * 100
        const caneH = (p.cane_user_count / max) * 100
        return (
          <div key={i} className="flex-1 flex flex-col items-center justify-end h-full group relative">
            <div className="w-full relative flex flex-col justify-end h-full">
              <div
                className="w-full bg-[#2c4be0]/85 rounded-t-sm transition-all"
                style={{ height: `${totalH}%` }}
              />
              {p.cane_user_count > 0 && (
                <div
                  className="w-full bg-amber-500 absolute bottom-0 rounded-t-sm"
                  style={{ height: `${caneH}%` }}
                />
              )}
            </div>
            <span className="text-[9px] text-slate-400 mt-1 truncate w-full text-center">
              {i % Math.ceil(points.length / 12) === 0 ? label(p) : ''}
            </span>
            <div className="hidden group-hover:block absolute -top-8 z-10 px-2 py-1 rounded-lg bg-slate-900 text-white text-[10px] font-semibold whitespace-nowrap">
              {label(p)} · {p.total_count}명 (지팡이 {p.cane_user_count})
            </div>
          </div>
        )
      })}
    </div>
  )
}
