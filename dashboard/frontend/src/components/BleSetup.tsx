/**
 * 블루투스로 Wi-Fi 설정 — 아직 망에 없는 기기에 브라우저가 직접 SSID·비밀번호를 건넨다.
 *
 * 상대는 기기의 `device/ble_provisioning.py`다. 기기는 **Wi-Fi 버튼을 3초 눌러 연 3분
 * 창**에서만, 그리고 Wi-Fi에 붙어 있지 않을 때만 광고한다 — 그래서 기기 목록에 안
 * 보이면 대부분 버튼을 안 눌렀거나 창이 닫힌 것이다.
 *
 * 프로토콜(UUID는 기기와 반드시 같아야 한다):
 *   info(read) · command(write JSON) · result(read JSON) · event(notify 1바이트 순번)
 * 결과는 notify에 싣지 않는다 — notify는 20바이트까지라 스캔 목록이 안 들어간다.
 * 순번이 바뀌면 result를 다시 읽는다. notify가 끊겨도 진행되도록 폴링도 같이 한다.
 * result JSON의 `n`도 같은 순번이라, 명령 전후로 비교해 이번 명령의 결과만 고른다.
 *
 * Web Bluetooth는 Chrome·Edge(데스크톱/안드로이드)에서만, `localhost` 또는 HTTPS에서만 된다.
 */

import { useEffect, useRef, useState } from 'react'
import { Bluetooth, CheckCircle2, Loader2, Lock, RefreshCw, XCircle } from 'lucide-react'

import * as api from '../api'

const SERVICE_UUID = '6f1e0001-5a1b-4c7d-9e2f-56495347554e'
const INFO_UUID = '6f1e0002-5a1b-4c7d-9e2f-56495347554e'
const COMMAND_UUID = '6f1e0003-5a1b-4c7d-9e2f-56495347554e'
const RESULT_UUID = '6f1e0004-5a1b-4c7d-9e2f-56495347554e'
const EVENT_UUID = '6f1e0005-5a1b-4c7d-9e2f-56495347554e'

/** nmcli 연결 시도(최대 30초) + DHCP + 여유. */
const CONNECT_TIMEOUT_MS = 60_000
/** 새 IP에 대시보드 서버가 닿을 때까지 기다리는 시간. */
const REACH_TIMEOUT_MS = 30_000

type Info = { product: string; host: string | null; ver: string; wifi: { mode: string; ssid: string | null } }
type Network = { s: string; q: number; l: 0 | 1 }
/** 기기는 결과마다 순번 `n`(0~255 순환)을 붙인다. */
type Result = { n?: number } & (
  | { state: 'idle' | 'scanning' }
  | { state: 'scanned'; networks: Network[] }
  | { state: 'connecting'; ssid: string }
  | { state: 'connected'; ip: string | null }
  | { state: 'failed' | 'error'; error: string })

type Chars = {
  info: BluetoothRemoteGATTCharacteristic
  command: BluetoothRemoteGATTCharacteristic
  result: BluetoothRemoteGATTCharacteristic
  event: BluetoothRemoteGATTCharacteristic
}

type Phase =
  | { kind: 'idle' }
  | { kind: 'pairing' }
  | { kind: 'ready' }
  | { kind: 'scanning' }
  | { kind: 'connecting'; ssid: string }
  | { kind: 'registering'; ip: string }
  | { kind: 'done'; ip: string; known?: boolean }

const dec = new TextDecoder()
const enc = new TextEncoder()
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

export function bleSupport(): { ok: true } | { ok: false; reason: string } {
  if (!('bluetooth' in navigator)) {
    return { ok: false, reason: '이 브라우저는 Web Bluetooth를 지원하지 않습니다. 데스크톱 Chrome 또는 Edge를 쓰세요(iPhone Safari 미지원).' }
  }
  if (!window.isSecureContext) {
    return { ok: false, reason: `Web Bluetooth는 localhost 또는 HTTPS에서만 동작합니다. 지금 주소(${location.host})에서는 쓸 수 없습니다 — 대시보드를 띄운 PC에서 http://localhost:5173 으로 여세요.` }
  }
  return { ok: true }
}

