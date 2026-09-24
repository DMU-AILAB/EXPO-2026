import sys
from pathlib import Path

# Pi 런타임(device/)을 import 경로에 넣는다 — 서버와 기기의 계약을 **같은 프로세스에서**
# 맞대어 보는 테스트(test_pi_contract.py) 때문이다. 스키마를 눈으로 비교하는 방식은
# 이미 한 번 실패했다: `confidence` 필수화 때문에 Pi가 보낸 이벤트가 전부 버려지는데도
# 백엔드 테스트 15건이 전부 통과했다.
# 루트 tests/conftest.py와 같은 이유로 device/를 평면 경로로 넣는다(Pi 배치와 동일).
_REPO_ROOT = Path(__file__).resolve().parents[3]
for _p in (_REPO_ROOT, _REPO_ROOT / "device"):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.database import Base, get_db
from app.config import settings
from app.models.user import User
from app.models.device import Device
from app.models.camera import Camera
from app.models.roi import Roi
from app.models.event import DetectionEvent
from app.models.token import TokenBlacklist
from app.models.schedule import ScheduledReboot
from app.models.stats import HourlyStats

from sqlalchemy.pool import StaticPool

# Use an in-memory SQLite database for testing
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, 
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(scope="function")
def db_session():
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)

@pytest.fixture(scope="function")
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass
    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    del app.dependency_overrides[get_db]

@pytest.fixture(scope="function")
def admin_user(db_session):
    from passlib.context import CryptContext
    from app.models import User
    
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    hashed_password = pwd_context.hash("test_password")
    user = User(
        username="admin",
        display_name="Test Admin",
        team="Test Team",
        password_hash=hashed_password
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user
