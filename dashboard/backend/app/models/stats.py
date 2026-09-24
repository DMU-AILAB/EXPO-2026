from sqlalchemy import Column, String, Integer, DateTime
from ..database import Base

class HourlyStats(Base):
    __tablename__ = "hourly_stats"
    device_id = Column(String, primary_key=True)
    camera_id = Column(String, primary_key=True)
    hour = Column(DateTime, primary_key=True)
    foot_traffic_count = Column(Integer, default=0)
    cane_user_count = Column(Integer, default=0)
    detection_count = Column(Integer, default=0)
