/**
 * CPU 온도 색 기준 — 화면마다 따로 두면 같은 장치가 목록과 상세에서 다른 색으로 보인다.
 *
 * Raspberry Pi는 80°C부터 클럭을 낮춘다(소프트 스로틀링). 그래서
 * 60°C 미만은 정상, 60~80°C는 스로틀링에 다가가는 주의 구간, 80°C 이상은 스로틀링 중이다.
 */
export const TEMP_WARN_C = 60
export const TEMP_THROTTLE_C = 80

export function tempTone(temp: number | null | undefined): string {
  if (temp == null) return 'text-slate-400'
  if (temp >= TEMP_THROTTLE_C) return 'text-red-600'
  if (temp >= TEMP_WARN_C) return 'text-amber-600'
  return 'text-emerald-600'
}
