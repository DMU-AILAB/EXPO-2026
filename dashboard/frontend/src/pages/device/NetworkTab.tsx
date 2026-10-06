/**
 * 네트워크 — 이미 망에 있는 기기의 Wi-Fi를 바꾼다.
 *
 * 연결을 바꾸는 순간 기기와의 연결이 끊긴다. 새 망에서 IP가 바뀌어도 하트비트가
 * 서버의 기기 주소를 따라 갱신하므로 다시 붙지만, **새 망에서 이 서버에 닿지
 * 않으면** 대시보드로는 되돌릴 수 없다(현장에서 Wi-Fi 버튼이나 기기 화면으로 복구).
 *
 * AP(핫스팟) 전환은 일부러 없다 — 원격에서 누르면 기기가 망에서 사라진다.
 * 망에 없는 기기의 첫 연결은 "기기 추가 → 블루투스로 설정"이 맡는다.
 */

import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, Loader2, Lock, RefreshCw, Wifi, XCircle } from 'lucide-react'

import * as api from '../../api'
import { useApi } from '../../hooks/useApi'
import type { WifiNetwork } from '../../types'
import { type Ctx, ErrorBox, Panel, inputCls, labelCls, primaryBtn, secondaryBtn } from './shared'

const MODE_LABEL = { station: 'Wi-Fi 연결됨', ap: '핫스팟(AP) 모드', disconnected: '연결 없음' } as const
/** 전환 후 결과를 기다리는 최대 시간. Pi의 nmcli 연결 타임아웃(30초) + 하트비트 주기 여유. */
const SWITCH_TIMEOUT_MS = 120_000

type Switch =
  | { phase: 'idle' }
  | { phase: 'waiting'; ssid: string; since: number }
  | { phase: 'ok'; ssid: string; ip: string | null }
  | { phase: 'failed'; ssid: string; error: string }
  | { phase: 'lost'; ssid: string }

