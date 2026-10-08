"""bootstrap.py — Pi 부트스트랩 설치를 돕는 엔드포인트 (`deploy/install.sh`의 서버 쪽).

Pi에서 한 줄로 코드·유닛·sudoers를 설치하고 서버에 등록하게 한다. 관리자가 대시보드에서
**설치 토큰**을 만들면 그 토큰이 든 명령이 나오고, Pi는 같은 토큰으로 파일을 받고 등록한다.

- 토큰은 30분 유효, **메모리에만** 둔다(`--workers` 금지 전제 — 하트비트 버퍼와 같은 가정).
  내려받기는 만료 전까지 반복할 수 있고 **등록만 1회용**이다.
- 파일 내려받기는 JWT가 아니라 토큰으로 인증한다 — Pi에는 관리자 로그인이 없다.
- 등록은 서버가 기기에 신원을 직접 심는다(`_provision_device`): 키를 Pi에 돌려주지 않는다.
  기기 IP는 요청의 TCP 상대 주소(`peer_ipv4`)로 정한다 — 속일 수 없는 값이다.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import Device
from ..services.bundle_builder import (BundleError, build_assets, build_bundle, installer_script,
                                       normalize_models, repo_root)
from ..services.device_address import peer_ipv4
from ..services.server_address import public_url_default

router = APIRouter(prefix="/api/bootstrap", tags=["bootstrap"])

TOKEN_TTL_SEC = 30 * 60
_tokens: dict[str, dict] = {}          # token -> {"expires": monotonic 시각, "registered": bool}


def _now() -> float:
    return time.monotonic()


def issue_token() -> tuple[str, float]:
    _purge()
    token = secrets.token_urlsafe(16)
    _tokens[token] = {"expires": _now() + TOKEN_TTL_SEC, "registered": False}
    return token, TOKEN_TTL_SEC


def _purge() -> None:
    for t in [t for t, v in _tokens.items() if v["expires"] <= _now()]:
        _tokens.pop(t, None)


def require_token(token: Optional[str], *, consume: bool = False) -> None:
    """유효하지 않으면 401. `consume`이면 등록용으로 한 번만 통과한다."""
    _purge()
    entry = _tokens.get(token or "")
    if entry is None:
        raise HTTPException(status_code=401, detail={
            "error": "INVALID_TOKEN",
            "message": "설치 토큰이 없거나 만료됐습니다 — 대시보드에서 설치 명령을 새로 만드세요"})
    if consume:
        if entry["registered"]:
            raise HTTPException(status_code=401, detail={
                "error": "TOKEN_USED", "message": "이미 등록에 쓴 토큰입니다 — 새 설치 명령을 만드세요"})
        entry["registered"] = True


class RegisterRequest(BaseModel):
    token: str
    hostname: Optional[str] = None


def _server_url(request: Request) -> str:
    url = public_url_default()
    if not url:
        raise HTTPException(status_code=503, detail={
            "error": "NO_SERVER_ADDRESS", "message": "서버 주소를 정할 수 없습니다 (PUBLIC_BASE_URL을 확인하세요)"})
    return url


@router.post("/token")
async def create_token(request: Request, current_user=Depends(get_current_user)):
    """설치 토큰과 Pi에 붙여 넣을 명령을 만든다 (관리자)."""
    server = _server_url(request)
    token, ttl = issue_token()
    # 프로세스 치환 — 파이프(curl | bash)로 넘기면 스크립트가 stdin을 차지해 sudo 프롬프트가 꼬인다.
    url = f"{server}/api/bootstrap/install.sh?token={token}"
    return {"data": {
        "token": token, "expires_in": ttl, "server_url": server,
        "command": f'bash <(curl -fsSL "{url}")',
        "dry_run_command": f'bash <(curl -fsSL "{url}") --dry-run',
    }, "ok": True}


@router.get("/install.sh")
async def get_install_script(request: Request, token: str = ""):
    require_token(token)
    try:
        script = installer_script(_server_url(request))
    except BundleError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(script, media_type="text/x-shellscript; charset=utf-8")


@router.get("/self_update.py")
async def get_self_update(token: str = ""):
    require_token(token)
    path = repo_root() / "device" / "self_update.py"
    if not path.is_file():
        raise HTTPException(status_code=503, detail="device/self_update.py가 없습니다")
    return Response(path.read_bytes(), media_type="text/x-python; charset=utf-8")


@router.get("/bundle.tar.gz")
async def get_bundle(token: str = "", models: str = "", include_models: bool = False):
    """`models=none|default|all` — 신규 설치는 `default`(현행 모델 + 예비, 약 6MB).

    `include_models=true`는 예전 호출을 위한 별칭이다(`all`). 둘 다 없으면 코드만 보낸다.
    """
    require_token(token)
    try:
        mode = normalize_models(models or include_models)
    except BundleError as exc:
        raise HTTPException(status_code=400, detail={"error": "INVALID_MODELS", "message": str(exc)}) from exc
    try:
        bundle = build_bundle(mode)
    except BundleError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(bundle.data, media_type="application/gzip",
                    headers={"X-Bundle-Id": bundle.bundle_id})


@router.get("/assets.tar.gz")
async def get_assets(token: str = ""):
    require_token(token)
    try:
        assets = build_assets()
    except BundleError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(assets.data, media_type="application/gzip",
                    headers={"X-Bundle-Id": assets.bundle_id})


def _device_id_for(ip: str) -> str:
    # 기기 추가 화면(PiScan)의 기본 id 규칙과 같다 — 같은 기기를 두 경로로 등록해도 한 행이다.
    return "pi-" + ip.replace(".", "-")


@router.post("/register")
async def register(req: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    """설치 직후 기기가 호출한다 — 서버가 기기를 등록하고 신원을 직접 심는다.

    이미 같은 id의 기기가 있으면(재설치) **키를 새로 발급해 다시 심는다**. 토큰은 여기서 소모된다.
    """
    require_token(req.token, consume=True)
    ip = peer_ipv4(request)
    if ip is None:
        raise HTTPException(status_code=400, detail={
            "error": "UNTRUSTED_ADDRESS", "message": "사설 LAN 주소에서 온 요청만 등록할 수 있습니다"})

    # 순환 import를 피하려고 함수 안에서 가져온다 — devices 라우터가 서비스 계층을 쓴다.
    from .devices import _initial_sync, _provision_device

    device_id = _device_id_for(ip)
    raw_api_key = f"vg_{secrets.token_urlsafe(16)}"
    api_key_hash = hashlib.sha256(raw_api_key.encode()).hexdigest()
    name = re.sub(r"[^\w.\- ]", "", req.hostname or "")[:80] or device_id

    device = db.query(Device).filter(Device.id == device_id).first()
    if device is None:
        device = Device(id=device_id, name=name, ip=ip, api_key_hash=api_key_hash)
        db.add(device)
    else:
        device.ip = ip
        device.api_key_hash = api_key_hash
    db.commit()

    provisioned, reason = await _provision_device(device, raw_api_key)
    if provisioned:
        db.commit()                                 # control_key는 기기가 받아들인 뒤에만 저장된다
    synced = await _initial_sync(db, device) if provisioned else False
    return {"data": {"device_id": device.id, "provisioned": provisioned,
                     "provision_error": reason, "synced": synced}, "ok": True}
