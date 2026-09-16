import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  ChevronLeft, Activity, Thermometer, Clock, Wifi, Camera, Video,
  MapPin, Power, SlidersHorizontal, Plus, Pencil, RotateCcw, CheckCircle, CalendarClock, Trash2,
} from 'lucide-react'
import StatusBadge from '../components/StatusBadge'
import { mockDevices, mockEvents, MOCK_AUDIO_FILES } from '../data/mockData'
import type { Device, DetectionEvent, Roi } from '../types'

const STREAM_IMAGES = [
  '/streams/entrance.jpg',
  '/streams/hall.jpg',
  '/streams/street.jpg',
  '/streams/campus.jpg',
  '/streams/night.jpg',
  '/streams/elevator.jpg',
]

// ─── CPU Gauge ────────────────────────────────────────────────────────────────
function CpuGauge({ value }: { value: number }) {
  const r = 40, circ = 2 * Math.PI * r
  const color = value > 80 ? '#d3372c' : value > 60 ? '#d98a2b' : '#2c4be0'
  return (
    <div className="flex flex-col items-center">
      <svg width="100" height="100" viewBox="0 0 100 100">
        <circle cx="50" cy="50" r={r} fill="none" stroke="#e2e8f0" strokeWidth="10" />
        <circle cx="50" cy="50" r={r} fill="none" stroke={color} strokeWidth="10" strokeLinecap="round"
          strokeDasharray={circ} strokeDashoffset={circ - (value / 100) * circ}
          transform="rotate(-90 50 50)" style={{ transition: 'stroke-dashoffset 0.6s ease' }} />
        <text x="50" y="50" textAnchor="middle" dominantBaseline="central" fill={color} fontSize="18" fontWeight="800">{value}%</text>
        <text x="50" y="68" textAnchor="middle" fill="#94a3b8" fontSize="9" fontWeight="500">CPU</text>
      </svg>
    </div>
  )
}

