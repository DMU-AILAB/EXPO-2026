"""통계 화면 시연용 더미 데이터 — 대시보드 DB의 `hourly_stats`에 가짜 기기 `demo-dummy`로 넣는다.

실제 기기 데이터는 건드리지 않는다. 지울 때도 `device_id = 'demo-dummy'` 행만 지운다.
통계 API는 기기를 가리지 않고 합산하므로, 더미가 들어 있는 동안에는 실제 수치에 섞여 보인다.

    python tools/dev/seed_demo_stats.py            # 최근 30일 + 오늘(현재 시각까지) 채움 (다시 실행해도 같은 결과)
    python tools/dev/seed_demo_stats.py --remove   # 더미만 삭제

`--db`로 DB 경로를 바꿀 수 있다(기본 dashboard/backend/data/visionguide.db). 대시보드는 재시작할 필요 없다.
"""

from __future__ import annotations

import argparse
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
DEVICE_ID = "demo-dummy"
CAMERA_ID = "demo"
DEFAULT_DB = Path(__file__).resolve().parents[2] / "dashboard" / "backend" / "data" / "visionguide.db"

# 시간대별 상대 유동량 — 출퇴근(8시, 18시)과 점심에 몰린다.
HOUR_WEIGHT = [0, 0, 0, 0, 0, 1, 3, 8, 14, 9, 6, 8, 12, 8, 6, 7, 10, 13, 15, 9, 5, 3, 1, 0]


def rows(now_kst: datetime, days: int = 30, seed: int = 7):
    rng = random.Random(seed)                         # 고정 시드 — 실행할 때마다 같은 모양
    today0 = now_kst.replace(hour=0, minute=0, second=0, microsecond=0)
    for d in range(days, -1, -1):
        day0 = today0 - timedelta(days=d)
        weekend = day0.weekday() >= 5
        day_scale = rng.uniform(0.7, 1.3) * (0.6 if weekend else 1.0)
        for h in range(24):
            at = day0 + timedelta(hours=h)
            if at > now_kst:                          # 아직 오지 않은 시각은 비워 둔다
                break
            total = int(HOUR_WEIGHT[h] * day_scale * rng.uniform(0.7, 1.3) * 2.5)
            cane = min(total, int(round(total * rng.uniform(0.03, 0.12))))
            detections = cane + (rng.randint(0, 2) if cane else 0)   # 지팡이 사용자 → 안내
            if total == 0:
                continue
            yield (DEVICE_ID, CAMERA_ID,
                   at.astimezone(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S.%f"),
                   total, cane, detections)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--remove", action="store_true", help="더미(demo-dummy)만 삭제")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = ap.parse_args()
    if not args.db.is_file():
        raise SystemExit(f"DB가 없습니다: {args.db}")

    con = sqlite3.connect(args.db)
    with con:
        con.execute("DELETE FROM hourly_stats WHERE device_id = ?", (DEVICE_ID,))
        removed = con.total_changes
        if args.remove:
            print(f"더미 {removed}행 삭제")
            return 0
        data = list(rows(datetime.now(KST)))
        con.executemany(
            "INSERT INTO hourly_stats (device_id, camera_id, hour, foot_traffic_count, cane_user_count, detection_count)"
            " VALUES (?,?,?,?,?,?)", data)
    print(f"더미 {len(data)}행 삽입 (기존 더미 {removed}행 교체) — 실제 기기 데이터는 그대로")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
