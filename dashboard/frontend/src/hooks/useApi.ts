/** 화면이 반복해서 쓰는 두 가지 — 한 번 불러오기, 주기적으로 다시 불러오기. */

import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiError } from '../api/client'

type State<T> = {
  data: T | null
  error: ApiError | Error | null
  loading: boolean
  /** 수동 새로고침 — 저장 직후처럼 즉시 반영이 필요할 때. */
  reload: () => void
}

/**
 * `fetcher`를 불러 결과를 담는다. `intervalMs`를 주면 그 주기로 다시 부른다.
 *
 * 폴링 중에는 **loading을 다시 켜지 않는다** — 1초마다 화면 전체가 "불러오는 중"으로
 * 깜빡이면 관제 화면으로 쓸 수 없다. 첫 로드에서만 켠다.
 */
export function useApi<T>(fetcher: () => Promise<T>, deps: unknown[] = [],
                          intervalMs?: number, initialData: T | null = null): State<T> {
  const [data, setData] = useState<T | null>(initialData)
  const [error, setError] = useState<ApiError | Error | null>(null)
  const [loading, setLoading] = useState(initialData === null)
  const [nonce, setNonce] = useState(0)

  // fetcher는 매 렌더마다 새 함수라 deps에 넣으면 무한 루프가 된다.
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  const reload = useCallback(() => setNonce((n) => n + 1), [])

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setInterval> | undefined
    const hasInitialData = initialData !== null

    if (hasInitialData) {
      // Show the last successful response immediately while the fresh request
      // runs in the background.
      setData(initialData)
      setError(null)
      setLoading(false)
    }

    const run = async (isFirst: boolean) => {
      if (isFirst) setLoading(true)
      try {
        const result = await fetcherRef.current()
        if (!cancelled) {
          setData(result)
          setError(null)
        }
      } catch (e) {
        // 폴링 중 한 번 실패했다고 이미 보여주던 데이터를 지우지 않는다.
        if (!cancelled) setError(e as Error)
      } finally {
        if (!cancelled && isFirst) setLoading(false)
      }
    }

    run(!hasInitialData)
    if (intervalMs) timer = setInterval(() => run(false), intervalMs)

    return () => {
      cancelled = true
      if (timer) clearInterval(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce, intervalMs])

  return { data, error, loading, reload }
}
