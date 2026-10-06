/**
 * 오탐 관리 — 구조물 마스크와 오탐 지점 제안.
 *
 * 둘 다 **제안-확인** 구조다. 기기가 후보를 모으고 운영자가 골라 적용한다 —
 * 자동으로 적용하면 수집 중 지나간 청소·보수 인력의 자리가 구조물로 굳어
 * 영구 사각지대가 된다(CLAUDE.md "구조물 마스크").
 *
 * - 마스크는 **움직임 게이트의 사전확률**일 뿐 최종 판정이 아니다. 기둥 앞을 걸어가는
 *   사람은 트랙이 움직이므로 살아난다.
 * - 지팡이 후보는 기본 선택, 사람 후보는 기본 해제 — 사람 마스크는 그 자리에 가만히
 *   선 진짜 사람을 가릴 수 있다.
 */

import { useEffect, useMemo, useState } from 'react'
import { Eraser, Loader2, MapPinOff, Moon, Shield, X } from 'lucide-react'

import * as api from '../../api'
import { authedUrl } from '../../components/streamUrl'
import { useApi } from '../../hooks/useApi'
import type { Camera, FpHotspot } from '../../types'
import {
  CLASS_NAME, type Ctx, ErrorBox, NoCamera, Panel, dangerBtn, formatSec, inputCls, primaryBtn,
  secondaryBtn, useCameraPicker,
} from './shared'

export default function CalibrationTab({ device, reload }: Ctx) {
  const { camera, picker } = useCameraPicker(device.cameras)
  if (!camera) return <NoCamera />
  return <CameraCalibration key={camera.id} ctx={{ device, reload }} camera={camera} picker={picker} />
}

function CameraCalibration({ ctx, camera, picker }: { ctx: Ctx; camera: Camera; picker: React.ReactNode }) {
  const { device } = ctx
  return (
    <div className="space-y-6">
      {picker && <div className="flex items-center gap-2 text-xs font-semibold text-slate-500">카메라 {picker}</div>}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        <CollectPanel deviceId={device.id} cameraId={camera.id} />
        <MaskPanel deviceId={device.id} cameraId={camera.id} className="xl:col-span-2" />
      </div>
      <HotspotPanel ctx={ctx} cameraId={camera.id} />
    </div>
  )
}

// ─── 폐장 시간 수집 ───────────────────────────────────────────────────────────

function CollectPanel({ deviceId, cameraId }: { deviceId: string; cameraId: string }) {
  const status = useApi(() => api.getCalibration(deviceId, cameraId), [deviceId, cameraId], 2000)
  const [minutes, setMinutes] = useState(5)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const running = status.data?.running === true

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true); setError(null)
    try { await fn(); status.reload() } catch (e) { setError(e) } finally { setBusy(false) }
  }

  return (
    <Panel title="구조물 수집">
      <ErrorBox error={error ?? status.error} />
      <p className="text-xs text-slate-500 leading-relaxed mb-4">
        <b>사람이 없는 시간</b>(폐장 후)에 몇 분 관측하면, 그동안 탐지되는 것은 전부 고정 구조물의
        오탐입니다. 라벨링 없이 이 현장의 오탐 후보를 모읍니다.
      </p>
      {running ? (
        <div className="space-y-3">
          <div className="flex items-center gap-2 text-sm font-bold text-slate-800">
            <Loader2 className="w-4 h-4 animate-spin text-[#2c4be0]" />
            수집 중 · 남은 시간 {formatSec(status.data?.remaining_sec)} · {status.data?.frames ?? 0}프레임
          </div>
          <button className={dangerBtn} disabled={busy}
                  onClick={() => run(() => api.cancelCalibration(deviceId, cameraId))}>
            <X className="w-3.5 h-3.5" />수집 취소
          </button>
        </div>
      ) : (
        <div className="flex items-center gap-2">
          <select className={`${inputCls} !w-28`} value={minutes} onChange={(e) => setMinutes(Number(e.target.value))}>
            {[1, 3, 5, 10, 20, 30].map((m) => <option key={m} value={m}>{m}분</option>)}
          </select>
          <button className={primaryBtn} disabled={busy || status.data == null}
                  onClick={() => run(() => api.startCalibration(deviceId, cameraId, minutes * 60))}>
            <Moon className="w-3.5 h-3.5" />수집 시작
          </button>
        </div>
      )}
      {status.data?.result && !running && (
        <p className="mt-3 text-[11px] text-slate-500">
          지난 수집이 끝났습니다. 오른쪽 후보 목록에서 적용할 것을 고르세요.
        </p>
      )}
    </Panel>
  )
}

// ─── 구조물 마스크 ────────────────────────────────────────────────────────────

