"""서버 스키마와 Pi가 실제로 보내는 페이로드를 **같은 프로세스에서** 맞대어 본다.

이 파일이 없어서 생긴 사고가 두 건이다.

1. `EventIngestRequest.confidence`가 필수라 **가상 지팡이 박스로 발사된 안내**가 전부
   422로 거부됐다. Pi는 4xx(429 제외)를 fatal로 보고 outbox에서 그 행을 즉시 지우므로
   (`device/event_logger.py:193-197`) 재시도 없이 영구 소실이었다.
2. `HeartbeatPayload`의 `memory`·`cpu_temp_c`·`load_avg`가 필수·non-null이라, Pi가
   `/sys` 읽기에 실패해 `None`을 보내면 422였다.

둘 다 백엔드 테스트 15건이 **전부 통과하는 상태**에서 일어났다. 스키마를 눈으로
비교하는 방식으로는 못 잡는다 — Pi의 빌더를 실제로 돌려서 넣어봐야 한다.
"""

import pytest

from app.schemas.event import EventIngestRequest
from app.routers.devices import HeartbeatPayload
from app.services.pi_client import _looks_like_device_status


# --------------------------------------------------------------------------
# 이벤트 ingest
# --------------------------------------------------------------------------

def _pi_event_payload(confidence=None):
    """`EventSender._flush_once()`가 만드는 바디 (device/event_logger.py:182-187).

    outbox 컬럼의 DEFAULT가 ''이라 문자열 필드는 빈 값으로 올 수 있고,
    `confidence`는 NULL이면 **키 자체가 빠진다**.
    """
    payload = {
        "camera_id": "dev-cam0",
        "roi_name": "정문 진입 구역",
        "class_name": "white_cane",
        "event_type": "ANNOUNCEMENT",
        "timestamp": "2026-09-23T05:32:07Z",
    }
    if confidence is not None:
        payload["confidence"] = round(float(confidence), 4)
    return payload


def test_ingest_accepts_payload_with_confidence():
    req = EventIngestRequest(**_pi_event_payload(0.9412))
    assert req.confidence == pytest.approx(0.9412)


def test_ingest_accepts_payload_without_confidence():
    """가상 지팡이 박스로 발사된 이벤트 — 이게 거부되면 안내 기록이 영구 소실된다."""
    req = EventIngestRequest(**_pi_event_payload())
    assert req.confidence is None


def test_ingest_accepts_empty_strings():
    """outbox DEFAULT ''가 그대로 올라오는 경우."""
    req = EventIngestRequest(
        camera_id="", roi_name="", class_name="",
        event_type="ANNOUNCEMENT", timestamp="2026-09-23T05:32:07Z",
    )
    assert req.camera_id == ""


def test_ingest_accepts_rf_announcement_class():
    """RF 트리거 경로는 class_name을 'announcement'로 적는다 (device/announcement_router.py)."""
    payload = _pi_event_payload()
    payload["class_name"] = "announcement"
    assert EventIngestRequest(**payload).class_name == "announcement"


def test_ingest_timestamp_is_normalized_to_naive_utc():
    """Pi는 'Z'를 달고 보낸다 — DB에는 naive UTC만 들어가야 한 컬럼이 섞이지 않는다."""
    req = EventIngestRequest(**_pi_event_payload())
    assert req.timestamp.tzinfo is None
    assert req.timestamp.hour == 5


# --------------------------------------------------------------------------
# 하트비트
# --------------------------------------------------------------------------

def _pi_heartbeat_payload():
    """`HeartbeatSender.build_payload()`의 전체 형태 (device/event_logger.py:278-327)."""
    return {
        "status": "online",
        "load_avg": [0.82, 0.74, 0.69],
        "cpu_percent": 38.5,
        "cpu_temp_c": 51.2,
        "memory": {"used_mb": 1228.8, "total_mb": 4096.0},
        "uptime_seconds": 302542.0,
        "latency_ms": 11,
        "npu_ms": 48,
        "cameras": [
            {"id": "dev-cam0", "is_streaming": True, "configured": True,
             "current_alert": None, "today_detections": 27},
        ],
    }


def test_heartbeat_accepts_full_payload():
    hb = HeartbeatPayload(**_pi_heartbeat_payload())
    assert hb.cpu_percent == pytest.approx(38.5)


@pytest.mark.parametrize("key", [
    "load_avg", "cpu_percent", "cpu_temp_c", "memory",
    "uptime_seconds", "latency_ms", "npu_ms",
])
def test_heartbeat_accepts_none_for_every_optional_key(key):
    """Pi는 읽기에 실패한 항목을 None으로 두고 그대로 보낸다 — 전부 통과해야 한다."""
    payload = _pi_heartbeat_payload()
    payload[key] = None
    HeartbeatPayload(**payload)


def test_heartbeat_accepts_payload_from_non_pi_host():
    """개발 PC처럼 /sys가 없는 환경에서는 거의 전부 None이 된다."""
    HeartbeatPayload(**{
        "status": "online", "load_avg": None, "cpu_percent": None,
        "cpu_temp_c": None, "memory": None, "uptime_seconds": None,
        "latency_ms": None, "npu_ms": None, "cameras": [],
    })


def test_heartbeat_accepts_missing_cpu_percent():
    """cpu_percent는 Pi가 나중에 추가한 필드 — 구버전 기기는 보내지 않는다."""
    payload = _pi_heartbeat_payload()
    del payload["cpu_percent"]
    assert HeartbeatPayload(**payload).cpu_percent is None


def test_heartbeat_flush_survives_all_none(monkeypatch):
    """None투성이 하트비트가 플러시 루프를 죽이지 않아야 한다.

    죽으면 그 주기의 **모든 기기** 상태가 DB에 남지 않는다.
    """
    from app.services import heartbeat_service as hs

    payload = {"load_avg": None, "memory": None}
    assert hs._load_avg_at(payload, 0) is None
    assert hs._load_avg_at(payload, 2) is None
    assert hs._memory_at(payload, "used_mb") is None
    # 길이가 모자란 경우도 있다(방어적으로 잘린 값)
    assert hs._load_avg_at({"load_avg": [0.5]}, 1) is None


# --------------------------------------------------------------------------
# 기기 판별
# --------------------------------------------------------------------------

def test_device_status_schema_matches_pi_reader():
    """`device_status.read_status()`가 내는 키로 탐색 판별식이 성립해야 한다.

    판별을 "404가 아니면 있음"으로 하면 캡티브 포털 catch-all 때문에 오판한다
    (명세 §12).
    """
    from device_status import read_status

    status = read_status()
    assert _looks_like_device_status(status), status


def test_device_status_keys_are_exactly_five():
    """이 스키마가 곧 탐색 계약이라 **늘리면 안 된다** (roi_editor/server.py:140-143)."""
    from device_status import read_status

    assert set(read_status().keys()) == {
        "uptime_seconds", "cpu_temp_c", "load_avg", "mem_used_mb", "mem_total_mb",
    }