// ─── Overview Tab ─────────────────────────────────────────────────────────────
function OverviewTab({ device, events }: { device: Device; events: DetectionEvent[] }) {
  const memPct = Math.round((device.memory.used / device.memory.total) * 100)
  return (
    <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
      <div className="lg:col-span-2 flex flex-col gap-5">
        <div className="light-glass-panel p-5">
          <h2 className="text-sm font-bold text-slate-700 mb-4 pb-3 border-b border-slate-200/70">시스템 상태</h2>
          <div className="flex items-center justify-center mb-4"><CpuGauge value={device.cpu} /></div>
          <div className="space-y-3">
            <div>
              <div className="flex justify-between text-xs font-medium text-slate-600 mb-1">
                <span className="flex items-center gap-1.5"><Activity className="w-3.5 h-3.5 text-[#2c4be0]" />메모리</span>
                <span className="font-mono font-bold text-slate-800">{device.memory.used}GB / {device.memory.total}GB</span>
              </div>
              <div className="w-full bg-slate-200 rounded-full h-2 overflow-hidden">
                <div className="bg-[#2c4be0] h-2 rounded-full transition-all" style={{ width: `${memPct}%` }} />
              </div>
            </div>
            <div className="flex items-center justify-between text-xs font-medium">
              <span className="flex items-center gap-1.5 text-slate-600"><Thermometer className="w-3.5 h-3.5 text-emerald-500" />온도</span>
              <span className={`font-mono font-bold ${device.temperature > 65 ? 'text-red-600' : device.temperature > 55 ? 'text-amber-600' : 'text-emerald-600'}`}>
                {device.temperature > 0 ? `${device.temperature}°C` : '--°C'}
              </span>
            </div>
            <div className="flex items-center justify-between text-xs font-medium">
              <span className="flex items-center gap-1.5 text-slate-600"><Clock className="w-3.5 h-3.5 text-slate-400" />가동 시간</span>
              <span className="font-mono font-bold text-slate-800">{device.uptime}</span>
            </div>
            <div className="flex items-center justify-between text-xs font-medium">
              <span className="flex items-center gap-1.5 text-slate-600"><Wifi className="w-3.5 h-3.5 text-slate-400" />네트워크 지연</span>
              <span className={`font-mono font-bold ${device.latency > 30 ? 'text-amber-600' : 'text-slate-800'}`}>
                {device.latency > 0 ? `${device.latency}ms` : '--'}
              </span>
            </div>
          </div>
        </div>
        <div className="light-glass-panel p-5">
          <h2 className="text-sm font-bold text-slate-700 mb-4 pb-3 border-b border-slate-200/70">카메라 목록 ({device.cameras.length}개)</h2>
          {device.cameras.length === 0 ? (
            <p className="text-xs text-slate-400 text-center py-4">카메라 없음 (오프라인)</p>
          ) : (
            <div className="space-y-3">
              {device.cameras.map((cam) => (
                <div key={cam.id} className="p-3 rounded-xl bg-slate-50/80 border border-slate-200/80 hover:border-[#2c4be0]/30 hover:bg-white transition">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-sm font-bold text-slate-800 flex items-center gap-2">
                      <Camera className="w-3.5 h-3.5 text-[#2c4be0]" />카메라 {cam.id}
                    </span>
                    <span className="glass-badge px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-50/80 text-emerald-700 border border-emerald-200/70">스트리밍 중</span>
                  </div>
                  <p className="text-[11px] text-slate-500 mb-2">{cam.resolution} · {cam.fps}fps</p>
                  <div className="flex items-center justify-between text-[11px] text-slate-500">
                    <span>ROI {cam.roiCount}개 · 오늘 탐지 {cam.todayDetections}회</span>
                    <button onClick={() => window.open(`http://${device.ip}:${cam.port}/stream.mjpg`, '_blank')}
                      className="glass-btn text-[10px] px-2 py-0.5 rounded-md">스트림</button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
      <div className="lg:col-span-3">
        <div className="light-glass-panel p-5 h-full">
          <div className="flex items-center justify-between mb-4 pb-3 border-b border-slate-200/70">
            <h2 className="text-sm font-bold text-slate-700">최근 감지 이벤트</h2>
            <div className="flex items-center gap-2 text-xs text-emerald-700 font-semibold">
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-500 opacity-75" />
                <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-600" />
              </span>실시간
            </div>
          </div>
          {events.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 text-slate-400">
              <Video className="w-8 h-8 mb-2" /><p className="text-sm">감지 이벤트 없음</p>
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
                    <tr key={idx} className={`border-b border-slate-100 ${idx === 0 ? 'bg-[#2c4be0]/5 border-l-2 border-l-[#2c4be0]' : 'hover:bg-slate-50'} transition`}>
                      <td className="py-2.5 pr-4 font-mono text-slate-700 font-semibold">{ev.time}</td>
                      <td className="py-2.5 pr-4 text-slate-600">{ev.camera}</td>
                      <td className="py-2.5 pr-4 text-slate-600">{ev.roi}</td>
                      <td className="py-2.5 text-right">
                        <span className="glass-badge px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-50/80 text-emerald-700 border border-emerald-200/70">{ev.confidence.toFixed(2)}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="mt-4 pt-3 border-t border-slate-200/70 flex items-center justify-between text-[11px]">
                <span className="text-slate-500 font-medium">총 오늘 <strong className="text-slate-800">{device.todayDetections}건</strong></span>
                <button className="text-[#2c4be0] font-semibold hover:underline">더 보기 →</button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ─── ROI Tab ──────────────────────────────────────────────────────────────────
function RoiTab({ device }: { device: Device }) {
  const [selectedCam, setSelectedCam] = useState(0)
  const [rois, setRois] = useState<Roi[]>(device.rois ?? [])
  const [editingId, setEditingId] = useState<number | 'new' | null>(null)
  const [form, setForm] = useState<Partial<Roi>>({})

  const openEdit = (roi: Roi) => { setEditingId(roi.id); setForm({ ...roi }) }
  const openNew = () => { setEditingId('new'); setForm({ name: '', zoneType: 'trigger', priority: 1, announcementText: '', audioFile: '', isActive: true }) }
  const closeForm = () => { setEditingId(null); setForm({}) }

  const saveForm = () => {
    if (editingId === 'new') {
      const newId = Math.max(0, ...rois.map(r => r.id)) + 1
      setRois(prev => [...prev, { id: newId, name: form.name ?? '', zoneType: form.zoneType ?? 'trigger', priority: form.priority ?? 1, announcementText: form.announcementText ?? '', audioFile: form.audioFile ?? '', isActive: form.isActive ?? true }])
    } else {
      setRois(prev => prev.map(r => r.id === editingId ? { ...r, ...form } : r))
    }
    closeForm()
  }

  const toggleActive = (id: number) => setRois(prev => prev.map(r => r.id === id ? { ...r, isActive: !r.isActive } : r))

  const inputCls = 'w-full border border-slate-200 rounded-xl px-3 py-1.5 text-sm bg-white focus:outline-none focus:border-[#2c4be0] focus:ring-2 focus:ring-[#2c4be0]/10 transition'
  const labelCls = 'text-xs font-bold text-slate-600 mb-1 block'

  const activeCam = device.cameras[selectedCam]
  const imgSrc = activeCam
    ? STREAM_IMAGES[(activeCam.imageIndex ?? activeCam.id) % STREAM_IMAGES.length]
    : null

  return (
    <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
      {/* 좌: 카메라 스트림 */}
      <div className="lg:col-span-2">
        <div className="glass-panel p-4">
          <div className="flex items-center justify-between mb-3 pb-2.5 border-b border-slate-200/70">
            <h2 className="text-sm font-bold text-slate-700">카메라 스트림</h2>
            {device.cameras.length > 1 && (
              <div className="flex gap-1">
                {device.cameras.map((cam, idx) => (
                  <button key={cam.id} onClick={() => setSelectedCam(idx)}
                    className={`px-2.5 py-1 rounded-lg text-[11px] font-semibold transition ${selectedCam === idx ? 'bg-[#2c4be0] text-white' : 'bg-slate-100/80 text-slate-500 hover:bg-slate-200'}`}>
                    CAM {cam.id}
                  </button>
                ))}
              </div>
            )}
          </div>

          {device.status === 'offline' || !activeCam ? (
            <div className="aspect-video bg-slate-900/90 rounded-xl flex flex-col items-center justify-center border border-slate-700/50">
              <Camera className="w-8 h-8 text-slate-600 mb-2" />
              <p className="text-xs font-semibold text-slate-500">오프라인 — 스트림 없음</p>
            </div>
          ) : (
            <div className="relative aspect-video rounded-xl overflow-hidden border border-emerald-500/40 shadow-[0_0_14px_rgba(16,185,129,0.12)]">
              <img src={imgSrc!} alt="" className="w-full h-full object-cover" draggable={false} />
              {/* 상단 HUD */}
              <div className="absolute top-0 left-0 right-0 px-3 py-2 flex items-center justify-between"
                style={{ background: 'linear-gradient(to bottom, rgba(0,0,0,0.65), transparent)' }}>
                <span className="text-[10.5px] font-bold text-white">{device.name} — CAM {activeCam.id}</span>
                <div className="flex items-center gap-1.5">
                  <span className="relative flex h-1.5 w-1.5">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75" />
                    <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-red-500" />
                  </span>
                  <span className="text-[10px] font-bold text-white/90 tracking-wider">LIVE</span>
                </div>
              </div>
              {/* 하단 정보 */}
              <div className="absolute bottom-0 left-0 right-0 px-3 py-2"
                style={{ background: 'linear-gradient(to top, rgba(0,0,0,0.60), transparent)' }}>
                <div className="flex items-center justify-between text-[10px] text-white/80">
                  <span>{activeCam.resolution} · {activeCam.fps}fps</span>
                  <span>ROI {activeCam.roiCount}개</span>
                </div>
              </div>
            </div>
          )}

          {activeCam && (
            <div className="mt-3 flex items-center gap-2 text-[11px] text-slate-500">
              <span className="font-mono text-slate-700 font-semibold">{device.ip}:{activeCam.port}</span>
              <span>·</span>
              <span>오늘 탐지 <strong className="text-slate-800">{activeCam.todayDetections}건</strong></span>
            </div>
          )}
        </div>
      </div>

      {/* 우: ROI 목록 */}
      <div className="lg:col-span-3 glass-panel p-5">
        <div className="flex items-center justify-between mb-4 pb-3 border-b border-slate-200/70">
          <h2 className="text-sm font-bold text-slate-700">ROI 구역 관리</h2>
          <button onClick={openNew} className="glass-btn-brand px-3 py-1.5 rounded-xl text-xs gap-1.5 flex items-center">
            <Plus className="w-3.5 h-3.5" />새 구역 추가
          </button>
        </div>

        {rois.length === 0 ? (
          <div className="py-12 text-center text-slate-400 text-sm">ROI 구역이 없습니다. 새 구역을 추가하세요.</div>
        ) : (
          <div className="space-y-2">
            {rois.map((roi) => (
              <div key={roi.id} className={`p-3 rounded-xl border transition ${roi.isActive ? 'bg-slate-50/80 border-slate-200/80' : 'bg-slate-100/40 border-slate-200/40 opacity-60'}`}>
                <div className="flex items-center gap-2 flex-wrap">
                  <span className={`w-2 h-2 rounded-full flex-shrink-0 ${roi.isActive ? 'bg-emerald-500' : 'bg-slate-400'}`} />
                  <span className="text-sm font-semibold text-slate-800">{roi.name}</span>
                  <span className={`text-[10px] px-2 py-0.5 rounded-full font-bold border ${roi.zoneType === 'trigger' ? 'bg-[#2c4be0]/8 text-[#2c4be0] border-[#2c4be0]/25' : 'bg-slate-100 text-slate-500 border-slate-200'}`}>
                    {roi.zoneType === 'trigger' ? '안내 구역' : '제외 구역'}
                  </span>
                  <span className="text-[10px] text-slate-400">우선순위 {roi.priority}</span>
                  {roi.audioFile && <span className="text-[10px] text-slate-400 italic">{roi.audioFile}</span>}
                  <div className="flex gap-1.5 ml-auto">
                    <button onClick={() => toggleActive(roi.id)}
                      className={`text-[10px] px-2 py-0.5 rounded-full border font-semibold transition ${roi.isActive ? 'bg-emerald-50 text-emerald-700 border-emerald-200/70' : 'bg-slate-100 text-slate-400 border-slate-200'}`}>
                      {roi.isActive ? '활성' : '비활성'}
                    </button>
                    <button onClick={() => openEdit(roi)} className="glass-btn p-1.5 rounded-lg">
                      <Pencil className="w-3 h-3" />
                    </button>
                  </div>
                </div>
                {roi.announcementText && <p className="text-xs text-slate-500 mt-1.5 ml-4">"{roi.announcementText}"</p>}

                {editingId === roi.id && (
                  <div className="mt-3 pt-3 border-t border-slate-200/70 grid grid-cols-2 gap-3">
                    <div className="col-span-2"><label className={labelCls}>이름</label><input className={inputCls} value={form.name ?? ''} onChange={e => setForm(p => ({ ...p, name: e.target.value }))} /></div>
                    <div>
                      <label className={labelCls}>구역 유형</label>
                      <div className="flex gap-1.5">
                        {(['trigger', 'exclude'] as const).map(t => (
                          <button key={t} onClick={() => setForm(p => ({ ...p, zoneType: t }))}
                            className={`flex-1 py-1.5 rounded-lg text-xs font-semibold transition ${form.zoneType === t ? (t === 'trigger' ? 'bg-[#2c4be0] text-white' : 'bg-slate-700 text-white') : 'bg-slate-100 text-slate-500 hover:bg-slate-200'}`}>
                            {t === 'trigger' ? '안내 구역' : '제외 구역'}
                          </button>
                        ))}
                      </div>
                    </div>
                    <div><label className={labelCls}>우선순위</label><input type="number" min={0} max={10} className={inputCls} value={form.priority ?? 1} onChange={e => setForm(p => ({ ...p, priority: Number(e.target.value) }))} /></div>
                    <div className="col-span-2"><label className={labelCls}>오디오 파일</label>
                      <select className={inputCls} value={form.audioFile ?? ''} onChange={e => setForm(p => ({ ...p, audioFile: e.target.value }))}>
                        <option value="">— 없음 —</option>
                        {MOCK_AUDIO_FILES.map(f => <option key={f} value={f}>{f}</option>)}
                      </select>
                    </div>
                    <div className="col-span-2"><label className={labelCls}>안내 텍스트</label><textarea rows={2} className={`${inputCls} resize-none`} value={form.announcementText ?? ''} onChange={e => setForm(p => ({ ...p, announcementText: e.target.value }))} /></div>
                    <div className="col-span-2 flex items-center gap-2 text-xs">
                      <input type="checkbox" id={`active-${roi.id}`} checked={form.isActive ?? true} onChange={e => setForm(p => ({ ...p, isActive: e.target.checked }))} className="accent-[#2c4be0]" />
                      <label htmlFor={`active-${roi.id}`} className="font-medium text-slate-700 cursor-pointer">활성화</label>
                    </div>
                    <div className="col-span-2 flex justify-end gap-2">
                      <button onClick={closeForm} className="glass-btn px-4 py-1.5 rounded-xl text-xs">취소</button>
                      <button onClick={saveForm} className="bg-[#2c4be0] text-white px-4 py-1.5 rounded-xl text-xs font-semibold shadow-md shadow-[#2c4be0]/25 hover:bg-[#1d35b5] transition">저장</button>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {editingId === 'new' && (
          <div className="mt-4 glass-panel-subtle p-4">
            <p className="text-xs font-bold text-slate-700 mb-3">새 ROI 구역</p>
            <div className="grid grid-cols-2 gap-3">
              <div className="col-span-2"><label className={labelCls}>이름</label><input className={inputCls} value={form.name ?? ''} onChange={e => setForm(p => ({ ...p, name: e.target.value }))} placeholder="구역 이름" /></div>
              <div>
                <label className={labelCls}>구역 유형</label>
                <div className="flex gap-1.5">
                  {(['trigger', 'exclude'] as const).map(t => (
                    <button key={t} onClick={() => setForm(p => ({ ...p, zoneType: t }))}
                      className={`flex-1 py-1.5 rounded-lg text-xs font-semibold transition ${form.zoneType === t ? (t === 'trigger' ? 'bg-[#2c4be0] text-white' : 'bg-slate-700 text-white') : 'bg-slate-100 text-slate-500 hover:bg-slate-200'}`}>
                      {t === 'trigger' ? '안내 구역' : '제외 구역'}
                    </button>
                  ))}
                </div>
              </div>
              <div><label className={labelCls}>우선순위</label><input type="number" min={1} max={10} className={inputCls} value={form.priority ?? 1} onChange={e => setForm(p => ({ ...p, priority: Number(e.target.value) }))} /></div>
              <div className="col-span-2"><label className={labelCls}>오디오 파일</label>
                <select className={inputCls} value={form.audioFile ?? ''} onChange={e => setForm(p => ({ ...p, audioFile: e.target.value }))}>
                  <option value="">— 없음 —</option>
                  {MOCK_AUDIO_FILES.map(f => <option key={f} value={f}>{f}</option>)}
                </select>
              </div>
              <div className="col-span-2"><label className={labelCls}>안내 텍스트</label><textarea rows={2} className={`${inputCls} resize-none`} value={form.announcementText ?? ''} onChange={e => setForm(p => ({ ...p, announcementText: e.target.value }))} /></div>
              <div className="col-span-2 flex justify-end gap-2">
                <button onClick={closeForm} className="glass-btn px-4 py-1.5 rounded-xl text-xs">취소</button>
                <button onClick={saveForm} className="bg-[#2c4be0] text-white px-4 py-1.5 rounded-xl text-xs font-semibold shadow-md shadow-[#2c4be0]/25 hover:bg-[#1d35b5] transition">추가</button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ─── Settings Tab ─────────────────────────────────────────────────────────────
type CamForm = { resolution: string; fps: number; modelVariant: string; rotation: number; requirePerson: boolean }

type ScheduleEntry = { id: number; days: number[]; hour: number; enabled: boolean }
const DAY_LABELS = ['일', '월', '화', '수', '목', '금', '토']

function SettingsTab({ device }: { device: Device }) {
  const [selectedCam, setSelectedCam] = useState(0)
  const [confirmAction, setConfirmAction] = useState<'restart' | 'reboot' | null>(null)
  const [actionDone, setActionDone] = useState(false)
  const [saved, setSaved] = useState(false)

  // 예약 재부팅
  const [schedules, setSchedules] = useState<ScheduleEntry[]>([
    { id: 1, days: [1], hour: 3, enabled: true },
  ])
  const [newDays, setNewDays] = useState<number[]>([])
  const [newHour, setNewHour] = useState(3)
  const [scheduleSaved, setScheduleSaved] = useState(false)

  const toggleDay = (d: number) =>
    setNewDays(prev => prev.includes(d) ? prev.filter(x => x !== d) : [...prev, d].sort())

  const addSchedule = () => {
    if (newDays.length === 0) return
    const id = Math.max(0, ...schedules.map(s => s.id)) + 1
    setSchedules(prev => [...prev, { id, days: newDays, hour: newHour, enabled: true }])
    setNewDays([]); setNewHour(3)
    setScheduleSaved(true); setTimeout(() => setScheduleSaved(false), 2000)
  }

  const removeSchedule = (id: number) => setSchedules(prev => prev.filter(s => s.id !== id))
  const toggleSchedule = (id: number) =>
    setSchedules(prev => prev.map(s => s.id === id ? { ...s, enabled: !s.enabled } : s))

  const initForms = (): Record<number, CamForm> =>
    Object.fromEntries(device.cameras.map(c => [c.id, { resolution: c.resolution, fps: c.fps, modelVariant: 'v5b_ft320', rotation: 0, requirePerson: false }]))
  const [camForms, setCamForms] = useState<Record<number, CamForm>>(initForms)

  const cam = device.cameras[selectedCam]
  const form = cam ? (camForms[cam.id] ?? { resolution: '640×480', fps: 15, modelVariant: 'v5b_ft320', rotation: 0, requirePerson: false }) : null

  const update = (field: keyof CamForm, val: string | number | boolean) => {
    if (!cam) return
    setCamForms(p => ({ ...p, [cam.id]: { ...p[cam.id], [field]: val } }))
  }

  const handleConfirm = (_action: 'restart' | 'reboot') => {
    setConfirmAction(null); setActionDone(true)
    setTimeout(() => setActionDone(false), 3000)
  }

  const selectCls = 'w-full border border-slate-200 rounded-xl px-3 py-1.5 text-sm bg-white focus:outline-none focus:border-[#2c4be0] focus:ring-2 focus:ring-[#2c4be0]/10 transition'
  const labelCls = 'text-xs font-bold text-slate-600 mb-1 block'

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
      {/* Camera settings */}
      <div className="lg:col-span-2 glass-panel p-5 self-start">
        <h2 className="text-sm font-bold text-slate-700 mb-4 pb-3 border-b border-slate-200/70">카메라 설정</h2>
        {device.cameras.length === 0 ? (
          <p className="text-xs text-slate-400 text-center py-8">오프라인 상태 — 카메라 없음</p>
        ) : (
          <>
            {device.cameras.length > 1 && (
              <div className="flex gap-1 mb-5">
                {device.cameras.map((c, idx) => (
                  <button key={c.id} onClick={() => setSelectedCam(idx)}
                    className={`px-3 py-1 rounded-lg text-xs font-semibold transition ${selectedCam === idx ? 'bg-[#2c4be0] text-white' : 'bg-slate-100/80 text-slate-500 hover:bg-slate-200'}`}>
                    CAM {c.id}
                  </button>
                ))}
              </div>
            )}
            {form && cam && (
              <div className="grid grid-cols-2 gap-4">
                <div><label className={labelCls}>포트 (읽기 전용)</label>
                  <input readOnly value={cam.port} className={`${selectCls} bg-slate-100 cursor-not-allowed font-mono`} /></div>
                <div><label className={labelCls}>해상도</label>
                  <select value={form.resolution} onChange={e => update('resolution', e.target.value)} className={selectCls}>
                    {['640×480', '1280×720', '1920×1080'].map(r => <option key={r}>{r}</option>)}
                  </select></div>
                <div><label className={labelCls}>FPS</label>
                  <select value={form.fps} onChange={e => update('fps', Number(e.target.value))} className={selectCls}>
                    {[10, 15, 20, 25, 30].map(f => <option key={f} value={f}>{f}fps</option>)}
                  </select></div>
                <div><label className={labelCls}>모델 변형</label>
                  <select value={form.modelVariant} onChange={e => update('modelVariant', e.target.value)} className={selectCls}>
                    {['v1-2', 'v2', 'v3_320', 'v4_320', 'v5b_ft320'].map(m => <option key={m} value={m}>{m}</option>)}
                  </select></div>
                <div><label className={labelCls}>회전</label>
                  <select value={form.rotation} onChange={e => update('rotation', Number(e.target.value))} className={selectCls}>
                    {[0, 90, 180, 270].map(r => <option key={r} value={r}>{r}°</option>)}
                  </select></div>
                <div className="col-span-2 flex items-center gap-2 text-xs pt-1">
                  <input type="checkbox" id="reqPerson" checked={form.requirePerson} onChange={e => update('requirePerson', e.target.checked)} className="accent-[#2c4be0] w-4 h-4" />
                  <label htmlFor="reqPerson" className="font-medium text-slate-700 cursor-pointer">보행자 동반 필수 (사람 없는 지팡이 탐지 무시)</label>
                </div>
                <div className="col-span-2 flex items-center justify-end gap-3 pt-3 border-t border-slate-200/70">
                  {saved && (
                    <span className="flex items-center gap-1.5 text-xs font-semibold text-emerald-600 mr-auto">
                      <CheckCircle className="w-3.5 h-3.5" />저장되었습니다
                    </span>
                  )}
                  <button onClick={() => { setSaved(true); setTimeout(() => setSaved(false), 2000) }}
                    className="bg-[#2c4be0] text-white px-5 py-2 rounded-xl text-xs font-semibold shadow-md shadow-[#2c4be0]/25 hover:bg-[#1d35b5] transition">
                    저장
                  </button>
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {/* Restart / Power + 예약 재부팅 */}
      <div className="flex flex-col gap-5">
      <div className="glass-panel p-5">
        <h2 className="text-sm font-bold text-slate-700 mb-4 pb-3 border-b border-slate-200/70">재시작 / 전원</h2>
        <div className="flex items-center gap-3 p-3 rounded-xl bg-slate-50/80 border border-slate-200/60 mb-4">
          <Clock className="w-4 h-4 text-[#2c4be0] flex-shrink-0" />
          <span className="text-xs text-slate-600">가동 시간</span>
          <span className="ml-auto font-mono font-bold text-slate-900 text-xs">{device.uptime}</span>
        </div>

        {/* Service restart */}
        <div className="p-4 rounded-2xl bg-amber-50/60 border border-amber-200/60 mb-3">
          <div className="flex items-start gap-3 mb-2">
            <RotateCcw className="w-4 h-4 text-amber-600 mt-0.5 flex-shrink-0" />
            <div>
              <p className="text-sm font-bold text-amber-900">서비스 재시작</p>
              <p className="text-xs text-amber-700 mt-0.5">visionguide-device 서비스만 재시작합니다 (~5–10초)</p>
            </div>
          </div>
          {confirmAction === 'restart' ? (
            <div className="mt-2">
              <p className="text-xs font-semibold text-amber-800 mb-2">정말 재시작하시겠습니까?</p>
              <div className="flex gap-2">
                <button onClick={() => handleConfirm('restart')} className="bg-amber-500 hover:bg-amber-600 text-white px-3 py-1.5 rounded-lg text-xs font-semibold shadow-sm shadow-amber-500/30 transition">확인</button>
                <button onClick={() => setConfirmAction(null)} className="glass-btn px-3 py-1.5 rounded-lg text-xs">취소</button>
              </div>
            </div>
          ) : (
            <button onClick={() => setConfirmAction('restart')} className="w-full flex items-center justify-center gap-1.5 glass-btn py-1.5 rounded-xl text-xs text-amber-700 border-amber-200/70 hover:border-amber-300 mt-1">
              <RotateCcw className="w-3.5 h-3.5" />재시작
            </button>
          )}
        </div>

        {/* Reboot */}
        <div className="p-4 rounded-2xl bg-red-50/60 border border-red-200/60">
          <div className="flex items-start gap-3 mb-2">
            <Power className="w-4 h-4 text-red-600 mt-0.5 flex-shrink-0" />
            <div>
              <p className="text-sm font-bold text-red-900">장치 재부팅</p>
              <p className="text-xs text-red-700 mt-0.5">라즈베리파이를 완전히 재부팅합니다 (~30–60초)</p>
            </div>
          </div>
          {confirmAction === 'reboot' ? (
            <div className="mt-2">
              <p className="text-xs font-semibold text-red-800 mb-2">정말 재부팅하시겠습니까?</p>
              <div className="flex gap-2">
                <button onClick={() => handleConfirm('reboot')} className="bg-red-500 hover:bg-red-600 text-white px-3 py-1.5 rounded-lg text-xs font-semibold shadow-sm shadow-red-500/30 transition">확인</button>
                <button onClick={() => setConfirmAction(null)} className="glass-btn px-3 py-1.5 rounded-lg text-xs">취소</button>
              </div>
            </div>
          ) : (
            <button onClick={() => setConfirmAction('reboot')} className="w-full flex items-center justify-center gap-1.5 glass-btn py-1.5 rounded-xl text-xs text-red-600 border-red-200/70 hover:border-red-300 mt-1">
              <Power className="w-3.5 h-3.5" />재부팅
            </button>
          )}
        </div>

        {actionDone && (
          <div className="mt-3 p-3 rounded-xl bg-emerald-50/80 border border-emerald-200/60 flex items-center gap-2 text-xs font-semibold text-emerald-700">
            <CheckCircle className="w-3.5 h-3.5" />명령이 전송되었습니다 (목 동작)
          </div>
        )}
      </div>

      {/* 예약 재부팅 */}
      <div className="glass-panel p-5">
        <div className="flex items-center gap-2 mb-4 pb-3 border-b border-slate-200/70">
          <CalendarClock className="w-4 h-4 text-[#2c4be0]" />
          <h2 className="text-sm font-bold text-slate-700">예약 재부팅</h2>
        </div>

        {/* 기존 스케줄 목록 */}
        <div className="space-y-2 mb-4">
          {schedules.length === 0 && (
            <p className="text-xs text-slate-400 text-center py-3">등록된 예약 없음</p>
          )}
          {schedules.map(s => (
            <div key={s.id} className={`flex items-center gap-2 px-3 py-2 rounded-xl border text-xs transition ${s.enabled ? 'bg-slate-50/80 border-slate-200/80' : 'bg-slate-100/40 border-slate-200/40 opacity-50'}`}>
              <span className="font-mono font-bold text-slate-800 w-10 text-center">{String(s.hour).padStart(2, '0')}:00</span>
              <div className="flex gap-0.5 flex-1">
                {DAY_LABELS.map((label, idx) => (
                  <span key={idx} className={`w-5 h-5 flex items-center justify-center rounded text-[10px] font-bold ${s.days.includes(idx) ? 'bg-[#2c4be0] text-white' : 'text-slate-300'}`}>{label}</span>
                ))}
              </div>
              <button onClick={() => toggleSchedule(s.id)}
                className={`text-[10px] px-2 py-0.5 rounded-full border font-semibold transition ${s.enabled ? 'bg-emerald-50 text-emerald-700 border-emerald-200/70' : 'bg-slate-100 text-slate-400 border-slate-200'}`}>
                {s.enabled ? '활성' : '비활성'}
              </button>
              <button onClick={() => removeSchedule(s.id)} className="glass-btn p-1 rounded-lg text-red-400 hover:text-red-600 hover:border-red-200">
                <Trash2 className="w-3 h-3" />
              </button>
            </div>
          ))}
        </div>

        {/* 새 스케줄 추가 */}
        <div className="pt-3 border-t border-slate-200/70">
          <p className="text-[11px] font-bold text-slate-500 mb-2">새 예약 추가</p>
          <div className="mb-2">
            <p className="text-[10px] text-slate-400 mb-1.5">요일 선택</p>
            <div className="flex gap-1">
              {DAY_LABELS.map((label, idx) => (
                <button key={idx} onClick={() => toggleDay(idx)}
                  className={`w-7 h-7 rounded-lg text-[11px] font-bold transition ${newDays.includes(idx) ? 'bg-[#2c4be0] text-white shadow-sm shadow-[#2c4be0]/25' : 'bg-slate-100 text-slate-500 hover:bg-slate-200'}`}>
                  {label}
                </button>
              ))}
            </div>
          </div>
          <div className="mb-3">
            <p className="text-[10px] text-slate-400 mb-1.5">시각</p>
            <select value={newHour} onChange={e => setNewHour(Number(e.target.value))}
              className="w-full border border-slate-200 rounded-xl px-3 py-1.5 text-sm bg-white focus:outline-none focus:border-[#2c4be0] focus:ring-2 focus:ring-[#2c4be0]/10 transition font-mono">
              {Array.from({ length: 24 }, (_, i) => (
                <option key={i} value={i}>{String(i).padStart(2, '0')}:00</option>
              ))}
            </select>
          </div>
          <div className="flex items-center gap-2">
            {scheduleSaved && (
              <span className="flex items-center gap-1 text-[11px] font-semibold text-emerald-600 mr-auto">
                <CheckCircle className="w-3 h-3" />저장됨
              </span>
            )}
            <button onClick={addSchedule} disabled={newDays.length === 0}
              className="ml-auto bg-[#2c4be0] text-white px-4 py-1.5 rounded-xl text-xs font-semibold shadow-md shadow-[#2c4be0]/25 hover:bg-[#1d35b5] transition disabled:opacity-40 disabled:cursor-not-allowed">
              추가
            </button>
          </div>
        </div>
      </div>
      </div>
    </div>
  )
}

// ─── Main Page ────────────────────────────────────────────────────────────────
type Tab = 'overview' | 'roi' | 'settings'

const TABS: { key: Tab; label: string; icon: typeof MapPin }[] = [
  { key: 'overview', label: '개요', icon: Activity },
  { key: 'roi', label: 'ROI 관리', icon: MapPin },
  { key: 'settings', label: '설정', icon: SlidersHorizontal },
]

export default function DeviceDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [tab, setTab] = useState<Tab>('overview')

  const device = mockDevices.find((d) => d.id === id)
  const events = mockEvents.filter((e) => e.deviceId === id)

  if (!device) {
    return (
      <div className="max-w-[1720px] mx-auto px-8 py-16 text-center">
        <p className="text-slate-500 text-lg">디바이스를 찾을 수 없습니다: {id}</p>
        <button onClick={() => navigate('/devices')} className="mt-4 text-[#2c4be0] font-semibold hover:underline">디바이스 목록으로</button>
      </div>
    )
  }

  return (
    <div className="max-w-[1720px] mx-auto px-8 py-7">
      {/* Top bar */}
      <div className="flex items-center gap-4 mb-5">
        <button onClick={() => navigate('/devices')} className="flex items-center gap-1.5 text-slate-500 hover:text-slate-900 text-sm font-medium transition">
          <ChevronLeft className="w-4 h-4" />디바이스 목록
        </button>
        <span className="text-slate-300">|</span>
        <h1 className="text-xl font-extrabold tracking-tight text-slate-900 flex items-center gap-3">
          {device.name}<StatusBadge status={device.status} pulse />
        </h1>
      </div>

      {/* Tab bar */}
      <div className="flex items-center gap-1 p-1 rounded-2xl bg-slate-200/50 border border-white/80 mb-6 w-fit">
        {TABS.map(({ key, label, icon: Icon }) => (
          <button key={key} onClick={() => setTab(key)}
            className={`px-5 py-1.5 rounded-xl text-xs font-semibold flex items-center gap-2 transition-all ${tab === key ? 'bg-[#2c4be0] text-white shadow-md shadow-[#2c4be0]/25' : 'text-slate-600 hover:text-slate-900 hover:bg-white/80'}`}>
            <Icon className="w-3.5 h-3.5" />{label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {tab === 'overview' && <OverviewTab device={device} events={events} />}
      {tab === 'roi' && <RoiTab device={device} />}
      {tab === 'settings' && <SettingsTab device={device} />}
    </div>
  )
}
