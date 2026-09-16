import type { DeviceStatus } from '../types'

interface StatusBadgeProps {
  status: DeviceStatus
  pulse?: boolean
  size?: 'sm' | 'md'
}

const config: Record<DeviceStatus, { dot: string; text: string; bg: string; border: string; label: string }> = {
  online: {
    dot: 'bg-emerald-500',
    text: 'text-emerald-700',
    bg: 'bg-emerald-400/20',
    border: 'border-emerald-300/50',
    label: '온라인',
  },
  warning: {
    dot: 'bg-amber-500',
    text: 'text-amber-700',
    bg: 'bg-amber-400/20',
    border: 'border-amber-300/50',
    label: '주의',
  },
  offline: {
    dot: 'bg-red-500',
    text: 'text-red-600',
    bg: 'bg-red-400/20',
    border: 'border-red-300/50',
    label: '오프라인',
  },
  unknown: {
    dot: 'bg-slate-400',
    text: 'text-slate-500',
    bg: 'bg-slate-400/15',
    border: 'border-slate-300/50',
    label: '알 수 없음',
  },
}

export default function StatusBadge({ status, pulse = false, size = 'md' }: StatusBadgeProps) {
  const c = config[status]
  const sizeClass = size === 'sm' ? 'text-[10px] px-2 py-0.5' : 'text-xs px-2.5 py-0.5'

  return (
    <span
      className={`glass-badge gap-1.5 rounded-full font-bold shadow-xs ${sizeClass} ${c.text} ${c.bg} border ${c.border}`}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${c.dot} ${pulse && status !== 'offline' ? 'animate-pulse' : ''}`} />
      {c.label}
    </span>
  )
}
