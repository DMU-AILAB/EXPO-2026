import { useCallback, useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  ChevronLeft, Activity, Thermometer, Clock, Wifi, Camera, Video,
  MapPin, Power, SlidersHorizontal, Plus, Pencil, RotateCcw, CheckCircle,
  CalendarClock, Trash2, AlertTriangle, Loader2,
} from 'lucide-react'

import * as api from '../api'
import { ApiError } from '../api/client'
import StatusBadge from '../components/StatusBadge'
import { streamUrl } from '../components/StreamThumbnail'
import { DAY_NAMES, formatDays, formatMemory, memoryPercent } from '../format'
import { useApi } from '../hooks/useApi'
import type { Camera as CameraType, DeviceDetail as DeviceDetailType, RecentEvent, Roi } from '../types'

const inputCls =
  'w-full border border-slate-200 rounded-xl px-3 py-1.5 text-sm bg-white focus:outline-none focus:border-[#2c4be0] focus:ring-2 focus:ring-[#2c4be0]/10 transition'
const labelCls = 'text-xs font-bold text-slate-600 mb-1 block'

/** 저장·삭제 후 상세를 다시 읽어 **서버가 준 새 etag**를 받는다. */
type Ctx = { device: DeviceDetailType; reload: () => void }

// ─── CPU Gauge ────────────────────────────────────────────────────────────────

function CpuGauge({ value }: { value: number | null }) {
  const pct = value ?? 0
  const r = 42
  const c = 2 * Math.PI * r
  const color = pct > 80 ? '#dc2626' : pct > 60 ? '#f59e0b' : '#2c4be0'
  return (
    <div className="relative w-28 h-28">
      <svg viewBox="0 0 100 100" className="w-full h-full -rotate-90">
        <circle cx="50" cy="50" r={r} fill="none" stroke="#e2e8f0" strokeWidth="8" />
        <circle cx="50" cy="50" r={r} fill="none" stroke={color} strokeWidth="8"
                strokeLinecap="round" strokeDasharray={c}
                strokeDashoffset={c - (c * pct) / 100}
                className="transition-all duration-700" />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-xl font-black text-slate-900">
          {value == null ? '—' : `${Math.round(value)}%`}
        </span>
        <span className="text-[10px] font-semibold text-slate-400">CPU</span>
      </div>
    </div>
  )
}

// ─── Overview Tab ─────────────────────────────────────────────────────────────

