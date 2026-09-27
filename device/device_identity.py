"""device_identity.py — 이 기기가 **누구인지**와 서버를 어디로 아는지.

Pi가 여러 대로 늘어나면서 필요해진 것이다. 지금까지 기기는 "이 Pi가 세상의 전부"라는
전제로 동작했고, 로컬 sqlite 어디에도 기기를 구분하는 값이 없었다. 서버가 이벤트를
모아 집계하려면 보내는 쪽이 자기 이름을 말할 수 있어야 한다.

**값의 주인은 서버다.** `device_id`와 `api_key`는 서버가 기기를 등록할 때(`POST
/api/devices`) 발급하고, 관리자가 대시보드에서 [등록]을 누르면 서버가 Pi의
`POST /api/identity`로 밀어 넣는다. Pi는 **그것을 받아 보관하고 요청에 실어 보내는
역할만** 한다 — 기기가 자기 id를 스스로 지어내면 서버 것과 두 체계가 생긴다.

`rois.json`·`camera_config.json`과 같은 성격의 **Pi 로컬 런타임 파일**이다:
rsync 배포 대상도 git 추적 대상도 아니다. 다만 저 둘과 달리 **원본은 서버에 있다**
(§13.0의 "설정 소유권"은 카메라/ROI 이야기이고, 신원은 반대 방향이다).

표준 라이브러리만 쓴다 — `device/`의 다른 순수 모듈과 같은 원칙이다.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

__all__ = [
    "APP_VERSION", "DeviceIdentity", "load_identity", "save_identity",
    "clear_identity", "default_path",
]

# 기기가 스스로 밝히는 버전. 서버의 기기 탐색(`GET /api/scan/{id}`)이 `version`
# 필드를 채우는 유일한 소스다 — 명세서에 "이 필드를 채울 소스가 현재 Pi에 없다"고
# 적힌 공백을 메운다. 배포 코드가 바뀌면 여기를 올린다.
APP_VERSION = "1.0.0"

_FILENAME = "device_identity.json"


@dataclass
class DeviceIdentity:
    """서버가 발급해 Pi에 심어 둔 신원."""

    device_id: str
    api_key: str
    server_url: str = ""          # 예: "http://192.168.0.50:8000" (끝 슬래시 없음)
    name: str = ""
    location: str = ""
    registered_at: str = ""       # ISO8601. 서버가 준 값을 그대로 보관한다

    def is_usable(self) -> bool:
        """서버로 무언가 보낼 수 있는 상태인가."""
        return bool(self.device_id and self.api_key and self.server_url)


def default_path(base: Path | None = None) -> Path:
    """신원 파일 경로. `rois.json`과 같은 자리(기기 루트)에 둔다.

    Pi는 평면 배치(`~/visionguide/`), PC 개발 트리는 저장소 루트다 — 호출부가
    이미 알고 있는 기준 경로를 넘겨준다.
    """
    return (base or Path.cwd()) / _FILENAME


def load_identity(path: Path) -> DeviceIdentity | None:
    """등록 전이거나 파일이 깨졌으면 None.

    **예외를 던지지 않는다.** 신원이 없다고 탐지·안내가 멈추면 안 된다 — 서버
    연동은 부가 기능이고, 기기 단독 동작이 이 시스템의 기본 전제다.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("device_id"):
        return None
    known = {f for f in DeviceIdentity.__dataclass_fields__}
    return DeviceIdentity(**{k: v for k, v in data.items() if k in known})


def save_identity(path: Path, ident: DeviceIdentity) -> None:
    """원자적 저장 — `rois.json`과 같은 방식.

    쓰는 도중 전원이 끊겨 반쪽짜리 파일이 남으면 기기가 신원을 잃는다.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(asdict(ident), f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    # api_key가 들어 있으므로 소유자만 읽게 한다. 실패해도(FAT 등) 저장 자체는 유효하다.
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def clear_identity(path: Path) -> bool:
    """등록 해제. 파일이 없으면 False."""
    try:
        Path(path).unlink()
        return True
    except OSError:
        return False
