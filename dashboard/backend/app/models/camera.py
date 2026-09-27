from sqlalchemy import Column, String, Integer, Float, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from ..database import Base

class Camera(Base):
    __tablename__ = "cameras"
    id = Column(String, primary_key=True)
    device_id = Column(String, ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True)
    port = Column(Integer, nullable=False)
    capture_preset = Column(String, default='auto')
    fps = Column(Integer, default=10)
    model_variant = Column(String, default='v10_320')
    rotation = Column(Integer, default=0)
    require_person = Column(Boolean, default=True)
    is_active = Column(Boolean, default=True)
    
    conf_white_cane = Column(Float, default=0.55)
    conf_person = Column(Float, default=0.55)
    cooldown = Column(Float, default=10.0)
    debounce = Column(Float, default=0.5)

    device = relationship("Device", back_populates="cameras")
