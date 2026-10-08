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
    # "auto"(기본)면 기기에 닿는 경로의 서버 IP를 그때그때 계산한다(services/server_address.py) —
    # Wi-Fi가 바뀌어도 .env를 고치지 않는다. 명시한 URL은 그대로 쓴다.
    # 기기에서 닿지 않는 주소면 Pi의 DeviceIdentity.is_usable()은 True여도 아무것도
    # 도착하지 않는다 — 진단 탭이 이를 잡아 준다.
    public_base_url: str = "auto"
    
    cors_origins: str = "http://localhost:5173"

    # 기기 업데이트 번들을 만들 **저장소 루트**. 비우면 이 파일 기준 상위 4단계(저장소 루트).
    # 번들 목록은 이 루트의 `Makefile` DEPLOY_PY가 단일 출처다.
    repo_root: str = ""
    
    initial_admin_username: str = "admin"
    initial_admin_password: str = "admin"
    
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
