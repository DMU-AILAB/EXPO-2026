/**
 * 연결 진단 — 기기가 왜 "알 수 없음/주의"인지 원인과 해결 방법을 보여준다.
 *
 * 점검은 서버가 기기에 직접 묻고(`services/diagnosis.py`), 기기가 서버에 닿는지·전원·시계는
 * 기기 스스로 확인한 값이다. 기기 부하를 줄이려고 자동 갱신하지 않고 "다시 확인"을 누를 때만 돈다.
 * 고칠 수 있는 항목은 같은 자리에서 바로 고치는 버튼을 준다.
 */

import { useState } from 'react'
import { AlertTriangle, CheckCircle2, HelpCircle, Loader2, RefreshCw, XCircle } from 'lucide-react'

import * as api from '../../api'
import { useApi } from '../../hooks/useApi'
import { type Ctx, ErrorBox, Panel, describe, primaryBtn, secondaryBtn } from './shared'

const STYLE: Record<api.CheckStatus, { icon: typeof XCircle; tone: string; chip: string; label: string }> = {
  ok: { icon: CheckCircle2, tone: 'text-emerald-600', chip: 'bg-emerald-50 text-emerald-700 border-emerald-200', label: '정상' },
  warn: { icon: AlertTriangle, tone: 'text-amber-600', chip: 'bg-amber-50 text-amber-700 border-amber-200', label: '주의' },
  fail: { icon: XCircle, tone: 'text-red-600', chip: 'bg-red-50 text-red-700 border-red-200', label: '문제' },
  unknown: { icon: HelpCircle, tone: 'text-slate-400', chip: 'bg-slate-100 text-slate-600 border-slate-200', label: '확인 불가' },
}

const ACTION_LABEL = {
  refresh_address: '서버 주소 갱신',
  provision: '신원 재주입',
  update: '코드 업데이트',
} as const

export default function DiagnoseTab({ device, reload }: Ctx) {
  const { data, loading, error, reload: recheck } = useApi(() => api.getDiagnosis(device.id), [device.id])
  const [busy, setBusy] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [actionMsg, setActionMsg] = useState<string | null>(null)

  const act = async (action: NonNullable<api.DiagnosisCheck['action']>) => {
    setBusy(action); setActionError(null); setActionMsg(null)
    try {
      if (action === 'refresh_address') {
        const r = await api.refreshDeviceAddress(device.id)
        if (!r.ok) throw new Error(r.error ?? '서버 주소를 갱신하지 못했습니다')
        setActionMsg(r.changed ? `서버 주소를 ${r.server_url}로 바꿨습니다` : '이미 최신 주소입니다')
      } else if (action === 'provision') {
        await api.provisionDevice(device.id)
        setActionMsg('기기에 신원을 심었습니다')
      } else {
        const r = (await api.updateDevices([device.id])).results[0]
        if (!r.ok) throw new Error(r.error ?? '업데이트하지 못했습니다')
        setActionMsg(r.came_back ? '업데이트했습니다' : '적용했지만 기기가 아직 돌아오지 않았습니다')
      }
      reload(); recheck()
    } catch (e) {
      setActionError(describe(e))
    } finally {
      setBusy(null)
    }
  }

  const overall = data ? STYLE[data.overall] : null

  return (
    <Panel
      title="연결 진단"
      actions={
        <>
          {overall && (
            <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full border ${overall.chip}`}>
              전체 {overall.label}
            </span>
          )}
          <button className={secondaryBtn} onClick={recheck} disabled={loading || busy !== null}>
            {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
            다시 확인
          </button>
        </>
      }
    >
      <ErrorBox error={error} />
      <ErrorBox error={actionError} />
      {actionMsg && (
        <div className="mb-3 px-3.5 py-2.5 rounded-xl bg-emerald-50 border border-emerald-200 text-xs font-semibold text-emerald-700">
          {actionMsg}
        </div>
      )}
      {loading && !data && <p className="py-8 text-center text-sm text-slate-400">기기를 점검하는 중…</p>}

      <div className="space-y-2">
        {data?.checks.map((c) => {
          const st = STYLE[c.status]
          const Icon = st.icon
          return (
            <div key={c.key} className="flex items-start gap-3 p-3 rounded-xl bg-slate-50/70 border border-slate-200/60">
              <Icon className={`w-4 h-4 mt-0.5 shrink-0 ${st.tone}`} />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-bold text-slate-800">{c.label}</span>
                  <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded-full border ${st.chip}`}>{st.label}</span>
                </div>
                <p className="text-xs text-slate-600 mt-0.5 break-words">{c.detail}</p>
                {c.fix && c.status !== 'ok' && <p className="text-xs text-slate-500 mt-1">→ {c.fix}</p>}
              </div>
              {c.action && c.status !== 'ok' && (
                <button className={primaryBtn} disabled={busy !== null} onClick={() => void act(c.action!)}>
                  {busy === c.action && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                  {ACTION_LABEL[c.action]}
                </button>
              )}
            </div>
          )
        })}
      </div>
      {data && <p className="mt-3 text-[11px] text-slate-400">확인 시각 {new Date(data.checked_at).toLocaleTimeString()}</p>}
    </Panel>
  )
}
