/** 로그인 상태와 보호 라우트.
 *
 * 화면이 6개인데 로그인 화면 자체가 없었다 — 목 데이터로만 돌았기 때문이다.
 * 모든 API가 JWT를 요구하므로 여기가 먼저 있어야 나머지가 성립한다.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'

import * as api from './api'
import { getToken, setUnauthorizedHandler } from './api/client'

type User = { id: number; username: string; display_name: string | null; team: string | null }

type AuthState = {
  user: User | null
  /** 토큰은 있는데 아직 /me 확인 전 — 이때 로그인 화면으로 보내면 깜빡인다. */
  loading: boolean
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

const Ctx = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(Boolean(getToken()))

  // 저장된 토큰이 아직 유효한지 확인한다. 만료됐으면 /me가 401을 내고
  // client.ts의 핸들러가 토큰을 지운다.
  useEffect(() => {
    let cancelled = false
    if (!getToken()) {
      setLoading(false)
      return
    }
    api.me()
      .then((u) => { if (!cancelled) setUser(u) })
      .catch(() => { if (!cancelled) setUser(null) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null))
    return () => setUnauthorizedHandler(null)
  }, [])

  const login = useCallback(async (username: string, password: string) => {
    await api.login(username, password)
    setUser(await api.me())
  }, [])

  const logout = useCallback(async () => {
    await api.logout()
    setUser(null)
  }, [])

  const value = useMemo(() => ({ user, loading, login, logout }), [user, loading, login, logout])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useAuth(): AuthState {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useAuth는 AuthProvider 안에서만 쓸 수 있습니다')
  return ctx
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth()
  const location = useLocation()

  if (loading) {
    return (
      <div className="flex items-center justify-center py-32 text-sm text-slate-500">
        세션 확인 중…
      </div>
    )
  }
  if (!user) return <Navigate to="/login" state={{ from: location }} replace />
  return <>{children}</>
}