function MaskPanel({ deviceId, cameraId, className }: { deviceId: string; cameraId: string; className?: string }) {
  const candidates = useApi(() => api.listMaskCandidates(deviceId, cameraId), [deviceId, cameraId])
  const hits = useApi(() => api.getMaskHits(deviceId, cameraId), [deviceId, cameraId])
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [confirmClear, setConfirmClear] = useState(false)

  // 이미 적용된 것이 있으면 그 상태를, 없으면 권장값(지팡이 ON / 사람 OFF)을 초기 선택으로.
  useEffect(() => {
    const rows = candidates.data ?? []
    const anyApplied = rows.some((r) => r.applied)
    setSelected(new Set(rows.filter((r) => (anyApplied ? r.applied : r.recommend)).map((r) => r.id)))
  }, [candidates.data])

  const dirty = useMemo(() => {
    const applied = new Set((candidates.data ?? []).filter((r) => r.applied).map((r) => r.id))
    return applied.size !== selected.size || [...selected].some((id) => !applied.has(id))
  }, [candidates.data, selected])

  const run = async (fn: () => Promise<unknown>, msg: string) => {
    setBusy(true); setError(null); setNotice(null)
    try {
      await fn()
      setNotice(msg)
      candidates.reload(); hits.reload()
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false); setConfirmClear(false)
    }
  }

  const toggle = (id: number) => setSelected((cur) => {
    const next = new Set(cur)
    if (next.has(id)) next.delete(id); else next.add(id)
    return next
  })

  const thumbUrl = (name: string) =>
    authedUrl(`/api/devices/${deviceId}/cameras/${cameraId}/static-mask/thumb`, { name })

  return (
    <Panel title={`구조물 마스크 후보 ${candidates.data?.length ?? 0}개`} className={className}
           actions={
             confirmClear ? (
               <>
                 <span className="text-[11px] font-semibold text-red-600">후보·적용·기록을 모두 지울까요?</span>
                 <button className={dangerBtn} disabled={busy}
                         onClick={() => run(() => api.clearMask(deviceId, cameraId), '초기화했습니다.')}>지우기</button>
                 <button className={secondaryBtn} onClick={() => setConfirmClear(false)}>취소</button>
               </>
             ) : (
               <button className={secondaryBtn} disabled={busy || !candidates.data?.length}
                       onClick={() => setConfirmClear(true)}><Eraser className="w-3.5 h-3.5" />초기화</button>
             )
           }>
      <ErrorBox error={error ?? candidates.error} />
      {notice && <p className="mb-3 text-xs font-semibold text-emerald-700">{notice}</p>}
      {!candidates.data?.length ? (
        <p className="py-8 text-center text-slate-400 text-sm">후보가 없습니다. 사람이 없는 시간에 구조물 수집을 실행하세요.</p>
      ) : (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 max-h-[420px] overflow-y-auto pr-1">
            {candidates.data.map((c) => (
              <label key={c.id} className={`rounded-2xl border overflow-hidden bg-white/70 cursor-pointer ${
                selected.has(c.id) ? 'border-[#2c4be0] ring-2 ring-[#2c4be0]/20' : 'border-slate-200/80'}`}>
                <div className="aspect-square bg-slate-100">
                  {c.thumb && <img src={thumbUrl(c.thumb)} alt="" className="w-full h-full object-cover" />}
                </div>
                <div className="px-2.5 py-2 text-[11px]">
                  <div className="flex items-center gap-1.5">
                    <input type="checkbox" className="accent-[#2c4be0]" checked={selected.has(c.id)}
                           onChange={() => toggle(c.id)} />
                    <span className={`rounded-md px-1.5 py-0.5 text-[10px] font-bold ${
                      c.cls === 0 ? 'bg-indigo-50 text-indigo-700' : 'bg-amber-50 text-amber-700'}`}>
                      {CLASS_NAME[c.cls] ?? c.cls}
                    </span>
                    {c.applied && <span className="text-[10px] font-bold text-emerald-600">적용 중</span>}
                  </div>
                  <p className="mt-1 text-slate-500">탐지 {c.hits}회 · 신뢰도 {c.max_conf.toFixed(2)}</p>
                  <p className="text-slate-400">이동량 {(c.max_disp * 100).toFixed(1)}%</p>
                </div>
              </label>
            ))}
          </div>
          <div className="mt-4 flex items-center justify-between gap-3">
            <p className="text-[11px] text-slate-400">
              사람 후보는 그 자리에 선 진짜 사람을 가릴 수 있어 기본으로 꺼져 있습니다.
            </p>
            <button className={primaryBtn} disabled={busy || !dirty}
                    onClick={() => run(() => api.applyMask(deviceId, cameraId, [...selected]),
                      `${selected.size}개를 적용했습니다. 기기에 몇 초 안에 반영됩니다.`)}>
              <Shield className="w-3.5 h-3.5" />선택한 {selected.size}개 적용
            </button>
          </div>
        </>
      )}

      <div className="mt-5 pt-4 border-t border-slate-200/70">
        <h3 className="text-xs font-bold text-slate-600 mb-2">마스크가 걸러낸 트랙</h3>
        {!hits.data?.length ? (
          <p className="text-[11px] text-slate-400">아직 기록이 없습니다.</p>
        ) : (
          <div className="flex gap-3">
            {hits.data.map((h) => (
              <div key={h.cls} className={`rounded-xl px-3 py-2 border text-xs ${
                h.cls === 1 ? 'border-amber-200 bg-amber-50' : 'border-slate-200 bg-slate-50'}`}>
                <span className="font-semibold">{CLASS_NAME[h.cls] ?? h.cls}</span>
                <span className="ml-2 font-black tabular-nums">{h.count}</span>
              </div>
            ))}
          </div>
        )}
        <p className="mt-2 text-[11px] text-slate-400">
          안내가 줄어든 것이 오탐이 줄어서인지 사람을 못 봐서인지는 이 값으로만 구분됩니다.
          사람 수가 늘고 있다면 사람 마스크를 해제하세요.
        </p>
      </div>
    </Panel>
  )
}

