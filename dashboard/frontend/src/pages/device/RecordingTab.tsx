/**
 * 녹화 — Pi 카메라 MJPEG 포트의 `/recording/*`를 백엔드가 중계한다.
 *
 * 녹화 파일은 Pi에만 쌓인다(카메라별 개수·용량 상한을 넘으면 오래된 것부터 지워짐).
 * 대시보드는 목록·재생·다운로드만 한다.
 */

import { useState } from 'react'
import { Circle, Download, FlaskConical, Loader2, Square, Video } from 'lucide-react'

import * as api from '../../api'
import { authedUrl } from '../../components/streamUrl'
import { useApi } from '../../hooks/useApi'
import type { RecordingClip } from '../../types'
import {
  type Ctx, ErrorBox, NoCamera, Panel, dangerBtn, formatBytes, formatSec, primaryBtn,
  secondaryBtn, useCameraPicker,
} from './shared'

export default function RecordingTab({ device }: Ctx) {
  const { camera, picker } = useCameraPicker(device.cameras)
  if (!camera) return <NoCamera />
  return <CameraRecording key={camera.id} deviceId={device.id} cameraId={camera.id} picker={picker} />
}

function CameraRecording({ deviceId, cameraId, picker }: {
  deviceId: string; cameraId: string; picker: React.ReactNode
}) {
  const status = useApi(() => api.getRecordingStatus(deviceId, cameraId), [deviceId, cameraId], 2000)
  const clips = useApi(() => api.listRecordings(deviceId, cameraId), [deviceId, cameraId])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [playing, setPlaying] = useState<RecordingClip | null>(null)

  const clipBase = `/api/devices/${deviceId}/cameras/${cameraId}/recording/clips`
  const recording = status.data?.recording === true

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true); setError(null)
    try {
      await fn()
      status.reload()
      clips.reload()
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
      <Panel title="녹화 제어" className="lg:col-span-2" actions={picker}>
        <ErrorBox error={error ?? status.error} />
        <div className="flex items-center gap-3 mb-4">
          <span className={`w-3 h-3 rounded-full ${recording ? 'bg-red-500 animate-pulse' : 'bg-slate-300'}`} />
          <span className="text-sm font-bold text-slate-800">
            {status.data == null ? '상태 확인 중…'
              : recording ? `녹화 중 · ${formatSec(status.data.recording ? status.data.elapsed_sec : 0)}`
              : '대기'}
          </span>
        </div>

        {recording ? (
          <button className={dangerBtn} disabled={busy}
                  onClick={() => run(() => api.stopRecording(deviceId, cameraId))}>
            {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Square className="w-3.5 h-3.5" />}
            녹화 중지
          </button>
        ) : (
          <div className="flex flex-wrap gap-2">
            <button className={primaryBtn} disabled={busy || status.data == null}
                    onClick={() => run(() => api.startRecording(deviceId, cameraId, false))}>
              <Circle className="w-3.5 h-3.5" />녹화 시작
            </button>
            <button className={secondaryBtn} disabled={busy || status.data == null}
                    onClick={() => run(() => api.startRecording(deviceId, cameraId, true))}>
              <FlaskConical className="w-3.5 h-3.5" />학습용 원본 녹화
            </button>
          </div>
        )}

        <ul className="mt-5 space-y-1.5 text-[11px] text-slate-500 leading-relaxed list-disc pl-4">
          <li><b>녹화 시작</b>은 탐지 박스·ROI가 그려진 화면을 저장합니다(데모·검토용).</li>
          <li><b>학습용 원본</b>은 오버레이 없이 저장합니다. 박스가 그려진 영상으로 학습하면
            모델이 그려진 박스를 단서로 배우므로, 학습 데이터는 반드시 이쪽으로 찍으세요.</li>
          <li>공공장소 영상 저장은 데모 범위의 예외입니다. 실제 배치 전 개인정보 정책을 재검토해야 합니다.</li>
        </ul>
      </Panel>

      <Panel title={`클립 ${clips.data?.length ?? 0}개`} className="lg:col-span-3"
             actions={<button className={secondaryBtn} onClick={clips.reload}>새로고침</button>}>
        <ErrorBox error={clips.error} />
        {playing && (
          <div className="mb-4">
            {/* Pi는 cv2 mp4v로 인코딩한다 — 브라우저에 따라 재생이 안 되면 다운로드해서 보면 된다. */}
            <video key={playing.id} controls autoPlay className="w-full rounded-xl bg-black"
                   src={authedUrl(`${clipBase}/${playing.id}.mp4`)} />
            <p className="mt-1.5 text-[11px] text-slate-400">
              브라우저에서 재생되지 않으면 다운로드해서 확인하세요(코덱: MPEG-4 Part 2).
            </p>
          </div>
        )}
        {clips.loading ? (
          <p className="py-8 text-center text-slate-400 text-sm">불러오는 중…</p>
        ) : !clips.data?.length ? (
          <p className="py-8 text-center text-slate-400 text-sm">저장된 클립이 없습니다.</p>
        ) : (
          <div className="grid grid-cols-2 xl:grid-cols-3 gap-3">
            {clips.data.map((c) => (
              <div key={c.id} className={`rounded-2xl border overflow-hidden bg-white/70 ${
                playing?.id === c.id ? 'border-[#2c4be0] ring-2 ring-[#2c4be0]/20' : 'border-slate-200/80'}`}>
                <button className="block w-full aspect-video bg-slate-100" onClick={() => setPlaying(c)}>
                  {c.has_thumb
                    ? <img src={authedUrl(`${clipBase}/${c.id}.jpg`)} alt="" className="w-full h-full object-cover" />
                    : <Video className="w-6 h-6 text-slate-300 mx-auto" />}
                </button>
                <div className="px-2.5 py-2 flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <p className="text-[11px] font-bold text-slate-700 truncate">
                      {c.started_at ? new Date(c.started_at).toLocaleString('ko-KR') : c.id}
                    </p>
                    <p className="text-[10px] text-slate-400">{formatSec(c.duration_sec)} · {formatBytes(c.size_bytes)}</p>
                  </div>
                  <a href={authedUrl(`${clipBase}/${c.id}.mp4`, { download: '1' })}
                     className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-500" title="다운로드">
                    <Download className="w-3.5 h-3.5" />
                  </a>
                </div>
              </div>
            ))}
          </div>
        )}
      </Panel>
    </div>
  )
}
