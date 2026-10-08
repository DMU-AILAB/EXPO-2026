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


class ModelRolloutRequest(BaseModel):
    """기존 기기의 카메라 모델을 일괄 변경한다."""
    to: str
    device_ids: Optional[list[str]] = None          # 비우면 전체
    from_variants: Optional[list[str]] = None       # 지정하면 이 모델을 쓰는 카메라만(예: 오래된 v4 이하)
    push_models: bool = False                       # 기기에 가중치가 없으면 코드 번들(기본 모델 포함)을 먼저 올린다
    verify: bool = True                             # 바꾼 뒤 FPS를 확인하고 미달이면 되돌린다
    dry_run: bool = False                           # 바꾸지 않고 계획만 본다


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
