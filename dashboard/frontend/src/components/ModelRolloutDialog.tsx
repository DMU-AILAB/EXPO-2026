/**
 * 모델 일괄 변경 — 이미 깔린 기기의 카메라 모델을 한 번에 올린다.
 *
 * 신규 설치의 기본 모델이 바뀌어도 **기존 기기의 설정은 그대로**라서 따로 바꿔야 한다. 흐름은 항상
 * "미리보기 → 확인 → 실행"이다: 미리보기는 실제 기기를 읽어 무엇이 바뀔지(그리고 가중치가 없어 건너뛸 기기)를
 * 보여 주고 아무것도 바꾸지 않는다. 실행 뒤에는 FPS를 확인해 **미달이면 그 카메라만 이전 모델로 되돌린다.**
 */

import { useEffect, useMemo, useState } from 'react'
import { CheckCircle2, Cpu, Loader2, RotateCcw, X, XCircle } from 'lucide-react'

import * as api from '../api'
import { useApi } from '../hooks/useApi'
import { ErrorBox, inputCls, labelCls, primaryBtn } from '../pages/device/shared'

type Phase = 'edit' | 'previewing' | 'preview' | 'running' | 'done'

function Row({ r, name }: { r: api.ModelRolloutResult; name: string }) {
  const tone = r.skipped ? 'text-amber-700' : !r.ok ? 'text-red-700' : 'text-emerald-700'
  const Icon = r.skipped ? XCircle : !r.ok ? XCircle : CheckCircle2
  return (
    <li className="p-2.5 rounded-lg border border-slate-200 bg-white/70 text-[11px] space-y-1">
      <p className={`flex items-center gap-1.5 font-bold ${tone}`}>
        <Icon className="w-3.5 h-3.5 shrink-0" />{name}
        {r.skipped && <span className="font-semibold">건너뜀</span>}
        {r.models_pushed && <span className="font-semibold text-slate-500">· 모델 파일을 먼저 올림</span>}
        {r.needs_push && <span className="font-semibold text-slate-500">· 실행하면 모델 파일을 먼저 올립니다</span>}
      </p>
      {r.reason && <p className="text-amber-700">{r.reason}</p>}
      {r.error && <p className="text-red-700">{r.error}</p>}
      {r.changed.map((c) => (
        <p key={c.camera_id} className="font-mono text-slate-600 flex items-center gap-1.5">
          {c.camera_id}: {c.previous} → {c.current}
          {c.fps != null && <span className="text-slate-500">· {c.fps.toFixed(1)} FPS</span>}
          {c.rolled_back && (
            <span className="flex items-center gap-0.5 font-sans font-bold text-red-700">
              <RotateCcw className="w-3 h-3" />이전 모델로 되돌림
            </span>
          )}
        </p>
      ))}
      {r.skipped_cameras.map((c) => (
        <p key={c.camera_id} className="text-slate-500">{c.camera_id}: {c.reason}</p>
      ))}
      {!r.skipped && !r.error && r.changed.length === 0 && r.skipped_cameras.length === 0 && (
        <p className="text-slate-400">바꿀 카메라가 없습니다 (이미 목표 모델이거나 대상이 아님)</p>
      )}
    </li>
  )
}

