from pydantic import BaseModel
from typing import Optional

class LoginRequest(BaseModel):
    username: str
    password: str

class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int

class UserResponse(BaseModel):
    id: int
    username: str
    display_name: Optional[str] = None
    team: Optional[str] = None
    
    class Config:
        from_attributes = True

class GlobalResponse(BaseModel):
    error: Optional[str] = None
    message: Optional[str] = None
    ok: bool
