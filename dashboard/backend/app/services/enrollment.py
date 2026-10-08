"""enrollment.py — 기기를 서버에 등록하는 일(설치 토큰 등록 · 자동 등록 · 승인)의 공통 구현.

세 경로가 **같은 등록 함수**를 쓴다: `register`(설치 토큰), `enroll`(Pi가 스스로 요청), 승인(관리자).
각자 구현하면 키 발급·신원 주입·초기 동기화 순서가 어긋난다.

## 소속 판정 (`handle_enroll`)

Pi가 등록을 요청하면 **서버가 직접 그 Pi의 `:5000`을 읽어** 판정한다 — Pi의 자기 신고는 믿지 않는다.

| Pi의 상태 | 처리 |
|---|---|
| 신원 없음 | 즉시 등록 |
| 신원이 있고 **우리가 그 키를 알고 있다** | 우리 소속 — 주소만 갱신 |
| 신원이 있고 그 외 | **승인 대기** (기기는 건드리지 않는다) |

★ **"우리 키를 아는가"를 확인하는 방법이 두 가지로 위험하다.**
- 신원을 다시 보내 보기: Pi의 `POST /api/identity`는 키가 틀려도 **덮어쓴다**(인수) — 시험이 곧 탈취다.
- 키를 헤더로 보내 보기: 상대는 **아직 증명되지 않았다.** 가짜 Pi가 우리 기기의 `device_id`(`pi-<ip>`로
  추측 가능)를 주장하면 코드 푸시·재부팅까지 가능한 키를 건네게 된다.
그래서 **키를 보내지 않는 챌린지-응답**(`GET /api/identity/proof?nonce=` → `HMAC(키, nonce)`)으로 확인한다.
이 엔드포인트가 없는 구버전 Pi는 증명할 수 없으므로 **안전한 쪽(승인 대기)** 으로 둔다. 그리고 **증명이
끝나기 전에는 우리 행을 바꾸지 않는다**(`adopt_peer_ip`도 증명 뒤에만).

같은 IP의 요청은 **직렬화**한다(`install.sh`의 등록과 `JoinAgent`의 첫 시도가 몇 초 안에 겹친다 — 둘이
동시에 키를 돌리면 Pi와 DB의 키가 갈라지거나 같은 id로 두 번 INSERT해 500이 난다).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import secrets
import time
from typing import Optional
from urllib.parse import urlparse

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models.device import Device
from .address_sync import refresh_device_address
from .device_address import adopt_peer_ip
from .pending_enrollments import pending_store
from .pi_client import PiClient
from .server_address import public_url_for

logger = logging.getLogger(__name__)

__all__ = ["device_id_for", "enroll_device", "handle_enroll", "approve_pending", "url_key"]

PRODUCT = "VisionGuide"
# 같은 IP의 요청을 이 시간 안에 다시 받으면 직전 결과를 돌려준다 — 서버가 요청자의 :5000을 매번
# 두드리게 되므로, 무인증 엔드포인트가 증폭기가 되지 않게 한다.
THROTTLE_SEC = 10.0
_MAX_TRACKED = 512                     # 기억하는 IP 수의 상한 — LAN 주소는 유한하지만 무한히 늘리지 않는다
_FIELD_MAX = 200                       # 상대 기기가 준 문자열은 길이를 자른다(대기 목록에 그대로 저장되므로)
_recent: dict[str, tuple[float, object]] = {}     # 결과(dict) 또는 실패(HTTPException) — 실패도 기억한다
_locks: dict[str, asyncio.Lock] = {}


def _field(value: object) -> str:
    """상대 기기의 응답 필드를 문자열로 — 타입이 틀리면 빈 값, 길면 자른다."""
    return value[:_FIELD_MAX] if isinstance(value, str) else ""


def device_id_for(ip: str) -> str:
    # 기기 추가 화면(PiScan)의 기본 id 규칙과 같다 — 같은 기기를 두 경로로 등록해도 한 행이다.
    return "pi-" + ip.replace(".", "-")


def url_key(url: Optional[str]) -> str:
    """서버 주소를 `host:port`로 정규화해 비교한다 (스킴·끝 슬래시·대소문자 차이를 무시)."""
    if not url:
        return ""
    parsed = urlparse(url if "://" in url else f"http://{url}")
    host = (parsed.hostname or "").lower()
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:                      # 기기 신원에 이상한 server_url이 있어도 enroll이 500으로 죽지 않게
        return ""
    return f"{host}:{port}" if host else ""


def _clean_name(hostname: Optional[str], fallback: str) -> str:
    return re.sub(r"[^\w.\- ]", "", hostname or "")[:80] or fallback


async def enroll_device(db: Session, ip: str, hostname: Optional[str] = None) -> dict:
    """기기 한 대를 등록하고 신원을 심는다. **키를 새로 발급한다**(재등록·인수 포함).

    같은 IP의 기존 행이 있으면(다른 id로 수동 등록한 경우 포함) 그 행을 재사용한다 — 새 행을 만들면
    같은 기기가 두 줄이 된다. 결과: `{device_id, provisioned, provision_error, synced}`.
    """
    # 순환 import를 피하려고 함수 안에서 가져온다 — devices 라우터가 서비스 계층을 쓴다.
    from ..routers.devices import _initial_sync, _provision_device

    device = db.query(Device).filter(Device.ip == ip).first()
    if device is None:
        device = db.query(Device).filter(Device.id == device_id_for(ip)).first()

    raw_api_key = f"vg_{secrets.token_urlsafe(16)}"
    api_key_hash = hashlib.sha256(raw_api_key.encode()).hexdigest()

    if device is None:
        did = device_id_for(ip)
        device = Device(id=did, name=_clean_name(hostname, did), ip=ip, api_key_hash=api_key_hash)
        db.add(device)
        db.commit()                                 # 새 기기는 신원 주입이 실패해도 행을 남긴다(재시도할 수 있게)
        provisioned, reason = await _provision_device(device, raw_api_key)
    else:
        # ★ 기존 행은 **Pi가 받아들인 뒤에만** 바꾼다. 먼저 키를 돌리면 주입이 실패했을 때(또는 그 IP가 다른
        # 기기로 넘어간 경우) 멀쩡히 동작하던 기기의 키가 서버에서 거부된다.
        device.ip, device.api_key_hash = ip, api_key_hash
        provisioned, reason = await _provision_device(device, raw_api_key)
        if not provisioned:
            db.rollback()                           # 메모리에서만 바꿨으므로 원래 값으로 돌아간다
    if provisioned:
        db.commit()                                 # control_key는 기기가 받아들인 뒤에만 저장된다
    synced = await _initial_sync(db, device) if provisioned else False
    return {"device_id": device.id, "provisioned": provisioned,
            "provision_error": reason, "synced": synced}


def _find_owned_row(db: Session, ip: str, device_id: str) -> Optional[Device]:
    """기기가 쓰는 신원 id에 해당하는, **키를 가진** 우리 행."""
    row = db.query(Device).filter(Device.id == device_id).first()
    if row is not None and row.control_key:
        return row
    return None


async def _ours(client: PiClient, row: Device) -> bool:
    """기기가 우리 키를 아는가 — 키를 보내지 않는 챌린지-응답. 증명할 수 없으면 False(안전한 쪽)."""
    try:
        return await client.prove_identity(row.control_key)
    except HTTPException as exc:
        logger.info("소속 확인 불가 (%s): %s — 승인 대기로 둡니다", row.id, exc.detail)
        return False


def _prune(now: float) -> None:
    if len(_recent) > _MAX_TRACKED:
        for ip in [i for i, (t, _) in _recent.items() if now - t >= THROTTLE_SEC]:
            _recent.pop(ip, None)
    if len(_locks) > _MAX_TRACKED:
        for ip in [i for i, lock in _locks.items() if not lock.locked()]:
            _locks.pop(ip, None)


async def handle_enroll(db: Session, ip: str, hostname: Optional[str] = None,
                        *, now: Optional[float] = None) -> dict:
    """Pi의 등록 요청을 판정하고 처리한다. 반환 `status`: enrolled | known | pending | rejected | failed.

    **같은 IP의 요청은 한 번에 하나만** 처리한다. 직전 결과(성공이든 실패든)가 10초 안이면 그것을 돌려준다 —
    무인증 엔드포인트라서, 실패하는 요청을 반복하는 LAN 호스트가 서버를 시켜 계속 :5000을 두드리게 하지 못한다.
    """
    lock = _locks.setdefault(ip, asyncio.Lock())
    async with lock:
        now = time.monotonic() if now is None else now
        _prune(now)
        cached = _recent.get(ip)
        if cached and now - cached[0] < THROTTLE_SEC:
            if isinstance(cached[1], HTTPException):
                raise cached[1]
            return {**cached[1], "throttled": True}
        try:
            result = await _handle_enroll(db, ip, hostname)
        except HTTPException as exc:
            _recent[ip] = (now, exc)
            raise
        _recent[ip] = (now, result)
        return result


async def _handle_enroll(db: Session, ip: str, hostname: Optional[str]) -> dict:
    if pending_store.is_rejected(ip):
        return {"status": "rejected"}

    client = PiClient(ip)
    version = await client.get_version()          # 연결 실패는 HTTPException(502~504)로 올라간다
    if not isinstance(version, dict) or version.get("product") != PRODUCT:
        raise HTTPException(status_code=400, detail={
            "error": "NOT_VISIONGUIDE", "message": "이 주소의 기기는 VisionGuide가 아닙니다"})
    ident = await client.get_identity()
    if not isinstance(ident, dict):                 # 가짜 기기가 이상한 JSON을 주면 500이 아니라 400이다
        raise HTTPException(status_code=400, detail={
            "error": "NOT_VISIONGUIDE", "message": "기기의 신원 응답이 올바르지 않습니다"})

    # 1) 신원 없음 → 즉시 등록
    if not ident.get("registered"):
        pending_store.remove(ip)
        res = await enroll_device(db, ip, hostname)
        res["status"] = "enrolled" if res["provisioned"] else "failed"
        logger.info("자동 등록: %s (%s) -> %s", res["device_id"], ip, res["status"])
        return res

    device_id = _field(ident.get("device_id"))
    current_url = _field(ident.get("server_url")).rstrip("/")
    row = _find_owned_row(db, ip, device_id)

    # 2) 우리 소속 → 주소만 갱신 (키는 그대로). **증명이 끝난 뒤에만** 행을 바꾼다 — 증명 전에 IP를 옮기면
    #    가짜 기기가 우리 기기의 device_id를 주장하는 것만으로 행이 자기 쪽을 가리키게 된다.
    if row is not None and await _ours(client, row):
        pending_store.remove(ip)
        adopt_peer_ip(db, row, ip)                 # 기기 IP가 바뀐 경우를 따라간다
        address = await refresh_device_address(db, row)
        return {"status": "known", "device_id": row.id, "address": address}

    # 3) 우리 행이 없는데 기기가 이미 **우리 서버를 보고 있다** → 우리 DB가 날아간 경우다.
    #    다른 서버의 기기가 아니므로 다시 등록해도 누구의 것도 빼앗지 않는다.
    if row is None and url_key(current_url) and url_key(current_url) == url_key(public_url_for(ip)):
        pending_store.remove(ip)
        res = await enroll_device(db, ip, hostname)
        res["status"] = "enrolled" if res["provisioned"] else "failed"
        logger.info("자동 재등록(DB 유실 복구): %s (%s) -> %s", res["device_id"], ip, res["status"])
        return res

    # 4) 그 외 = 다른 서버 소속 → 승인 대기. 기기는 건드리지 않는다.
    entry = pending_store.upsert(ip, device_id=device_id, hostname=_clean_name(hostname, device_id or ip),
                                 current_server_url=current_url)
    logger.info("승인 대기: %s (%s) 현재 서버 %s", entry.device_id, ip, current_url or "(없음)")
    return {"status": "pending", "device_id": device_id, "current_server_url": current_url}


async def approve_pending(db: Session, ip: str) -> Optional[dict]:
    """승인 — 기존 인수 경로(키 없는 덮어쓰기)로 신원을 심는다. 대기 항목이 없으면 None.

    **승인하는 순간 그 주소의 기기를 다시 확인한다.** 대기 항목은 최대 15분 묵은 것이라, 그 사이 같은 IP를
    다른 기기(우리 소속이거나 전혀 다른 Pi)가 받았을 수 있다 — 확인 없이 덮어쓰면 엉뚱한 기기를 뺏는다.
    기기가 달라졌으면 `{"stale": True}`를 돌려주고 항목을 지운다(기기가 다시 요청하면 새로 판정한다).

    Pi에는 `[WARN] 기기 신원 인수` 로그와 부저 1회가 남고, 이전 서버와의 연결이 끊긴다.
    """
    entry = pending_store.get(ip)
    if entry is None:
        return None
    ident = await PiClient(ip).get_identity()
    if (not isinstance(ident, dict) or not ident.get("registered")
            or _field(ident.get("device_id")) != entry.device_id):
        pending_store.remove(ip)
        _recent.pop(ip, None)
        return {"stale": True}
    res = await enroll_device(db, ip, entry.hostname)
    if res["provisioned"]:
        pending_store.remove(ip)
    _recent.pop(ip, None)
    return res


def reset_throttle() -> None:
    """테스트용."""
    _recent.clear()
    _locks.clear()
