import uuid

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
    # 기기가 **서버로** 보내는 요청을 검증하는 용도 — 검증만 하면 되므로 해시로 둔다.
    api_key_hash = Column(String, nullable=False)
    # 서버가 **기기를** 호출할 때 제시하는 자격증명(`X-Device-Key`).
    #
    # 해시로 둘 수 없다 — 제시해야 하는 쪽은 원문이 필요하다. 방향이 반대라서 생기는
    # 비대칭이며, 저장은 평문이다. 그래서 `provision`으로 심을 때만 채워지고, 노출
    # 범위는 서버 DB 파일 하나로 제한된다(조회 API로 돌려주지 않는다).
    control_key = Column(String)
    # **빈 문자열이면 안 된다.** 변경 API가 If-Match를 요구하는데 갓 등록한 기기의
    # etag가 ''이면 클라이언트가 보낼 값이 없어 첫 ROI 저장부터 400이 난다.
    config_etag = Column(String, nullable=False, default=lambda: uuid.uuid4().hex)
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
    # Pi가 /proc/stat 두 시점 차이로 산출한다 (device/device_status.py의 CpuSampler).
    # 명세 §3은 "Pi가 CPU 사용률을 산출하지 않는다"고 적혀 있으나 이제 보내온다.
    cpu_percent = Column(Float)
    cpu_temp_c = Column(Float)
    memory_used_mb = Column(Float)
    memory_total_mb = Column(Float)
    uptime_seconds = Column(Integer)
    latency_ms = Column(Integer)
    npu_ms = Column(Integer)
    updated_at = Column(DateTime)
