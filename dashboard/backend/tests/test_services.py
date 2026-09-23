"""서비스 계층 단위 테스트 — 조용히 틀리는 종류만 모았다."""

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from app.models.camera import Camera
from app.models.device import Device
from app.models.stats import HourlyStats
from app.services.foot_traffic_puller import pull_once
from app.services.monitor_service import sweep_offline_devices
from app.services.scheduler_service import to_apscheduler_dow
from app.utils.timeutil import (format_uptime, kst_day_bounds_utc, naive_utc_to_kst,
                                to_naive_utc, utcnow)

PI = "http://192.168.1.101:5000"


# --------------------------------------------------------------------------
# 예약 재부팅 요일 매핑
# --------------------------------------------------------------------------

def test_weekday_mapping_matches_apscheduler():
    """명세 0=일 … 6=토, APScheduler 0=월 … 6=일. 변환하지 않으면 하루 밀린다."""
    assert to_apscheduler_dow([0]) == "6"        # 일요일
    assert to_apscheduler_dow([1]) == "0"        # 월요일
    assert to_apscheduler_dow([6]) == "5"        # 토요일
    assert to_apscheduler_dow([1, 3, 5]) == "0,2,4"   # 월·수·금


def test_weekday_mapping_actually_fires_on_the_right_days():
    """변환 결과를 CronTrigger에 넣어 실제 발사일을 확인한다.

    수정 전에는 "월·수·금 03:00"이 화·목·토에 실행됐다(실측).
    """
    from apscheduler.triggers.cron import CronTrigger

    trigger = CronTrigger(day_of_week=to_apscheduler_dow([1, 3, 5]), hour=3, minute=0)
    cursor = datetime(2026, 9, 23, 0, 0)
    fired = []
    prev = None
    for _ in range(3):
        cursor = trigger.get_next_fire_time(prev, cursor)
        fired.append(cursor.strftime("%a"))
        prev = cursor
        cursor += timedelta(minutes=1)

    assert fired == ["Wed", "Fri", "Mon"]


# --------------------------------------------------------------------------
# 시각 정규화
# --------------------------------------------------------------------------

def test_to_naive_utc_converts_aware_and_passes_naive():
    aware = datetime(2026, 9, 23, 14, 32, 7, tzinfo=timezone(timedelta(hours=9)))
    assert to_naive_utc(aware) == datetime(2026, 9, 23, 5, 32, 7)

    naive = datetime(2026, 9, 23, 5, 32, 7)
    assert to_naive_utc(naive) is naive
    assert to_naive_utc(None) is None


def test_kst_day_bounds_span_exactly_one_day():
    start, end = kst_day_bounds_utc("2026-09-23")
    # KST 자정 = 전날 15:00 UTC
    assert start == datetime(2026, 9, 22, 15, 0)
    assert (end - start) < timedelta(days=1)
    assert naive_utc_to_kst(start).hour == 0


def test_kst_day_bounds_falls_back_to_today_on_bad_input():
    """조회 파라미터 하나 때문에 500을 내는 것보다 오늘을 보여주는 쪽이 낫다."""
    start, _ = kst_day_bounds_utc("not-a-date")
    assert naive_utc_to_kst(start).hour == 0


@pytest.mark.parametrize("seconds,expected", [
    (302542, "3일 12시간 2분"),
    (3661, "1시간 1분"),
    (59, "0분"),
    (None, None),
])
def test_format_uptime(seconds, expected):
    assert format_uptime(seconds) == expected


# --------------------------------------------------------------------------
# 오프라인 전이
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_offline_sweep_transitions_only_on_change(db_session, monkeypatch):
    """하트비트가 끊긴 기기를 offline으로 내린다. 'unknown'은 건드리지 않는다."""
    from app.services import monitor_service

    monkeypatch.setattr(monitor_service, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    sent = []

    async def fake_broadcast(kind, data, device_id=None):
        sent.append((kind, data["device_id"], data["status"]))

    monkeypatch.setattr(monitor_service.manager, "broadcast_event", fake_broadcast)

    now = utcnow()
    db_session.add_all([
        Device(id="live", name="a", ip="1.1.1.1", api_key_hash="x",
               status="online", last_seen=now),
        Device(id="dead", name="b", ip="1.1.1.2", api_key_hash="x",
               status="online", last_seen=now - timedelta(seconds=120)),
        # 아직 한 번도 하트비트를 받지 못한 기기 — offline과 구분해야 한다.
        Device(id="never", name="c", ip="1.1.1.3", api_key_hash="x",
               status="unknown", last_seen=None),
    ])
    db_session.commit()

    assert await sweep_offline_devices() == 1
    assert sent == [("device_status_change", "dead", "offline")]
    assert db_session.query(Device).filter_by(id="never").one().status == "unknown"

    # 두 번째 스윕에서는 전이가 없어야 한다 (같은 상태를 다시 쏘지 않는다).
    sent.clear()
    assert await sweep_offline_devices() == 0
    assert sent == []


# --------------------------------------------------------------------------
# 유동인구 수집기
# --------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_foot_traffic_puller_maps_pi_keys(db_session, monkeypatch):
    """Pi `points[].total_count` → 서버 `hourly_stats.foot_traffic_count`."""
    from app.services import foot_traffic_puller as puller

    monkeypatch.setattr(puller, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(puller, "BACKFILL_DAYS", 1)

    db_session.add(Device(id="d1", name="a", ip="192.168.1.101", api_key_hash="x"))
    db_session.add(Camera(id="dev-cam0", device_id="d1", port=8080))
    db_session.commit()

    respx.get(f"{PI}/api/stats/timeseries").mock(return_value=httpx.Response(200, json={
        "granularity": "hour",
        "points": [{"hour": h, "total_count": 0, "cane_user_count": 0} for h in range(14)]
                  + [{"hour": 14, "total_count": 96, "cane_user_count": 3}]
                  + [{"hour": h, "total_count": 0, "cane_user_count": 0} for h in range(15, 24)],
    }))

    assert await pull_once() == 1

    stat = db_session.query(HourlyStats).one()
    assert stat.foot_traffic_count == 96
    assert stat.cane_user_count == 3
    # ingest가 세는 값이라 수집기가 덮으면 안 된다.
    assert stat.detection_count == 0
    # 저장은 naive UTC — KST 14시는 UTC 05시다.
    assert stat.hour.hour == 5


@pytest.mark.asyncio
@respx.mock
async def test_foot_traffic_puller_skips_offline_devices(db_session, monkeypatch):
    from app.services import foot_traffic_puller as puller

    monkeypatch.setattr(puller, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    db_session.add(Device(id="d1", name="a", ip="192.168.1.101", api_key_hash="x"))
    db_session.add(Camera(id="dev-cam0", device_id="d1", port=8080))
    db_session.commit()

    respx.get(f"{PI}/api/stats/timeseries").mock(side_effect=httpx.ConnectError("offline"))

    assert await pull_once() == 0       # 예외가 새어 나오지 않아야 한다


@pytest.mark.asyncio
async def test_foot_traffic_puller_ignores_daily_granularity(db_session):
    """일별 응답을 시간별 표에 넣으면 안 된다."""
    from app.services.foot_traffic_puller import _apply_points

    data = {"granularity": "day", "points": [{"date": "2026-09-23", "total_count": 10}]}
    assert _apply_points(db_session, "d1", "c1", datetime(2026, 9, 23).date(), data) == 0