function OverviewTab({ device, events }: { device: DeviceDetailType; events: RecentEvent[] }) {
  const memPct = memoryPercent(device.memory.used_mb, device.memory.total_mb)
  const temp = device.temperature
  const latency = device.latency

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
                <span className="font-mono font-bold text-slate-800">
                  {formatMemory(device.memory.used_mb, device.memory.total_mb)}
                </span>
              </div>
              <div className="w-full bg-slate-200 rounded-full h-2 overflow-hidden">
                <div className="bg-[#2c4be0] h-2 rounded-full transition-all" style={{ width: `${memPct}%` }} />
              </div>
            </div>
            <Row icon={<Thermometer className="w-3.5 h-3.5 text-emerald-500" />} label="온도"
                 value={temp != null ? `${temp.toFixed(1)}°C` : '—'}
                 tone={temp == null ? '' : temp > 65 ? 'text-red-600' : temp > 55 ? 'text-amber-600' : 'text-emerald-600'} />
            <Row icon={<Clock className="w-3.5 h-3.5 text-slate-400" />} label="가동 시간"
                 value={device.uptime ?? '—'} />
            <Row icon={<Wifi className="w-3.5 h-3.5 text-slate-400" />} label="프레임 처리"
                 value={latency != null ? `${latency}ms` : '—'}
                 tone={latency != null && latency > 200 ? 'text-amber-600' : ''} />
            <Row icon={<Activity className="w-3.5 h-3.5 text-slate-400" />} label="추론 시간"
                 value={device.npu_ms != null ? `${device.npu_ms}ms` : '—'} />
          </div>
        </div>

        <div className="light-glass-panel p-5">
          <h2 className="text-sm font-bold text-slate-700 mb-4 pb-3 border-b border-slate-200/70">
            카메라 목록 ({device.cameras.length}개)
          </h2>
          {device.cameras.length === 0 ? (
            <p className="text-xs text-slate-400 text-center py-4">카메라 정보를 아직 받지 못했습니다</p>
          ) : (
            <div className="space-y-3">
              {device.cameras.map((cam) => (
                <div key={cam.id} className="p-3 rounded-xl bg-slate-50/80 border border-slate-200/80 hover:border-[#2c4be0]/30 hover:bg-white transition">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-sm font-bold text-slate-800 flex items-center gap-2">
                      <Camera className="w-3.5 h-3.5 text-[#2c4be0]" />{cam.id}
                    </span>
                    <span className={`glass-badge px-2 py-0.5 rounded-full text-[10px] font-bold border ${
                      cam.is_streaming
                        ? 'bg-emerald-50/80 text-emerald-700 border-emerald-200/70'
                        : 'bg-slate-100 text-slate-500 border-slate-200'}`}>
                      {cam.is_streaming ? '스트리밍 중' : '중지'}
                    </span>
                  </div>
                  <p className="text-[11px] text-slate-500 mb-2">
                    {cam.capture_preset} · {cam.model_variant}
                    {cam.rotation ? ` · ${cam.rotation}°` : ''}
                  </p>
                  <div className="flex items-center justify-between text-[11px] text-slate-500">
                    <span>ROI {cam.roi_count}개</span>
                    <button
                      onClick={() => window.open(streamUrl(device.id, cam.id), '_blank')}
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
                    <tr key={ev.id} className={`border-b border-slate-100 ${
                      idx === 0 ? 'bg-[#2c4be0]/5 border-l-2 border-l-[#2c4be0]' : 'hover:bg-slate-50'} transition`}>
                      <td className="py-2.5 pr-4 font-mono text-slate-700 font-semibold">{ev.time}</td>
                      <td className="py-2.5 pr-4 text-slate-600">{ev.camera}</td>
                      <td className="py-2.5 pr-4 text-slate-600">{ev.roi ?? '—'}</td>
                      <td className="py-2.5 text-right">
                        {/* 가상 지팡이 박스로 발사된 안내에는 신뢰도가 없다. */}
                        {ev.confidence != null ? (
                          <span className="glass-badge px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-50/80 text-emerald-700 border border-emerald-200/70">
                            {ev.confidence.toFixed(2)}
                          </span>
                        ) : <span className="text-slate-400">—</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="mt-4 pt-3 border-t border-slate-200/70 text-[11px]">
                <span className="text-slate-500 font-medium">
                  오늘 총 <strong className="text-slate-800">{device.today_detections}건</strong>
                </span>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function Row({ icon, label, value, tone = '' }: {
  icon: React.ReactNode; label: string; value: string; tone?: string
}) {
  return (
    <div className="flex items-center justify-between text-xs font-medium">
      <span className="flex items-center gap-1.5 text-slate-600">{icon}{label}</span>
      <span className={`font-mono font-bold ${tone || 'text-slate-800'}`}>{value}</span>
    </div>
  )
}

// ─── ROI Tab ──────────────────────────────────────────────────────────────────

type RoiForm = Partial<Roi> & { camera_id?: string }

function RoiTab({ device, reload }: Ctx) {
  const [selectedCam, setSelectedCam] = useState(0)
  const [editingId, setEditingId] = useState<number | 'new' | null>(null)
  const [form, setForm] = useState<RoiForm>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const { data: audioFiles } = useApi(() => api.listAudio(), [])
  const activeCam: CameraType | undefined = device.cameras[selectedCam]
  const rois = device.rois.filter((r) => !activeCam || r.camera_id === activeCam.id)

  const openEdit = (roi: Roi) => { setEditingId(roi.id); setForm({ ...roi }); setError(null) }
  const openNew = () => {
    setEditingId('new')
    setForm({
      camera_id: activeCam?.id, name: '', zone_type: 'trigger', priority: 1,
      announcement_text: '', audio_file: '', is_active: true,
    })
    setError(null)
  }
  const closeForm = () => { setEditingId(null); setForm({}); setError(null) }

  const save = async () => {
    if (!activeCam) return
    setBusy(true); setError(null)
    try {
      if (editingId === 'new') {
        // 폴리곤 편집기는 아직 없다 — 화면 전체를 덮는 사각형으로 만들고
        // 현장에서 Pi의 roi_editor로 다듬는 흐름을 전제한다.
        await api.createRoi(device.id, device.etag, {
          camera_id: form.camera_id ?? activeCam.id,
          name: form.name ?? '',
          zone_type: form.zone_type ?? 'trigger',
          priority: form.priority ?? 1,
          announcement_text: form.announcement_text ?? '',
          audio_file: form.audio_file ?? '',
          is_active: form.is_active ?? true,
          polygon: form.polygon ?? [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]],
        })
      } else if (typeof editingId === 'number') {
        const { id: _id, camera_id: _c, ...patch } = form as Roi
        await api.updateRoi(device.id, editingId, device.etag, patch)
      }
      closeForm()
      reload()
    } catch (e) {
      setError(describe(e))
    } finally {
      setBusy(false)
    }
  }

  const remove = async (roi: Roi) => {
    if (!window.confirm(`'${roi.name}' 구역을 삭제할까요?\n과거 이벤트·통계와의 연결이 끊깁니다.`)) return
    setBusy(true); setError(null)
    try {
      await api.deleteRoi(device.id, roi.id, device.etag)
      reload()
    } catch (e) {
      setError(describe(e))
    } finally {
      setBusy(false)
    }
  }

  const toggleActive = async (roi: Roi) => {
    setBusy(true); setError(null)
    try {
      await api.updateRoi(device.id, roi.id, device.etag, { is_active: !roi.is_active })
      reload()
    } catch (e) {
      setError(describe(e))
    } finally {
      setBusy(false)
    }
  }

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
                    className={`px-2.5 py-1 rounded-lg text-[11px] font-semibold transition ${
                      selectedCam === idx ? 'bg-[#2c4be0] text-white' : 'bg-slate-100/80 text-slate-500 hover:bg-slate-200'}`}>
                    {cam.id}
                  </button>
                ))}
              </div>
            )}
          </div>

          {device.status === 'offline' || !activeCam || !activeCam.is_streaming ? (
            <div className="aspect-video bg-slate-900/90 rounded-xl flex flex-col items-center justify-center border border-slate-700/50">
              <Camera className="w-8 h-8 text-slate-600 mb-2" />
              <p className="text-xs font-semibold text-slate-500">
                {device.status === 'offline' ? '오프라인 — 스트림 없음' : '스트리밍 중지'}
              </p>
            </div>
          ) : (
            <div className="relative aspect-video rounded-xl overflow-hidden border border-emerald-500/40 shadow-[0_0_14px_rgba(16,185,129,0.12)]">
              <img src={streamUrl(device.id, activeCam.id)} alt=""
                   className="w-full h-full object-cover bg-slate-900" draggable={false} />
              <div className="absolute top-0 left-0 right-0 px-3 py-2 flex items-center justify-between"
                   style={{ background: 'linear-gradient(to bottom, rgba(0,0,0,0.65), transparent)' }}>
                <span className="text-[10.5px] font-bold text-white">{device.name} — {activeCam.id}</span>
                <span className="text-[10px] font-bold text-white/90 tracking-wider">LIVE</span>
              </div>
              <div className="absolute bottom-0 left-0 right-0 px-3 py-2"
                   style={{ background: 'linear-gradient(to top, rgba(0,0,0,0.60), transparent)' }}>
                <div className="flex items-center justify-between text-[10px] text-white/80">
                  <span>{activeCam.capture_preset} · {activeCam.model_variant}</span>
                  <span>ROI {activeCam.roi_count}개</span>
                </div>
              </div>
            </div>
          )}

          {activeCam && (
            <div className="mt-3 text-[11px] text-slate-500">
              <span className="font-mono text-slate-700 font-semibold">{device.ip}:{activeCam.port}</span>
            </div>
          )}

          <p className="mt-3 text-[10.5px] text-slate-400 leading-relaxed">
            폴리곤 모양은 기기의 ROI 편집기(포트 5000)에서 그립니다. 여기서는 이름·안내
            문구·오디오·우선순위를 관리합니다.
          </p>
        </div>
      </div>

      {/* 우: ROI 목록 */}
      <div className="lg:col-span-3 glass-panel p-5">
        <div className="flex items-center justify-between mb-4 pb-3 border-b border-slate-200/70">
          <h2 className="text-sm font-bold text-slate-700">ROI 구역 관리</h2>
          <button onClick={openNew} disabled={!activeCam}
                  className="glass-btn-brand px-3 py-1.5 rounded-xl text-xs gap-1.5 flex items-center disabled:opacity-50">
            <Plus className="w-3.5 h-3.5" />새 구역 추가
          </button>
        </div>

        {error && (
          <div className="mb-4 px-3.5 py-2.5 rounded-xl bg-red-50 border border-red-200 text-xs font-semibold text-red-700">
            {error}
          </div>
        )}

        {rois.length === 0 ? (
          <div className="py-12 text-center text-slate-400 text-sm">
            ROI 구역이 없습니다. 새 구역을 추가하세요.
          </div>
        ) : (
          <div className="space-y-2">
            {rois.map((roi) => (
              <div key={roi.id} className={`p-3 rounded-xl border transition ${
                roi.is_active ? 'bg-slate-50/80 border-slate-200/80' : 'bg-slate-100/40 border-slate-200/40 opacity-60'}`}>
                <div className="flex items-center gap-2 flex-wrap">
                  <span className={`w-2 h-2 rounded-full flex-shrink-0 ${roi.is_active ? 'bg-emerald-500' : 'bg-slate-400'}`} />
                  <span className="text-sm font-semibold text-slate-800">{roi.name}</span>
                  <span className={`text-[10px] px-2 py-0.5 rounded-full font-bold border ${
                    roi.zone_type === 'trigger'
                      ? 'bg-[#2c4be0]/8 text-[#2c4be0] border-[#2c4be0]/25'
                      : 'bg-slate-100 text-slate-500 border-slate-200'}`}>
                    {roi.zone_type === 'trigger' ? '안내 구역' : '감지 제외'}
                  </span>
                  <span className="text-[10px] text-slate-400">우선순위 {roi.priority}</span>
                  {roi.audio_file && (
                    <span className="text-[10px] text-slate-400 italic truncate max-w-[10rem]">
                      {roi.audio_file.split('/').pop()}
                    </span>
                  )}
                  <div className="flex gap-1.5 ml-auto">
                    <button onClick={() => void toggleActive(roi)} disabled={busy}
                      className={`text-[10px] px-2 py-0.5 rounded-full border font-semibold transition disabled:opacity-50 ${
                        roi.is_active
                          ? 'bg-emerald-50 text-emerald-700 border-emerald-200/70'
                          : 'bg-slate-100 text-slate-400 border-slate-200'}`}>
                      {roi.is_active ? '활성' : '비활성'}
                    </button>
                    <button onClick={() => openEdit(roi)} className="glass-btn p-1.5 rounded-lg">
                      <Pencil className="w-3 h-3" />
                    </button>
                    <button onClick={() => void remove(roi)} disabled={busy}
                            className="glass-btn p-1.5 rounded-lg text-red-500 disabled:opacity-50">
                      <Trash2 className="w-3 h-3" />
                    </button>
                  </div>
                </div>
                {roi.announcement_text && (
                  <p className="text-xs text-slate-500 mt-1.5 ml-4">"{roi.announcement_text}"</p>
                )}

                {editingId === roi.id && (
                  <RoiForm form={form} setForm={setForm} audioFiles={audioFiles ?? []}
                           onCancel={closeForm} onSave={() => void save()} busy={busy}
                           submitLabel="저장" warnRename={roi.name !== form.name} />
                )}
              </div>
            ))}
          </div>
        )}

        {editingId === 'new' && (
          <div className="mt-4 glass-panel-subtle p-4">
            <p className="text-xs font-bold text-slate-700 mb-3">새 ROI 구역</p>
            <RoiForm form={form} setForm={setForm} audioFiles={audioFiles ?? []}
                     onCancel={closeForm} onSave={() => void save()} busy={busy}
                     submitLabel="추가" warnRename={false} />
          </div>
        )}
      </div>
    </div>
  )
}

