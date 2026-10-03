/**
 * 검증 재생 — 저장된 영상을 **배포와 같은 게이트 체인**으로 돌려 "이 ROI면 안내가
 * 나갔을까"를 화면으로 확인한다(`device/replay_engine.py`).
 *
 * - ROI는 기기의 현재 설정을 그대로 쓴다 → ROI 관리 탭에서 저장한 뒤 여기서 바로 확인.
 * - 시각은 영상 시간이라 같은 영상이면 결과가 재현된다.
 * - 오디오는 재생하지 않는다(현장 안내 스피커에 끼어들면 안 된다). 발사는 목록으로 남는다.
 * - 재생 세션은 기기에 하나뿐이다 — 다른 사람이 시작하면 이 화면의 재생이 바뀐다.
 * - 추론을 기기 CPU에서 돌리므로 재생하는 동안 실제 탐지 FPS가 떨어질 수 있다.
 */

import { useEffect, useState } from 'react'
import { Loader2, Pause, Play, SkipForward, Square } from 'lucide-react'

import * as api from '../../api'
import { replayStreamUrl } from '../../components/streamUrl'
import { useApi } from '../../hooks/useApi'
import {
  type Ctx, ErrorBox, Panel, inputCls, labelCls, primaryBtn, secondaryBtn,
} from './shared'

const COUNTER_LABEL: Record<string, string> = {
  raw: '지팡이 탐지',
  gated: '게이트 통과',
  virtual: '가상 박스',
  latched: '지팡이 사용자',
  announcements: '안내 발사',
}

