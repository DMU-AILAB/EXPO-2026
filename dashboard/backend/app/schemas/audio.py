from pydantic import BaseModel
from typing import List, Optional

class AudioItem(BaseModel):
    filename: str
    label: Optional[str] = None
    size_bytes: int

class AudioListResponse(BaseModel):
    data: List[AudioItem]
    ok: bool = True

class AudioUploadResponse(BaseModel):
    data: AudioItem
    ok: bool = True
