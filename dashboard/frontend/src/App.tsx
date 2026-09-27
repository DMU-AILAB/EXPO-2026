import { useEffect, useState } from 'react'
import { Routes, Route, useLocation } from 'react-router-dom'
import Header from './components/Header'
import GlowBlobs from './components/GlowBlobs'
import Overview from './pages/Overview'
import DeviceList from './pages/DeviceList'
import DeviceDetail from './pages/DeviceDetail'
import LiveStreams from './pages/LiveStreams'
import Stats from './pages/Stats'
import PiScan from './pages/PiScan'
import Login from './pages/Login'
import { AuthProvider, RequireAuth, useAuth } from './auth'
import { useEventStream } from './hooks/useEventStream'

function Shell() {
  const location = useLocation()
  const { user } = useAuth()
  const [alertMessage, setAlertMessage] = useState<string | null>(null)
  const [alertKey, setAlertKey] = useState(0)

  useEventStream({ onMessage: (message) => {
    if (message.type === 'alert') {
      setAlertMessage(message.data.message)
      setAlertKey((current) => current + 1)
    }
  } })

  useEffect(() => {
    if (!alertMessage) return
    const timer = window.setTimeout(() => setAlertMessage(null), 8000)
    return () => window.clearTimeout(timer)
  }, [alertKey, alertMessage])
  // 로그인 전에는 헤더를 띄우지 않는다 — 헤더가 인증이 필요한 API를 폴링하므로
  // 토큰 없이 렌더하면 401이 쏟아진다.
  const bare = location.pathname === '/login' || !user

  return (
    <div className="min-h-screen bg-[#f4f6fc] text-slate-900 relative overflow-x-hidden antialiased selection:bg-[#2c4be0]/20 selection:text-[#2c4be0]">
      <GlowBlobs />
      {!bare && <Header />}
      {!bare && alertMessage && (
        <div
          key={alertKey}
          role="alert"
          className="fixed right-6 top-[84px] z-[60] max-w-sm rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm font-semibold text-amber-900 shadow-lg"
        >
          {alertMessage}
        </div>
      )}
      <main className={bare ? 'relative z-10 pt-24' : 'pt-[68px] relative z-10'}>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/" element={<RequireAuth><Overview /></RequireAuth>} />
          <Route path="/devices" element={<RequireAuth><DeviceList /></RequireAuth>} />
          <Route path="/devices/:id" element={<RequireAuth><DeviceDetail /></RequireAuth>} />
          <Route path="/streams" element={<RequireAuth><LiveStreams /></RequireAuth>} />
          <Route path="/stats" element={<RequireAuth><Stats /></RequireAuth>} />
          <Route path="/scan" element={<RequireAuth><PiScan /></RequireAuth>} />
        </Routes>
      </main>
    </div>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <Shell />
    </AuthProvider>
  )
}
