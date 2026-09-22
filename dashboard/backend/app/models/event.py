from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from ..database import Base

class DetectionEvent(Base):
    __tablename__ = "detection_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(String, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    camera_id = Column(String, nullable=False)
    roi_id = Column(Integer)
    roi_name = Column(String)
    class_name = Column(String, default='white_cane')
    confidence = Column(Float)
    event_type = Column(String, default='DETECTION')
    timestamp = Column(DateTime, nullable=False, index=True)
    
    device = relationship("Device", back_populates="events")
