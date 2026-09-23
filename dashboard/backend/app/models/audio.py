from sqlalchemy import Column, String, Integer, DateTime, ForeignKey
from sqlalchemy.sql import func
from ..database import Base

class Audio(Base):
    __tablename__ = "audios"

    filename = Column(String, primary_key=True, index=True)
    label = Column(String)
    size_bytes = Column(Integer)
    created_at = Column(DateTime, default=func.now())


class AudioDeployment(Base):
    """어떤 mp3가 어느 기기의 어느 절대경로에 놓였는지.

    ROI의 `audio_file`은 파일명이 아니라 **Pi 로컬 절대경로**여야 하고
    (`camera_live_pi.py`가 그 경로로 그대로 재생한다), 그 경로는 Pi의 업로드 API가
    정해서 돌려준다 — 같은 이름이 이미 있으면 `_1`, `_2`를 붙이므로 서버가 미리
    계산할 수 없다.

    이 표가 없으면 ROI를 저장할 때마다 mp3를 다시 올리게 된다. ROI 편집은 현장에서
    반복되는 작업이라 그때마다 수 MB를 밀어 넣는 것은 낭비다.
    """

    __tablename__ = "audio_deployments"

    device_id = Column(String, ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True)
    filename = Column(String, primary_key=True)
    pi_path = Column(String, nullable=False)
    uploaded_at = Column(DateTime, server_default=func.now())
