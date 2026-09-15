import { useLocation, useNavigate } from 'react-router-dom'
import { Scan, Bell, LayoutGrid, Cpu, Video, BarChart3, Search } from 'lucide-react'
import { mockDevices } from '../data/mockData'

const tabs = [
  { label: 'Overview', path: '/', icon: LayoutGrid },
  { label: 'Devices', path: '/devices', icon: Cpu, badge: String(mockDevices.length) },
  { label: 'Live Streams', path: '/streams', icon: Video, live: true },
  { label: 'Statistics', path: '/stats', icon: BarChart3 },
  { label: 'Pi 탐색', path: '/scan', icon: Search },
] as const

export default function Header() {
  const location = useLocation()
  const navigate = useNavigate()
  const onlineCount = mockDevices.filter((d) => d.status !== 'offline').length

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
                {'badge' in tab && tab.badge && (
                  <span className={`text-[10px] px-1.5 rounded-full font-semibold ${active ? 'bg-white/20 text-white' : 'bg-slate-300/60 text-slate-700'}`}>
                    {tab.badge}
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
          <div className="glass-badge gap-2 px-3.5 py-1.5 rounded-full bg-emerald-50/80 border border-emerald-200/70 shadow-sm shadow-emerald-500/5">
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-500 opacity-75" />
              <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-600" />
            </span>
            <span className="text-xs font-bold text-emerald-700 tracking-tight">
              {onlineCount} / {mockDevices.length} Online
            </span>
          </div>

          {/* Notification bell */}
          <button className="glass-btn w-10 h-10 rounded-2xl justify-center relative">
            <Bell className="w-4 h-4" strokeWidth={2} />
            <span className="absolute top-2.5 right-2.5 w-2 h-2 rounded-full bg-red-500 ring-2 ring-white" />
          </button>

          {/* Admin avatar */}
          <div className="flex items-center gap-3 pl-2.5 border-l border-slate-200">
            <div className="w-9 h-9 rounded-2xl bg-gradient-to-tr from-[#2c4be0] to-[#7c3aed] flex items-center justify-center text-xs font-bold text-white shadow-md shadow-[#2c4be0]/20">
              관제
            </div>
            <div className="hidden xl:block text-left">
              <div className="text-xs font-bold text-slate-800 leading-tight">Admin_Ops</div>
              <div className="text-[10.5px] text-slate-500 font-medium">보행안전 통합팀</div>
            </div>
          </div>
        </div>
      </div>
    </header>
  )
}
