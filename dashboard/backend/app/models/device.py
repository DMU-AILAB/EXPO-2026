from sqlalchemy import Column, String, DateTime, Float, Integer
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from ..database import Base

class Device(Base):
    __tablename__ = "devices"
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    ip = Column(String, nullable=False)
    location = Column(String)
    status = Column(String, default='unknown')
    api_key_hash = Column(String, nullable=False)
    config_etag = Column(String, default='')
    last_seen = Column(DateTime)
    created_at = Column(DateTime, server_default=func.now())
    
    cameras = relationship("Camera", back_populates="device", cascade="all, delete")
    rois = relationship("Roi", back_populates="device", cascade="all, delete")
    events = relationship("DetectionEvent", back_populates="device", cascade="all, delete")

class DeviceStatusCache(Base):
    __tablename__ = "device_status_cache"
    device_id = Column(String, primary_key=True)
    load_avg_1m = Column(Float)
    load_avg_5m = Column(Float)
    load_avg_15m = Column(Float)
    cpu_temp_c = Column(Float)
    memory_used_mb = Column(Float)
    memory_total_mb = Column(Float)
    uptime_seconds = Column(Integer)
    latency_ms = Column(Integer)
    npu_ms = Column(Integer)
    updated_at = Column(DateTime)
