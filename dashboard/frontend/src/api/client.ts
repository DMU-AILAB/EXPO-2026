/**
 * 백엔드 호출의 단일 창구.
 *
 * 서버는 모든 응답을 명세 §1.4의 봉투로 감싼다.
 *   성공: { data, ok: true }  (목록은 total도)
 *   실패: { error, message, ok: false }
 * 그래서 호출부가 매번 `.data`를 벗기고 `.error`를 확인하는 대신 여기서 한 번만 한다.
 *
 * 토큰은 localStorage에 둔다. 새로고침으로 로그인이 풀리면 관제 화면으로서 못 쓰고,
 * 이 대시보드는 사내망에서 쓰는 단일 관리자 도구라 그 정도 보관이면 충분하다.
 */

const TOKEN_KEY = 'visionguide.token'

/** 개발 서버는 Vite 프록시 없이 백엔드를 직접 부른다. */
export const API_BASE: string =
  (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://localhost:8000'

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null // 프라이빗 모드 등에서 접근이 막힐 수 있다
  }
}

export function setToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* 저장 못 해도 이번 세션은 메모리로 굴러간다 */
  }
}

/** 서버가 내려준 오류 코드를 그대로 들고 다닌다 — 화면이 코드로 분기할 수 있게. */
export class ApiError extends Error {
  code: string
  status: number
  detail: unknown

  constructor(status: number, code: string, message: string, detail?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.detail = detail
  }
}

type Options = {
  method?: string
  body?: unknown
  /** 변경 API는 If-Match(config_etag)를 요구한다 — 없으면 400이다. */
  etag?: string
  signal?: AbortSignal
  /** multipart 업로드는 Content-Type을 브라우저가 정해야 한다. */
  form?: FormData
}

/** 401을 만났을 때 앱이 로그인 화면으로 보내도록 걸어두는 훅. */
let onUnauthorized: (() => void) | null = null
export function setUnauthorizedHandler(fn: (() => void) | null) {
  onUnauthorized = fn
}

export async function request<T>(path: string, opts: Options = {}): Promise<T> {
  const headers: Record<string, string> = {}
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  if (opts.etag) headers['If-Match'] = opts.etag

  let body: BodyInit | undefined
  if (opts.form) {
    body = opts.form
  } else if (opts.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(opts.body)
  }

  const res = await fetch(`${API_BASE}${path}`, {
    method: opts.method ?? 'GET',
    headers,
    body,
    signal: opts.signal,
  })

  if (res.status === 204) return undefined as T

  let payload: any = null
  try {
    payload = await res.json()
  } catch {
    /* 본문이 없는 응답 */
  }

  if (!res.ok) {
    if (res.status === 401) {
      setToken(null)
      onUnauthorized?.()
    }
    const message =
      typeof payload?.message === 'string'
        ? payload.message
        : Array.isArray(payload?.message)
          ? payload.message.join('\n')
          : `요청이 실패했습니다 (HTTP ${res.status})`
    throw new ApiError(res.status, payload?.error ?? 'UNKNOWN', message, payload?.message)
  }

  // 봉투를 벗긴다. `data`가 없는 응답(예: ingest의 {id, ok})은 통째로 돌려준다.
  return (payload && 'data' in payload ? payload.data : payload) as T
}

/** `total`·`stale`·`etag` 같은 봉투의 곁가지까지 필요할 때 쓴다. */
export async function requestEnvelope<T>(path: string, opts: Options = {}): Promise<T> {
  const headers: Record<string, string> = {}
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  if (opts.etag) headers['If-Match'] = opts.etag

  const res = await fetch(`${API_BASE}${path}`, {
    method: opts.method ?? 'GET',
    headers,
    signal: opts.signal,
  })
  const payload = await res.json().catch(() => null)
  if (!res.ok) {
    if (res.status === 401) {
      setToken(null)
      onUnauthorized?.()
    }
    throw new ApiError(res.status, payload?.error ?? 'UNKNOWN',
      payload?.message ?? `요청이 실패했습니다 (HTTP ${res.status})`)
  }
  return payload as T
}

export function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') sp.set(k, String(v))
  }
  const s = sp.toString()
  return s ? `?${s}` : ''
}
