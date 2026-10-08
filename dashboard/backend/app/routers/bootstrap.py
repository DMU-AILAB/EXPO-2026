"""bootstrap.py — Pi 부트스트랩 설치를 돕는 엔드포인트 (`deploy/install.sh`의 서버 쪽).

Pi에서 한 줄로 코드·유닛·sudoers를 설치하고 서버에 등록하게 한다. 관리자가 대시보드에서
**설치 토큰**을 만들면 그 토큰이 든 명령이 나오고, Pi는 같은 토큰으로 파일을 받고 등록한다.

- 토큰은 30분 유효, **메모리에만** 둔다(`--workers` 금지 전제 — 하트비트 버퍼와 같은 가정).
  내려받기는 만료 전까지 반복할 수 있고 **등록만 1회용**이다.
- 파일 내려받기는 JWT가 아니라 토큰으로 인증한다 — Pi에는 관리자 로그인이 없다. **`AUTO_ENROLL`이
  켜져 있으면 사설 LAN에서 온 요청은 토큰 없이도 받을 수 있다**(`authorize`) — 토큰을 복사해 붙이지
  않고 `install.sh`를 받게 하려는 것이다. 토큰이 **있으면** 언제나 기존대로 엄격히 검증한다.
- 등록은 서버가 기기에 신원을 직접 심는다(`_provision_device`): 키를 Pi에 돌려주지 않는다.
  기기 IP는 요청의 TCP 상대 주소(`peer_ipv4`)로 정한다 — 속일 수 없는 값이다.
"""

from __future__ import annotations

import secrets
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import get_current_user
from ..services.bundle_builder import (BundleError, build_assets, build_bundle, installer_script,
                                       normalize_models, repo_root)
from ..services.device_address import lan_ipv4, peer_ipv4
from ..services.enrollment import approve_pending, enroll_device, handle_enroll
from ..services.pending_enrollments import PendingStore, pending_store
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


def authorize(request: Request, token: Optional[str]) -> None:
    """파일 내려받기 인증 — 토큰이 있으면 그것을 엄격히 검증하고, 없으면 `AUTO_ENROLL` + 사설 LAN이면 통과.

    토큰이 **틀린** 경우를 LAN이라는 이유로 통과시키지 않는다 — 만료된 명령을 붙여 넣었다면 그
    사실을 알려야 한다.
    """
    if token:
        require_token(token)
        return
    if settings.auto_enroll and peer_ipv4(request) is not None:
        return
    require_token(token)            # 항상 401 — 토큰이 없고 자동 등록 대상도 아니다


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
    authorize(request, token)
    try:
        script = installer_script(_server_url(request))
    except BundleError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(script, media_type="text/x-shellscript; charset=utf-8")


@router.get("/self_update.py")
async def get_self_update(request: Request, token: str = ""):
    authorize(request, token)
    path = repo_root() / "device" / "self_update.py"
    if not path.is_file():
        raise HTTPException(status_code=503, detail="device/self_update.py가 없습니다")
    return Response(path.read_bytes(), media_type="text/x-python; charset=utf-8")


@router.get("/bundle.tar.gz")
async def get_bundle(request: Request, token: str = "", models: str = "", include_models: bool = False):
    """`models=none|default|all` — 신규 설치는 `default`(현행 모델 + 예비, 약 6MB).

    `include_models=true`는 예전 호출을 위한 별칭이다(`all`). 둘 다 없으면 코드만 보낸다.
    """
    authorize(request, token)
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
async def get_assets(request: Request, token: str = ""):
    authorize(request, token)
    try:
        assets = build_assets()
    except BundleError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(assets.data, media_type="application/gzip",
                    headers={"X-Bundle-Id": assets.bundle_id})


