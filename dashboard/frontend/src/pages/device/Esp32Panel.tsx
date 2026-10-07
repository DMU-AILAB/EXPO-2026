import { useState } from 'react'
import { Bluetooth, CheckCircle2, Loader2, RefreshCw, Unlink, Wifi } from 'lucide-react'

import * as api from '../../api'
import { useApi } from '../../hooks/useApi'
import type { Esp32Status } from '../../types'
import { type Ctx, ErrorBox, Panel, inputCls, labelCls, primaryBtn, secondaryBtn } from './shared'

const STATE_LABEL: Record<string, string> = {
  unpaired: '연결 승인 대기', scanning: '주변 ESP32 검색 중', connecting: 'BLE 연결 중',
  online: 'BLE 연결됨', offline: 'ESP32 연결 끊김', error: '연결 오류',
}

function RelaySummary({ status }: { status: Esp32Status | null }) {
  if (!status) return <span className="text-slate-400">Pi 상태 보고 대기 중</span>
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-xs">
      <dt className="text-slate-400">BLE</dt>
      <dd className="font-bold text-slate-800">{STATE_LABEL[status.state] ?? status.state}</dd>
      <dt className="text-slate-400">기기 ID</dt><dd className="font-mono text-slate-700">{status.device_id ?? '—'}</dd>
      <dt className="text-slate-400">ESP32 Wi‑Fi</dt>
      <dd className="font-semibold text-slate-700">
        {status.wifi_connected ? `${status.wifi_ssid ?? '연결됨'}${status.ip ? ` · ${status.ip}` : ''}` : '연결 안 됨'}
      </dd>
      <dt className="text-slate-400">GPIO13 출력</dt>
      <dd className={status.relay_state === 'on' ? 'font-bold text-emerald-700' : 'font-semibold text-slate-700'}>
        {status.relay_state === 'on' ? 'ON' : 'OFF'}
      </dd>
      {status.last_error && <><dt className="text-slate-400">최근 오류</dt><dd className="text-red-700">{status.last_error}</dd></>}
    </dl>
  )
}

export default function Esp32Panel({ device }: Ctx) {
  const status = useApi(() => api.getEsp32(device.id), [device.id], 5000)
  const [ssid, setSsid] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [notice, setNotice] = useState('')
  const data = status.data

  const bind = async (esp32Id: string) => {
    setBusy(true); setError(null); setNotice('')
    try {
      await api.bindEsp32(device.id, esp32Id)
      setNotice(`${esp32Id} 연결을 승인했습니다.`)
      status.reload()
    } catch (e) { setError(e) } finally { setBusy(false) }
  }

  const unbind = async () => {
    setBusy(true); setError(null); setNotice('')
    try {
      await api.unbindEsp32(device.id)
      setNotice('ESP32 연결을 해제했습니다.')
      status.reload()
    } catch (e) { setError(e) } finally { setBusy(false) }
  }

  const configureWifi = async () => {
    setBusy(true); setError(null); setNotice('')
    try {
      await api.configureEsp32Wifi(device.id, ssid.trim(), password)
      setPassword('')
      setNotice('Wi‑Fi 변경 명령을 Pi에 전달했습니다. ESP32가 연결을 확인하면 상태가 갱신됩니다.')
      status.reload()
    } catch (e) { setError(e) } finally { setBusy(false) }
  }

  const command = data?.command
  return (
    <Panel title="ESP32 · BLE 출력 장치" className="lg:col-span-5"
      actions={<button className={secondaryBtn} onClick={status.reload} disabled={busy}>
        <RefreshCw className="w-3.5 h-3.5" />새로고침
      </button>}>
      <ErrorBox error={error ?? status.error} />
      {notice && <p className="mb-3 text-xs font-semibold text-emerald-700">{notice}</p>}
      {data?.stale && <p className="mb-3 text-xs text-amber-700">Pi의 상태 보고가 오래됐습니다. Pi의 네트워크 연결을 확인하세요.</p>}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div>
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-xs font-bold text-slate-700">기기 연결 승인</h3>
            <span className="text-[10px] text-slate-400">Pi가 BLE로 자동 검색합니다</span>
          </div>
          <RelaySummary status={data?.status ?? null} />
          {data?.binding ? (
            <div className="mt-4 flex items-center gap-2">
              <span className="text-[11px] text-slate-500">승인된 ESP32: <b className="font-mono">{data.binding}</b></span>
              <button className={secondaryBtn} onClick={unbind} disabled={busy}>
                <Unlink className="w-3.5 h-3.5" />연결 해제
              </button>
            </div>
          ) : (
            <div className="mt-4 space-y-2">
              {(data?.status?.candidates ?? []).length === 0 && (
                <p className="text-xs text-slate-400">아직 발견된 ESP32가 없습니다. 전원과 Pi와의 거리를 확인하세요.</p>
              )}
              {(data?.status?.candidates ?? []).map((candidate) => (
                <div key={candidate.device_id} className="flex items-center gap-3 rounded-xl border border-slate-200 p-3">
                  <Bluetooth className="w-4 h-4 text-[#2c4be0]" />
                  <div className="min-w-0 flex-1">
                    <div className="text-xs font-bold text-slate-700">{candidate.name || 'VisionGuide ESP32'}</div>
                    <div className="text-[10px] font-mono text-slate-400 truncate">{candidate.device_id}
                      {candidate.rssi != null ? ` · RSSI ${candidate.rssi} dBm` : ''}</div>
                  </div>
                  <button className={primaryBtn} disabled={busy} onClick={() => bind(candidate.device_id)}>
                    연결 승인
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        <div>
          <h3 className="text-xs font-bold text-slate-700 mb-3">ESP32 Wi‑Fi 변경</h3>
          {!data?.binding ? (
            <p className="text-xs text-slate-400">먼저 발견 목록에서 ESP32 연결을 승인하세요.</p>
          ) : (
            <>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <label><span className={labelCls}>새 Wi‑Fi 이름(SSID)</span>
                  <input className={inputCls} value={ssid} maxLength={32} onChange={(e) => setSsid(e.target.value)} />
                </label>
                <label><span className={labelCls}>비밀번호</span>
                  <input className={inputCls} type="password" value={password} maxLength={63}
                    autoComplete="new-password" onChange={(e) => setPassword(e.target.value)} />
                </label>
              </div>
              <button className={`${primaryBtn} mt-3`} disabled={busy || !ssid.trim()}
                onClick={configureWifi}>
                {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Wifi className="w-3.5 h-3.5" />}
                Wi‑Fi 변경
              </button>
              <p className="mt-2 text-[10px] text-slate-400">
                비밀번호는 적용 대기 중 메모리에만 보관되며 Pi가 결과를 확인하면 삭제됩니다.
              </p>
              {command && (
                <p className={`mt-2 text-xs flex items-center gap-1.5 ${command.state === 'failed' || command.state === 'expired' ? 'text-red-700' : 'text-slate-600'}`}>
                  {command.state === 'queued' || command.state === 'delivered'
                    ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    : <CheckCircle2 className="w-3.5 h-3.5" />}
                  {command.ssid}: {command.state === 'ok' ? '연결 완료' : command.state === 'failed' ? `실패${command.message ? ` · ${command.message}` : ''}` : command.state === 'expired' ? '명령 만료' : '적용 대기 중'}
                </p>
              )}
            </>
          )}
        </div>
      </div>
    </Panel>
  )
}
