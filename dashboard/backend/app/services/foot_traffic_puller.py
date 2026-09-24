"""유동인구 수집기 — Pi가 이미 집계해 둔 값을 서버로 끌어온다.

명세 §8의 세 지표 중 `total_count`(유동인구)와 `cane_user_count`(지팡이 사용자)는
**Pi가 이미 sqlite에 쌓고 있다.** 그런데 그것을 서버로 옮기는 경로가 없어서
`hourly_stats`의 두 컬럼이 늘 0이었고, 대시보드의 유동인구가 영구히 0이었다
(ingest는 `detection_count`만 올린다).

방향이 이벤트와 반대라는 점이 중요하다. 이벤트는 **Pi가 밀어 올리고**(발생 시점이
중요하므로), 유동인구는 **서버가 끌어온다**(누적 집계라 언제 읽어도 같은 답이 나오고,
Pi에 스케줄러를 하나 더 두지 않아도 된다).

키 이름이 서버와 다르다:

| Pi | 서버 |
|---|---|
| `granularity: "hour" \\| "day"` | `hourly` \\| `daily` |
| `points[].total_count` | `hourly_stats.foot_traffic_count` |
| `points[].cane_user_count` | `hourly_stats.cane_user_count` |
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models.camera import Camera
from ..models.device import Device
from ..models.stats import HourlyStats
from ..utils.timeutil import KST, utcnow
from .pi_client import PiClient

logger = logging.getLogger(__name__)

__all__ = ["pull_once", "PULL_INTERVAL_SEC"]

# 유동인구는 시간 단위 집계라 자주 끌어올 이유가 없다. 5분이면 화면이 충분히 최신이고
# 기기 부하도 무시할 만하다.
PULL_INTERVAL_SEC = 300

# 서버가 꺼져 있던 구간을 메우기 위해 오늘 말고 며칠 더 거슬러 확인한다.
# Pi의 시간별 API는 `date` 쿼리를 받으므로 과거 하루도 그대로 읽을 수 있다.
BACKFILL_DAYS = 2


async def pull_once() -> int:
    """등록된 모든 기기×카메라의 시간별 집계를 끌어와 UPSERT. 갱신한 행 수를 돌려준다."""
    db: Session = SessionLocal()
    updated = 0
    try:
        devices = db.query(Device).all()
        for device in devices:
            cameras = db.query(Camera).filter(Camera.device_id == device.id).all()
            if not cameras:
                continue
            client = PiClient(device.ip)
            for camera in cameras:
                for day_offset in range(BACKFILL_DAYS):
                    date_kst = (datetime.now(KST) - timedelta(days=day_offset)).date()
                    try:
                        data = await client.get_timeseries(camera.id, period="today") \
                            if day_offset == 0 else \
                            await _get_dated(client, camera.id, date_kst.isoformat())
                    except Exception as exc:          # noqa: BLE001
                        # 기기가 꺼져 있는 것은 정상 상황이다 — 다음 주기에 다시 온다.
                        logger.debug("유동인구 수집 건너뜀 (%s/%s): %s",
                                     device.id, camera.id, exc)
                        break
                    updated += _apply_points(db, device.id, camera.id, date_kst, data)
        db.commit()
    except Exception:
        logger.exception("유동인구 수집 실패")
        db.rollback()
        return 0
    finally:
        db.close()
    return updated


async def _get_dated(client: PiClient, camera_id: str, date_iso: str) -> dict:
    """과거 하루의 시간별 집계. Pi가 `date` 쿼리를 모르는 구버전이면 예외가 난다."""
    return await client._get_json(          # noqa: SLF001 - 얇은 래퍼라 그대로 쓴다
        "/api/stats/timeseries",
        {"camera": camera_id, "period": "today", "date": date_iso},
    )


def _apply_points(db: Session, device_id: str, camera_id: str,
                  date_kst, data: dict) -> int:
    """Pi의 시간별 포인트 24개를 `hourly_stats`에 반영한다.

    **`detection_count`는 건드리지 않는다.** 그쪽은 ingest가 세는 값이라 여기서 덮으면
    서버가 받은 안내 이벤트 수가 0으로 밀린다.
    """
    if data.get("granularity") != "hour":
        return 0

    updated = 0
    for point in data.get("points", []) or []:
        hour = point.get("hour")
        if hour is None:
            continue
        total = int(point.get("total_count") or 0)
        canes = int(point.get("cane_user_count") or 0)
        if total == 0 and canes == 0:
            continue                       # 0-채움된 빈 시간대까지 행을 만들 이유가 없다

        # Pi의 시간은 기기 로컬 시각(KST)이다. DB는 naive UTC로 통일한다.
        hour_kst = datetime(date_kst.year, date_kst.month, date_kst.day, hour, tzinfo=KST)
        hour_utc = hour_kst.astimezone(timezone.utc).replace(tzinfo=None)

        stat = db.query(HourlyStats).filter(
            HourlyStats.device_id == device_id,
            HourlyStats.camera_id == camera_id,
            HourlyStats.hour == hour_utc,
        ).first()
        if stat is None:
            stat = HourlyStats(device_id=device_id, camera_id=camera_id, hour=hour_utc,
                               foot_traffic_count=total, cane_user_count=canes,
                               detection_count=0)
            db.add(stat)
        else:
            stat.foot_traffic_count = total
            stat.cane_user_count = canes
        updated += 1
    return updated
