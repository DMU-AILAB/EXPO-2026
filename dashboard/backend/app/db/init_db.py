import os
from sqlalchemy.orm import Session
from passlib.context import CryptContext
from ..database import engine, Base, SessionLocal
from ..models import User
from ..config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def init_db():
    # Extract path from sqlite URL
    db_path = settings.database_url.replace('sqlite:///', '')
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    
    # Create all tables
    Base.metadata.create_all(bind=engine)
    
    db: Session = SessionLocal()
    
    # Check if admin user already exists
    admin_user = db.query(User).filter(User.username == settings.initial_admin_username).first()
    if not admin_user:
        hashed_password = pwd_context.hash(settings.initial_admin_password)
        admin_user = User(
            username=settings.initial_admin_username,
            display_name="Admin_Ops",
            team="보행안전 통합팀",
            password_hash=hashed_password
        )
        db.add(admin_user)
        db.commit()
        db.refresh(admin_user)
        print(f"Created initial admin user: {settings.initial_admin_username}")
    else:
        print("Admin user already exists.")
    
    db.close()

if __name__ == "__main__":
    init_db()
