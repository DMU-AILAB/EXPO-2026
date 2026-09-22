from pydantic import BaseModel
from typing import List, Optional

class MostActiveDevice(BaseModel):
    id: str
    name: str
    location: Optional[str] = None
    today_detections: int

class StatsSummaryData(BaseModel):
    total_foot_traffic_today: int
    cane_user_count_today: int
    total_detections_today: int
    avg_confidence: float
    active_streams: int
    total_streams: int
    online_device_count: int
    total_device_count: int
    online_rate: float
    active_alert_count: int
    avg_cpu_temperature: float
    most_active_device: Optional[MostActiveDevice] = None

class StatsSummaryResponse(BaseModel):
    data: StatsSummaryData
    ok: bool = True

class DeviceStatsItem(BaseModel):
    device_id: str
    name: str
    foot_traffic: int
    cane_users: int
    detections: int

class DeviceStatsResponse(BaseModel):
    data: List[DeviceStatsItem]
    ok: bool = True

class TimeSeriesItem(BaseModel):
    hour: Optional[int] = None
    date: Optional[str] = None
    total_count: int
    cane_user_count: int
    detections: int

class TimeSeriesResponse(BaseModel):
    data: List[TimeSeriesItem]
    period: str
    granularity: str
    ok: bool = True
