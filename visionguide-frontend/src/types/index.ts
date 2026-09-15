export type DeviceStatus = 'online' | 'offline' | 'warning' | 'unknown'

export interface Camera {
  id: number
  port: number
  resolution: string
  fps: number
  roiCount: number
  todayDetections: number
  isStreaming: boolean
  currentAlert?: string
  imageIndex?: number
}

export interface Device {
  id: string
  name: string
  ip: string
  location: string
  status: DeviceStatus
  cpu: number
  temperature: number
  memory: { used: number; total: number }
  uptime: string
  latency: number
  cameras: Camera[]
  todayDetections: number
  npuMs: number
  lastSeen: string
}

export interface DetectionEvent {
  time: string
  camera: string
  roi: string
  confidence: number
  deviceId: string
}
