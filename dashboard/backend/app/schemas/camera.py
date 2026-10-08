from pydantic import BaseModel, Field, validator
from typing import Optional, Union, Dict
from enum import Enum

class CapturePreset(str, Enum):
    AUTO = "auto"
    RES_320x180 = "320x180"
    RES_320x240 = "320x240"
    RES_480x270 = "480x270"
    RES_480x360 = "480x360"
    RES_480x480 = "480x480"
    RES_640x360 = "640x360"
    RES_640x480 = "640x480"
    RES_848x480 = "848x480"
    RES_800x600 = "800x600"
    RES_960x540 = "960x540"

class ModelVariant(str, Enum):
    # ★ device/camera_config.MODEL_VARIANTS와 같아야 한다 — 계약 테스트가 대조한다.
    V4_320 = "v4_320"
    V10_320 = "v10_320"
    V15_320 = "v15_320"

class CameraUpdate(BaseModel):
    capture_preset: Optional[CapturePreset] = None
    fps: Optional[int] = None
    model_variant: Optional[ModelVariant] = None
    rotation: Optional[int] = None
    require_person: Optional[bool] = None
    privacy_mask: Optional[bool] = None

class PrivacyMaskUpdate(BaseModel):
    enabled: bool

class CameraResponse(BaseModel):
    id: str
    port: int
    capture_preset: str
    fps: int
    model_variant: str
    rotation: int
    require_person: bool
    privacy_mask: bool = True
    is_active: bool
    
    class Config:
        from_attributes = True

class ConfSchema(BaseModel):
    white_cane: float = Field(..., ge=0.0, le=1.0)
    person: float = Field(..., ge=0.0, le=1.0)

class DetectionParamsUpdate(BaseModel):
    # conf can be a scalar float or a dict. We normalize it to a dict.
    conf: Optional[Union[float, ConfSchema]] = None
    cooldown: Optional[float] = Field(None, ge=0.0)
    debounce: Optional[float] = Field(None, ge=0.0)

    @validator('conf')
    def validate_conf(cls, v):
        if v is None:
            return v
        if isinstance(v, float) or isinstance(v, int):
            return ConfSchema(white_cane=float(v), person=float(v))
        return v

class DetectionParamsResponse(BaseModel):
    conf: ConfSchema
    cooldown: float
    debounce: float

    class Config:
        from_attributes = True
