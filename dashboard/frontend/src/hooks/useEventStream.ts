/** `WS /ws/events` 구독 (명세 §7).
 *
 * 서버는 30초마다 `{"type":"ping"}`을 보낸다 — 좀비 연결을 끊기 위한 것이라
 * 화면에서는 무시하면 된다.
 */

import { useEffect, useRef, useState } from 'react'

import { API_BASE, getToken } from '../api/client'
import type { WsMessage } from '../types'

type Options = {
  /** 특정 기기만 받고 싶을 때. 비우면 전부 받는다. */
  deviceIds?: string[]
  onMessage?: (msg: WsMessage) => void
}

export function useEventStream({ deviceIds, onMessage }: Options = {}) {
  const [connected, setConnected] = useState(false)
  const handlerRef = useRef(onMessage)
  handlerRef.current = onMessage

  // 배열을 deps에 그대로 넣으면 매 렌더마다 재연결된다.
  const key = (deviceIds ?? []).join(',')

  useEffect(() => {
    const token = getToken()
    if (!token) return

    let ws: WebSocket | null = null
    let retry: ReturnType<typeof setTimeout> | undefined
    let closed = false
    let backoff = 1000

    const connect = () => {
      if (closed) return
      const url = `${API_BASE.replace(/^http/, 'ws')}/ws/events?token=${encodeURIComponent(token)}`
      ws = new WebSocket(url)

      ws.onopen = () => {
        setConnected(true)
        backoff = 1000
        if (key) ws?.send(JSON.stringify({ type: 'subscribe', device_ids: key.split(',') }))
      }
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data) as WsMessage
          if (msg.type === 'ping') return
          handlerRef.current?.(msg)
        } catch {
          /* 서버가 보낸 것이 JSON이 아니면 무시 */
        }
      }
      ws.onclose = () => {
        setConnected(false)
        if (closed) return
        // 서버 재시작·네트워크 끊김에서 스스로 돌아와야 한다.
        retry = setTimeout(connect, backoff)
        backoff = Math.min(backoff * 2, 30_000)
      }
      ws.onerror = () => ws?.close()
    }

    connect()
    return () => {
      closed = true
      if (retry) clearTimeout(retry)
      ws?.close()
    }
  }, [key])

  return { connected }
}
