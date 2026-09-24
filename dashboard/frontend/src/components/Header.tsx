import { useLocation, useNavigate } from 'react-router-dom'
import { Scan, LayoutGrid, Cpu, Video, BarChart3, Search, LogOut } from 'lucide-react'

import * as api from '../api'
import { useApi } from '../hooks/useApi'
import { useAuth } from '../auth'

const tabs = [
  { label: '관제 현황', path: '/', icon: LayoutGrid },
  { label: '디바이스', path: '/devices', icon: Cpu },
  { label: '실시간 스트림', path: '/streams', icon: Video, live: true },
  { label: '통계', path: '/stats', icon: BarChart3 },
  { label: '디바이스 탐색', path: '/scan', icon: Search },
] as const

export default function Header() {
  const location = useLocation()
  const navigate = useNavigate()
  const { user, logout } = useAuth()

  // 헤더의 온라인 배지는 어느 화면에 있든 최신이어야 해서 자체적으로 폴링한다.
  const { data: summary } = useApi(() => api.statsSummary(), [], 10_000)
  const onlineCount = summary?.online_device_count ?? 0
  const totalCount = summary?.total_device_count ?? 0

  const isActive = (path: string) => {
    if (path === '/') return location.pathname === '/'
    return location.pathname.startsWith(path)
  }

  return (
    <header className="fixed top-0 z-50 w-full border-b border-white/80 bg-white/75 backdrop-blur-2xl shadow-sm shadow-slate-200/50">
      <div className="max-w-[1720px] mx-auto px-8 h-[68px] flex items-center justify-between">
        {/* Logo */}
        <div className="flex items-center space-x-4">
          <div className="w-11 h-11 rounded-2xl bg-gradient-to-tr from-[#2c4be0] via-[#4361ee] to-[#06b6d4] p-[1.5px] flex items-center justify-center shadow-lg shadow-[#2c4be0]/20">
            <div className="w-full h-full bg-white rounded-[14px] flex items-center justify-center">
              <Scan className="w-5 h-5 text-[#2c4be0]" strokeWidth={2.4} />
            </div>
          </div>
          <span className="font-bold text-[18px] tracking-tight text-[#0f172a]">
            VisionGuide
          </span>
        </div>

        {/* Navigation */}
        <nav className="hidden lg:flex items-center gap-1.5 p-1.5 rounded-2xl bg-slate-200/50 border border-white/80 backdrop-blur-md shadow-inner">
          {tabs.map((tab) => {
            const Icon = tab.icon
            const active = isActive(tab.path)
            return (
              <button
                key={tab.path}
                onClick={() => navigate(tab.path)}
                className={`px-4 py-1.5 rounded-xl text-xs font-semibold flex items-center gap-2 transition-all ${
                  active
                    ? 'bg-[#2c4be0] text-white shadow-md shadow-[#2c4be0]/25'
                    : 'text-slate-600 hover:text-slate-900 hover:bg-white/80'
                }`}
              >
                <Icon className="w-3.5 h-3.5" strokeWidth={active ? 2.2 : 2} />
                {tab.label}
                {tab.path === '/devices' && totalCount > 0 && (
                  <span className={`text-[10px] px-1.5 rounded-full font-semibold ${active ? 'bg-white/20 text-white' : 'bg-slate-300/60 text-slate-700'}`}>
                    {totalCount}
                  </span>
                )}
                {'live' in tab && tab.live && (
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                )}
              </button>
            )
          })}
        </nav>

        {/* Right actions */}
        <div className="flex items-center gap-3.5">
          {/* Online status */}
          <div className={`glass-badge gap-2 px-3.5 py-1.5 rounded-full shadow-sm ${
            onlineCount === totalCount && totalCount > 0
              ? 'bg-emerald-50/80 border border-emerald-200/70 shadow-emerald-500/5'
              : 'bg-amber-50/80 border border-amber-200/70 shadow-amber-500/5'}`}>
            <span className="relative flex h-2 w-2">
              <span className={`animate-ping absolute inline-flex h-full w-full rounded-full opacity-75 ${
                onlineCount === totalCount && totalCount > 0 ? 'bg-emerald-500' : 'bg-amber-500'}`} />
              <span className={`relative inline-flex rounded-full h-2 w-2 ${
                onlineCount === totalCount && totalCount > 0 ? 'bg-emerald-600' : 'bg-amber-600'}`} />
            </span>
            <span className={`text-xs font-bold tracking-tight ${
              onlineCount === totalCount && totalCount > 0 ? 'text-emerald-700' : 'text-amber-700'}`}>
              {onlineCount} / {totalCount} 온라인
            </span>
          </div>

          {/* Admin */}
          <div className="flex items-center gap-3 pl-2.5 border-l border-slate-200">
            <div className="w-9 h-9 rounded-2xl bg-gradient-to-tr from-[#2c4be0] to-[#7c3aed] flex items-center justify-center text-xs font-bold text-white shadow-md shadow-[#2c4be0]/20">
              관제
            </div>
            <div className="hidden xl:block text-left">
              <div className="text-xs font-bold text-slate-800 leading-tight">
                {user?.display_name ?? user?.username ?? '—'}
              </div>
              <div className="text-[10.5px] text-slate-500 font-medium">{user?.team ?? ''}</div>
            </div>
            <button
              onClick={() => { void logout().then(() => navigate('/login')) }}
              title="로그아웃"
              className="glass-btn w-10 h-10 rounded-2xl justify-center">
              <LogOut className="w-4 h-4" strokeWidth={2} />
            </button>
          </div>
        </div>
      </div>
    </header>
  )
}
