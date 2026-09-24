"""이벤트 ingest/조회.

ingest의 실패 모드가 특별하다 — Pi는 4xx(429 제외)를 "다시 보내도 소용없다"로 읽고
outbox에서 그 행을 **삭제**한다(`device/event_logger.py:193-197`). 그래서 여기서
확인하는 것은 "거부되는가"가 아니라 **"거부되면 안 되는 것이 통과하는가"**다.
"""

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from app.models.device import Device
from app.models.event import DetectionEvent
from app.models.stats import HourlyStats

RAW_KEY = "vg_test_key_123"


@pytest.fixture
def device(db_session):
    d = Device(id="cam-entrance-01", name="정문", ip="192.168.1.101",
               api_key_hash=hashlib.sha256(RAW_KEY.encode()).hexdigest())
    db_session.add(d)
    db_session.commit()
    return d


@pytest.fixture
def auth(client, admin_user):
    token = client.post("/api/auth/login", json={
        "username": "admin", "password": "test_password"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _payload(**overrides):
    payload = {
        "camera_id": "dev-cam0",
        "roi_name": "정문 진입 구역",
        "class_name": "white_cane",
        "event_type": "ANNOUNCEMENT",
        "timestamp": "2026-09-23T05:32:07Z",
    }
    payload.update(overrides)
    return payload


def test_ingest_unauthorized(client):
    res = client.post("/api/events/ingest", json=_payload(),
                      headers={"X-API-Key": "invalid-key"})
    assert res.status_code == 401


def test_ingest_accepts_event_without_confidence(client, device, db_session):
    """가상 지팡이 박스로 발사된 안내 — Pi가 `confidence` 키를 생략한다.

    이게 거부되면 그 이벤트는 **재시도 없이 영구 소실**된다.
    """
    res = client.post("/api/events/ingest", json=_payload(),
                      headers={"X-API-Key": RAW_KEY})

    assert res.status_code == 201, res.text
    saved = db_session.query(DetectionEvent).one()
    assert saved.confidence is None
    assert saved.device_id == "cam-entrance-01"


def test_ingest_accepts_empty_strings(client, device):
    """Pi outbox 컬럼의 DEFAULT는 ''이다."""
    res = client.post("/api/events/ingest",
                      json=_payload(camera_id="", roi_name="", class_name=""),
                      headers={"X-API-Key": RAW_KEY})
    assert res.status_code == 201


def test_ingest_normalizes_timestamp_to_naive_utc(client, device, db_session):
    """한 컬럼에 aware/naive가 섞이면 통계 경계 비교가 조용히 어긋난다."""
    client.post("/api/events/ingest", json=_payload(),
                headers={"X-API-Key": RAW_KEY})

    saved = db_session.query(DetectionEvent).one()
    assert saved.timestamp.tzinfo is None
    assert saved.timestamp.hour == 5


def test_ingest_bumps_hourly_detection_count(client, device, db_session):
    for _ in range(3):
        client.post("/api/events/ingest", json=_payload(),
                    headers={"X-API-Key": RAW_KEY})

    stat = db_session.query(HourlyStats).one()
    assert stat.detection_count == 3
    # 유동인구 계열은 수집기가 채운다 — ingest가 덮으면 안 된다.
    assert stat.foot_traffic_count == 0


def test_ingest_rate_limit_returns_429_with_retry_after(client, device, monkeypatch):
    """429는 Pi가 **재시도하는** 유일한 4xx다 — 다른 코드로 내면 이벤트가 버려진다."""
    from app.routers import events as events_router

    monkeypatch.setattr(events_router, "_RATE_LIMIT_STORE", {})
    monkeypatch.setattr(events_router, "RATE_LIMIT_MAX_REQUESTS", 2)

    for _ in range(2):
        assert client.post("/api/events/ingest", json=_payload(),
                           headers={"X-API-Key": RAW_KEY}).status_code == 201

    res = client.post("/api/events/ingest", json=_payload(),
                      headers={"X-API-Key": RAW_KEY})
    assert res.status_code == 429
    assert res.headers.get("Retry-After")
    assert res.json()["error"] == "RATE_LIMIT_EXCEEDED"


# --------------------------------------------------------------------------
# 조회
# --------------------------------------------------------------------------

def test_get_events_rejects_range_over_90_days(client, auth, device):
    # `+00:00`이 URL에서 공백이 되지 않도록 params로 넘긴다.
    res = client.get("/api/events", headers=auth, params={
        "start": (datetime.now(timezone.utc) - timedelta(days=100)).isoformat(),
        "end": datetime.now(timezone.utc).isoformat(),
    })

    assert res.status_code == 400
    assert res.json()["error"] == "DATE_RANGE_TOO_LARGE"


def test_get_events_checks_range_when_only_start_is_given(client, auth, device):
    """이전에는 start·end가 둘 다 있을 때만 검사해 한 줄로 전량 조회가 가능했다."""
    res = client.get("/api/events", headers=auth, params={
        "start": (datetime.now(timezone.utc) - timedelta(days=200)).isoformat(),
    })

    assert res.status_code == 400
    assert res.json()["error"] == "DATE_RANGE_TOO_LARGE"


def test_get_events_without_params_works(client, auth, device):
    """목록 화면의 기본 호출에는 인자가 없다 — 거절하면 첫 화면부터 400이 난다."""
    res = client.get("/api/events", headers=auth)
    assert res.status_code == 200
    assert res.json()["ok"] is True


def test_get_events_returns_time_display(client, auth, device):
    client.post("/api/events/ingest", json=_payload(confidence=0.94),
                headers={"X-API-Key": RAW_KEY})

    res = client.get("/api/events", headers=auth)

    row = res.json()["data"][0]
    assert row["time_display"] == "05:32:07"
    assert row["confidence"] == pytest.approx(0.94)


def test_get_events_shows_slightly_future_timestamps(client, auth, device):
    """기기 시계가 조금 빨라도 최신 이벤트가 목록에서 사라지면 안 된다.

    타임스탬프는 Pi가 찍는다 — 상한을 `now`로 걸면 시계가 몇 초 빠른 기기의
    이벤트가 통째로 보이지 않는다.
    """
    future = (datetime.now(timezone.utc) + timedelta(minutes=5))
    client.post("/api/events/ingest",
                json=_payload(timestamp=future.isoformat().replace("+00:00", "Z")),
                headers={"X-API-Key": RAW_KEY})

    res = client.get("/api/events", headers=auth)
    assert res.json()["total"] == 1
