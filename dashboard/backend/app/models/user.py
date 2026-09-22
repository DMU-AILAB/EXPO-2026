from sqlalchemy import Column, Integer, String, DateTime
from ..database import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    display_name = Column(String)
    team = Column(String)
    password_hash = Column(String, nullable=False)
    login_fail_count = Column(Integer, default=0)
    locked_until = Column(DateTime, nullable=True)
