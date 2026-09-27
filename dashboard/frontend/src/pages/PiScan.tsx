import { useEffect, useRef, useState } from 'react'
import { Wifi, Plus, CheckCircle2, AlertCircle, Loader2, Search, KeyRound } from 'lucide-react'

import * as api from '../api'
import { ApiError } from '../api/client'
import type { DiscoveredDevice, ScanResult } from '../types'

type Verify = { state: 'idle' | 'loading' | 'ok' | 'fail'; version?: string | null; cameras?: number | null }

/** 등록 응답의 평문 api_key는 **1회만** 노출된다 — 화면에서 반드시 보여줘야 한다. */
type Issued = { id: string; apiKey: string; provisioned: boolean; error: string | null }

export default function PiScan() {
  const [subnet, setSubnet] = useState('192.168.1.0/24')
  const [scan, setScan] = useState<ScanResult | null>(null)
  const [scanning, setScanning] = useState(false)
  const [scanError, setScanError] = useState<string | null>(null)
  const [issued, setIssued] = useState<Issued[]>([])
  const [busyIp, setBusyIp] = useState<string | null>(null)

  const [manualIp, setManualIp] = useState('')
  const [manualPort, setManualPort] = useState('5000')
  const [manualAlias, setManualAlias] = useState('')
  const [verify, setVerify] = useState<Verify>({ state: 'idle' })

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // 스캔은 백그라운드로 돌고 진행률은 폴링으로 읽는다 (명세 §12).
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current) }, [])

  const startScan = async () => {
    setScanError(null)
    setScanning(true)
    setScan(null)
    try {
      const { scan_id } = await api.startScan(subnet)
      pollRef.current = setInterval(async () => {
        try {
          const result = await api.getScan(scan_id)
          setScan(result)
          if (result.status === 'completed' || result.status === 'failed') {
            if (pollRef.current) clearInterval(pollRef.current)
            setScanning(false)
            if (result.status === 'failed') setScanError('스캔에 실패했습니다')
          }
        } catch {
          if (pollRef.current) clearInterval(pollRef.current)
          setScanning(false)
        }
      }, 700)
    } catch (e) {
      setScanning(false)
      setScanError(e instanceof Error ? e.message : '스캔을 시작하지 못했습니다')
    }
  }

  const stopScan = () => {
    if (pollRef.current) clearInterval(pollRef.current)
    setScanning(false)
  }

  /** 발견한 기기를 등록한다 — 등록이 곧 신원 주입이다(백엔드가 Pi에 심는다). */
  const addDevice = async (d: DiscoveredDevice, alias?: string) => {
    setBusyIp(d.ip)
    try {
      // id는 Pi가 이미 아는 것이 있으면 그것을, 없으면 별칭이나 IP에서 만든다.
      const id = d.device_id || alias || `pi-${d.ip.replace(/\./g, '-')}`
      const res = await api.createDevice({
        id, name: alias || id, ip: d.ip,
      })
      setIssued((prev) => [...prev, {
        id: res.id, apiKey: res.api_key,
        provisioned: res.provisioned, error: res.provision_error,
      }])
      // 잘못된 토큰이나 일시적인 연결 실패라면 같은 토큰으로 재시도할 수 있게 둔다.
    } catch (e) {
      const msg = e instanceof ApiError && e.code === 'DEVICE_ALREADY_EXISTS'
        ? '이미 등록된 디바이스입니다'
        : e instanceof Error ? e.message : '등록에 실패했습니다'
      setScanError(msg)
    } finally {
      setBusyIp(null)
    }
  }

  const handleVerify = async () => {
    if (!manualIp) return
    setVerify({ state: 'loading' })
    try {
      const res = await api.verifyDevice(manualIp, Number(manualPort) || 5000)
      setVerify(res.reachable
        ? { state: 'ok', version: res.version, cameras: res.camera_count }
        : { state: 'fail' })
    } catch {
      setVerify({ state: 'fail' })
    }
  }

  const discovered = scan?.discovered ?? []
  const addedIds = new Set(issued.map((i) => i.id))
  const progressPct = scan && scan.total > 0
    ? Math.round((scan.progress / scan.total) * 100) : 0

  return (
    <div className="max-w-[1720px] mx-auto px-8 py-7">
      <div className="pb-6 border-b border-slate-200/60 mb-6">
        <h1 className="text-2xl font-extrabold tracking-tight text-slate-900 mb-1">
          Pi 네트워크 탐색
        </h1>
        <p className="text-xs text-slate-500 font-medium">
          로컬 네트워크에서 VisionGuide Pi를 자동으로 탐색하거나 IP를 직접 입력해 추가하세요.
          등록하면 백엔드가 기기에 서버 주소와 API 키를 심습니다.
        </p>
      </div>

      {/* 발급된 API 키 — 1회만 노출되므로 눈에 띄게 남긴다 */}
      {issued.length > 0 && (
        <div className="mb-6 p-4 rounded-2xl bg-amber-50 border border-amber-200">
          <div className="flex items-center gap-2 mb-2.5">
            <KeyRound className="w-4 h-4 text-amber-700" />
            <h2 className="text-xs font-bold text-amber-900">
              발급된 API 키 — 이 화면을 벗어나면 다시 볼 수 없습니다
            </h2>
          </div>
          <div className="space-y-1.5">
            {issued.map((i) => (
              <div key={i.id} className="flex items-center gap-3 text-[11px]">
                <span className="font-bold text-amber-900 w-40 truncate">{i.id}</span>
                <code className="flex-1 font-mono bg-white/70 px-2 py-1 rounded-lg border border-amber-200 text-amber-900 truncate">
                  {i.apiKey}
                </code>
                {i.provisioned ? (
                  <span className="text-emerald-700 font-semibold whitespace-nowrap">기기에 심음</span>
                ) : (
                  <span className="text-red-700 font-semibold whitespace-nowrap" title={i.error ?? ''}>
                    주입 실패
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* 자동 탐색 */}
        <div className="light-glass-panel p-6">
          <h2 className="text-sm font-bold text-slate-700 mb-4">자동 탐색</h2>

          <label className="text-xs font-semibold text-slate-600 block mb-1.5">서브넷 (CIDR)</label>
          <input
            className="w-full mb-4 bg-white border border-slate-200 rounded-xl px-3.5 py-2 text-xs font-mono text-slate-800 focus:outline-none focus:border-[#2c4be0] focus:ring-2 focus:ring-[#2c4be0]/15"
            value={subnet}
            onChange={(e) => setSubnet(e.target.value)}
            placeholder="192.168.1.0/24"
          />

          {!scan && !scanning && (
            <div className="mb-5 p-4 rounded-xl bg-slate-50 border border-slate-200 text-center">
              <Wifi className="w-8 h-8 text-slate-400 mx-auto mb-2" />
              <p className="text-xs text-slate-500">
                스캔을 시작하면 같은 서브넷에서 VisionGuide 기기를 찾습니다.
              </p>
            </div>
          )}

          {scanning && (
            <div className="mb-5 p-4 rounded-xl bg-[#2c4be0]/8 border border-[#2c4be0]/25">
              <div className="flex items-center gap-3 mb-2">
                <Loader2 className="w-4 h-4 text-[#2c4be0] animate-spin" />
                <span className="text-xs font-semibold text-[#2c4be0]">
                  {subnet} 스캔 중… ({scan?.progress ?? 0}/{scan?.total ?? 0})
                </span>
              </div>
              <div className="w-full bg-[#2c4be0]/15 rounded-full h-1.5 overflow-hidden">
                <div className="bg-[#2c4be0] h-1.5 rounded-full transition-all"
                     style={{ width: `${progressPct}%` }} />
              </div>
            </div>
          )}

          {scan?.status === 'completed' && (
            <div className="mb-5 p-4 rounded-xl bg-emerald-50 border border-emerald-200 flex items-center gap-3">
              <CheckCircle2 className="w-4 h-4 text-emerald-600 flex-shrink-0" />
              <span className="text-xs font-semibold text-emerald-700">
                스캔 완료 — {discovered.length}개 발견
              </span>
            </div>
          )}

          {scanError && (
            <div className="mb-4 px-3.5 py-2.5 rounded-xl bg-red-50 border border-red-200 text-xs font-semibold text-red-700">
              {scanError}
            </div>
          )}

          {discovered.length > 0 && (
            <div className="space-y-2 mb-5 max-h-72 overflow-y-auto">
              {discovered.map((d) => {
                const done = d.already_registered || addedIds.has(d.device_id ?? '')
                return (
                  <div key={d.ip}
                       className={`flex items-center gap-3 p-3 rounded-xl border transition ${
                         done ? 'bg-slate-50/60 border-slate-200 opacity-60'
                              : 'bg-white/80 border-slate-200 hover:border-[#2c4be0]/30'}`}>
                    <div className="flex-1 min-w-0">
                      <p className="text-xs font-bold text-slate-800 truncate">
                        {d.device_id ?? '(미등록 기기)'}
                      </p>
                      <p className="text-[10.5px] text-slate-500 font-mono">
                        {d.ip} · 포트 {d.port} · VisionGuide {d.version ?? '?'}
                      </p>
                    </div>
                    {done ? (
                      <span className="flex items-center gap-1 text-[10px] font-bold text-emerald-700 bg-emerald-50 border border-emerald-200 px-2 py-0.5 rounded-full whitespace-nowrap">
                        <CheckCircle2 className="w-3 h-3" />
                        등록됨
                      </span>
                    ) : (
                      <button
                        onClick={() => void addDevice(d)}
                        disabled={busyIp === d.ip}
                        className="text-[10.5px] font-semibold text-[#2c4be0] border border-[#2c4be0]/40 px-3 py-1 rounded-lg hover:bg-[#2c4be0]/10 disabled:opacity-50 transition whitespace-nowrap">
                        {busyIp === d.ip ? '등록 중…' : '추가'}
                      </button>
                    )}
                  </div>
                )
              })}
            </div>
          )}

          <div className="flex items-center justify-between gap-3">
            {scanning ? (
              <button onClick={stopScan}
                      className="px-4 py-2 rounded-xl text-xs font-semibold text-slate-600 border border-slate-200 bg-white hover:bg-slate-50 transition">
                스캔 중지
              </button>
            ) : (
              <button onClick={() => void startScan()}
                      className="px-4 py-2 rounded-xl text-xs font-semibold text-[#2c4be0] border border-[#2c4be0]/30 bg-[#2c4be0]/8 hover:bg-[#2c4be0]/14 flex items-center gap-2 transition">
                <Wifi className="w-3.5 h-3.5" />
                {scan ? '다시 스캔' : '네트워크 스캔 시작'}
              </button>
            )}
          </div>
        </div>

        {/* 수동 추가 */}
        <div className="light-glass-panel p-6">
          <h2 className="text-sm font-bold text-slate-700 mb-4">수동 추가</h2>
          <div className="space-y-4">
            <div>
              <label className="text-xs font-semibold text-slate-600 block mb-1.5">IP 주소</label>
              <input
                className="w-full bg-white border border-slate-200 rounded-xl px-3.5 py-2 text-xs text-slate-800 placeholder-slate-400 focus:outline-none focus:border-[#2c4be0] focus:ring-2 focus:ring-[#2c4be0]/15 transition"
                placeholder="192.168.1.100"
                value={manualIp}
                onChange={(e) => { setManualIp(e.target.value); setVerify({ state: 'idle' }) }}
              />
            </div>
            <div>
              <label className="text-xs font-semibold text-slate-600 block mb-1.5">포트</label>
              <input
                className="w-full bg-white border border-slate-200 rounded-xl px-3.5 py-2 text-xs text-slate-800 placeholder-slate-400 focus:outline-none focus:border-[#2c4be0] focus:ring-2 focus:ring-[#2c4be0]/15 transition"
                placeholder="5000"
                value={manualPort}
                onChange={(e) => setManualPort(e.target.value)}
              />
            </div>
            <div>
              <label className="text-xs font-semibold text-slate-600 block mb-1.5">
                디바이스 ID (선택)
              </label>
              <input
                className="w-full bg-white border border-slate-200 rounded-xl px-3.5 py-2 text-xs text-slate-800 placeholder-slate-400 focus:outline-none focus:border-[#2c4be0] focus:ring-2 focus:ring-[#2c4be0]/15 transition"
                placeholder="cam-entrance-01"
                value={manualAlias}
                onChange={(e) => setManualAlias(e.target.value)}
              />
            </div>

            {verify.state === 'ok' && (
              <div className="flex items-center gap-2 text-emerald-600 text-xs font-semibold">
                <CheckCircle2 className="w-4 h-4" />
                연결 성공 — VisionGuide {verify.version ?? ''}
                {verify.cameras != null && ` · 카메라 ${verify.cameras}대`}
              </div>
            )}
            {verify.state === 'fail' && (
              <div className="flex items-center gap-2 text-red-600 text-xs font-semibold">
                <AlertCircle className="w-4 h-4" />
                연결 실패 — VisionGuide 기기가 아니거나 응답이 없습니다
              </div>
            )}

            <div className="flex gap-2 pt-1">
              <button
                onClick={() => void handleVerify()}
                disabled={!manualIp || verify.state === 'loading'}
                className="flex-1 py-2 rounded-xl text-xs font-semibold text-slate-600 border border-slate-200 bg-white hover:bg-slate-50 disabled:opacity-50 flex items-center justify-center gap-2 transition"
              >
                {verify.state === 'loading'
                  ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  : <Search className="w-3.5 h-3.5" />}
                연결 확인
              </button>
              <button
                disabled={!manualIp || verify.state !== 'ok' || busyIp === manualIp}
                className="flex-1 py-2 rounded-xl text-xs font-semibold text-white bg-[#2c4be0] hover:bg-[#1d35b5] disabled:opacity-40 disabled:cursor-not-allowed shadow-md shadow-[#2c4be0]/25 flex items-center justify-center gap-2 transition"
                onClick={() => void addDevice({
                  ip: manualIp, hostname: null, port: Number(manualPort) || 5000,
                  version: verify.version ?? null, registered: false,
                  device_id: manualAlias || null, already_registered: false,
                }, manualAlias || undefined)}
              >
                <Plus className="w-3.5 h-3.5" />
                추가
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
