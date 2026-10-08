import { useState } from 'react'
import { Check, Loader2, ShieldQuestion, X } from 'lucide-react'

import * as api from '../api'
import { useApi } from '../hooks/useApi'

/** "n분 전" — 15분 무요청이면 서버가 목록에서 지우므로 분 단위면 충분하다. */
function ago(epochSec: number): string {
  const sec = Math.max(0, Math.round(Date.now() / 1000 - epochSec))
  if (sec < 60) return `${sec}초 전`
  return `${Math.round(sec / 60)}분 전`
}

/**
 * 다른 서버에 등록된 Pi가 스스로 올라와 **승인을 기다리는** 목록.
 *
 * 서버 PC를 바꿨을 때의 정상 경로다. 승인하면 그 기기의 신원이 이 서버 것으로 바뀌고 이전 서버와의
 * 연결이 끊긴다 — 되돌릴 수 없으므로 확인을 받는다. 대기가 없으면 아무것도 그리지 않는다.
 */
export default function PendingEnrollments({ onChanged }: { onChanged?: () => void }) {
  // 서버가 15분 무요청이면 지우고 기기는 5분마다 다시 알리므로 10초 간격으로 따라가면 충분하다.
  const { data, reload } = useApi(() => api.listPendingEnrollments(), [], 10_000)
  const [busy, setBusy] = useState<string | null>(null)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const items = data ?? []

  if (items.length === 0 && !message) return null

  const approve = async (e: api.PendingEnrollment) => {
    const from = e.current_server_url || '(알 수 없음)'
    if (!window.confirm(
      `${e.hostname || e.device_id} (${e.ip})를 이 서버로 옮깁니다.\n\n` +
      `현재 소속: ${from}\n승인하면 이전 서버와의 연결이 끊기고, 기기에서 부저가 한 번 울립니다. 계속할까요?`)) return
    setBusy(e.ip); setMessage(null)
    try {
      const r = await api.approvePendingEnrollment(e.ip)
      setMessage(r.provisioned
        ? { ok: true, text: `${e.hostname || e.ip}를 이 서버로 옮겼습니다` }
        : { ok: false, text: `신원을 심지 못했습니다 — ${r.provision_error ?? '알 수 없는 오류'} (목록에 남아 있습니다)` })
      onChanged?.()
    } catch (err) {
      setMessage({ ok: false, text: err instanceof Error ? err.message : '승인하지 못했습니다' })
    } finally {
      setBusy(null); reload()
    }
  }

  const reject = async (e: api.PendingEnrollment) => {
    if (!window.confirm(`${e.hostname || e.ip}의 요청을 거절합니다. 1시간 동안 같은 기기의 요청을 무시합니다.`)) return
    setBusy(e.ip); setMessage(null)
    try {
      await api.rejectPendingEnrollment(e.ip)
    } catch (err) {
      setMessage({ ok: false, text: err instanceof Error ? err.message : '거절하지 못했습니다' })
    } finally {
      setBusy(null); reload()
    }
  }

  return (
    <div className="mb-4 p-4 rounded-2xl bg-amber-50 border border-amber-200" role="region" aria-label="승인 대기 기기">
      <div className="flex items-center gap-2 mb-2">
        <ShieldQuestion className="w-4 h-4 text-amber-700" />
        <span className="text-xs font-bold text-amber-900">승인 대기 기기</span>
        {items.length > 0 && (
          <span className="text-[10px] font-bold text-amber-800 bg-amber-200 px-2 py-0.5 rounded-full">{items.length}</span>
        )}
        <span className="text-[10.5px] text-amber-800/80">
          다른 서버에 등록돼 있던 기기가 이 서버를 찾았습니다. 승인하면 이 서버로 옮겨 옵니다.
        </span>
      </div>

      {message && (
        <p className={`text-xs font-semibold mb-2 ${message.ok ? 'text-emerald-700' : 'text-red-700'}`}>{message.text}</p>
      )}

      <div className="space-y-2">
        {items.map((e) => (
          <div key={e.ip} className="flex items-center gap-3 p-3 rounded-xl bg-white/80 border border-amber-200">
            <div className="flex-1 min-w-0">
              <p className="text-xs font-bold text-slate-800 truncate">{e.hostname || e.device_id}</p>
              <p className="text-[10.5px] text-slate-500 font-mono">
                {e.ip} · 현재 소속 <span className="font-semibold text-slate-700">{e.current_server_url || '(알 수 없음)'}</span>
                {' · '}마지막 요청 {ago(e.last_seen)}
              </p>
            </div>
            <button onClick={() => void approve(e)} disabled={busy !== null}
                    className="flex items-center gap-1 text-[10.5px] font-semibold text-emerald-700 border border-emerald-300 px-3 py-1 rounded-lg hover:bg-emerald-50 disabled:opacity-50 transition whitespace-nowrap">
              {busy === e.ip ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />}승인
            </button>
            <button onClick={() => void reject(e)} disabled={busy !== null}
                    className="flex items-center gap-1 text-[10.5px] font-semibold text-slate-600 border border-slate-200 px-3 py-1 rounded-lg hover:bg-slate-50 disabled:opacity-50 transition whitespace-nowrap">
              <X className="w-3 h-3" />거절
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}
