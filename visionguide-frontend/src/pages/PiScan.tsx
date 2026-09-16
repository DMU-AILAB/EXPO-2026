import { useState, useEffect } from 'react'
import { Wifi, Plus, CheckCircle2, AlertCircle, Loader2, Search } from 'lucide-react'
import { mockDevices } from '../data/mockData'
import StatusBadge from '../components/StatusBadge'
import type { DeviceStatus } from '../types'

type ScanState = 'idle' | 'scanning' | 'done'

export default function PiScan() {
  const [scanState, setScanState] = useState<ScanState>('idle')
  const [scanProgress, setScanProgress] = useState(0)
  const [added, setAdded] = useState<Set<string>>(new Set())
  const [manualIp, setManualIp] = useState('')
  const [manualPort, setManualPort] = useState('5000')
  const [manualAlias, setManualAlias] = useState('')
  const [verifyState, setVerifyState] = useState<'idle' | 'loading' | 'ok' | 'fail'>('idle')

  useEffect(() => {
    if (scanState !== 'scanning') return
    setScanProgress(0)
    const interval = setInterval(() => {
      setScanProgress((p) => {
        if (p >= 254) {
          clearInterval(interval)
          setScanState('done')
          return 254
        }
        return p + Math.floor(Math.random() * 12) + 4
      })
    }, 80)
    return () => clearInterval(interval)
  }, [scanState])

  const handleVerify = () => {
    if (!manualIp) return
    setVerifyState('loading')
    setTimeout(() => {
      setVerifyState(Math.random() > 0.3 ? 'ok' : 'fail')
    }, 1500)
  }

  const toggleAdd = (id: string) => {
    setAdded((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  return (
    <div className="max-w-[1720px] mx-auto px-8 py-7">
      {/* Header */}
      <div className="pb-6 border-b border-slate-200/60 mb-6">
        <h1 className="text-2xl font-extrabold tracking-tight text-slate-900 flex items-center gap-2.5 mb-1">
          Pi 네트워크 탐색
          <span className="text-xs px-2.5 py-0.5 rounded-lg bg-slate-200/70 text-slate-700 font-semibold border border-slate-300/60">
            최대 100개
          </span>
        </h1>
        <p className="text-xs text-slate-500 font-medium">
          로컬 네트워크에서 VisionGuide Pi를 자동으로 탐색하거나 IP를 직접 입력해 추가하세요.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Auto scan panel */}
        <div className="light-glass-panel p-6">
          <h2 className="text-sm font-bold text-slate-700 mb-4">자동 탐색</h2>

          {/* Scan status banner */}
          {scanState === 'idle' && (
            <div className="mb-5 p-4 rounded-xl bg-slate-50 border border-slate-200 text-center">
              <Wifi className="w-8 h-8 text-slate-400 mx-auto mb-2" />
              <p className="text-xs text-slate-500">네트워크 스캔을 시작하면 같은 서브넷의 Pi를 자동으로 탐색합니다.</p>
            </div>
          )}

          {scanState === 'scanning' && (
            <div className="mb-5 p-4 rounded-xl bg-[#2c4be0]/8 border border-[#2c4be0]/25">
              <div className="flex items-center gap-3 mb-2">
                <Loader2 className="w-4 h-4 text-[#2c4be0] animate-spin" />
                <span className="text-xs font-semibold text-[#2c4be0]">
                  192.168.1.0/24 스캔 중... ({Math.min(scanProgress, 254)}/254)
                </span>
              </div>
              <div className="w-full bg-[#2c4be0]/15 rounded-full h-1.5 overflow-hidden">
                <div
                  className="bg-[#2c4be0] h-1.5 rounded-full transition-all"
                  style={{ width: `${(Math.min(scanProgress, 254) / 254) * 100}%` }}
                />
              </div>
            </div>
          )}

          {scanState === 'done' && (
            <div className="mb-5 p-4 rounded-xl bg-emerald-50 border border-emerald-200 flex items-center gap-3">
              <CheckCircle2 className="w-4 h-4 text-emerald-600 flex-shrink-0" />
              <span className="text-xs font-semibold text-emerald-700">
                스캔 완료 — {mockDevices.length}개 디바이스 발견
              </span>
            </div>
          )}

          {/* Device list */}
          {(scanState === 'scanning' || scanState === 'done') && (
            <div className="space-y-2 mb-5 max-h-72 overflow-y-auto">
              {mockDevices.map((device) => {
                const isAdded = added.has(device.id)
                return (
                  <div
                    key={device.id}
                    className={`flex items-center gap-3 p-3 rounded-xl border transition ${
                      isAdded
                        ? 'bg-slate-50/60 border-slate-200 opacity-60'
                        : 'bg-white/80 border-slate-200 hover:border-[#2c4be0]/30'
                    }`}
                  >
                    <div>
                      <StatusBadge status={device.status as DeviceStatus} size="sm" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-xs font-bold text-slate-800 truncate">{device.name}.local</p>
                      <p className="text-[10.5px] text-slate-500 font-mono">
                        {device.ip} · 포트 5000 · VisionGuide v1.2
                      </p>
                    </div>
                    {isAdded ? (
                      <span className="flex items-center gap-1 text-[10px] font-bold text-emerald-700 bg-emerald-50 border border-emerald-200 px-2 py-0.5 rounded-full whitespace-nowrap">
                        <CheckCircle2 className="w-3 h-3" />
                        추가됨
                      </span>
                    ) : (
                      <button
                        onClick={() => toggleAdd(device.id)}
                        className="text-[10.5px] font-semibold text-[#2c4be0] border border-[#2c4be0]/40 px-3 py-1 rounded-lg hover:bg-[#2c4be0]/10 transition whitespace-nowrap"
                      >
                        추가
                      </button>
                    )}
                  </div>
                )
              })}
            </div>
          )}

          {/* Footer buttons */}
          <div className="flex items-center justify-between gap-3">
            {scanState === 'scanning' ? (
              <button
                onClick={() => setScanState('idle')}
                className="px-4 py-2 rounded-xl text-xs font-semibold text-slate-600 border border-slate-200 bg-white hover:bg-slate-50 transition"
              >
                스캔 중지
              </button>
            ) : (
              <button
                onClick={() => setScanState('scanning')}
                className="px-4 py-2 rounded-xl text-xs font-semibold text-[#2c4be0] border border-[#2c4be0]/30 bg-[#2c4be0]/8 hover:bg-[#2c4be0]/14 flex items-center gap-2 transition"
              >
                <Wifi className="w-3.5 h-3.5" />
                {scanState === 'done' ? '다시 스캔' : '네트워크 스캔 시작'}
              </button>
            )}
            {scanState !== 'idle' && added.size < mockDevices.length && (
              <button
                onClick={() => setAdded(new Set(mockDevices.map((d) => d.id)))}
                className="px-4 py-2 rounded-xl text-xs font-semibold text-white bg-[#2c4be0] hover:bg-[#1d35b5] shadow-md shadow-[#2c4be0]/25 flex items-center gap-2 transition"
              >
                <Plus className="w-3.5 h-3.5" />
                전체 추가 ({mockDevices.length - added.size}개)
              </button>
            )}
          </div>
        </div>

        {/* Manual add panel */}
        <div className="light-glass-panel p-6">
          <h2 className="text-sm font-bold text-slate-700 mb-4">수동 추가</h2>
          <div className="space-y-4">
            <div>
              <label className="text-xs font-semibold text-slate-600 block mb-1.5">IP 주소</label>
              <input
                type="text"
                className="w-full bg-white border border-slate-200 rounded-xl px-3.5 py-2 text-xs text-slate-800 placeholder-slate-400 focus:outline-none focus:border-[#2c4be0] focus:ring-2 focus:ring-[#2c4be0]/15 transition"
                placeholder="192.168.1.100"
                value={manualIp}
                onChange={(e) => { setManualIp(e.target.value); setVerifyState('idle') }}
              />
            </div>
            <div>
              <label className="text-xs font-semibold text-slate-600 block mb-1.5">포트</label>
              <input
                type="text"
                className="w-full bg-white border border-slate-200 rounded-xl px-3.5 py-2 text-xs text-slate-800 placeholder-slate-400 focus:outline-none focus:border-[#2c4be0] focus:ring-2 focus:ring-[#2c4be0]/15 transition"
                placeholder="5000"
                value={manualPort}
                onChange={(e) => setManualPort(e.target.value)}
              />
            </div>
            <div>
              <label className="text-xs font-semibold text-slate-600 block mb-1.5">별칭 (선택)</label>
              <input
                type="text"
                className="w-full bg-white border border-slate-200 rounded-xl px-3.5 py-2 text-xs text-slate-800 placeholder-slate-400 focus:outline-none focus:border-[#2c4be0] focus:ring-2 focus:ring-[#2c4be0]/15 transition"
                placeholder="cam-entrance-01"
                value={manualAlias}
                onChange={(e) => setManualAlias(e.target.value)}
              />
            </div>

            {/* Verify state */}
            {verifyState === 'ok' && (
              <div className="flex items-center gap-2 text-emerald-600 text-xs font-semibold">
                <CheckCircle2 className="w-4 h-4" />
                연결 성공 — VisionGuide Pi 확인됨
              </div>
            )}
            {verifyState === 'fail' && (
              <div className="flex items-center gap-2 text-red-600 text-xs font-semibold">
                <AlertCircle className="w-4 h-4" />
                연결 실패 — 응답 없음
              </div>
            )}

            <div className="flex gap-2 pt-1">
              <button
                onClick={handleVerify}
                disabled={!manualIp || verifyState === 'loading'}
                className="flex-1 py-2 rounded-xl text-xs font-semibold text-slate-600 border border-slate-200 bg-white hover:bg-slate-50 disabled:opacity-50 flex items-center justify-center gap-2 transition"
              >
                {verifyState === 'loading' ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Search className="w-3.5 h-3.5" />
                )}
                연결 확인
              </button>
              <button
                disabled={!manualIp || verifyState !== 'ok'}
                className="flex-1 py-2 rounded-xl text-xs font-semibold text-white bg-[#2c4be0] hover:bg-[#1d35b5] disabled:opacity-40 disabled:cursor-not-allowed shadow-md shadow-[#2c4be0]/25 flex items-center justify-center gap-2 transition"
                onClick={() => alert(`${manualIp}:${manualPort} 추가됨 (목 동작)`)}
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
