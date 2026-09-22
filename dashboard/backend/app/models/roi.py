from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from ..database import Base

class Roi(Base):
    __tablename__ = "rois"
    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(String, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    camera_id = Column(String, nullable=False)
    name = Column(String, nullable=False)
    zone_type = Column(String, default='trigger')
    priority = Column(Integer, default=0)
    announcement_text = Column(String, default='')
    audio_file = Column(String, default='')
    color = Column(String(7), default='#00FF00') # Add color column for UI
    polygon = Column(String, nullable=False) # JSON array
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    device = relationship("Device", back_populates="rois")