// ─── 오탐 지점 → 제외구역 제안 ─────────────────────────────────────────────────

/** 평균 박스를 조금 넓힌 사각형 — 제외구역은 bbox **중심점**으로 판정하므로 여유를 둔다. */
function hotspotPolygon(h: FpHotspot): [number, number][] {
  const [x1, y1, x2, y2] = h.bbox
  const px = (x2 - x1) * 0.15
  const py = (y2 - y1) * 0.15
  const c = (v: number) => Math.min(1, Math.max(0, Number(v.toFixed(4))))
  return [[c(x1 - px), c(y1 - py)], [c(x2 + px), c(y1 - py)], [c(x2 + px), c(y2 + py)], [c(x1 - px), c(y2 + py)]]
}

function HotspotPanel({ ctx, cameraId }: { ctx: Ctx; cameraId: string }) {
  const { device, reload } = ctx
  const hotspots = useApi(() => api.listFpHotspots(device.id, cameraId), [device.id, cameraId])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [pending, setPending] = useState<FpHotspot | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const createZone = async (h: FpHotspot) => {
    setBusy(true); setError(null); setNotice(null)
    try {
      await api.createRoi(device.id, device.etag, {
        camera_id: cameraId,
        name: `제외구역 ${h.cell[0]}-${h.cell[1]}`,
        zone_type: 'exclude',
        priority: 1,
        announcement_text: '',
        audio_file: '',
        is_active: true,
        polygon: hotspotPolygon(h),
      })
      setNotice('제외구역을 만들었습니다. ROI 관리 탭에서 모양을 다듬을 수 있습니다.')
      setPending(null)
      reload()
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Panel title="오탐 다발 지점"
           actions={<button className={secondaryBtn} disabled={busy || !hotspots.data?.length}
                            onClick={async () => {
                              setBusy(true)
                              try { await api.clearFpHotspots(device.id, cameraId); hotspots.reload() }
                              catch (e) { setError(e) } finally { setBusy(false) }
                            }}>
             <Eraser className="w-3.5 h-3.5" />누적 초기화</button>}>
      <ErrorBox error={error ?? hotspots.error} />
      {notice && <p className="mb-3 text-xs font-semibold text-emerald-700">{notice}</p>}
      <p className="text-xs text-slate-500 mb-4 leading-relaxed">
        오래 정지해 있어 억제된(= 배경 구조물일 가능성이 높은) 지팡이 탐지가 모인 위치입니다.
        지팡이 사용자가 늘 같은 자리에 멈춰 서도 여기에 잡힐 수 있으니 확인 후 만드세요.
        <b> 제외구역 안에서는 사람도 탐지되지 않습니다</b> — 사람이 지나다니는 자리라면 구조물 마스크가 더 안전합니다.
      </p>
      {!hotspots.data?.length ? (
        <p className="py-6 text-center text-slate-400 text-sm">누적된 오탐 지점이 없습니다.</p>
      ) : (
        <div className="space-y-2">
          {hotspots.data.map((h) => {
            const key = `${h.cell[0]}-${h.cell[1]}`
            const isPending = pending && pending.cell[0] === h.cell[0] && pending.cell[1] === h.cell[1]
            const [x1, y1, x2, y2] = h.bbox
            return (
              <div key={key} className="flex items-center gap-3 px-3 py-2 rounded-xl border border-slate-200/80 bg-slate-50/80 text-xs">
                <MapPinOff className="w-4 h-4 text-amber-500 shrink-0" />
                <span className="font-semibold text-slate-700">{h.count}회</span>
                <span className="text-slate-400 tabular-nums">
                  화면 {Math.round(((x1 + x2) / 2) * 100)}%, {Math.round(((y1 + y2) / 2) * 100)}% 부근
                </span>
                <span className="flex-1" />
                {isPending ? (
                  <>
                    <span className="text-[11px] font-semibold text-slate-600">이 자리에 제외구역을 만들까요?</span>
                    <button className={primaryBtn} disabled={busy} onClick={() => createZone(h)}>
                      {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : null}만들기
                    </button>
                    <button className={secondaryBtn} onClick={() => setPending(null)}>취소</button>
                  </>
                ) : (
                  <button className={secondaryBtn} onClick={() => setPending(h)}>제외구역 만들기</button>
                )}
              </div>
            )
          })}
        </div>
      )}
    </Panel>
  )
}
