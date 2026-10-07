/**
 * 구조물 수집 예약 — 디바이스 목록 상단의 "수집 예약"에서 연다.
 *
 * 정해진 시각에 여러 기기(전체 / 모델별 / 선택한 기기)가 한꺼번에 폐장 시간 관측을 한다.
 * 모인 후보는 **자동 적용되지 않는다** — 각 기기의 "오탐 관리" 탭에서 운영자가 고른다
 * (CLAUDE.md "구조물 마스크": 예약이 자동 적용 경로가 되면 그 시각에 지나간 청소 인력의
 * 자리가 구조물로 굳는다).
 */

import { useEffect, useMemo, useState } from 'react'
import { CalendarClock, CheckCircle2, Plus, Trash2, X, XCircle } from 'lucide-react'

import * as api from '../api'
import { DAY_NAMES, formatDays, formatLastSeen } from '../format'
import { useApi } from '../hooks/useApi'
import type { CalibrationFleetDevice, CalibrationSchedule, CalibrationTargetMode } from '../types'
import { cameraDisplayName } from '../utils/cameraLabel'
import { ErrorBox, dangerBtn, inputCls, labelCls, primaryBtn } from '../pages/device/shared'
import StatusBadge from './StatusBadge'

const MINUTE_OPTIONS = [1, 3, 5, 10, 20, 30]   // 기기가 10초~30분으로 자른다

type Target = { mode: CalibrationTargetMode; targets: string[] }

function describeTarget(s: CalibrationSchedule, devices: CalibrationFleetDevice[]) {
  if (s.target_mode === 'all') return '전체 기기'
  if (s.target_mode === 'model') return `모델 ${s.targets.join(', ')}`
  const names = s.targets.map((id) => devices.find((d) => d.id === id)?.name ?? id)
  return names.length <= 2 ? names.join(', ') : `${names.slice(0, 2).join(', ')} 외 ${names.length - 2}대`
}

export default function CalibrationScheduleDialog({ onClose }: { onClose: () => void }) {
  const targets = useApi(() => api.getCalibrationTargets(), [])
  const schedules = useApi(() => api.listCalibrationSchedules(), [])
  const runs = useApi(() => api.listCalibrationRuns(30), [], 15_000)
  const devices = targets.data?.devices ?? []
  const models = targets.data?.model_variants ?? []

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const scheduledRuns = (runs.data ?? []).filter((r) => r.schedule_id != null)

  return (
    <div className="fixed inset-0 z-[60] flex justify-end" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-slate-900/20 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative w-full max-w-[560px] h-full overflow-y-auto bg-white shadow-2xl border-l border-slate-200 p-6 space-y-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-extrabold text-slate-900 flex items-center gap-2">
              <CalendarClock className="w-5 h-5 text-[#2c4be0]" />구조물 수집 예약
            </h2>
            <p className="mt-1 text-xs text-slate-500 leading-relaxed">
              정해진 시각에 여러 기기가 한꺼번에 폐장 시간 관측을 합니다. <b>사람이 없는 시간</b>으로 잡으세요 —
              수집 중 지나간 사람도 후보가 됩니다. 모인 후보는 자동으로 제외되지 않으며, 각 기기의 오탐 관리 탭에서 제외할 것을 고릅니다.
            </p>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-500" aria-label="닫기">
            <X className="w-4 h-4" />
          </button>
        </div>

        <ErrorBox error={targets.error} />
        <ScheduleEditor devices={devices} models={models} schedules={schedules.data ?? []}
                        error={schedules.error} reload={schedules.reload} />

        <section>
          <h3 className="text-xs font-bold text-slate-600 mb-2">최근 예약 실행</h3>
          {scheduledRuns.length === 0
            ? <p className="text-xs text-slate-400">아직 실행된 예약이 없습니다.</p>
            : (
              <ul className="space-y-1">
                {scheduledRuns.map((r) => (
                  <li key={r.id} className="flex items-center gap-2 text-[11px]">
                    {r.ok
                      ? <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600 shrink-0" />
                      : <XCircle className="w-3.5 h-3.5 text-red-500 shrink-0" />}
                    <span className="text-slate-400 w-16 shrink-0">{formatLastSeen(r.started_at)}</span>
                    <span className="font-semibold text-slate-700">{r.device_name}</span>
                    <span className="text-slate-400">{cameraDisplayName(r.camera_id)}</span>
                    {r.error && <span className="text-red-600 truncate" title={r.error}>{r.error}</span>}
                  </li>
                ))}
              </ul>
            )}
        </section>
      </div>
    </div>
  )
}

