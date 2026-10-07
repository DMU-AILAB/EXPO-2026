import { useState } from 'react'
import { CheckCircle, Download, Loader2, Undo2 } from 'lucide-react'

import * as api from '../api'
import { useApi } from '../hooks/useApi'

/**
 * 기기 한 대의 코드 업데이트 — SSH 없이 서버가 번들을 올리고 기기가 재시작한다.
 * 업데이트하면 탐지·음성 안내가 몇 초 끊기므로 실행 전에 한 번 확인한다.
 */
export default function DeviceUpdatePanel({ deviceId, offline }: { deviceId: string; offline: boolean }) {
  const { data: status, reload } = useApi(() => api.getDeviceUpdateStatus(deviceId), [deviceId], 30_000)
  const [confirm, setConfirm] = useState<'update' | 'rollback' | null>(null)
  const [includeModels, setIncludeModels] = useState(false)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ tone: 'ok' | 'warn' | 'err'; text: string } | null>(null)

  const run = async (kind: 'update' | 'rollback') => {
    setConfirm(null); setBusy(true); setMsg(null)
    try {
      if (kind === 'update') {
        const res = await api.updateDevices([deviceId], includeModels)
        const r = res.results[0]
        if (!r.ok) setMsg({ tone: 'err', text: r.error ?? '업데이트하지 못했습니다' })
        else if (!r.came_back) setMsg({ tone: 'warn', text: '적용했지만 기기가 아직 돌아오지 않았습니다 — 잠시 뒤 상태를 확인하세요' })
        else setMsg({ tone: 'ok', text: `업데이트 완료 (${r.applied ?? '?'}개 파일)` })
      } else {
        await api.rollbackDeviceUpdate(deviceId)
        setMsg({ tone: 'ok', text: '이전 버전으로 되돌렸습니다. 기기가 재시작됩니다' })
      }
    } catch (e) {
      setMsg({ tone: 'err', text: e instanceof Error ? e.message : '요청에 실패했습니다' })
    } finally {
      setBusy(false)
      window.setTimeout(reload, 8_000)         // 재시작이 끝난 뒤 버전을 다시 읽는다
      reload()
    }
  }

  const known = !!status?.bundle_id
  const badge = !status ? null
    : status.error && !known ? { cls: 'bg-slate-100 text-slate-500 border-slate-200', t: '버전 확인 불가' }
    : status.up_to_date ? { cls: 'bg-emerald-50 text-emerald-700 border-emerald-200', t: '최신' }
    : { cls: 'bg-amber-50 text-amber-700 border-amber-200', t: '업데이트 가능' }
  const tones = { ok: 'bg-emerald-50 border-emerald-200 text-emerald-700',
                  warn: 'bg-amber-50 border-amber-200 text-amber-700',
                  err: 'bg-red-50 border-red-200 text-red-700' }

  return (
    <div className="glass-panel p-5">
      <div className="flex items-center justify-between mb-4 pb-3 border-b border-slate-200/70">
        <h2 className="text-sm font-bold text-slate-700">코드 업데이트</h2>
        {badge && <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full border ${badge.cls}`}>{badge.t}</span>}
      </div>

      <p className="text-[11px] text-slate-400 mb-3 font-mono break-all">
        기기 {status?.bundle_id || '—'} · 서버 {status?.latest || '—'}
      </p>
      {status?.error && !known && (
        <p className="text-[11px] text-slate-500 mb-3">{status.error}</p>
      )}
      {msg && <div className={`mb-3 px-3 py-2 rounded-xl border text-xs font-semibold ${tones[msg.tone]}`}>{msg.text}</div>}

      <label className="flex items-center gap-2 text-xs text-slate-600 mb-3">
        <input type="checkbox" checked={includeModels} onChange={(e) => setIncludeModels(e.target.checked)} />
        모델 가중치 포함 (용량이 커서 오래 걸립니다)
      </label>

      {confirm ? (
        <div>
          <p className="text-xs font-semibold text-slate-700 mb-2">
            {confirm === 'update'
              ? '탐지·음성 안내가 몇 초 끊깁니다. 업데이트할까요?'
              : '직전 업데이트 이전 상태로 되돌립니다. 계속할까요?'}
          </p>
          <div className="flex gap-2">
            <button onClick={() => void run(confirm)} className="flex-1 py-1.5 rounded-lg text-xs font-bold bg-[#2c4be0] text-white">실행</button>
            <button onClick={() => setConfirm(null)} className="flex-1 py-1.5 rounded-lg text-xs font-bold bg-slate-200 text-slate-700">취소</button>
          </div>
        </div>
      ) : (
        <div className="flex gap-2">
          <button onClick={() => setConfirm('update')} disabled={busy || offline}
            title={offline ? '오프라인 기기는 업데이트할 수 없습니다' : undefined}
            className="flex-1 py-1.5 rounded-lg text-xs font-bold text-[#2c4be0] border border-[#2c4be0]/30 bg-[#2c4be0]/8 hover:bg-[#2c4be0]/14 disabled:opacity-50 flex items-center justify-center gap-1.5">
            {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />}업데이트
          </button>
          <button onClick={() => setConfirm('rollback')} disabled={busy || offline || !status?.has_backup}
            title={status?.has_backup ? undefined : '되돌릴 백업이 없습니다'}
            className="px-3 py-1.5 rounded-lg text-xs font-bold text-slate-600 border border-slate-300 bg-white hover:bg-slate-50 disabled:opacity-50 flex items-center gap-1.5">
            <Undo2 className="w-3.5 h-3.5" />롤백
          </button>
        </div>
      )}
      {status?.up_to_date && !msg && (
        <p className="mt-3 text-[11px] text-emerald-600 flex items-center gap-1"><CheckCircle className="w-3 h-3" />서버와 같은 버전입니다</p>
      )}
    </div>
  )
}