@router.post("/register")
async def register(req: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    """설치 직후 기기가 호출한다 — 서버가 기기를 등록하고 신원을 직접 심는다.

    이미 같은 기기가 있으면(재설치) **키를 새로 발급해 다시 심는다**. 토큰은 여기서 소모된다.
    토큰 없이 스스로 등록하는 경로는 `enroll`이다(같은 등록 함수를 쓴다).
    """
    require_token(req.token, consume=True)
    ip = peer_ipv4(request)
    if ip is None:
        raise HTTPException(status_code=400, detail={
            "error": "UNTRUSTED_ADDRESS", "message": "사설 LAN 주소에서 온 요청만 등록할 수 있습니다"})
    result = await enroll_device(db, ip, req.hostname)
    return {"data": {**result}, "ok": True}


class EnrollRequest(BaseModel):
    hostname: Optional[str] = None


@router.post("/enroll")
async def enroll(req: EnrollRequest, request: Request, db: Session = Depends(get_db)):
    """Pi가 **토큰 없이** 스스로 등록을 요청한다 (`device/server_join.py`).

    서버가 직접 그 Pi의 `:5000`을 읽어 판정한다 — 요청 본문의 자기 신고는 쓰지 않는다.
    `status`: `enrolled`(신원 없음 → 등록) · `known`(우리 소속 → 주소만 갱신) ·
    `pending`(다른 서버 소속 → 승인 대기) · `rejected`(거절돼 억제 중) · `failed`(신원 주입 실패).
    """
    if not settings.auto_enroll:
        raise HTTPException(status_code=403, detail={
            "error": "ENROLL_DISABLED", "message": "자동 등록이 꺼져 있습니다 (AUTO_ENROLL=false)"})
    ip = peer_ipv4(request)
    if ip is None:
        raise HTTPException(status_code=400, detail={
            "error": "UNTRUSTED_ADDRESS", "message": "사설 LAN 주소에서 온 요청만 등록할 수 있습니다"})
    return {"data": await handle_enroll(db, ip, req.hostname), "ok": True}


# --------------------------------------------------------------------------- 승인 대기
# 다른 서버에 등록된 기기가 스스로 올라온 목록. 관리자가 승인해야 인수한다.

@router.get("/pending")
async def list_pending(current_user=Depends(get_current_user)):
    items = [PendingStore.to_dict(e) for e in pending_store.list()]
    return {"data": items, "total": len(items), "ok": True}


def _lan_or_400(ip: str) -> str:
    """정규화한 주소를 돌려준다 — 대기 목록의 키와 같은 표기여야 한다(`::ffff:192.168.0.5` 같은 변형 방지)."""
    norm = lan_ipv4(ip)
    if norm is None:
        raise HTTPException(status_code=400, detail={
            "error": "INVALID_ADDRESS", "message": "사설 LAN IPv4 주소가 아닙니다"})
    return norm


@router.post("/pending/{ip}/approve")
async def approve(ip: str, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """승인 — 기존 인수 경로로 신원을 심는다. 기기에는 `[WARN] 기기 신원 인수` 로그와 부저 1회가 남고
    이전 서버와의 연결이 끊긴다."""
    result = await approve_pending(db, _lan_or_400(ip))
    if result is None:
        raise HTTPException(status_code=404, detail={
            "error": "PENDING_NOT_FOUND", "message": "승인 대기 중인 기기가 아닙니다 (만료됐을 수 있습니다)"})
    if result.get("stale"):
        raise HTTPException(status_code=409, detail={
            "error": "PENDING_STALE",
            "message": "그 주소의 기기가 바뀌었거나 신원이 달라졌습니다 — 기기가 다시 요청하면 새로 나타납니다"})
    return {"data": result, "ok": True}


@router.post("/pending/{ip}/reject")
async def reject(ip: str, current_user=Depends(get_current_user)):
    """거절 — 대기에서 지우고 1시간 동안 같은 주소의 요청을 무시한다."""
    existed = pending_store.reject(_lan_or_400(ip))
    return {"data": {"ip": ip, "was_pending": existed}, "ok": True}
