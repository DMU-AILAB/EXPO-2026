from sqlalchemy import Column, String, Integer, DateTime, Boolean
from sqlalchemy.sql import func
from ..database import Base

class Audio(Base):
    __tablename__ = "audios"

    filename = Column(String, primary_key=True, index=True)
    label = Column(String)
    size_bytes = Column(Integer)
    created_at = Column(DateTime, default=func.now())
