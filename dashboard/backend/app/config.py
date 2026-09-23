from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False
    
    jwt_secret_key: str = "changeme_secret_key_in_production"
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 24
    
    database_url: str = "sqlite:///./data/visionguide.db"
    audio_dir: str = "./data/audio"

    # 기기가 이벤트·하트비트를 보낼 **서버 자신의 주소**. 등록 시 Pi에 심어진다.
    # 비어 있으면 Pi의 DeviceIdentity.is_usable()이 False가 되어 아무것도 전송되지
    # 않는다(device/device_identity.py:49-51) — 그래서 등록 단계에서 막는다.
    public_base_url: str = "http://localhost:8000"
    
    cors_origins: str = "http://localhost:5173"
    
    initial_admin_username: str = "admin"
    initial_admin_password: str = "admin"
    
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