export default function BleSetup({ onRegister }: {
  /** 기기가 새 IP로 망에 붙으면 부른다 — 탐색 화면의 등록(신원 주입)을 그대로 쓴다. */
  onRegister: (ip: string) => Promise<boolean>
}) {
  const support = bleSupport()
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' })
  const [info, setInfo] = useState<Info | null>(null)
  const [networks, setNetworks] = useState<Network[] | null>(null)
  const [ssid, setSsid] = useState('')
  const [psk, setPsk] = useState('')
  const [error, setError] = useState<string | null>(null)

  const deviceRef = useRef<BluetoothDevice | null>(null)
  const charsRef = useRef<Chars | null>(null)
  const lastResult = useRef<Result>({ state: 'idle' })

  useEffect(() => () => { deviceRef.current?.gatt?.disconnect() }, [])

  const readJson = async <T,>(c: BluetoothRemoteGATTCharacteristic): Promise<T> =>
    JSON.parse(dec.decode(await c.readValue())) as T

  /** GATT를 (다시) 연다 — Wi-Fi 전환 순간 같은 칩의 BLE가 끊길 수 있어 재사용한다. */
  const openGatt = async (device: BluetoothDevice): Promise<Chars> => {
    const server = await device.gatt!.connect()
    const svc = await server.getPrimaryService(SERVICE_UUID)
    const chars: Chars = {
      info: await svc.getCharacteristic(INFO_UUID),
      command: await svc.getCharacteristic(COMMAND_UUID),
      result: await svc.getCharacteristic(RESULT_UUID),
      event: await svc.getCharacteristic(EVENT_UUID),
    }
    try {
      await chars.event.startNotifications()
      chars.event.addEventListener('characteristicvaluechanged', () => { void refreshResult() })
    } catch {
      /* 알림이 안 되면 폴링으로만 간다 */
    }
    charsRef.current = chars
    return chars
  }

  const refreshResult = async (): Promise<Result> => {
    const c = charsRef.current
    if (!c) return lastResult.current
    try {
      const r = await readJson<Result>(c.result)
      lastResult.current = r
      if (r.state === 'scanned') setNetworks(r.networks)
      return r
    } catch {
      return lastResult.current
    }
  }

  const pair = async () => {
    setError(null)
    setPhase({ kind: 'pairing' })
    let device: BluetoothDevice
    try {
      device = await navigator.bluetooth.requestDevice({ filters: [{ services: [SERVICE_UUID] }] })
    } catch (e) {
      setPhase({ kind: 'idle' })
      // 선택 창을 닫은 것만 조용히 넘긴다(NotFoundError). 그 뒤 단계의 NotFoundError는
      // "서비스가 없다"는 진짜 오류라 여기서 같이 삼키면 안 된다.
      if (!(e instanceof DOMException && e.name === 'NotFoundError')) setError(describeBle(e))
      return
    }
    try {
      deviceRef.current = device
      const chars = await openGatt(device)
      setInfo(await readJson<Info>(chars.info))
      setPhase({ kind: 'ready' })
      void scan()
    } catch (e) {
      setPhase({ kind: 'idle' })
      setError(describeBle(e))
    }
  }

  const send = async (cmd: object) => {
    let c = charsRef.current
    if (!c || !deviceRef.current?.gatt?.connected) c = await openGatt(deviceRef.current!)
    await c.command.writeValueWithResponse(enc.encode(JSON.stringify(cmd)))
  }

  /**
   * 명령을 보내고 그 결과를 기다린다. **보내기 직전과 순번 `n`이 달라진** 종료 상태만
   * 인정한다 — 그렇지 않으면 직전 명령의 결과(예: 틀린 비밀번호의 failed)가 그대로 남아
   * 있어 그것을 이번 결과로 오인한다. 진행 상태(scanning)는 캐시된 스캔이면 순식간에
   * 지나가 관찰을 보장할 수 없으므로 기준으로 쓰지 않는다.
   */
  const command = async (cmd: object, done: Result['state'][], timeoutMs: number): Promise<Result | null> => {
    const before = (await refreshResult()).n
    await send(cmd)
    const t0 = Date.now()
    while (Date.now() - t0 < timeoutMs) {
      if (deviceRef.current && !deviceRef.current.gatt?.connected) {
        try { await openGatt(deviceRef.current) } catch { /* 전환 중 — 잠시 뒤 다시 */ }
      }
      const r = await refreshResult()
      if (r.n !== before && done.includes(r.state)) return r
      await sleep(700)
    }
    return null
  }

  const scan = async () => {
    setError(null)
    setPhase({ kind: 'scanning' })
    try {
      const r = await command({ op: 'scan' }, ['scanned', 'error'], 25_000)
      if (r?.state === 'error') setError(r.error)
      else if (!r) setError('기기가 검색 결과를 돌려주지 않습니다.')
    } catch (e) {
      setError(describeBle(e))
    } finally {
      setPhase({ kind: 'ready' })
    }
  }

  const connect = async () => {
    const target = ssid.trim()
    if (!target) return
    setError(null)
    setPhase({ kind: 'connecting', ssid: target })
    try {
      const r = await command({ op: 'connect', ssid: target, psk }, ['connected', 'failed', 'error'],
                              CONNECT_TIMEOUT_MS)
      if (!r) throw new Error('기기가 연결 결과를 알려주지 않습니다. 기기의 LED·부저를 확인하세요.')
      if (r.state === 'failed' || r.state === 'error') throw new Error(`연결 실패: ${r.error}`)
      if (r.state !== 'connected' || !r.ip) throw new Error('연결은 됐지만 IP를 받지 못했습니다.')
      setPsk('')
      deviceRef.current?.gatt?.disconnect()   // 목적을 이뤘다 — 기기도 창을 닫는다
      await register(r.ip)
    } catch (e) {
      setError(describeBle(e))
      setPhase({ kind: 'ready' })
    }
  }

  /** 새 IP가 대시보드 서버에서 보일 때까지 기다렸다가 기존 등록 경로로 넘긴다. */
  const register = async (ip: string) => {
    setPhase({ kind: 'registering', ip })
    const t0 = Date.now()
    while (Date.now() - t0 < REACH_TIMEOUT_MS) {
      try {
        const v = await api.verifyDevice(ip)
        if (v.reachable) {
          // 이미 이 대시보드에 등록된 기기(Wi-Fi만 바꾼 경우)는 다시 등록하지 않는다 —
          // 새로 만들면 "이미 등록된 디바이스"로 거부된다. 주소는 하트비트가 따라간다.
          if (v.already_registered) { setPhase({ kind: 'done', ip, known: true }); return }
          if (await onRegister(ip)) { setPhase({ kind: 'done', ip, known: false }); return }
          break
        }
      } catch { /* 아직 안 보인다 */ }
      await sleep(2000)
    }
    setError(`기기는 ${ip}로 연결됐지만 이 서버에서 닿지 않습니다. 같은 네트워크인지 확인한 뒤 위의 수동 추가로 등록하세요.`)
    setPhase({ kind: 'done', ip, known: false })
  }

  const busy = ['pairing', 'scanning', 'connecting', 'registering'].includes(phase.kind)
  const paired = phase.kind !== 'idle' && phase.kind !== 'pairing'

  return (
    <div id="ble-setup" className="light-glass-panel p-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-bold text-slate-700 flex items-center gap-2">
          <Bluetooth className="w-4 h-4 text-[#2c4be0]" />블루투스로 설정
        </h2>
        {info && <span className="text-[11px] text-slate-500 font-mono">{info.host ?? '기기'} · {deviceRef.current?.name}</span>}
      </div>

      {!support.ok ? (
        <p className="text-xs text-slate-500 leading-relaxed">{support.reason}</p>
      ) : (
        <>
          <ol className="mb-4 space-y-1 text-[11px] text-slate-500 list-decimal pl-4">
            <li>기기가 홈 Wi-Fi에 연결돼 있으면 먼저 <b>Wi-Fi 버튼을 짧게</b> 눌러 핫스팟으로 바꾸세요(비프 2번).</li>
            <li><b>Wi-Fi 버튼을 3초</b> 누르세요. 길게 한 번 울리고 LED가 0.5초 간격으로 고르게 깜빡이면 3분간 열립니다.
              짧게 3번 울리면 아직 홈 Wi-Fi에 연결된 상태입니다.</li>
            <li>아래 버튼을 눌러 <b>VG-</b>로 시작하는 기기를 고르세요.</li>
            <li>Wi-Fi를 고르고 비밀번호를 넣으면 연결 후 이 대시보드에 자동으로 등록됩니다.</li>
          </ol>

          {error && (
            <div className="mb-3 px-3.5 py-2.5 rounded-xl bg-red-50 border border-red-200 text-xs font-semibold text-red-700 flex gap-2">
              <XCircle className="w-4 h-4 shrink-0" />{error}
            </div>
          )}

          {!paired && (
            <button onClick={() => void pair()} disabled={busy}
                    className="px-4 py-2 rounded-xl text-xs font-semibold text-white bg-[#2c4be0] hover:bg-[#1d35b5] disabled:opacity-50 shadow-md shadow-[#2c4be0]/25 flex items-center gap-2 transition">
              {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Bluetooth className="w-3.5 h-3.5" />}
              주변 기기 찾기
            </button>
          )}

          {paired && phase.kind !== 'done' && phase.kind !== 'registering' && (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-slate-600">주변 Wi-Fi</span>
                <button onClick={() => void scan()} disabled={busy}
                        className="text-[11px] font-semibold text-[#2c4be0] flex items-center gap-1 disabled:opacity-50">
                  {phase.kind === 'scanning' ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
                  다시 검색
                </button>
              </div>
              <div className="space-y-1.5 max-h-52 overflow-y-auto pr-1">
                {phase.kind === 'scanning' && !networks && (
                  <p className="py-3 text-center text-xs text-slate-400">기기가 Wi-Fi를 검색하는 중…(최대 15초)</p>
                )}
                {networks?.map((n) => (
                  <button key={n.s} onClick={() => setSsid(n.s)}
                          className={`w-full flex items-center gap-3 px-3 py-2 rounded-xl border text-xs text-left transition ${
                            ssid === n.s ? 'border-[#2c4be0] bg-[#2c4be0]/5' : 'border-slate-200 bg-white/80 hover:bg-white'}`}>
                    <span className="flex-1 font-semibold text-slate-700 truncate">{n.s}</span>
                    {n.l === 1 && <Lock className="w-3 h-3 text-slate-400" />}
                    <span className="text-slate-400 tabular-nums">{n.q}%</span>
                  </button>
                ))}
              </div>
              <div className="grid grid-cols-2 gap-2">
                <input className="bg-white border border-slate-200 rounded-xl px-3 py-2 text-xs" placeholder="SSID"
                       value={ssid} maxLength={32} onChange={(e) => setSsid(e.target.value)} />
                <input className="bg-white border border-slate-200 rounded-xl px-3 py-2 text-xs" placeholder="비밀번호(개방형은 비움)"
                       type="password" value={psk} maxLength={63} onChange={(e) => setPsk(e.target.value)} />
              </div>
              <button onClick={() => void connect()} disabled={busy || !ssid.trim()}
                      className="w-full py-2 rounded-xl text-xs font-semibold text-white bg-[#2c4be0] hover:bg-[#1d35b5] disabled:opacity-40 flex items-center justify-center gap-2 transition">
                {phase.kind === 'connecting'
                  ? <><Loader2 className="w-3.5 h-3.5 animate-spin" />'{phase.ssid}'에 연결 중… (최대 1분)</>
                  : '이 Wi-Fi로 연결'}
              </button>
              <p className="text-[10.5px] text-slate-400">
                블루투스 구간은 암호화되지 않습니다. 근처에 낯선 사람이 없을 때 설정하세요.
              </p>
            </div>
          )}

          {phase.kind === 'registering' && (
            <p className="flex items-center gap-2 text-xs font-semibold text-slate-700">
              <Loader2 className="w-4 h-4 animate-spin text-[#2c4be0]" />
              {phase.ip}로 연결됨 — 대시보드에 등록하는 중…
            </p>
          )}
          {phase.kind === 'done' && !error && (
            <p className="flex items-center gap-2 text-xs font-semibold text-emerald-700">
              <CheckCircle2 className="w-4 h-4" />
              {phase.known
                ? `${phase.ip}로 연결했습니다. 이미 등록된 기기라 연결만 갱신했습니다.`
                : `${phase.ip}로 연결하고 등록했습니다. 위의 API 키를 확인하세요.`}
            </p>
          )}
          {phase.kind === 'done' && (
            <button onClick={() => { setPhase({ kind: 'idle' }); setInfo(null); setNetworks(null); setError(null) }}
                    className="mt-3 text-[11px] font-semibold text-[#2c4be0]">다른 기기 설정하기</button>
          )}
        </>
      )}
    </div>
  )
}

function describeBle(e: unknown): string {
  if (e instanceof DOMException) {
    if (e.name === 'NotAllowedError') {
      return '기기가 요청을 거부했습니다. 페어링 창이 닫혔을 수 있습니다 — Wi-Fi 버튼을 다시 3초 누르세요.'
    }
    if (e.name === 'NetworkError') return '블루투스 연결이 끊겼습니다. 기기가 가까이 있는지 확인하고 다시 시도하세요.'
    return `${e.name}: ${e.message}`
  }
  return e instanceof Error ? e.message : String(e)
}
