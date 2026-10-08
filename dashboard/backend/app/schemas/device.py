from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from .camera import CameraResponse

class DeviceCreate(BaseModel):
    id: str
    name: str
    ip: str
    location: Optional[str] = None


class ProvisionDeviceRequest(BaseModel):
    pass

class UpdateRequest(BaseModel):
    include_models: bool = False


class BulkUpdateRequest(BaseModel):
    device_ids: list[str]
    include_models: bool = False


class RefreshAddressRequest(BaseModel):
    """비우면 신원이 있는 모든 기기."""
    device_ids: Optional[list[str]] = None


class DeviceUpdate(BaseModel):
    name: Optional[str] = None
    location: Optional[str] = None

class DeviceResponse(BaseModel):
    id: str
    name: str
    ip: str
    location: Optional[str] = None
    status: str
    last_seen: Optional[datetime] = None
    
    class Config:
        from_attributes = True

class DeviceDetailResponse(DeviceResponse):
    cameras: List[CameraResponse] = []
    
    class Config:
        from_attributes = True
