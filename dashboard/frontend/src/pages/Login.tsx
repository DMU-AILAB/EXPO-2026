import { useState } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { Scan, Loader2 } from 'lucide-react'

import { ApiError } from '../api/client'
import { useAuth } from '../auth'

export default function Login() {
  const { user, login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation() as { state?: { from?: { pathname: string } } }

  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  if (user) return <Navigate to={location.state?.from?.pathname ?? '/'} replace />

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(username, password)
      navigate(location.state?.from?.pathname ?? '/', { replace: true })
    } catch (err) {
      // 서버의 오류 코드를 그대로 보고 분기한다 — 잠금은 실패와 안내가 달라야 한다.
      if (err instanceof ApiError && err.code === 'ACCOUNT_LOCKED') {
        setError('로그인 5회 실패로 계정이 잠겼습니다. 30분 후 다시 시도하세요.')
      } else if (err instanceof ApiError && err.code === 'INVALID_CREDENTIALS') {
        setError('아이디 또는 비밀번호가 올바르지 않습니다.')
      } else {
        setError(err instanceof Error ? err.message : '로그인에 실패했습니다.')
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-[70vh] flex items-center justify-center px-6">
      <form
        onSubmit={submit}
        className="w-full max-w-sm rounded-3xl bg-white/85 backdrop-blur-xl border border-white/80 shadow-xl shadow-slate-300/30 p-8"
      >
        <div className="flex items-center gap-3 mb-7">
          <div className="w-11 h-11 rounded-2xl bg-gradient-to-tr from-[#2c4be0] via-[#4361ee] to-[#06b6d4] p-[1.5px] flex items-center justify-center shadow-lg shadow-[#2c4be0]/20">
            <div className="w-full h-full bg-white rounded-[14px] flex items-center justify-center">
              <Scan className="w-5 h-5 text-[#2c4be0]" strokeWidth={2.4} />
            </div>
          </div>
          <div>
            <div className="font-bold text-[17px] tracking-tight text-slate-900">VisionGuide</div>
            <div className="text-[11px] text-slate-500 font-medium">관리자 대시보드</div>
          </div>
        </div>

        <label className="block text-[11px] font-bold text-slate-500 mb-1.5">아이디</label>
        <input
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoComplete="username"
          className="w-full mb-4 px-3.5 py-2.5 rounded-xl bg-white border border-slate-200 text-sm outline-none focus:border-[#2c4be0] focus:ring-2 focus:ring-[#2c4be0]/15"
        />

        <label className="block text-[11px] font-bold text-slate-500 mb-1.5">비밀번호</label>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
          className="w-full mb-5 px-3.5 py-2.5 rounded-xl bg-white border border-slate-200 text-sm outline-none focus:border-[#2c4be0] focus:ring-2 focus:ring-[#2c4be0]/15"
        />

        {error && (
          <div className="mb-4 px-3.5 py-2.5 rounded-xl bg-red-50 border border-red-200 text-xs font-semibold text-red-700">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={busy || !password}
          className="w-full py-2.5 rounded-xl bg-[#2c4be0] text-white text-sm font-bold shadow-md shadow-[#2c4be0]/25 disabled:opacity-50 flex items-center justify-center gap-2"
        >
          {busy && <Loader2 className="w-4 h-4 animate-spin" />}
          로그인
        </button>
      </form>
    </div>
  )
}