export default function NetworkTab({ device, reload }: Ctx) {
  const status = useApi(() => api.getNetwork(device.id), [device.id])
  const [networks, setNetworks] = useState<WifiNetwork[] | null>(null)
  const [scanning, setScanning] = useState(false)
  const [ssid, setSsid] = useState('')
  const [password, setPassword] = useState('')
  const [confirming, setConfirming] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [sw, setSw] = useState<Switch>({ phase: 'idle' })
  const timer = useRef<ReturnType<typeof setInterval>>()

  useEffect(() => () => clearInterval(timer.current), [])

  const scan = async () => {
    setScanning(true); setError(null)
    try { setNetworks(await api.scanWifi(device.id)) } catch (e) { setError(e) } finally { setScanning(false) }
  }

  const connect = async () => {
    setConfirming(false); setError(null)
    try {
      await api.connectWifi(device.id, ssid, password)
    } catch (e) {
      setError(e)
      return
    }
    setPassword('')
    const since = Date.now()
    setSw({ phase: 'waiting', ssid, since })
    // 기기는 몇 초 뒤 전환하고 그동안 끊긴다. 하트비트가 새 주소를 알려주면 백엔드의
    // 중계가 다시 닿으므로, 그때 결과를 읽는다.
    clearInterval(timer.current)
    timer.current = setInterval(async () => {
      if (Date.now() - since > SWITCH_TIMEOUT_MS) {
        clearInterval(timer.current)
        setSw({ phase: 'lost', ssid })
        return
      }
      try {
        const r = await api.getWifiConnectResult(device.id)
        if (r.in_progress || !r.result) return
        clearInterval(timer.current)
        if (r.result.status === 'ok') setSw({ phase: 'ok', ssid, ip: r.result.ip })
        else setSw({ phase: 'failed', ssid, error: r.result.error })
        status.reload()
        reload()   // 헤더의 IP를 새 주소로
      } catch {
        /* 전환 중에는 닿지 않는 게 정상이다 — 계속 기다린다 */
      }
    }, 3000)
  }

  const st = status.data
  const busy = sw.phase === 'waiting'

  return (
    <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
      <Panel title="현재 연결" className="lg:col-span-2"
             actions={<button className={secondaryBtn} onClick={status.reload}><RefreshCw className="w-3.5 h-3.5" /></button>}>
        <ErrorBox error={status.error} />
        {st ? (
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-xs">
            <dt className="text-slate-400">상태</dt><dd className="font-bold text-slate-800">{MODE_LABEL[st.mode]}</dd>
            <dt className="text-slate-400">SSID</dt><dd className="font-semibold text-slate-700">{st.ssid ?? '—'}</dd>
            <dt className="text-slate-400">Wi-Fi IP</dt><dd className="font-mono text-slate-700">{st.ip ?? '—'}</dd>
            <dt className="text-slate-400">서버가 보는 IP</dt><dd className="font-mono text-slate-700">{device.ip}</dd>
            <dt className="text-slate-400">호스트명</dt><dd className="font-mono text-slate-700">{st.hostname}</dd>
          </dl>
        ) : !status.error && <p className="text-sm text-slate-400">불러오는 중…</p>}
        {st && st.ip !== device.ip && st.mode === 'station' && (
          <p className="mt-3 text-[11px] text-slate-500">
            두 IP가 다르면 기기가 유선으로 서버와 통신하는 중입니다.
          </p>
        )}

        {sw.phase !== 'idle' && (
          <div className="mt-5 pt-4 border-t border-slate-200/70 text-xs">
            {sw.phase === 'waiting' && (
              <p className="flex items-center gap-2 font-semibold text-slate-700">
                <Loader2 className="w-4 h-4 animate-spin text-[#2c4be0]" />
                '{sw.ssid}'로 전환 중… 기기와 잠시 연결이 끊깁니다.
              </p>
            )}
            {sw.phase === 'ok' && (
              <p className="flex items-center gap-2 font-semibold text-emerald-700">
                <CheckCircle2 className="w-4 h-4" />'{sw.ssid}' 연결 완료{sw.ip ? ` · ${sw.ip}` : ''}
              </p>
            )}
            {sw.phase === 'failed' && (
              <p className="flex items-start gap-2 font-semibold text-red-700">
                <XCircle className="w-4 h-4 shrink-0" />'{sw.ssid}' 연결 실패: {sw.error}
              </p>
            )}
            {sw.phase === 'lost' && (
              <p className="flex items-start gap-2 font-semibold text-amber-700">
                <AlertTriangle className="w-4 h-4 shrink-0" />
                2분 동안 기기가 다시 보이지 않습니다. 새 망에서 이 서버에 닿지 않을 수 있습니다 —
                현장에서 Wi-Fi 버튼을 짧게 눌러 이전 연결로 되돌리세요.
              </p>
            )}
          </div>
        )}
      </Panel>

      <Panel title="Wi-Fi 변경" className="lg:col-span-3"
             actions={<button className={secondaryBtn} disabled={scanning || busy} onClick={scan}>
               {scanning ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Wifi className="w-3.5 h-3.5" />}
               {scanning ? '검색 중(최대 15초)…' : '주변 네트워크 검색'}
             </button>}>
        <ErrorBox error={error} />
        {networks && (
          networks.length === 0 ? (
            <p className="py-4 text-center text-sm text-slate-400">검색된 네트워크가 없습니다.</p>
          ) : (
            <div className="space-y-1.5 max-h-64 overflow-y-auto pr-1 mb-4">
              {networks.map((n) => (
                <button key={n.ssid} onClick={() => setSsid(n.ssid)}
                        className={`w-full flex items-center gap-3 px-3 py-2 rounded-xl border text-xs text-left transition ${
                          ssid === n.ssid ? 'border-[#2c4be0] bg-[#2c4be0]/5' : 'border-slate-200/80 bg-slate-50/80 hover:bg-white'}`}>
                  <div className="w-16 h-1.5 rounded-full bg-slate-200 overflow-hidden">
                    <div className="h-full bg-[#2c4be0]" style={{ width: `${n.signal_pct}%` }} />
                  </div>
                  <span className="flex-1 font-semibold text-slate-700 truncate">{n.ssid}</span>
                  {n.security && <Lock className="w-3 h-3 text-slate-400" />}
                  <span className="text-slate-400 tabular-nums">{n.signal_pct}%</span>
                </button>
              ))}
            </div>
          )
        )}

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div>
            <label className={labelCls}>SSID</label>
            <input className={inputCls} value={ssid} maxLength={32} onChange={(e) => setSsid(e.target.value)} />
          </div>
          <div>
            <label className={labelCls}>비밀번호</label>
            <input className={inputCls} type="password" value={password} maxLength={63}
                   onChange={(e) => setPassword(e.target.value)} placeholder="개방형이면 비워 두세요" />
          </div>
        </div>

        {confirming ? (
          <div className="mt-4 p-3.5 rounded-xl border border-amber-200 bg-amber-50 text-xs text-amber-800 space-y-2">
            <p className="font-bold flex items-center gap-1.5"><AlertTriangle className="w-4 h-4" />'{ssid}'로 전환할까요?</p>
            <ul className="list-disc pl-5 space-y-0.5">
              <li>전환하는 동안 기기와 연결이 끊기고, IP가 바뀔 수 있습니다.</li>
              <li>새 네트워크에서 이 서버에 닿지 않으면 대시보드에서 오프라인이 되며 원격으로 되돌릴 수 없습니다.</li>
              <li>비밀번호가 틀리면 기기는 연결에 실패하고 원래 네트워크로 돌아오지 않을 수 있습니다.</li>
            </ul>
            <div className="flex gap-2 pt-1">
              <button className={primaryBtn} onClick={connect}>전환</button>
              <button className={secondaryBtn} onClick={() => setConfirming(false)}>취소</button>
            </div>
          </div>
        ) : (
          <button className={`${primaryBtn} mt-4`} disabled={!ssid.trim() || busy}
                  onClick={() => setConfirming(true)}>
            연결
          </button>
        )}
      </Panel>
    </div>
  )
}
