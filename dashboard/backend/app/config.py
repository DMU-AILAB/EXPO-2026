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
    
    cors_origins: str = "http://localhost:5173"
    
    initial_admin_username: str = "admin"
    initial_admin_password: str = "admin"
    
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