// ─── 대상 선택 ────────────────────────────────────────────────────────────────

function TargetPicker({ value, onChange, devices, models }: {
  value: Target
  onChange: (t: Target) => void
  devices: CalibrationFleetDevice[]
  models: string[]
}) {
  // 모델 칩에는 **실제로 쓰이는 모델만** 보인다 — 아무 카메라도 안 쓰는 모델을 골라도 대상이 0이다.
  const usage = useMemo(() => {
    const m = new Map<string, number>()
    devices.forEach((d) => d.cameras.filter((c) => c.is_active && c.model_variant)
      .forEach((c) => m.set(c.model_variant!, (m.get(c.model_variant!) ?? 0) + 1)))
    return m
  }, [devices])
  const usedModels = models.filter((k) => usage.has(k))

  const toggle = (key: string) => onChange({
    ...value,
    targets: value.targets.includes(key) ? value.targets.filter((x) => x !== key) : [...value.targets, key],
  })
  const modes: { key: CalibrationTargetMode; label: string }[] = [
    { key: 'all', label: '전체' }, { key: 'model', label: '모델별' }, { key: 'devices', label: '기기 선택' },
  ]

  return (
    <div className="space-y-2.5">
      <div className="flex gap-1 p-1 rounded-xl bg-slate-100 w-fit">
        {modes.map((m) => (
          <button key={m.key} onClick={() => onChange({ mode: m.key, targets: [] })}
                  className={`px-3 py-1 rounded-lg text-xs font-bold transition ${
                    value.mode === m.key ? 'bg-white text-[#2c4be0] shadow-sm' : 'text-slate-500 hover:text-slate-800'}`}>
            {m.label}
          </button>
        ))}
      </div>
      {value.mode === 'model' && (
        <div className="flex flex-wrap gap-1.5">
          {usedModels.length === 0 && <span className="text-xs text-slate-400">사용 중인 모델이 없습니다</span>}
          {usedModels.map((k) => (
            <button key={k} onClick={() => toggle(k)}
                    className={`px-2.5 py-1 rounded-lg text-[11px] font-bold border transition ${
                      value.targets.includes(k)
                        ? 'bg-[#2c4be0] border-[#2c4be0] text-white'
                        : 'bg-white border-slate-200 text-slate-600 hover:border-slate-300'}`}>
              {k} <span className="opacity-70">· 카메라 {usage.get(k)}대</span>
            </button>
          ))}
        </div>
      )}
      {value.mode === 'devices' && (
        <div className="max-h-44 overflow-y-auto rounded-xl border border-slate-200 divide-y divide-slate-100 bg-white">
          {devices.map((d) => (
            <label key={d.id} className="flex items-center gap-2.5 px-3 py-1.5 text-xs cursor-pointer hover:bg-slate-50">
              <input type="checkbox" checked={value.targets.includes(d.id)} onChange={() => toggle(d.id)} />
              <span className="font-semibold text-slate-800">{d.name}</span>
              <span className="text-slate-400">{d.location}</span>
              <span className="ml-auto"><StatusBadge status={d.status} size="sm" /></span>
            </label>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── 예약 목록·추가 ───────────────────────────────────────────────────────────

function ScheduleEditor({ devices, models, schedules, error, reload }: {
  devices: CalibrationFleetDevice[]
  models: string[]
  schedules: CalibrationSchedule[]
  error: unknown
  reload: () => void
}) {
  const [name, setName] = useState('')
  const [days, setDays] = useState<number[]>([1, 2, 3, 4, 5])
  const [hour, setHour] = useState(22)
  const [minute, setMinute] = useState(0)
  const [minutes, setMinutes] = useState(5)
  const [target, setTarget] = useState<Target>({ mode: 'all', targets: [] })
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<unknown>(null)

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true); setActionError(null)
    try { await fn(); reload() } catch (e) { setActionError(e) } finally { setBusy(false) }
  }

  const canAdd = days.length > 0 && (target.mode === 'all' || target.targets.length > 0)
  const add = () => act(async () => {
    await api.createCalibrationSchedule({
      name, days, hour, minute, seconds: minutes * 60, is_enabled: true,
      target_mode: target.mode, targets: target.targets,
    })
    setName('')
  })

  return (
    <section className="space-y-4">
      <ErrorBox error={actionError ?? error} />

      <div className="space-y-2">
        {schedules.length === 0 && <p className="text-xs text-slate-400">등록된 예약이 없습니다.</p>}
        {schedules.map((s) => (
          <div key={s.id} className={`flex items-center gap-3 px-3 py-2 rounded-xl border ${
            s.is_enabled ? 'bg-white border-slate-200' : 'bg-slate-50 border-slate-200/70 opacity-70'}`}>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-bold text-slate-800">
                {s.display}
                {s.name && <span className="ml-2 text-xs font-semibold text-slate-500">{s.name}</span>}
              </p>
              <p className="text-[11px] text-slate-500 truncate">
                {describeTarget(s, devices)} · {Math.round(s.seconds / 60)}분 관측
              </p>
            </div>
            <label className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-600 cursor-pointer">
              <input type="checkbox" checked={s.is_enabled} disabled={busy}
                     onChange={() => act(() => api.updateCalibrationSchedule(s.id, { is_enabled: !s.is_enabled }))} />
              사용
            </label>
            <button className={dangerBtn} disabled={busy}
                    onClick={() => act(() => api.deleteCalibrationSchedule(s.id))} title="예약 삭제">
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </div>
        ))}
      </div>

      <div className="pt-4 border-t border-slate-200/70 space-y-3">
        <p className="text-[11px] font-bold text-slate-500">새 예약 추가</p>
        <div className="flex gap-1">
          {DAY_NAMES.map((label, d) => (
            <button key={d}
                    onClick={() => setDays((p) => p.includes(d) ? p.filter((x) => x !== d) : [...p, d].sort())}
                    className={`flex-1 py-1 rounded-lg text-[11px] font-bold transition ${
                      days.includes(d) ? 'bg-[#2c4be0] text-white' : 'bg-slate-100 text-slate-500 hover:bg-slate-200'}`}>
              {label}
            </button>
          ))}
        </div>
        <div className="grid grid-cols-4 gap-2">
          <div>
            <span className={labelCls}>시</span>
            <select className={inputCls} value={hour} onChange={(e) => setHour(Number(e.target.value))}>
              {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{String(h).padStart(2, '0')}</option>)}
            </select>
          </div>
          <div>
            <span className={labelCls}>분</span>
            <select className={inputCls} value={minute} onChange={(e) => setMinute(Number(e.target.value))}>
              {Array.from({ length: 12 }, (_, i) => i * 5).map((m) =>
                <option key={m} value={m}>{String(m).padStart(2, '0')}</option>)}
            </select>
          </div>
          <div>
            <span className={labelCls}>관측</span>
            <select className={inputCls} value={minutes} onChange={(e) => setMinutes(Number(e.target.value))}>
              {MINUTE_OPTIONS.map((m) => <option key={m} value={m}>{m}분</option>)}
            </select>
          </div>
          <div>
            <span className={labelCls}>이름 (선택)</span>
            <input className={inputCls} value={name} placeholder="폐장 후" onChange={(e) => setName(e.target.value)} />
          </div>
        </div>
        <TargetPicker value={target} onChange={setTarget} devices={devices} models={models} />
        <div className="flex items-center gap-3">
          <button className={primaryBtn} disabled={!canAdd || busy} onClick={() => void add()}>
            <Plus className="w-3.5 h-3.5" />예약 추가
          </button>
          {days.length > 0 && (
            <span className="text-[11px] text-slate-500">
              {formatDays(days)} {String(hour).padStart(2, '0')}:{String(minute).padStart(2, '0')}
              (대시보드 서버 시각)에 {minutes}분 관측
            </span>
          )}
        </div>
      </div>
    </section>
  )
}
