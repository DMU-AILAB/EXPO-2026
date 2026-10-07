from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from .config import settings

engine = create_engine(
    settings.database_url, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def ensure_columns() -> None:
    """이미 운영 중인 DB에 나중에 추가된 컬럼을 채운다.

    `create_all`은 **없는 테이블만** 만들고 기존 테이블에 컬럼을 더하지 않는다. 컬럼이
    없는 채로 기동하면 모델이 그 컬럼을 SELECT에 넣어 카메라 조회가 전부 실패한다.
    """
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    with engine.begin() as conn:
        if "cameras" in tables:
            have = {c["name"] for c in insp.get_columns("cameras")}
            if "privacy_mask" not in have:
                conn.execute(text("ALTER TABLE cameras ADD COLUMN privacy_mask BOOLEAN DEFAULT 1"))
        if "devices" in tables:
            have = {c["name"] for c in insp.get_columns("devices")}
            if "esp32_device_id" not in have:
                conn.execute(text("ALTER TABLE devices ADD COLUMN esp32_device_id VARCHAR"))
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_devices_esp32_device_id "
                "ON devices (esp32_device_id)"
            ))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
