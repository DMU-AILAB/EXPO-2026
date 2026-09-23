/** 화면 공통 포맷터. 여러 페이지에 같은 로직이 복붙되지 않게 한 곳에 둔다. */

/** 서버는 ISO8601 문자열을 준다 — 사람이 읽는 상대 시각으로 바꾼다. */
export function formatLastSeen(iso: string | null | undefined): string {
  if (!iso) return '—'
  const t = Date.parse(iso.endsWith('Z') || iso.includes('+') ? iso : `${iso}Z`)
  if (Number.isNaN(t)) return '—'

  const diff = Date.now() - t
  if (diff < 0) return '방금 전'            // 기기 시계가 살짝 빠른 경우
  const sec = Math.floor(diff / 1000)
  if (sec < 60) return '방금 전'
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min}분 전`
  const hour = Math.floor(min / 60)
  if (hour < 24) return `${hour}시간 전`
  return `${Math.floor(hour / 24)}일 전`
}

/**
 * 메모리는 API 경계에서 **MB**로 오간다 (Pi가 /proc에서 MB로 읽는다).
 * 화면에서만 GB로 바꾼다 — 경계에서 바꾸면 단위가 코드마다 달라진다.
 */
export function formatMemory(used: number | null, total: number | null): string {
  if (used == null || total == null || total === 0) return '—'
  return `${(used / 1024).toFixed(1)} / ${(total / 1024).toFixed(1)} GB`
}

export function memoryPercent(used: number | null, total: number | null): number {
  if (used == null || total == null || total === 0) return 0
  return Math.min(100, Math.round((used / total) * 100))
}

export function formatNumber(n: number | null | undefined): string {
  return n == null ? '—' : n.toLocaleString('ko-KR')
}

const DAY_NAMES = ['일', '월', '화', '수', '목', '금', '토']

/** 명세 §11의 요일 번호(0=일)를 사람이 읽는 문자열로. */
export function formatDays(days: number[]): string {
  return days
    .slice()
    .sort((a, b) => a - b)
    .map((d) => DAY_NAMES[d] ?? '?')
    .join('·')
}

export { DAY_NAMES }
