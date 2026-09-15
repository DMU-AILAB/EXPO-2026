import { useParams, useNavigate } from 'react-router-dom'
import { ChevronLeft, Activity, Thermometer, Clock, Wifi, Camera, Video } from 'lucide-react'
import StatusBadge from '../components/StatusBadge'
import { mockDevices, mockEvents } from '../data/mockData'

function CpuGauge({ value }: { value: number }) {
  const radius = 40
  const circumference = 2 * Math.PI * radius
  const offset = circumference - (value / 100) * circumference
  const color = value > 80 ? '#d3372c' : value > 60 ? '#d98a2b' : '#2c4be0'

  return (
    <div className="flex flex-col items-center">
      <svg width="100" height="100" viewBox="0 0 100 100">
        <circle cx="50" cy="50" r={radius} fill="none" stroke="#e2e8f0" strokeWidth="10" />
        <circle
          cx="50"
          cy="50"
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth="10"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          transform="rotate(-90 50 50)"
          style={{ transition: 'stroke-dashoffset 0.6s ease' }}
        />
        <text x="50" y="50" textAnchor="middle" dominantBaseline="central" className="font-black" fill={color} fontSize="18" fontWeight="800">
          {value}%
        </text>
        <text x="50" y="68" textAnchor="middle" fill="#94a3b8" fontSize="9" fontWeight="500">
          CPU
        </text>
      </svg>
    </div>
  )
}

