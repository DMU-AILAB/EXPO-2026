"""구조물 수집(폐장 시간 관측)의 일괄 실행·예약.

수집 자체는 기기 카메라 포트의 `/calibrate/start`가 하고, 여기는 **언제·어느 카메라에**
시킬지만 정한다. 수집 결과는 후보일 뿐이며 적용은 여전히 운영자가 기기 상세의
"오탐 관리" 탭에서 고른다 — 예약이 자동 적용 경로가 되면 안 된다.
"""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.sql import func

from ..database import Base


class CalibrationSchedule(Base):
    __tablename__ = "calibration_schedules"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False, default="")
    days = Column(String, nullable=False)          # JSON 배열, 0=일 … 6=토 (예약 재부팅과 같은 체계)
    hour = Column(Integer, nullable=False)
    minute = Column(Integer, nullable=False, default=0)
    seconds = Column(Integer, nullable=False, default=300)
    target_mode = Column(String, nullable=False, default="all")   # all | model | devices
    targets = Column(String, nullable=False, default="[]")        # JSON 배열 — 모델 키 또는 기기 id
    is_enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())


class CalibrationRun(Base):
    """카메라 한 대에 수집을 시킨 기록. 성공 여부는 **시작 요청**의 결과다 —
    수집이 끝났는지는 기기의 `/calibrate/status`가 안다."""
    __tablename__ = "calibration_runs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(String, nullable=False, index=True)
    schedule_id = Column(Integer, ForeignKey("calibration_schedules.id", ondelete="SET NULL"))
    device_id = Column(String, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    camera_id = Column(String, nullable=False)
    model_variant = Column(String)
    seconds = Column(Integer, nullable=False)
    started_at = Column(DateTime, server_default=func.now(), index=True)
    ok = Column(Boolean, nullable=False)
    error = Column(String)