export default function ModelRolloutDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const targets = useApi(() => api.getCalibrationTargets(), [])
  const devices = targets.data?.devices ?? []
  const models = targets.data?.model_variants ?? []

  const [to, setTo] = useState('')
  const [fromAll, setFromAll] = useState(true)
  const [from, setFrom] = useState<Set<string>>(new Set())
  const [pushModels, setPushModels] = useState(true)
  const [verify, setVerify] = useState(true)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [phase, setPhase] = useState<Phase>('edit')
  const [results, setResults] = useState<api.ModelRolloutResult[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && phase !== 'running') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, phase])

  // 기본 목표는 목록의 현행 후보 — 서버가 준 순서를 따르되 v15가 있으면 그것을 먼저 고른다.
  useEffect(() => {
    if (!to && models.length) setTo(models.includes('v15_320') ? 'v15_320' : models[models.length - 1])
  }, [models, to])

  const allIds = useMemo(() => devices.map((d) => d.id), [devices])
  const usedVariants = useMemo(
    () => [...new Set(devices.flatMap((d) => d.cameras.map((c) => c.model_variant ?? '(기본)')))].sort(), [devices])
  const chosen = selected.size ? [...selected] : allIds
  const nameOf = (id: string) => devices.find((d) => d.id === id)?.name ?? id

  const body = (dry: boolean) => ({
    to,
    device_ids: selected.size ? [...selected] : undefined,
    from_variants: fromAll || from.size === 0 ? undefined : [...from],
    push_models: pushModels,
    verify,
    dry_run: dry,
  })

  const run = async (dry: boolean) => {
    setError(null); setPhase(dry ? 'previewing' : 'running')
    try {
      const res = await api.changeModelVariant(body(dry))
      setResults(res.results); setPhase(dry ? 'preview' : 'done')
      if (!dry) onDone()
    } catch (e) {
      setError(e instanceof Error ? e.message : '요청에 실패했습니다'); setPhase(dry ? 'edit' : 'preview')
    }
  }

  const execute = () => {
    if (window.confirm(
      `${chosen.length}대의 카메라 모델을 ${to}(으)로 바꿉니다.\n\n` +
      '해당 카메라의 탐지가 몇 초 끊깁니다. 바꾼 뒤 FPS가 기준(10) 미만이면 그 카메라만 이전 모델로 되돌립니다.' +
      (pushModels ? '\n가중치가 없는 기기에는 코드를 먼저 올립니다(서비스가 재시작됩니다).' : '') +
      '\n\n구조물 마스크는 모델이 무엇을 오탐하느냐에 따라 달라지므로, 바꾼 카메라는 오탐 관리 탭에서 다시 수집하는 것을 권장합니다.')) void run(false)
  }

  const toggle = (set: Set<string>, setter: (s: Set<string>) => void, key: string) => {
    const next = new Set(set); if (next.has(key)) next.delete(key); else next.add(key); setter(next)
  }
  const busy = phase === 'previewing' || phase === 'running'
  const changedTotal = results.reduce((n, r) => n + r.changed.filter((c) => !c.rolled_back).length, 0)

  return (
    <div className="fixed inset-0 z-[60] flex justify-end" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-slate-900/20 backdrop-blur-[2px]" onClick={() => { if (!busy) onClose() }} />
      <div className="relative w-full max-w-[560px] h-full overflow-y-auto bg-white shadow-2xl border-l border-slate-200 p-6 space-y-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-extrabold text-slate-900 flex items-center gap-2">
              <Cpu className="w-5 h-5 text-[#2c4be0]" />모델 일괄 변경
            </h2>
            <p className="mt-1 text-xs text-slate-500 leading-relaxed">
              이미 설치된 기기의 카메라 모델을 한 번에 바꿉니다. 먼저 <b>미리보기</b>로 무엇이 바뀔지 확인하세요.
              가중치가 없거나 구버전인 기기, Coral 카메라는 건드리지 않습니다.
            </p>
          </div>
          <button onClick={onClose} disabled={busy} className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-500 disabled:opacity-40" aria-label="닫기">
            <X className="w-4 h-4" />
          </button>
        </div>

        <ErrorBox error={targets.error} />
        {error && <p className="text-xs font-semibold text-red-700">{error}</p>}

        <section className="space-y-3">
          <div>
            <label className={labelCls} htmlFor="rollout-to">바꿀 모델</label>
            <select id="rollout-to" className={inputCls} value={to} disabled={busy} onChange={(e) => { setTo(e.target.value); setPhase('edit') }}>
              {models.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>

          <div>
            <span className={labelCls}>대상 카메라</span>
            <label className="flex items-center gap-2 text-xs text-slate-700">
              <input type="checkbox" checked={fromAll} disabled={busy}
                     onChange={(e) => { setFromAll(e.target.checked); setPhase('edit') }} />
              지금 어떤 모델이든 (목표와 다른 모든 카메라)
            </label>
            {!fromAll && (
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {usedVariants.map((v) => (
                  <label key={v} className="flex items-center gap-1 text-[11px] px-2 py-1 rounded-lg border border-slate-200">
                    <input type="checkbox" checked={from.has(v)} disabled={busy}
                           onChange={() => { toggle(from, setFrom, v); setPhase('edit') }} />{v}
                  </label>
                ))}
              </div>
            )}
          </div>

          <div>
            <span className={labelCls}>대상 기기 {selected.size ? `(${selected.size}대 선택)` : '(전체)'}</span>
            <ul className="max-h-40 overflow-y-auto space-y-1 border border-slate-200 rounded-lg p-2">
              {devices.map((d) => (
                <li key={d.id}>
                  <label className="flex items-center gap-2 text-[11px] text-slate-700">
                    <input type="checkbox" checked={selected.has(d.id)} disabled={busy}
                           onChange={() => { toggle(selected, setSelected, d.id); setPhase('edit') }} />
                    <span className="font-semibold">{d.name}</span>
                    <span className="font-mono text-slate-400">{[...new Set(d.cameras.map((c) => c.model_variant ?? '(기본)'))].join(', ')}</span>
                    {d.status !== 'online' && <span className="text-amber-700">· {d.status}</span>}
                  </label>
                </li>
              ))}
              {devices.length === 0 && <li className="text-[11px] text-slate-400">등록된 기기가 없습니다</li>}
            </ul>
          </div>

          <label className="flex items-start gap-2 text-xs text-slate-700">
            <input type="checkbox" className="mt-0.5" checked={pushModels} disabled={busy}
                   onChange={(e) => { setPushModels(e.target.checked); setPhase('edit') }} />
            <span>가중치가 없는 기기에는 먼저 올립니다 <span className="text-slate-400">(코드 업데이트 — 서비스가 재시작됩니다)</span></span>
          </label>
          <label className="flex items-start gap-2 text-xs text-slate-700">
            <input type="checkbox" className="mt-0.5" checked={verify} disabled={busy}
                   onChange={(e) => { setVerify(e.target.checked); setPhase('edit') }} />
            <span>바꾼 뒤 FPS를 확인하고 <b>10 미만이면 되돌립니다</b> <span className="text-slate-400">(기기당 최대 1분 안팎)</span></span>
          </label>
        </section>

        <div className="flex items-center gap-2">
          <button onClick={() => void run(true)} disabled={busy || !to}
                  className="px-4 py-2 rounded-xl text-xs font-semibold text-slate-700 border border-slate-200 bg-white hover:bg-slate-50 disabled:opacity-50 transition flex items-center gap-1.5">
            {phase === 'previewing' && <Loader2 className="w-3.5 h-3.5 animate-spin" />}미리보기
          </button>
          <button onClick={execute} disabled={busy || phase !== 'preview'} className={primaryBtn}
                  title={phase === 'preview' ? '' : '먼저 미리보기를 확인하세요'}>
            {phase === 'running' ? <><Loader2 className="w-3.5 h-3.5 animate-spin inline mr-1" />변경 중…</> : '변경 실행'}
          </button>
        </div>

        {results.length > 0 && (
          <section>
            <h3 className="text-xs font-bold text-slate-600 mb-2">
              {phase === 'done' ? `결과 — ${changedTotal}개 카메라 변경됨` : '미리보기 (아직 바뀐 것은 없습니다)'}
            </h3>
            <ul className="space-y-1.5">
              {results.map((r) => <Row key={r.device_id} r={r} name={nameOf(r.device_id)} />)}
            </ul>
          </section>
        )}
      </div>
    </div>
  )
}
