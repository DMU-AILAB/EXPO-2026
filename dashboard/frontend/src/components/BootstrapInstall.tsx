import { useEffect, useState } from 'react'
import { Check, Copy, Loader2, Terminal } from 'lucide-react'

import * as api from '../api'

/**
 * 새 기기 설치 — Pi에서 한 줄로 코드·서비스·sudoers를 설치하고 이 서버에 등록한다.
 *
 * SSH·make 없이 되고, 푸시 업데이트를 받지 못하는 **구버전 기기**도 이 경로로 올린다.
 * 명령에 든 토큰은 30분 유효하고 등록은 1회만 된다. 명령은 서버가 내려주는 스크립트를 root
 * 권한으로 실행하므로, 먼저 `--dry-run`으로 무엇을 하는지 볼 수 있게 두 명령을 함께 준다.
 */
export default function BootstrapInstall() {
  const [info, setInfo] = useState<api.BootstrapToken | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState<'run' | 'dry' | null>(null)
  const [left, setLeft] = useState(0)

  useEffect(() => {
    if (!info) return
    const end = Date.now() + info.expires_in * 1000
    const tick = () => setLeft(Math.max(0, Math.round((end - Date.now()) / 1000)))
    tick()
    const id = window.setInterval(tick, 1000)
    return () => window.clearInterval(id)
  }, [info])

  const create = async () => {
    setBusy(true); setError(null)
    try {
      setInfo(await api.createBootstrapToken())
    } catch (e) {
      setError(e instanceof Error ? e.message : '설치 명령을 만들지 못했습니다')
    } finally {
      setBusy(false)
    }
  }

  const copy = async (which: 'run' | 'dry', text: string) => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(which)
      window.setTimeout(() => setCopied(null), 1500)
    } catch {
      /* 클립보드를 막은 환경 — 아래 텍스트를 직접 선택해 복사하면 된다 */
    }
  }

  const expired = info !== null && left === 0
  const mm = String(Math.floor(left / 60)).padStart(2, '0')
  const ss = String(left % 60).padStart(2, '0')

  return (
    <div className="light-glass-panel p-6">
      <div className="flex items-center gap-2 mb-1">
        <Terminal className="w-4 h-4 text-[#2c4be0]" />
        <h2 className="text-sm font-bold text-slate-700">새 기기 설치</h2>
      </div>
      <p className="text-[11px] text-slate-500 mb-4">
        Pi에 SSH나 모니터로 접속해 아래 명령을 붙여 넣으면 코드·서비스를 설치하고 이 서버에 등록합니다.
        구버전이라 코드 업데이트가 안 되는 기기도 이 방법으로 올릴 수 있습니다. sudo 비밀번호를 한 번 묻습니다.
      </p>

      {error && <div className="mb-3 px-3 py-2 rounded-xl bg-red-50 border border-red-200 text-xs font-semibold text-red-700">{error}</div>}

      {!info || expired ? (
        <button onClick={() => void create()} disabled={busy}
          className="px-4 py-1.5 rounded-xl text-xs font-bold bg-[#2c4be0] text-white shadow-md shadow-[#2c4be0]/25 hover:bg-[#2340c8] disabled:opacity-50 flex items-center gap-1.5">
          {busy && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
          {expired ? '만료됨 — 설치 명령 다시 만들기' : '설치 명령 만들기'}
        </button>
      ) : (
        <div className="space-y-3">
          <Command label="설치" text={info.command} done={copied === 'run'} onCopy={() => void copy('run', info.command)} />
          <Command label="먼저 확인만 (--dry-run, 아무것도 바꾸지 않음)" text={info.dry_run_command}
            done={copied === 'dry'} onCopy={() => void copy('dry', info.dry_run_command)} />
          <p className="text-[11px] text-slate-500">
            남은 시간 <span className="font-mono font-semibold text-slate-700">{mm}:{ss}</span>
            {' · '}서버 주소 <span className="font-mono">{info.server_url}</span> — 기기에서 이 주소가 열려야 합니다
            (방화벽·같은 Wi-Fi 확인).
          </p>
        </div>
      )}
    </div>
  )
}

function Command({ label, text, done, onCopy }: { label: string; text: string; done: boolean; onCopy: () => void }) {
  return (
    <div>
      <p className="text-[11px] font-semibold text-slate-600 mb-1">{label}</p>
      <div className="flex items-start gap-2">
        <code className="flex-1 font-mono text-[11px] bg-slate-900 text-slate-100 rounded-xl px-3 py-2 break-all select-all">{text}</code>
        <button onClick={onCopy} title="복사"
          className="glass-btn px-2.5 py-2 rounded-xl text-xs font-semibold flex items-center gap-1">
          {done ? <Check className="w-3.5 h-3.5 text-emerald-600" /> : <Copy className="w-3.5 h-3.5" />}
        </button>
      </div>
    </div>
  )
}
