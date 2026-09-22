from pydantic import BaseModel, Field, computed_field
from typing import Optional
from datetime import datetime
from enum import Enum

class EventType(str, Enum):
    DETECTION = "DETECTION"
    ANNOUNCEMENT = "ANNOUNCEMENT"
    OFFLINE = "OFFLINE"

class EventIngestRequest(BaseModel):
    camera_id: str
    roi_id: Optional[int] = None
    roi_name: str
    class_name: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    event_type: EventType
    timestamp: datetime

class EventResponse(BaseModel):
    id: int
    device_id: str
    camera_id: str
    roi_id: Optional[int] = None
    roi_name: str
    class_name: str
    confidence: float
    event_type: str
    timestamp: datetime

    @computed_field
    @property
    def time_display(self) -> str:
        return self.timestamp.strftime("%H:%M:%S")

    class Config:
        from_attributes = True
