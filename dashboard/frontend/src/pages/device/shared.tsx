/** 기기 상세의 탭들이 같이 쓰는 조각 — 탭이 파일별로 나뉘면서 한 곳에 모았다. */

import { useState } from 'react'

import { ApiError } from '../../api/client'
import { cameraDisplayName } from '../../utils/cameraLabel'
import type { Camera, DeviceDetail } from '../../types'

/** 저장·삭제 후 상세를 다시 읽어 **서버가 준 새 etag**를 받는다. */
export type Ctx = { device: DeviceDetail; reload: () => void }

export const inputCls =
  'w-full border border-slate-200 rounded-xl px-3 py-1.5 text-sm bg-white focus:outline-none focus:border-[#2c4be0] focus:ring-2 focus:ring-[#2c4be0]/10 transition'
export const labelCls = 'text-xs font-bold text-slate-600 mb-1 block'
export const primaryBtn =
  'px-4 py-1.5 rounded-xl text-xs font-bold bg-[#2c4be0] text-white shadow-md shadow-[#2c4be0]/25 hover:bg-[#2340c8] disabled:opacity-50 disabled:cursor-not-allowed transition flex items-center gap-1.5'
export const secondaryBtn =
  'glass-btn px-3 py-1.5 rounded-xl text-xs font-semibold flex items-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed'
export const dangerBtn =
  'px-3 py-1.5 rounded-xl text-xs font-bold border border-red-200 bg-red-50 text-red-700 hover:bg-red-100 disabled:opacity-50 flex items-center gap-1.5 transition'

/** 서버 오류를 사람이 읽을 문장으로. Pi의 검증 오류는 여러 줄로 온다. */
export function describe(e: unknown): string {
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

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null
  return (
    <div className="mb-3 px-3.5 py-2.5 rounded-xl bg-red-50 border border-red-200 text-xs font-semibold text-red-700 whitespace-pre-line">
      {typeof error === 'string' ? error : describe(error)}
    </div>
  )
}

export function Panel({ title, actions, children, className = '' }: {
  title: string
  actions?: React.ReactNode
  children: React.ReactNode
  className?: string
}) {
  return (
    <div className={`glass-panel p-5 ${className}`}>
      <div className="flex items-center justify-between gap-3 mb-4 pb-3 border-b border-slate-200/70">
        <h2 className="text-sm font-bold text-slate-700">{title}</h2>
        {actions && <div className="flex items-center gap-2">{actions}</div>}
      </div>
      {children}
    </div>
  )
}

/** 카메라별 기능(녹화·오탐 관리)의 카메라 선택. 활성 카메라를 먼저 고른다. */
export function useCameraPicker(cameras: Camera[]) {
  const first = cameras.find((c) => c.is_active) ?? cameras[0]
  const [cameraId, setCameraId] = useState<string | undefined>(first?.id)
  const current = cameras.find((c) => c.id === cameraId) ?? first
  const picker = cameras.length > 1 ? (
    <select className={`${inputCls} !w-auto !py-1 text-xs`} value={current?.id}
            onChange={(e) => setCameraId(e.target.value)}>
      {cameras.map((c) => (
        <option key={c.id} value={c.id}>{cameraDisplayName(c.id)} (:{c.port})</option>
      ))}
    </select>
  ) : null
  return { camera: current, picker }
}

export function NoCamera() {
  return <div className="glass-panel p-10 text-center text-sm text-slate-400">이 기기에 등록된 카메라가 없습니다.</div>
}

export const formatBytes = (n: number) =>
  n >= 1e9 ? `${(n / 1e9).toFixed(2)} GB` : n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.round(n / 1e3)} KB`

export const formatSec = (s: number | null | undefined) => {
  if (s == null) return '—'
  const m = Math.floor(s / 60)
  const r = Math.round(s % 60)
  return m > 0 ? `${m}분 ${r}초` : `${r}초`
}

export const CLASS_NAME: Record<number, string> = { 0: '지팡이', 1: '사람' }
