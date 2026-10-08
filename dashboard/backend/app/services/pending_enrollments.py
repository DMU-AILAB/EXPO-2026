"""pending_enrollments.py — 다른 서버 소속 기기의 **승인 대기** 목록.

Pi가 스스로 서버를 찾아 등록을 요청할 때(`POST /api/bootstrap/enroll`), 그 기기가 이미 **다른
서버에 등록돼 있으면** 우리 서버가 마음대로 가져오지 않고 여기에 올려 둔다. 관리자가 대시보드에서
승인해야 인수한다 — 서버 PC를 바꾼 경우의 정상 경로이면서, 같은 LAN의 남의 기기를 뺏지 않는 장치다.

- **메모리에만 둔다**(`--workers` 금지 전제 — 하트비트 버퍼·설치 토큰과 같은 가정). 재시작하면
  사라지지만 Pi가 곧 다시 요청한다.
- 키는 기기 IP다. Pi가 다시 요청할 때마다 `last_seen`이 갱신되고, **15분 동안 요청이 없으면
  만료**된다 — 꺼졌거나 다른 곳으로 옮겨진 기기가 목록에 남지 않게.
- 거절하면 **1시간 동안** 같은 IP의 요청을 무시한다(거절한 기기가 계속 올라오지 않게).
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Callable, Optional

__all__ = ["PendingEnrollment", "PendingStore", "pending_store", "PENDING_TTL_SEC", "REJECT_TTL_SEC"]

PENDING_TTL_SEC = 15 * 60
REJECT_TTL_SEC = 60 * 60


@dataclass
class PendingEnrollment:
    ip: str
    device_id: str                  # 기기가 지금 쓰는 신원(다른 서버가 발급한 것)
    hostname: str
    current_server_url: str         # 기기가 지금 보고하고 있는 서버
    first_seen: float               # 단조 시계(만료 계산용)
    last_seen: float
    first_seen_wall: float          # 벽시계(표시용)
    last_seen_wall: float


class PendingStore:
    def __init__(self, ttl: float = PENDING_TTL_SEC, reject_ttl: float = REJECT_TTL_SEC,
                 clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time) -> None:
        self.ttl = ttl
        self.reject_ttl = reject_ttl
        self._clock = clock
        self._wall = wall
        self._items: dict[str, PendingEnrollment] = {}
        self._rejected: dict[str, float] = {}          # ip -> 억제가 끝나는 시각

    def _purge(self) -> None:
        now = self._clock()
        for ip in [ip for ip, e in self._items.items() if now - e.last_seen > self.ttl]:
            del self._items[ip]
        for ip in [ip for ip, until in self._rejected.items() if until <= now]:
            del self._rejected[ip]

    def is_rejected(self, ip: str) -> bool:
        self._purge()
        return ip in self._rejected

    def upsert(self, ip: str, *, device_id: str, hostname: str, current_server_url: str) -> PendingEnrollment:
        """요청이 올 때마다 호출한다 — 처음이면 만들고, 아니면 `last_seen`과 현재 정보를 갱신한다."""
        self._purge()
        now, wall = self._clock(), self._wall()
        entry = self._items.get(ip)
        if entry is None:
            entry = PendingEnrollment(ip=ip, device_id=device_id, hostname=hostname,
                                      current_server_url=current_server_url,
                                      first_seen=now, last_seen=now,
                                      first_seen_wall=wall, last_seen_wall=wall)
            self._items[ip] = entry
        else:
            entry.device_id, entry.hostname = device_id, hostname
            entry.current_server_url = current_server_url
            entry.last_seen, entry.last_seen_wall = now, wall
        return entry

    def get(self, ip: str) -> Optional[PendingEnrollment]:
        self._purge()
        return self._items.get(ip)

    def list(self) -> list[PendingEnrollment]:
        self._purge()
        return sorted(self._items.values(), key=lambda e: e.first_seen)

    def remove(self, ip: str) -> bool:
        return self._items.pop(ip, None) is not None

    def reject(self, ip: str) -> bool:
        """대기 항목을 지우고 억제를 건다. 항목이 있었으면 True."""
        existed = self.remove(ip)
        self._rejected[ip] = self._clock() + self.reject_ttl
        return existed

    def clear(self) -> None:
        self._items.clear()
        self._rejected.clear()

    @staticmethod
    def to_dict(entry: PendingEnrollment) -> dict:
        d = asdict(entry)
        # 단조 시계 값은 외부에 의미가 없다 — 표시용 벽시계만 내보낸다.
        for k in ("first_seen", "last_seen"):
            d.pop(k)
        d["first_seen"], d["last_seen"] = d.pop("first_seen_wall"), d.pop("last_seen_wall")
        return d


pending_store = PendingStore()
