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
    if "cameras" not in insp.get_table_names():
        return
    have = {c["name"] for c in insp.get_columns("cameras")}
    if "privacy_mask" not in have:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE cameras ADD COLUMN privacy_mask BOOLEAN DEFAULT 1"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
