import uuid
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from passlib.context import CryptContext
import jwt

from ..database import get_db
from ..config import settings
from ..models import User, TokenBlacklist
from ..schemas.auth import LoginRequest, LoginResponse, UserResponse
from ..deps import get_current_user, security

router = APIRouter(prefix="/api/auth", tags=["auth"])
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def create_access_token(data: dict, expires_delta: timedelta):
    to_encode = data.copy()
    expire = datetime.utcnow() + expires_delta
    to_encode.update({"exp": expire, "jti": str(uuid.uuid4())})
    encoded_jwt = jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return encoded_jwt, to_encode["exp"]

@router.post("/login", response_model=LoginResponse)
def login(request: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == request.username).first()
    
    if not user:
        raise HTTPException(status_code=401, detail={"error": "INVALID_CREDENTIALS", "message": "아이디 또는 비밀번호가 올바르지 않습니다.", "ok": False})
        
    if user.locked_until and user.locked_until > datetime.utcnow():
        raise HTTPException(status_code=423, detail={"error": "ACCOUNT_LOCKED", "message": "로그인 실패 5회 초과. 30분 후 다시 시도하세요.", "ok": False})
        
    if not pwd_context.verify(request.password, user.password_hash):
        user.login_fail_count += 1
        if user.login_fail_count >= 5:
            user.locked_until = datetime.utcnow() + timedelta(minutes=30)
        db.commit()
        if user.login_fail_count >= 5:
            raise HTTPException(status_code=423, detail={"error": "ACCOUNT_LOCKED", "message": "로그인 실패 5회 초과. 30분 후 다시 시도하세요.", "ok": False})
        else:
            raise HTTPException(status_code=401, detail={"error": "INVALID_CREDENTIALS", "message": "아이디 또는 비밀번호가 올바르지 않습니다.", "ok": False})

    # Reset fail count on success
    user.login_fail_count = 0
    user.locked_until = None
    db.commit()

    access_token_expires = timedelta(hours=settings.jwt_expire_hours)
    access_token, expire = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": int(access_token_expires.total_seconds())
    }

@router.post("/logout", status_code=204)
def logout(credentials = Depends(security), db: Session = Depends(get_db)):
    token = credentials.credentials
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        jti = payload.get("jti")
        exp = payload.get("exp")
        
        if jti and exp:
            blacklisted = TokenBlacklist(jti=jti, expires_at=datetime.utcfromtimestamp(exp))
            db.add(blacklisted)
            db.commit()
    except Exception:
        pass # If token is invalid, just ignore on logout
    
    return

@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    return current_user
