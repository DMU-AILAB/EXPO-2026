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
