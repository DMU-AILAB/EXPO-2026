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

    # Pi가 서버를 스스로 찾아 등록하게 한다(UDP 발견 + 등록 요청). 끄면 응답기도 침묵하고 등록 요청은
    # 403이다. 신원이 없는 기기는 즉시 등록하지만 **다른 서버 소속 기기는 승인 대기**로 둔다.
    # 통제된 LAN을 전제로 한다 — 공용 망에서는 끈다.
    auto_enroll: bool = True
    # 발견 응답 UDP 포트. device/server_discovery.py의 DISCOVERY_PORT와 같아야 한다(계약 테스트가 대조).
    discovery_port: int = 48555

    # 빌드된 프런트(`npm run build`의 dist). 비우면 `<repo>/dashboard/frontend/dist`.
    # index.html이 있을 때만 백엔드가 같은 포트로 서빙한다 — 없으면 개발 모드(vite dev) 그대로.
    frontend_dist: str = ""

    initial_admin_username: str = "admin"
    initial_admin_password: str = "admin"
    
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
