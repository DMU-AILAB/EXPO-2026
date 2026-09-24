from sqlalchemy import Column, String, DateTime
from ..database import Base

class TokenBlacklist(Base):
    __tablename__ = "token_blacklist"
    jti = Column(String, primary_key=True)
    expires_at = Column(DateTime, nullable=False)
