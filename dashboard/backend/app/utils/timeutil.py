"""시각 정규화 — DB에 들어가는 datetime은 **항상 naive UTC** 하나로 통일한다.

이 모듈이 없던 시절 세 종류가 한 컬럼에 섞였다.

- `datetime.utcnow()` — naive, UTC
- `datetime.now(ZoneInfo("Asia/Seoul")).astimezone(utc).replace(tzinfo=None)` — naive, UTC
- Pi가 `POST /api/events/ingest`로 보내는 `"...Z"` — **aware**

SQLite는 aware/naive를 구분하지 않고 문자열로 적어버리므로, 섞이면 `WHERE hour >= ?`
비교가 조용히 어긋난다(같은 순간이 6시간 차이로 저장된다). 저장 직전에 반드시
`to_naive_utc()`를 통과시킨다.

KST 경계(오늘 0시~24시)를 구할 때도 여기 헬퍼를 쓴다 — 통계는 사람이 보는 날짜
기준이어야 하고, 그 변환이 여러 곳에 복붙되면 한 곳만 고쳐져 어긋난다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

__all__ = [
    "KST",
    "utcnow",
    "to_naive_utc",
    "kst_day_bounds_utc",
    "naive_utc_to_kst",
    "format_uptime",
]

KST = ZoneInfo("Asia/Seoul")


def utcnow() -> datetime:
    """현재 시각 (naive UTC). `datetime.utcnow()`의 deprecation 대체."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_naive_utc(dt: datetime | None) -> datetime | None:
    """aware든 naive든 받아 **naive UTC**로 되돌린다.

    naive는 이미 UTC라고 본다 — 이 서버가 만드는 naive는 전부 `utcnow()` 계열이고,
    외부에서 들어오는 값은 tz를 달고 오기 때문이다.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def kst_day_bounds_utc(date_str: str | None = None) -> tuple[datetime, datetime]:
    """KST 기준 하루의 [시작, 끝]을 naive UTC로 준다.

    `date_str`이 `YYYY-MM-DD`가 아니면 오늘로 떨어진다 — 조회 파라미터 하나 때문에
    500을 내는 것보다 오늘을 보여주는 쪽이 낫다.
    """
    if date_str:
        try:
            start_kst = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=KST)
        except ValueError:
            start_kst = datetime.now(KST).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start_kst = datetime.now(KST).replace(hour=0, minute=0, second=0, microsecond=0)

    end_kst = start_kst + timedelta(days=1) - timedelta(microseconds=1)
    return (
        start_kst.astimezone(timezone.utc).replace(tzinfo=None),
        end_kst.astimezone(timezone.utc).replace(tzinfo=None),
    )


def naive_utc_to_kst(dt: datetime) -> datetime:
    """naive UTC로 저장된 값을 KST aware로 되돌린다 (시간대별 버킷 배정용)."""
    return dt.replace(tzinfo=timezone.utc).astimezone(KST)


def format_uptime(seconds: float | None) -> str | None:
    """명세 §3의 `uptime_human` — "3일 14시간 22분".

    Pi는 초만 보내고 표시 문자열은 서버가 만든다(프런트마다 다르게 만들면 화면끼리
    어긋난다).
    """
    if seconds is None:
        return None
    total = int(seconds)
    if total < 0:
        return None
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts = []
    if days:
        parts.append(f"{days}일")
    if hours or days:
        parts.append(f"{hours}시간")
    parts.append(f"{minutes}분")
    return " ".join(parts)
