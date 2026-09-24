from pydantic import BaseModel, Field, computed_field, field_validator
from typing import Optional
from datetime import datetime
from enum import Enum

from ..utils.timeutil import to_naive_utc


class EventType(str, Enum):
    DETECTION = "DETECTION"
    ANNOUNCEMENT = "ANNOUNCEMENT"
    OFFLINE = "OFFLINE"


class EventIngestRequest(BaseModel):
    """Pi의 `EventSender`가 실제로 보내는 형태에 맞춘다 (device/event_logger.py:182-187).

    **필수 필드를 함부로 늘리면 이벤트가 영구 소실된다.** Pi는 4xx(429 제외)를 fatal로
    보고 outbox에서 그 행을 즉시 삭제하므로(`event_logger.py:193-197`), 스키마가 조금만
    빡빡해도 재시도 없이 사라진다. 실제로 `confidence`를 필수로 두는 바람에 가상 지팡이
    박스로 발사된 안내가 전부 422로 버려지고 있었다.
    """

    camera_id: str = ""
    roi_id: Optional[int] = None
    # Pi의 outbox 컬럼 DEFAULT가 ''이라 빈 문자열로 올 수 있다 — 막지 않는다.
    roi_name: str = ""
    class_name: str = ""
    # 가상 지팡이 박스로 발사되면 Pi가 **키 자체를 생략**한다.
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    # 현재 Pi가 내는 것은 사실상 ANNOUNCEMENT 한 종류다.
    event_type: EventType = EventType.ANNOUNCEMENT
    timestamp: datetime

    @field_validator("timestamp")
    @classmethod
    def _normalize_ts(cls, v: datetime) -> datetime:
        # Pi는 ISO8601 UTC 'Z'로 보내 aware로 파싱된다. DB에는 naive UTC만 넣는다.
        return to_naive_utc(v)


class EventResponse(BaseModel):
    id: int
    device_id: str
    camera_id: str
    roi_id: Optional[int] = None
    roi_name: Optional[str] = None
    class_name: Optional[str] = None
    # ingest에서 생략될 수 있으므로 저장값도 NULL일 수 있다.
    confidence: Optional[float] = None
    event_type: str
    timestamp: datetime

    @computed_field
    @property
    def time_display(self) -> str:
        return self.timestamp.strftime("%H:%M:%S")

    class Config:
        from_attributes = True