function RoiForm({ form, setForm, audioFiles, onCancel, onSave, busy, submitLabel, warnRename }: {
  form: RoiForm
  setForm: React.Dispatch<React.SetStateAction<RoiForm>>
  audioFiles: { filename: string }[]
  onCancel: () => void
  onSave: () => void
  busy: boolean
  submitLabel: string
  warnRename: boolean
}) {
  return (
    <div className="mt-3 pt-3 border-t border-slate-200/70 grid grid-cols-2 gap-3">
      <div className="col-span-2">
        <label className={labelCls}>이름</label>
        <input className={inputCls} value={form.name ?? ''}
               onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))} />
        {warnRename && (
          <p className="mt-1 flex items-start gap-1.5 text-[10.5px] text-amber-700">
            <AlertTriangle className="w-3 h-3 mt-0.5 shrink-0" />
            이름은 기기와 이벤트 로그의 식별자입니다 — 바꾸면 과거 이벤트·통계와의 연결이 끊깁니다.
          </p>
        )}
      </div>
      <div>
        <label className={labelCls}>구역 유형</label>
        <div className="flex gap-1.5">
          {(['trigger', 'exclude'] as const).map((t) => (
            <button key={t} onClick={() => setForm((p) => ({ ...p, zone_type: t }))}
              className={`flex-1 py-1.5 rounded-lg text-xs font-semibold transition ${
                form.zone_type === t
                  ? t === 'trigger' ? 'bg-[#2c4be0] text-white' : 'bg-slate-700 text-white'
                  : 'bg-slate-100 text-slate-500 hover:bg-slate-200'}`}>
              {t === 'trigger' ? '안내 구역' : '감지 제외'}
            </button>
          ))}
        </div>
      </div>
      <div>
        <label className={labelCls}>우선순위 (0~10)</label>
        <input type="number" min={0} max={10} className={inputCls} value={form.priority ?? 1}
               onChange={(e) => setForm((p) => ({ ...p, priority: Number(e.target.value) }))} />
      </div>
      <div className="col-span-2">
        <label className={labelCls}>오디오 파일</label>
        <select className={inputCls} value={form.audio_file ?? ''}
                onChange={(e) => setForm((p) => ({ ...p, audio_file: e.target.value }))}>
          <option value="">— 없음 —</option>
          {audioFiles.map((f) => <option key={f.filename} value={f.filename}>{f.filename}</option>)}
        </select>
      </div>
      <div className="col-span-2">
        <label className={labelCls}>안내 텍스트</label>
        <textarea rows={2} className={`${inputCls} resize-none`} value={form.announcement_text ?? ''}
                  onChange={(e) => setForm((p) => ({ ...p, announcement_text: e.target.value }))} />
      </div>
      <div className="col-span-2 flex items-center gap-2 text-xs">
        <input type="checkbox" checked={form.is_active ?? true}
               onChange={(e) => setForm((p) => ({ ...p, is_active: e.target.checked }))}
               className="accent-[#2c4be0]" id="roi-active" />
        <label htmlFor="roi-active" className="font-medium text-slate-700 cursor-pointer">
          활성화 (끄면 기기로 내려보내지 않습니다)
        </label>
      </div>
      <div className="col-span-2 flex justify-end gap-2">
        <button onClick={onCancel} className="glass-btn px-4 py-1.5 rounded-xl text-xs">취소</button>
        <button onClick={onSave} disabled={busy || !form.name}
                className="bg-[#2c4be0] text-white px-4 py-1.5 rounded-xl text-xs font-semibold shadow-md shadow-[#2c4be0]/25 hover:bg-[#1d35b5] disabled:opacity-50 transition flex items-center gap-1.5">
          {busy && <Loader2 className="w-3 h-3 animate-spin" />}{submitLabel}
        </button>
      </div>
    </div>
  )
}

