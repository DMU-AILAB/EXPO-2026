import { useCallback, useEffect, useRef, useState } from 'react'

import { streamUrl } from '../components/streamUrl'

/** Keep a dashboard image alive across short proxy or Wi-Fi interruptions. */
export function useMjpegStream(deviceId: string, cameraId: string) {
  const [attempt, setAttempt] = useState(0)
  const [retryCount, setRetryCount] = useState(0)
  const [failed, setFailed] = useState(false)
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    setAttempt(0)
    setRetryCount(0)
    setFailed(false)
    return () => {
      if (retryTimer.current !== null) clearTimeout(retryTimer.current)
      retryTimer.current = null
    }
  }, [deviceId, cameraId])

  const onError = useCallback(() => {
    setFailed(true)
    if (retryTimer.current !== null) clearTimeout(retryTimer.current)

    const delay = Math.min(1000 * 2 ** Math.min(retryCount, 3), 8000)
    retryTimer.current = setTimeout(() => {
      retryTimer.current = null
      setRetryCount((count) => count + 1)
      setAttempt((value) => value + 1)
      setFailed(false)
    }, delay)
  }, [retryCount])

  const onLoad = useCallback(() => {
    setFailed(false)
    setRetryCount(0)
  }, [])

  return {
    src: `${streamUrl(deviceId, cameraId)}&retry=${attempt}`,
    failed,
    onError,
    onLoad,
  }
}
