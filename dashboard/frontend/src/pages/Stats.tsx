import { useState } from 'react'
import { Scan, Target, Trophy, Wifi, Users } from 'lucide-react'

import * as api from '../api'
import { useApi } from '../hooks/useApi'
import { formatNumber } from '../format'
import type { TimeSeriesPoint } from '../types'

type Period = 'today' | '7d' | '30d'
type ChartKind = 'bar' | 'line'

const CHART_KIND_LABEL: Record<ChartKind, string> = { bar: '막대', line: '선' }

const PERIOD_LABEL: Record<Period, string> = {
  today: '오늘',
  '7d': '최근 7일',
  '30d': '최근 30일',
}

export default function Stats() {
  const [period, setPeriod] = useState<Period>('today')
  const [chartKind, setChartKind] = useState<ChartKind>('bar')

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
            <div className="flex items-center gap-1 p-0.5 rounded-lg bg-slate-200/60">
              {(Object.keys(CHART_KIND_LABEL) as ChartKind[]).map((k) => (
                <button
                  key={k}
                  onClick={() => setChartKind(k)}
                  aria-pressed={chartKind === k}
                  className={`px-2.5 py-1 rounded-md transition-all ${
                    chartKind === k
                      ? 'bg-white text-[#2c4be0] shadow-sm'
                      : 'text-slate-500 hover:text-slate-800'
                  }`}
                >
                  {CHART_KIND_LABEL[k]}
                </button>
              ))}
            </div>
            <span className="flex items-center gap-1.5 text-slate-600">
              <span className="w-2.5 h-2.5 rounded-sm bg-[#2c4be0]" /> 전체
            </span>
            <span className="flex items-center gap-1.5 text-slate-600">
              <span className="w-2.5 h-2.5 rounded-sm bg-amber-500" /> 지팡이 사용자
            </span>
          </div>
        </div>
        <TimeSeriesChart points={series} kind={chartKind} />
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
 * 순수 SVG 시계열 차트(막대/선) — 차트 라이브러리를 새로 들이지 않는다.
 *
 * roi_editor의 통계 탭도 같은 이유로 canvas 직접 그리기를 쓴다. 지표가 두 계열뿐이라
 * 의존성을 추가할 만큼의 복잡도가 아니다.
 *
 * 두 계열 모두 y=0(아래쪽 기준선)에서 위로만 자란다. 눈금 최댓값은 **두 계열의 최댓값**이라
 * 지팡이 사용자 수가 전체보다 커져도(집계 시점 차이) 차트 밖으로 넘치지 않는다.
 */
const CHART_H = 176
const COLOR_TOTAL = '#2c4be0'
const COLOR_CANE = '#f59e0b'

function TimeSeriesChart({ points, kind }: { points: TimeSeriesPoint[]; kind: ChartKind }) {
  if (points.length === 0) {
    return <p className="text-xs text-slate-400 py-12 text-center">집계된 데이터가 없습니다</p>
  }

  const n = points.length
  const max = Math.max(...points.map((p) => Math.max(p.total_count, p.cane_user_count)), 1)
  const label = (p: TimeSeriesPoint) =>
    p.hour != null ? `${p.hour}시` : (p.date ?? '').slice(5)
  const pct = (v: number) => Math.min(Math.max(v / max, 0), 1) * 100
  const cx = (i: number) => ((i + 0.5) / n) * 100

  // viewBox는 0~100 정규화 좌표 — y는 위가 0이므로 뒤집는다.
  const line = (pick: (p: TimeSeriesPoint) => number) =>
    points.map((p, i) => `${cx(i)},${100 - pct(pick(p))}`).join(' ')

  return (
    <div>
      <div className="relative" style={{ height: CHART_H }}>
        <span className="absolute left-0 -top-0.5 text-[9px] text-slate-400 font-mono z-[1]">
          {formatNumber(max)}
        </span>
        <svg
          className="absolute inset-0 w-full h-full"
          viewBox="0 0 100 100"
          preserveAspectRatio="none"
          aria-hidden="true"
        >
          {[0, 50].map((y) => (
            <line key={y} x1="0" x2="100" y1={y} y2={y} stroke="#e2e8f0" strokeWidth="1"
              vectorEffect="non-scaling-stroke" strokeDasharray="3 3" />
          ))}
          {kind === 'line' && (
            <>
              <polyline points={line((p) => p.total_count)} fill="none" stroke={COLOR_TOTAL}
                strokeWidth="2" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
              <polyline points={line((p) => p.cane_user_count)} fill="none" stroke={COLOR_CANE}
                strokeWidth="2" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
            </>
          )}
          {/* 0 기준선 */}
          <line x1="0" x2="100" y1="100" y2="100" stroke="#94a3b8" strokeWidth="1"
            vectorEffect="non-scaling-stroke" />
        </svg>

        {/* 막대와 점은 HTML — preserveAspectRatio="none"에서 SVG 도형은 찌그러진다 */}
        <div className="absolute inset-0 flex">
          {points.map((p, i) => (
            <div key={i} className="flex-1 h-full relative group">
              {kind === 'bar' ? (
                <>
                  <div
                    className="absolute bottom-0 left-[10%] right-[10%] rounded-t-sm"
                    style={{ height: `${pct(p.total_count)}%`, background: COLOR_TOTAL, opacity: 0.85 }}
                  />
                  {p.cane_user_count > 0 && (
                    <div
                      className="absolute bottom-0 left-[10%] right-[10%] rounded-t-sm"
                      style={{ height: `${pct(p.cane_user_count)}%`, background: COLOR_CANE }}
                    />
                  )}
                </>
              ) : (
                <>
                  <Dot bottom={pct(p.total_count)} color={COLOR_TOTAL} />
                  <Dot bottom={pct(p.cane_user_count)} color={COLOR_CANE} />
                </>
              )}
              <div className="hidden group-hover:block absolute inset-0 bg-slate-900/[0.04]" />
              <div className="hidden group-hover:block absolute -top-8 left-1/2 -translate-x-1/2 z-10 px-2 py-1 rounded-lg bg-slate-900 text-white text-[10px] font-semibold whitespace-nowrap">
                {label(p)} · {p.total_count}명 (지팡이 {p.cane_user_count})
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="flex mt-1">
        {points.map((p, i) => (
          <span key={i} className="flex-1 text-[9px] text-slate-400 truncate text-center">
            {i % Math.ceil(n / 12) === 0 ? label(p) : ''}
          </span>
        ))}
      </div>
    </div>
  )
}

function Dot({ bottom, color }: { bottom: number; color: string }) {
  return (
    <span
      className="absolute left-1/2 w-1.5 h-1.5 rounded-full -translate-x-1/2 translate-y-1/2"
      style={{ bottom: `${bottom}%`, background: color }}
    />
  )
}
