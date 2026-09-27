import { useState } from 'react'
import { Eraser } from 'lucide-react'

import { streamUrl } from './StreamThumbnail'
import type { Camera, Roi } from '../types'

type Point = [number, number]

type RoiCanvasProps = {
  deviceId: string
  camera: Camera
  rois: Roi[]
  editingId: number | 'new' | null
  draftPolygon: Point[]
  onPolygonChange: (polygon: Point[]) => void
}

function cameraAspect(camera: Camera): number {
  const match = /^(\d+)x(\d+)$/.exec(camera.capture_preset)
  if (!match) return 4 / 3
  return Number(match[1]) / Number(match[2])
}

function clamp(value: number): number {
  return Math.max(0, Math.min(1, value))
}

function distance(a: Point, b: Point): number {
  return Math.hypot(a[0] - b[0], a[1] - b[1])
}

function pointsAttr(points: Point[]): string {
  return points.map(([x, y]) => `${x},${y}`).join(' ')
}

export default function RoiCanvas({
  deviceId, camera, rois, editingId, draftPolygon, onPolygonChange,
}: RoiCanvasProps) {
  const [dragIndex, setDragIndex] = useState<number | null>(null)
  const editing = editingId !== null

  const eventPoint = (event: React.PointerEvent<SVGSVGElement>): Point => {
    const rect = event.currentTarget.getBoundingClientRect()
    return [
      clamp((event.clientX - rect.left) / rect.width),
      clamp((event.clientY - rect.top) / rect.height),
    ]
  }

  const updateDraggedPoint = (event: React.PointerEvent<SVGSVGElement>) => {
    if (dragIndex === null) return
    const next = [...draftPolygon] as Point[]
    next[dragIndex] = eventPoint(event)
    onPolygonChange(next)
  }

  const handlePointerDown = (event: React.PointerEvent<SVGSVGElement>) => {
    if (!editing) return
    event.preventDefault()
    const point = eventPoint(event)
    const vertex = draftPolygon.findIndex((candidate) => distance(candidate, point) < 0.035)
    if (vertex >= 0) {
      setDragIndex(vertex)
      event.currentTarget.setPointerCapture(event.pointerId)
      return
    }
    onPolygonChange([...draftPolygon, point])
  }

  const handlePointerUp = (event: React.PointerEvent<SVGSVGElement>) => {
    if (dragIndex !== null && event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
    setDragIndex(null)
  }

  return (
    <div className="space-y-2">
      <div
        className="relative w-full rounded-xl overflow-hidden bg-slate-950 border border-slate-700/60"
        style={{ aspectRatio: cameraAspect(camera) }}
      >
        {camera.is_streaming ? (
          <img
            src={streamUrl(deviceId, camera.id)}
            alt={`${camera.id} 실시간 화면`}
            className="absolute inset-0 w-full h-full object-fill"
            draggable={false}
          />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center text-xs text-slate-400">
            카메라 스트림이 없습니다.
          </div>
        )}

        <svg
          viewBox="0 0 1 1"
          preserveAspectRatio="none"
          className={`absolute inset-0 w-full h-full ${editing ? 'cursor-crosshair' : 'pointer-events-none'}`}
          onPointerDown={handlePointerDown}
          onPointerMove={updateDraggedPoint}
          onPointerUp={handlePointerUp}
          onPointerCancel={handlePointerUp}
        >
          {rois.map((roi) => {
            if (roi.id === editingId || roi.polygon.length < 3) return null
            const excluded = roi.zone_type === 'exclude'
            return (
              <polygon
                key={roi.id}
                points={pointsAttr(roi.polygon)}
                fill={excluded ? 'rgba(100,116,139,0.28)' : 'rgba(44,75,224,0.22)'}
                stroke={excluded ? '#94a3b8' : '#60a5fa'}
                strokeWidth="0.006"
                vectorEffect="non-scaling-stroke"
              />
            )
          })}

          {editing && draftPolygon.length >= 2 && (
            <polygon
              points={pointsAttr(draftPolygon)}
              fill="rgba(16,185,129,0.22)"
              stroke="#34d399"
              strokeWidth="0.008"
              vectorEffect="non-scaling-stroke"
            />
          )}
          {editing && draftPolygon.length > 0 && (
            <polyline
              points={pointsAttr(draftPolygon)}
              fill="none"
              stroke="#a7f3d0"
              strokeWidth="0.012"
              strokeLinecap="round"
              strokeLinejoin="round"
              vectorEffect="non-scaling-stroke"
            />
          )}
          {editing && draftPolygon.map(([x, y], index) => (
            <circle
              key={`${x}-${y}-${index}`}
              cx={x}
              cy={y}
              r="0.010"
              fill={index === 0 ? '#fbbf24' : '#34d399'}
              stroke="#052e16"
              strokeWidth="0.004"
              vectorEffect="non-scaling-stroke"
            />
          ))}
        </svg>
      </div>

      {editing && (
        <div className="flex justify-end">
          <button
            type="button"
            onClick={() => onPolygonChange([])}
            className="glass-btn px-2.5 py-1.5 rounded-lg text-[11px] font-semibold flex items-center gap-1"
          >
            <Eraser className="w-3.5 h-3.5" /> 꼭짓점 초기화
          </button>
        </div>
      )}
    </div>
  )
}