export default function ReplayTab({ device }: Ctx) {
  const videos = useApi(() => api.listReplayVideos(device.id), [device.id])
  const status = useApi(() => api.getReplayStatus(device.id), [device.id], 1000)

  const variants = Array.from(new Set(device.cameras.map((c) => c.model_variant).filter(Boolean)))
  const [video, setVideo] = useState('')
  const [conf, setConf] = useState(0.55)
  const [variant, setVariant] = useState(variants[0] ?? '')
  const [speed, setSpeed] = useState(1)
  const [loop, setLoop] = useState(false)
  const [requirePerson, setRequirePerson] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  // 새로 시작할 때마다 바꿔 브라우저가 스트림을 새로 연다.
  const [nonce, setNonce] = useState(0)
  // Chrome은 프레임이 하나뿐인 MJPEG(이미 끝난 세션을 새로 열었을 때)를 그리지 않는다.
  const [frameShown, setFrameShown] = useState(false)
  useEffect(() => setFrameShown(false), [nonce])

  useEffect(() => {
    if (!video && videos.data?.length) setVideo(videos.data[0].name)
  }, [videos.data, video])

  const s = status.data
  const active = !!s && (s.running || s.done) && !!s.video

  const run = async (fn: () => Promise<unknown>, restartStream = false) => {
    setBusy(true); setError(null)
    try {
      await fn()
      if (restartStream) setNonce((n) => n + 1)
      status.reload()
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  const start = () => run(() => api.startReplay(device.id, {
    video, conf, speed, loop, require_person: requirePerson,
    ...(variant ? { model_variant: variant } : {}),
  }), true)

  const progress = s?.total ? Math.min(100, ((s.frame ?? 0) / s.total) * 100) : 0

  return (
    <div className="grid grid-cols-1 xl:grid-cols-7 gap-6">
      <div className="xl:col-span-4 space-y-4">
        <div className="glass-panel p-3">
          <div className="aspect-video rounded-xl bg-slate-900 overflow-hidden flex items-center justify-center">
            {active && (
              <img key={nonce} src={replayStreamUrl(device.id, nonce)} alt="검증 재생"
                   onLoad={() => setFrameShown(true)}
                   className={`w-full h-full object-contain ${frameShown ? '' : 'hidden'}`} />
            )}
            {!active && <p className="text-sm text-slate-400">영상을 골라 재생을 시작하세요.</p>}
            {active && !frameShown && (
              <p className="text-sm text-slate-400">
                {s?.done ? '재생이 끝났습니다. 다시 재생하면 화면이 나옵니다.' : '영상을 불러오는 중…'}
              </p>
            )}
          </div>
          {active && (
            <div className="mt-3 px-1">
              <div className="h-1.5 rounded-full bg-slate-200 overflow-hidden">
                <div className="h-full bg-[#2c4be0] transition-all" style={{ width: `${progress}%` }} />
              </div>
              <div className="mt-1.5 flex justify-between text-[11px] text-slate-500 tabular-nums">
                <span>{s?.video} · {s?.paused ? '일시정지' : s?.done ? '완료' : '재생 중'}</span>
                <span>{s?.frame ?? 0} / {s?.total ?? '?'} 프레임{s?.fps ? ` · ${s.fps} fps` : ''}</span>
              </div>
            </div>
          )}
          {s?.error && <div className="mt-3"><ErrorBox error={s.error} /></div>}
        </div>

        {active && (
          <div className="flex flex-wrap gap-2">
            <button className={secondaryBtn} disabled={busy || s?.done}
                    onClick={() => run(() => api.pauseReplay(device.id))}>
              {s?.paused ? <Play className="w-3.5 h-3.5" /> : <Pause className="w-3.5 h-3.5" />}
              {s?.paused ? '재개' : '일시정지'}
            </button>
            <button className={secondaryBtn} disabled={busy || !s?.paused}
                    onClick={() => run(() => api.stepReplay(device.id))}>
              <SkipForward className="w-3.5 h-3.5" />한 프레임
            </button>
            <button className={secondaryBtn} disabled={busy}
                    onClick={() => run(() => api.stopReplay(device.id))}>
              <Square className="w-3.5 h-3.5" />정지
            </button>
          </div>
        )}
      </div>

      <div className="xl:col-span-3 space-y-6">
        <Panel title="재생 설정">
          <ErrorBox error={error ?? videos.error} />
          <div className="space-y-3">
            <div>
              <label className={labelCls}>영상</label>
              <select className={inputCls} value={video} onChange={(e) => setVideo(e.target.value)}>
                {!videos.data?.length && <option value="">기기에 영상이 없습니다</option>}
                {videos.data?.map((v) => <option key={v.name} value={v.name}>{v.name} ({v.size_mb} MB)</option>)}
              </select>
              <p className="mt-1 text-[11px] text-slate-400">기기의 <code>~/visionguide/videos/</code>에 있는 mp4·avi</p>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className={labelCls}>신뢰도 {conf.toFixed(2)}</label>
                <input type="range" min={0.1} max={0.9} step={0.05} value={conf}
                       onChange={(e) => setConf(Number(e.target.value))} className="w-full accent-[#2c4be0]" />
              </div>
              <div>
                <label className={labelCls}>속도</label>
                <select className={inputCls} value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
                  {[0.25, 0.5, 1, 2, 4].map((v) => <option key={v} value={v}>{v}×</option>)}
                </select>
              </div>
            </div>
            {variants.length > 0 && (
              <div>
                <label className={labelCls}>모델</label>
                <select className={inputCls} value={variant} onChange={(e) => setVariant(e.target.value)}>
                  {variants.map((v) => <option key={v} value={v}>{v}</option>)}
                </select>
              </div>
            )}
            <label className="flex items-center gap-2 text-xs text-slate-600">
              <input type="checkbox" className="accent-[#2c4be0]" checked={requirePerson}
                     onChange={(e) => setRequirePerson(e.target.checked)} />사람 동반 필수(배포 기본값)
            </label>
            <label className="flex items-center gap-2 text-xs text-slate-600">
              <input type="checkbox" className="accent-[#2c4be0]" checked={loop}
                     onChange={(e) => setLoop(e.target.checked)} />반복 재생
            </label>
            <button className={primaryBtn} disabled={busy || !video} onClick={start}>
              {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
              {active ? '처음부터 다시 재생' : '재생 시작'}
            </button>
            <p className="text-[11px] text-slate-400 leading-relaxed">
              ROI는 기기의 현재 설정을 그대로 씁니다. 추론을 기기에서 돌리므로 재생 중에는 실제 탐지가 느려질 수 있습니다.
            </p>
          </div>
        </Panel>

        {active && (
          <Panel title="판정 결과">
            <div className="grid grid-cols-3 gap-2 mb-4">
              {Object.entries(COUNTER_LABEL).map(([k, label]) => (
                <div key={k} className="rounded-xl bg-slate-50 border border-slate-200/70 px-2.5 py-2">
                  <p className="text-[10px] font-semibold text-slate-400">{label}</p>
                  <p className="text-base font-black text-slate-800 tabular-nums">{s?.counters?.[k] ?? 0}</p>
                </div>
              ))}
            </div>
            <h3 className="text-xs font-bold text-slate-600 mb-2">안내 발사 (최근 30건)</h3>
            {!s?.events?.length ? (
              <p className="py-4 text-center text-xs text-slate-400">아직 발사된 안내가 없습니다.</p>
            ) : (
              <div className="max-h-56 overflow-y-auto">
                <table className="w-full text-xs">
                  <thead className="text-slate-400 text-[10px]">
                    <tr><th className="text-left py-1">시각</th><th className="text-left">ROI</th><th className="text-left">주체</th></tr>
                  </thead>
                  <tbody>
                    {[...s.events].reverse().map((ev, i) => (
                      <tr key={`${ev.frame}-${i}`} className="border-t border-slate-100">
                        <td className="py-1 tabular-nums">{ev.t.toFixed(1)}초</td>
                        <td className="font-semibold text-slate-700">{ev.roi}</td>
                        <td className="text-slate-500">#{ev.subject}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>
        )}
      </div>
    </div>
  )
}
