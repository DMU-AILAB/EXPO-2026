from pydantic import BaseModel, validator, Field
from typing import List, Optional
import json
from datetime import datetime
from shapely.geometry import Polygon

class RoiBase(BaseModel):
    camera_id: str = Field(..., max_length=32, pattern=r"^[A-Za-z0-9_-]{1,32}$")
    name: str
    zone_type: str = "trigger"
    priority: int = Field(default=0, ge=0, le=10)
    announcement_text: str = ""
    audio_file: str = ""
    color: str = "#00FF00"
    is_active: bool = True
    polygon: List[List[float]]

    @validator('polygon')
    def validate_polygon(cls, v):
        if len(v) < 3:
            raise ValueError('Polygon must have at least 3 points')
        
        # Validate that points are [x, y]
        for pt in v:
            if len(pt) != 2:
                raise ValueError('Each point must have exactly 2 coordinates [x, y]')
            if not (0.0 <= pt[0] <= 1.0 and 0.0 <= pt[1] <= 1.0):
                raise ValueError('Coordinates must be between 0.0 and 1.0')
        
        # Shapely validation
        poly = Polygon(v)
        if not poly.is_valid or not poly.is_simple:
            raise ValueError('Polygon is invalid or self-intersecting')
            
        return v

class RoiCreate(RoiBase):
    pass

class RoiUpdate(BaseModel):
    name: Optional[str] = None
    zone_type: Optional[str] = None
    priority: Optional[int] = Field(None, ge=0, le=10)
    announcement_text: Optional[str] = None
    audio_file: Optional[str] = None
    color: Optional[str] = None
    is_active: Optional[bool] = None
    polygon: Optional[List[List[float]]] = None

    @validator('polygon')
    def validate_polygon(cls, v):
        if v is None:
            return v
        if len(v) < 3:
            raise ValueError('Polygon must have at least 3 points')
            
        for pt in v:
            if len(pt) != 2:
                raise ValueError('Each point must have exactly 2 coordinates [x, y]')
            if not (0.0 <= pt[0] <= 1.0 and 0.0 <= pt[1] <= 1.0):
                raise ValueError('Coordinates must be between 0.0 and 1.0')
                
        poly = Polygon(v)
        if not poly.is_valid or not poly.is_simple:
            raise ValueError('Polygon is invalid or self-intersecting')
            
        return v

class RoiResponse(RoiBase):
    id: int
    device_id: str
    created_at: datetime
    updated_at: datetime

    @validator('polygon', pre=True)
    def parse_polygon(cls, v):
        if isinstance(v, str):
            return json.loads(v)
        return v

    class Config:
        from_attributes = True
