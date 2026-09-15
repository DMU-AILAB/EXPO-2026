import { Routes, Route } from 'react-router-dom'
import Header from './components/Header'
import GlowBlobs from './components/GlowBlobs'
import Overview from './pages/Overview'
import DeviceDetail from './pages/DeviceDetail'
import LiveStreams from './pages/LiveStreams'
import PiScan from './pages/PiScan'

export default function App() {
  return (
    <div className="min-h-screen bg-[#f4f6fc] text-slate-900 relative overflow-x-hidden antialiased selection:bg-[#2c4be0]/20 selection:text-[#2c4be0]">
      <GlowBlobs />
      <Header />
      <main className="pt-[68px] relative z-10">
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/devices/:id" element={<DeviceDetail />} />
          <Route path="/streams" element={<LiveStreams />} />
          <Route path="/scan" element={<PiScan />} />
        </Routes>
      </main>
    </div>
  )
}
