import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { CalendarClock, ChevronRight, Download, Loader2, Search, ShieldAlert } from 'lucide-react'

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
  const { data, loading, error, reload } = useApi(
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

  // 선택한 기기들에 코드를 올린다. 기기별 결과를 따로 보여주므로 한 대가 실패해도 나머지는 진행된다.
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [includeModels, setIncludeModels] = useState(false)
  const [updating, setUpdating] = useState(false)
  const [updateResults, setUpdateResults] = useState<api.UpdateResult[] | null>(null)
  const [updateError, setUpdateError] = useState<string | null>(null)
  const toggleSelected = (id: string) =>
    setSelected((cur) => { const next = new Set(cur); if (next.has(id)) next.delete(id); else next.add(id); return next })
  const allSelected = devices.length > 0 && devices.every((d) => selected.has(d.id))
  const runUpdate = async () => {
    if (!window.confirm(`${selected.size}대를 업데이트합니다. 탐지·음성 안내가 몇 초 끊깁니다. 계속할까요?`)) return
    setUpdating(true); setUpdateError(null); setUpdateResults(null)
    try {
      const res = await api.updateDevices([...selected], includeModels)
      setUpdateResults(res.results)
    } catch (e) {
      setUpdateError(e instanceof Error ? e.message : '업데이트하지 못했습니다')
    } finally {
      setUpdating(false)
    }
  }

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
        <label className="flex items-center gap-1.5 text-[11px] text-slate-500">
          <input type="checkbox" checked={includeModels} onChange={(e) => setIncludeModels(e.target.checked)} />
          모델 포함
        </label>
        <button
          onClick={() => void runUpdate()}
          disabled={selected.size === 0 || updating}
          className="glass-btn px-3.5 py-1.5 rounded-xl text-xs font-semibold flex items-center gap-1.5 disabled:opacity-50"
        >
          {updating ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />}
          선택 업데이트{selected.size > 0 ? ` (${selected.size})` : ''}
        </button>
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

      {(updateResults || updateError) && (
        <div className="glass-panel p-4 mb-4 text-xs">
          <div className="flex items-center justify-between mb-2">
            <span className="font-bold text-slate-700">업데이트 결과</span>
            <button onClick={() => { setUpdateResults(null); setUpdateError(null) }}
                    className="text-slate-400 hover:text-slate-600">닫기</button>
          </div>
          {updateError && <p className="font-semibold text-red-700">{updateError}</p>}
          {updateResults?.map((r) => {
            const name = devices.find((d) => d.id === r.device_id)?.name ?? r.device_id
            const tone = !r.ok ? 'text-red-700' : r.came_back ? 'text-emerald-700' : 'text-amber-700'
            const text = !r.ok ? `실패 — ${r.error ?? ''}`
              : r.came_back ? `완료 (${r.applied ?? '?'}개 파일)`
              : '적용됨, 아직 돌아오지 않음 — 상태를 확인하세요'
            return (
              <p key={r.device_id} className={`py-0.5 font-semibold ${tone}`}>
                <span className="font-mono">{name}</span> · {text}
              </p>
            )
          })}
        </div>
      )}

      {/* Table */}
      <div className="glass-panel overflow-hidden p-0">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-slate-50/70 border-b border-slate-200/70">
              <th className="px-4 py-3 w-8">
                <input type="checkbox" checked={allSelected} aria-label="전체 선택"
                  onChange={() => setSelected(allSelected ? new Set() : new Set(devices.map((d) => d.id)))} />
              </th>
              {['상태', '장치명', '위치', 'IP', '가동시간', 'CPU', '온도', '오탐 관리', '얼굴 모자이크', '마지막 연결', ''].map((h) => (
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
                  <td className="px-4 py-3.5" onClick={(e) => e.stopPropagation()}>
                    <input type="checkbox" checked={selected.has(device.id)}
                           onChange={() => toggleSelected(device.id)} aria-label={`${device.name} 선택`} />
                  </td>
                  <td className="px-4 py-3.5">
                    <StatusBadge status={device.status} size="sm" />
                  </td>
                  <td className="px-4 py-3.5 font-mono font-semibold text-slate-800 whitespace-nowrap">
                    {device.name}
                    {!device.provisioned && (
                      <span className="ml-2 text-[10px] font-bold px-1.5 py-0.5 rounded-full bg-amber-50 text-amber-700 border border-amber-200"
                            title="이 서버가 기기의 키를 모릅니다 — 상세에서 신원 재주입">
                        신원 미주입
                      </span>
                    )}
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
                  <td className="px-4 py-3.5">
                    <PrivacyMaskToggle
                      deviceId={device.id}
                      cameras={device.cameras}
                      offline={isOffline}
                      onChanged={reload}
                    />
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

/**
 * 기기의 켜 둔 카메라 전체에 얼굴 모자이크를 켜고 끈다. 일부만 켜져 있으면 "일부"로
 * 보이고, 누르면 전부 켠다(꺼진 쪽이 개인정보 위험이라 안전한 쪽으로 모은다).
 * 행 전체가 상세로 가는 링크라 클릭이 행으로 번지지 않게 막는다.
 */
function PrivacyMaskToggle({ deviceId, cameras, offline, onChanged }: {
  deviceId: string
  cameras: { privacy_mask: boolean }[]
  offline: boolean
  onChanged: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const on = cameras.filter((c) => c.privacy_mask).length
  const state: 'on' | 'off' | 'partial' | 'none' =
    cameras.length === 0 ? 'none' : on === cameras.length ? 'on' : on === 0 ? 'off' : 'partial'

  if (state === 'none') return <span className="text-slate-400">—</span>

  const toggle = async (e: React.MouseEvent) => {
    e.stopPropagation()
    setBusy(true); setErr(null)
    try {
      await api.setPrivacyMask(deviceId, state !== 'on')
      onChanged()
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : '변경하지 못했습니다')
    } finally {
      setBusy(false)
    }
  }

  const label = state === 'on' ? 'ON' : state === 'off' ? 'OFF' : '일부'
  const tone = state === 'on'
    ? 'bg-emerald-50 border-emerald-200 text-emerald-700 hover:bg-emerald-100'
    : state === 'off'
      ? 'bg-slate-50 border-slate-200 text-slate-500 hover:bg-slate-100'
      : 'bg-amber-50 border-amber-200 text-amber-700 hover:bg-amber-100'
  return (
    <button
      onClick={(e) => void toggle(e)}
      disabled={busy || offline}
      title={err ?? (offline ? '오프라인 기기는 바꿀 수 없습니다'
        : state === 'on' ? '클릭하면 모든 카메라의 모자이크를 끕니다'
        : '클릭하면 모든 카메라의 모자이크를 켭니다')}
      className={`px-2.5 py-1 rounded-lg text-[11px] font-bold whitespace-nowrap flex items-center gap-1.5 border transition disabled:opacity-50 disabled:cursor-not-allowed ${err ? 'border-red-300 bg-red-50 text-red-700' : tone}`}
    >
      {busy && <Loader2 className="w-3 h-3 animate-spin" />}
      {err ? '실패' : label}
    </button>
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
