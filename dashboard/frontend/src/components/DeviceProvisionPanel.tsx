import { useState } from 'react'
import { KeyRound, Loader2 } from 'lucide-react'

import * as api from '../api'

/**
 * 신원 재주입 — 기기에 이 서버의 신원(device_id·api_key·server_url)을 다시 심는다.
 *
 * 쓰는 때: 서버를 바꿨거나 DB를 잃어 이 서버가 기기의 키를 모를 때(개발 PC ↔ 운영 PC 전환 등).
 * 기기는 기존 키 없이도 덮어쓰기(인수)를 받고, 인수 사실은 기기 로그와 부저로 남는다.
 * **새 키가 발급되므로** 이 기기를 다른 서버가 쓰고 있었다면 그쪽 연결은 끊긴다.
 */
export default function DeviceProvisionPanel({ deviceId, provisioned, offline, onDone }: {
  deviceId: string
  provisioned: boolean
  offline: boolean
  onDone: () => void
}) {
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const run = async () => {
    setConfirm(false); setBusy(true); setMsg(null)
    try {
      await api.provisionDevice(deviceId)
      setMsg({ ok: true, text: '기기에 신원을 심었습니다. 부저가 한 번 울릴 수 있습니다' })
      onDone()
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : '신원을 심지 못했습니다' })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="glass-panel p-5">
      <div className="flex items-center justify-between mb-4 pb-3 border-b border-slate-200/70">
        <h2 className="text-sm font-bold text-slate-700">서버 연동</h2>
        <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full border ${
          provisioned ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                      : 'bg-amber-50 text-amber-700 border-amber-200'}`}>
          {provisioned ? '신원 있음' : '신원 미주입'}
        </span>
      </div>
      <p className="text-[11px] text-slate-500 mb-3">
        {provisioned
          ? '이 서버가 기기를 제어할 수 있습니다. 서버를 바꿨다면 다시 심으세요.'
          : '이 서버가 기기의 키를 모릅니다 — 재시작·코드 업데이트를 하려면 먼저 신원을 심어야 합니다.'}
      </p>
      {msg && (
        <div className={`mb-3 px-3 py-2 rounded-xl border text-xs font-semibold ${
          msg.ok ? 'bg-emerald-50 border-emerald-200 text-emerald-700' : 'bg-red-50 border-red-200 text-red-700'}`}>
          {msg.text}
        </div>
      )}
      {confirm ? (
        <div>
          <p className="text-xs font-semibold text-slate-700 mb-2">
            새 키가 발급됩니다. 다른 서버가 이 기기를 쓰고 있었다면 그쪽 연결은 끊깁니다. 계속할까요?
          </p>
          <div className="flex gap-2">
            <button onClick={() => void run()} className="flex-1 py-1.5 rounded-lg text-xs font-bold bg-[#2c4be0] text-white">실행</button>
            <button onClick={() => setConfirm(false)} className="flex-1 py-1.5 rounded-lg text-xs font-bold bg-slate-200 text-slate-700">취소</button>
          </div>
        </div>
      ) : (
        <button onClick={() => setConfirm(true)} disabled={busy || offline}
          title={offline ? '오프라인 기기는 신원을 심을 수 없습니다' : undefined}
          className="w-full py-1.5 rounded-lg text-xs font-bold text-[#2c4be0] border border-[#2c4be0]/30 bg-[#2c4be0]/8 hover:bg-[#2c4be0]/14 disabled:opacity-50 flex items-center justify-center gap-1.5">
          {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <KeyRound className="w-3.5 h-3.5" />}신원 재주입
        </button>
      )}
    </div>
  )
}