// ─── Settings Tab ─────────────────────────────────────────────────────────────

function SettingsTab({ device, reload }: Ctx) {
  const [selectedCam, setSelectedCam] = useState(0)
  const [confirmAction, setConfirmAction] = useState<'restart' | 'reboot' | null>(null)
  const [actionMsg, setActionMsg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)

  const cam: CameraType | undefined = device.cameras[selectedCam]
  const [form, setForm] = useState(() => camFormOf(cam))
  // 카메라를 바꾸면 폼을 그 카메라의 값으로 되돌린다.
  useEffect(() => { setForm(camFormOf(cam)) }, [cam?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const schedulesRes = useApi(() => api.listSchedules(device.id), [device.id])
  const schedules = schedulesRes.data ?? []
  const [newDays, setNewDays] = useState<number[]>([])
  const [newHour, setNewHour] = useState(3)

  const saveCamera = async () => {
    if (!cam) return
    setBusy(true); setError(null)
    try {
      await api.updateCamera(device.id, cam.id, device.etag, form)
      setSaved(true); setTimeout(() => setSaved(false), 2500)
      reload()
    } catch (e) {
      setError(describe(e))
    } finally {
      setBusy(false)
    }
  }

  const runAction = async (action: 'restart' | 'reboot') => {
    setConfirmAction(null); setBusy(true); setError(null)
    try {
      const res = action === 'restart'
        ? await api.restartDevice(device.id)
        : await api.rebootDevice(device.id)
      setActionMsg(res.message)
      setTimeout(() => setActionMsg(null), 5000)
    } catch (e) {
      setError(describe(e))
    } finally {
      setBusy(false)
    }
  }

  const addSchedule = async () => {
    if (newDays.length === 0) return
    setBusy(true); setError(null)
    try {
      await api.createSchedule(device.id, { days: newDays, hour: newHour })
      setNewDays([]); setNewHour(3)
      schedulesRes.reload()
    } catch (e) {
      setError(describe(e))
    } finally {
      setBusy(false)
    }
  }

  const rotationChanged = cam != null && form.rotation !== cam.rotation

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
      {/* 카메라 설정 */}
      <div className="lg:col-span-2 glass-panel p-5 self-start">
        <h2 className="text-sm font-bold text-slate-700 mb-4 pb-3 border-b border-slate-200/70">카메라 설정</h2>

        {error && (
          <div className="mb-4 px-3.5 py-2.5 rounded-xl bg-red-50 border border-red-200 text-xs font-semibold text-red-700 whitespace-pre-line">
            {error}
          </div>
        )}

        {device.cameras.length === 0 ? (
          <p className="text-xs text-slate-400 text-center py-8">카메라 정보를 아직 받지 못했습니다</p>
        ) : (
          <>
            {device.cameras.length > 1 && (
              <div className="flex gap-1 mb-5">
                {device.cameras.map((c, idx) => (
                  <button key={c.id} onClick={() => setSelectedCam(idx)}
                    className={`px-3 py-1 rounded-lg text-xs font-semibold transition ${
                      selectedCam === idx ? 'bg-[#2c4be0] text-white' : 'bg-slate-100/80 text-slate-500 hover:bg-slate-200'}`}>
                    {c.id}
                  </button>
                ))}
              </div>
            )}
            {cam && (
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className={labelCls}>포트 (읽기 전용)</label>
                  <input readOnly value={cam.port} className={`${inputCls} bg-slate-100 cursor-not-allowed font-mono`} />
                </div>
                <div>
                  <label className={labelCls}>캡처 프리셋</label>
                  <select value={form.capture_preset} className={inputCls}
                          onChange={(e) => setForm((p) => ({ ...p, capture_preset: e.target.value }))}>
                    {CAPTURE_PRESETS.map((r) => <option key={r} value={r}>{r}</option>)}
                  </select>
                </div>
                <div>
                  <label className={labelCls}>
                    FPS <span className="font-normal text-slate-400">(기기 미적용)</span>
                  </label>
                  <select value={form.fps} className={inputCls}
                          onChange={(e) => setForm((p) => ({ ...p, fps: Number(e.target.value) }))}>
                    {[10, 15, 20, 25, 30].map((f) => <option key={f} value={f}>{f}fps</option>)}
                  </select>
                </div>
                <div>
                  <label className={labelCls}>모델 변형</label>
                  <select value={form.model_variant} className={inputCls}
                          onChange={(e) => setForm((p) => ({ ...p, model_variant: e.target.value }))}>
                    {MODEL_VARIANTS.map((m) => <option key={m} value={m}>{m}</option>)}
                  </select>
                </div>
                <div>
                  <label className={labelCls}>회전</label>
                  <select value={form.rotation} className={inputCls}
                          onChange={(e) => setForm((p) => ({ ...p, rotation: Number(e.target.value) }))}>
                    {[0, 90, 180, 270].map((r) => <option key={r} value={r}>{r}°</option>)}
                  </select>
                </div>

                {rotationChanged && (
                  <div className="col-span-2 flex items-start gap-2 px-3 py-2.5 rounded-xl bg-amber-50 border border-amber-200/70">
                    <AlertTriangle className="w-3.5 h-3.5 text-amber-600 mt-0.5 shrink-0" />
                    <p className="text-[11px] font-semibold text-amber-800 leading-relaxed">
                      회전을 바꾸면 이 카메라에 그려진 ROI·제외구역 좌표계가 어긋납니다.
                      기기는 자동으로 재배치하지 않으므로 저장 후 ROI를 다시 그려야 합니다.
                    </p>
                  </div>
                )}

                <div className="col-span-2 flex items-center gap-2 text-xs pt-1">
                  <input type="checkbox" id="reqPerson" checked={form.require_person}
                         onChange={(e) => setForm((p) => ({ ...p, require_person: e.target.checked }))}
                         className="accent-[#2c4be0] w-4 h-4" />
                  <label htmlFor="reqPerson" className="font-medium text-slate-700 cursor-pointer">
                    보행자 동반 필수 (사람 없이 흔들리는 유사물을 걸러냅니다)
                  </label>
                </div>
                <div className="col-span-2 flex items-center justify-end gap-3 pt-3 border-t border-slate-200/70">
                  {saved && (
                    <span className="flex items-center gap-1.5 text-xs font-semibold text-emerald-600 mr-auto">
                      <CheckCircle className="w-3.5 h-3.5" />기기에 반영되었습니다
                    </span>
                  )}
                  <button onClick={() => void saveCamera()} disabled={busy}
                    className="bg-[#2c4be0] text-white px-5 py-2 rounded-xl text-xs font-semibold shadow-md shadow-[#2c4be0]/25 hover:bg-[#1d35b5] disabled:opacity-50 transition flex items-center gap-1.5">
                    {busy && <Loader2 className="w-3 h-3 animate-spin" />}저장
                  </button>
                </div>
              </div>
            )}
          </>
        )}
      </div>

      <div className="flex flex-col gap-5">
        {/* 재시작 / 전원 */}
        <div className="glass-panel p-5">
          <h2 className="text-sm font-bold text-slate-700 mb-4 pb-3 border-b border-slate-200/70">재시작 / 전원</h2>

          {actionMsg && (
            <div className="mb-4 px-3.5 py-2.5 rounded-xl bg-emerald-50 border border-emerald-200 text-xs font-semibold text-emerald-700">
              {actionMsg}
            </div>
          )}

          <div className="p-3 rounded-xl bg-slate-50/80 border border-slate-200/60 mb-3">
            <p className="text-sm font-bold text-slate-900">서비스 재시작</p>
            <p className="text-xs text-slate-500 mt-0.5">탐지 서비스만 재시작합니다 (~10초)</p>
            {confirmAction === 'restart' ? (
              <div className="mt-2.5 flex gap-2">
                <button onClick={() => void runAction('restart')}
                        className="flex-1 py-1.5 rounded-lg text-xs font-bold bg-[#2c4be0] text-white">실행</button>
                <button onClick={() => setConfirmAction(null)}
                        className="flex-1 py-1.5 rounded-lg text-xs font-bold bg-slate-200 text-slate-700">취소</button>
              </div>
            ) : (
              <button onClick={() => setConfirmAction('restart')} disabled={busy}
                className="mt-2.5 w-full py-1.5 rounded-lg text-xs font-bold text-[#2c4be0] border border-[#2c4be0]/30 bg-[#2c4be0]/8 hover:bg-[#2c4be0]/14 disabled:opacity-50 flex items-center justify-center gap-1.5">
                <RotateCcw className="w-3.5 h-3.5" />서비스 재시작
              </button>
            )}
          </div>

          <div className="p-3 rounded-xl bg-red-50/70 border border-red-200/60">
            <p className="text-sm font-bold text-red-900">장치 재부팅</p>
            <p className="text-xs text-red-700 mt-0.5">라즈베리파이를 완전히 재부팅합니다 (~60초)</p>
            {confirmAction === 'reboot' ? (
              <div className="mt-2.5">
                <p className="text-xs font-semibold text-red-800 mb-2">정말 재부팅하시겠습니까?</p>
                <div className="flex gap-2">
                  <button onClick={() => void runAction('reboot')}
                          className="flex-1 py-1.5 rounded-lg text-xs font-bold bg-red-600 text-white">재부팅</button>
                  <button onClick={() => setConfirmAction(null)}
                          className="flex-1 py-1.5 rounded-lg text-xs font-bold bg-slate-200 text-slate-700">취소</button>
                </div>
              </div>
            ) : (
              <button onClick={() => setConfirmAction('reboot')} disabled={busy}
                className="mt-2.5 w-full py-1.5 rounded-lg text-xs font-bold text-red-700 border border-red-300 bg-white hover:bg-red-50 disabled:opacity-50 flex items-center justify-center gap-1.5">
                <Power className="w-3.5 h-3.5" />재부팅
              </button>
            )}
          </div>
        </div>

        {/* 예약 재부팅 */}
        <div className="glass-panel p-5">
          <div className="flex items-center gap-2 mb-4 pb-3 border-b border-slate-200/70">
            <CalendarClock className="w-3.5 h-3.5 text-[#2c4be0]" />
            <h2 className="text-sm font-bold text-slate-700">예약 재부팅</h2>
          </div>

          <div className="space-y-2 mb-4">
            {schedules.length === 0 && (
              <p className="text-xs text-slate-400 text-center py-3">등록된 예약 없음</p>
            )}
            {schedules.map((s) => (
              <div key={s.id} className="flex items-center gap-2 p-2.5 rounded-xl bg-slate-50/80 border border-slate-200/70">
                <span className={`text-xs font-semibold flex-1 ${s.is_enabled ? 'text-slate-800' : 'text-slate-400 line-through'}`}>
                  {s.display}
                </span>
                <button
                  onClick={() => void api.updateSchedule(device.id, s.id, { is_enabled: !s.is_enabled })
                    .then(schedulesRes.reload).catch((e) => setError(describe(e)))}
                  className={`text-[10px] px-2 py-0.5 rounded-full border font-semibold ${
                    s.is_enabled
                      ? 'bg-emerald-50 text-emerald-700 border-emerald-200/70'
                      : 'bg-slate-100 text-slate-400 border-slate-200'}`}>
                  {s.is_enabled ? '활성' : '비활성'}
                </button>
                <button
                  onClick={() => void api.deleteSchedule(device.id, s.id)
                    .then(schedulesRes.reload).catch((e) => setError(describe(e)))}
                  className="glass-btn p-1 rounded-lg text-red-500">
                  <Trash2 className="w-3 h-3" />
                </button>
              </div>
            ))}
          </div>

          <div className="pt-3 border-t border-slate-200/70">
            <p className="text-[11px] font-bold text-slate-500 mb-2">새 예약 추가</p>
            <div className="flex gap-1 mb-2.5">
              {DAY_NAMES.map((label, d) => (
                <button key={d}
                  onClick={() => setNewDays((prev) =>
                    prev.includes(d) ? prev.filter((x) => x !== d) : [...prev, d].sort())}
                  className={`flex-1 py-1 rounded-lg text-[11px] font-bold transition ${
                    newDays.includes(d) ? 'bg-[#2c4be0] text-white' : 'bg-slate-100 text-slate-500 hover:bg-slate-200'}`}>
                  {label}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-2">
              <select value={newHour} onChange={(e) => setNewHour(Number(e.target.value))}
                      className={`${inputCls} flex-1`}>
                {Array.from({ length: 24 }, (_, h) => (
                  <option key={h} value={h}>{String(h).padStart(2, '0')}:00</option>
                ))}
              </select>
              <button onClick={() => void addSchedule()} disabled={newDays.length === 0 || busy}
                className="px-3 py-1.5 rounded-xl text-xs font-semibold text-white bg-[#2c4be0] disabled:opacity-40 flex items-center gap-1.5">
                <Plus className="w-3.5 h-3.5" />추가
              </button>
            </div>
            {newDays.length > 0 && (
              <p className="mt-2 text-[10.5px] text-slate-500">
                {formatDays(newDays)} {String(newHour).padStart(2, '0')}:00에 재부팅됩니다
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

const CAPTURE_PRESETS = [
  'auto', '320x180', '320x240', '480x270', '480x360', '480x480',
  '640x360', '640x480', '848x480', '800x600', '960x540',
]
const MODEL_VARIANTS = [
  'v2_640', 'v3_320', 'v4_320', 'v5b_320', 'v6_320', 'v10_320', 'v11_yolo26n_320',
]

function camFormOf(cam?: CameraType) {
  return {
    capture_preset: cam?.capture_preset ?? 'auto',
    fps: cam?.fps ?? 20,
    model_variant: cam?.model_variant ?? 'v10_320',
    rotation: cam?.rotation ?? 0,
    require_person: cam?.require_person ?? true,
  }
}

/** 서버 오류를 사람이 읽을 문장으로. Pi의 검증 오류는 여러 줄로 온다. */
function describe(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.code === 'DEVICE_OFFLINE' || e.status === 503) {
      return '기기에 연결할 수 없어 설정을 적용하지 못했습니다. 기기 전원과 네트워크를 확인하세요.'
    }
    if (e.code === 'ETAG_MISMATCH') {
      return '설정이 그 사이 변경되었습니다. 새로고침한 뒤 다시 시도하세요.'
    }
    return e.message
  }
  return e instanceof Error ? e.message : '알 수 없는 오류'
}

// ─── Page ─────────────────────────────────────────────────────────────────────

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

  const fetcher = useCallback(() => api.getDevice(id!), [id])
  // 개요 탭만 폴링한다 — 편집 중에 폼이 밑에서 갈리면 곤란하다.
  const { data: device, loading, error, reload } =
    useApi(fetcher, [id], tab === 'overview' ? 5000 : undefined)

  if (loading && !device) {
    return <div className="max-w-[1720px] mx-auto px-8 py-16 text-center text-slate-500">불러오는 중…</div>
  }

  if (error || !device) {
    return (
      <div className="max-w-[1720px] mx-auto px-8 py-16 text-center">
        <p className="text-slate-500 text-lg">
          {error instanceof ApiError && error.status === 404
            ? `디바이스를 찾을 수 없습니다: ${id}`
            : error?.message ?? '디바이스를 불러오지 못했습니다'}
        </p>
        <button onClick={() => navigate('/devices')}
                className="mt-4 text-[#2c4be0] font-semibold hover:underline">
          디바이스 목록으로
        </button>
      </div>
    )
  }

  return (
    <div className="max-w-[1720px] mx-auto px-8 py-7">
      <div className="flex items-center gap-4 mb-5">
        <button onClick={() => navigate('/devices')}
                className="flex items-center gap-1.5 text-slate-500 hover:text-slate-900 text-sm font-medium transition">
          <ChevronLeft className="w-4 h-4" />디바이스 목록
        </button>
        <span className="text-slate-300">|</span>
        <h1 className="text-xl font-extrabold tracking-tight text-slate-900 flex items-center gap-3">
          {device.name}<StatusBadge status={device.status} pulse />
        </h1>
        <span className="text-xs text-slate-400 font-mono">{device.ip}</span>
      </div>

      <div className="flex items-center gap-1 p-1 rounded-2xl bg-slate-200/50 border border-white/80 mb-6 w-fit">
        {TABS.map(({ key, label, icon: Icon }) => (
          <button key={key} onClick={() => setTab(key)}
            className={`px-5 py-1.5 rounded-xl text-xs font-semibold flex items-center gap-2 transition-all ${
              tab === key ? 'bg-[#2c4be0] text-white shadow-md shadow-[#2c4be0]/25'
                          : 'text-slate-600 hover:text-slate-900 hover:bg-white/80'}`}>
            <Icon className="w-3.5 h-3.5" />{label}
          </button>
        ))}
      </div>

      {tab === 'overview' && <OverviewTab device={device} events={device.recent_events} />}
      {tab === 'roi' && <RoiTab device={device} reload={reload} />}
      {tab === 'settings' && <SettingsTab device={device} reload={reload} />}
    </div>
  )
}