export default function DeviceDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const device = mockDevices.find((d) => d.id === id)
  const events = mockEvents.filter((e) => e.deviceId === id)

  if (!device) {
    return (
      <div className="max-w-[1720px] mx-auto px-8 py-16 text-center">
        <p className="text-slate-500 text-lg">디바이스를 찾을 수 없습니다: {id}</p>
        <button onClick={() => navigate('/')} className="mt-4 text-[#2c4be0] font-semibold hover:underline">
          Overview로 돌아가기
        </button>
      </div>
    )
  }

  const memPct = Math.round((device.memory.used / device.memory.total) * 100)

  return (
    <div className="max-w-[1720px] mx-auto px-8 py-7">
      {/* Top bar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between mb-6 gap-4">
        <div className="flex items-center gap-4">
          <button
            onClick={() => navigate('/')}
            className="flex items-center gap-1.5 text-slate-500 hover:text-slate-900 text-sm font-medium transition"
          >
            <ChevronLeft className="w-4 h-4" />
            디바이스 목록
          </button>
          <span className="text-slate-300">|</span>
          <h1 className="text-xl font-extrabold tracking-tight text-slate-900 flex items-center gap-3">
            {device.name}
            <StatusBadge status={device.status} pulse />
          </h1>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => alert('재시작 명령 전송 (목 동작)')}
            className="glass-btn px-4 py-2 rounded-xl text-xs"
          >
            재시작
          </button>
          <button
            onClick={() => window.open(`http://${device.ip}:5000`, '_blank')}
            className="glass-btn-brand px-4 py-2 rounded-xl text-xs"
          >
            ROI 편집
          </button>
          <button
            onClick={() => alert('설정 패널 (목 동작)')}
            className="px-4 py-2 rounded-xl text-xs font-semibold bg-[#2c4be0] text-white hover:bg-[#1d35b5] shadow-md shadow-[#2c4be0]/25 transition"
          >
            설정
          </button>
        </div>
      </div>

      {/* Two column layout */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
        {/* Left column */}
        <div className="lg:col-span-2 flex flex-col gap-5">
          {/* System status card */}
          <div className="light-glass-panel p-5">
            <h2 className="text-sm font-bold text-slate-700 mb-4 pb-3 border-b border-slate-200/70">시스템 상태</h2>
            <div className="flex items-center justify-center mb-4">
              <CpuGauge value={device.cpu} />
            </div>
            <div className="space-y-3">
              {/* Memory */}
              <div>
                <div className="flex justify-between text-xs font-medium text-slate-600 mb-1">
                  <span className="flex items-center gap-1.5">
                    <Activity className="w-3.5 h-3.5 text-[#2c4be0]" />
                    메모리
                  </span>
                  <span className="font-mono font-bold text-slate-800">
                    {device.memory.used}GB / {device.memory.total}GB
                  </span>
                </div>
                <div className="w-full bg-slate-200 rounded-full h-2 overflow-hidden">
                  <div
                    className="bg-[#2c4be0] h-2 rounded-full transition-all"
                    style={{ width: `${memPct}%` }}
                  />
                </div>
              </div>

              {/* Temperature */}
              <div className="flex items-center justify-between text-xs font-medium">
                <span className="flex items-center gap-1.5 text-slate-600">
                  <Thermometer className="w-3.5 h-3.5 text-emerald-500" />
                  온도
                </span>
                <span
                  className={`font-mono font-bold ${device.temperature > 65 ? 'text-red-600' : device.temperature > 55 ? 'text-amber-600' : 'text-emerald-600'}`}
                >
                  {device.temperature > 0 ? `${device.temperature}°C` : '--°C'}
                </span>
              </div>

              {/* Uptime */}
              <div className="flex items-center justify-between text-xs font-medium">
                <span className="flex items-center gap-1.5 text-slate-600">
                  <Clock className="w-3.5 h-3.5 text-slate-400" />
                  가동 시간
                </span>
                <span className="font-mono font-bold text-slate-800">{device.uptime}</span>
              </div>

              {/* Latency */}
              <div className="flex items-center justify-between text-xs font-medium">
                <span className="flex items-center gap-1.5 text-slate-600">
                  <Wifi className="w-3.5 h-3.5 text-slate-400" />
                  네트워크 지연
                </span>
                <span className={`font-mono font-bold ${device.latency > 30 ? 'text-amber-600' : 'text-slate-800'}`}>
                  {device.latency > 0 ? `${device.latency}ms` : '--'}
                </span>
              </div>
            </div>
          </div>

          {/* Camera list */}
          <div className="light-glass-panel p-5">
            <h2 className="text-sm font-bold text-slate-700 mb-4 pb-3 border-b border-slate-200/70">
              카메라 목록 ({device.cameras.length}개)
            </h2>
            {device.cameras.length === 0 ? (
              <p className="text-xs text-slate-400 text-center py-4">카메라 없음 (오프라인)</p>
            ) : (
              <div className="space-y-3">
                {device.cameras.map((cam) => (
                  <div
                    key={cam.id}
                    className="p-3 rounded-xl bg-slate-50/80 border border-slate-200/80 hover:border-[#2c4be0]/30 hover:bg-white transition"
                  >
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-sm font-bold text-slate-800 flex items-center gap-2">
                        <Camera className="w-3.5 h-3.5 text-[#2c4be0]" />
                        카메라 {cam.id}
                      </span>
                      <span className="glass-badge px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-50/80 text-emerald-700 border border-emerald-200/70">
                        스트리밍 중
                      </span>
                    </div>
                    <p className="text-[11px] text-slate-500 mb-2">
                      {cam.resolution} · {cam.fps}fps
                    </p>
                    <div className="flex items-center justify-between text-[11px] text-slate-500">
                      <span>ROI {cam.roiCount}개 · 오늘 탐지 {cam.todayDetections}회</span>
                      <div className="flex gap-1.5">
                        <button
                          onClick={() => window.open(`http://${device.ip}:${cam.port}/stream.mjpg`, '_blank')}
                          className="glass-btn text-[10px] px-2 py-0.5 rounded-md"
                        >
                          스트림
                        </button>
                        <button
                          onClick={() => window.open(`http://${device.ip}:5000`, '_blank')}
                          className="glass-btn-brand text-[10px] px-2 py-0.5 rounded-md"
                        >
                          ROI 편집
                        </button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Right column — Events */}
        <div className="lg:col-span-3">
          <div className="light-glass-panel p-5 h-full">
            <div className="flex items-center justify-between mb-4 pb-3 border-b border-slate-200/70">
              <h2 className="text-sm font-bold text-slate-700">최근 감지 이벤트</h2>
              <div className="flex items-center gap-2 text-xs text-emerald-700 font-semibold">
                <span className="relative flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-500 opacity-75" />
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-600" />
                </span>
                실시간
              </div>
            </div>

            {events.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-slate-400">
                <Video className="w-8 h-8 mb-2" />
                <p className="text-sm">감지 이벤트 없음</p>
              </div>
            ) : (
              <>
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-slate-200/70">
                      <th className="text-left pb-2 font-semibold text-slate-500 pr-4">시각</th>
                      <th className="text-left pb-2 font-semibold text-slate-500 pr-4">카메라</th>
                      <th className="text-left pb-2 font-semibold text-slate-500 pr-4">ROI</th>
                      <th className="text-right pb-2 font-semibold text-slate-500">신뢰도</th>
                    </tr>
                  </thead>
                  <tbody>
                    {events.map((ev, idx) => (
                      <tr
                        key={idx}
                        className={`border-b border-slate-100 ${idx === 0 ? 'bg-[#2c4be0]/5 border-l-2 border-l-[#2c4be0]' : 'hover:bg-slate-50'} transition`}
                      >
                        <td className="py-2.5 pr-4 font-mono text-slate-700 font-semibold">{ev.time}</td>
                        <td className="py-2.5 pr-4 text-slate-600">{ev.camera}</td>
                        <td className="py-2.5 pr-4 text-slate-600">{ev.roi}</td>
                        <td className="py-2.5 text-right">
                          <span className="glass-badge px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-50/80 text-emerald-700 border border-emerald-200/70">
                            {ev.confidence.toFixed(2)}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div className="mt-4 pt-3 border-t border-slate-200/70 flex items-center justify-between text-[11px]">
                  <span className="text-slate-500 font-medium">
                    총 오늘 <strong className="text-slate-800">{device.todayDetections}건</strong>
                  </span>
                  <button className="text-[#2c4be0] font-semibold hover:underline">더 보기 →</button>
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
