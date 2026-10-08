"""address_sync.py — 기기가 알고 있는 서버 주소를 지금 주소로 맞춘다.

노트북이 Wi-Fi를 옮겨 서버 IP가 바뀌면 Pi의 `server_url`이 사라진 주소가 된다. 하트비트가
안 오면 서버가 할 수 있는 일은 **기기에 직접 가서 주소를 고치는 것**뿐이다.

핵심은 **키를 새로 발급하지 않는 것**이다. `Device.control_key`에 평문이 보관돼 있으므로 같은
키로 같은 신원을 다시 보내면 되고(`api_key_hash`도 그대로), Pi는 키가 맞으니 인수로 취급하지
않는다(로그·부저 없음). 신원 재주입(`/provision`)은 키를 바꾸므로 이 용도로 쓰지 않는다.

★ **보내기 전에 소유를 증명한다**(`PiClient.prove_identity` — 키를 보내지 않는 챌린지-응답). Pi의
`POST /api/identity`는 키가 틀려도 **덮어쓰므로**, 증명 없이 보내면 다른 서버로 **승인 이동된 기기**를 이전 서버가
도로 빼앗는다. 실기기 실험에서 실제로 일어났다: 승인으로 A→B로 옮겨진 기기를 A의 이 점검이 약 40초 만에 되찾고, 이어서
B가 되찾는 **핑퐁**이 이어져 기기에 인수 경고와 부저가 반복됐다(두 서버가 같은 `pi-<ip>` id를 쓰므로 `device_id` 비교로는
못 막는다). 증명이 실패하면 아무것도 보내지 않고 건너뛴다. 증명 엔드포인트가 없는 구버전(404)만 예전 동작을 유지한다.

자동으로 건드리는 범위는 **이미 신원이 있는 기기**뿐이다. 신원이 없는 기기를 인수하는 것은
다른 서버에서 쓰는 기기를 빼앗을 수 있어 사람이 누른다(진단 탭이 안내).
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models.device import Device
from ..utils.timeutil import utcnow
from .device_view import is_stale
from .heartbeat_service import get_buffered_status
from .pi_client import PiClient
from .server_address import public_url_for

logger = logging.getLogger(__name__)

__all__ = ["refresh_device_address", "reconcile_addresses"]

# 실패한 기기를 계속 두드리지 않는다 — 꺼진 기기는 5분, 10분, … 최대 30분 간격으로 본다.
_BASE_RETRY_SEC = 300.0
_MAX_RETRY_SEC = 1800.0
_retry: dict[str, tuple[float, float]] = {}        # device_id -> (다음 시도 시각, 현재 간격)


def _detail(exc: HTTPException) -> str:
    d = exc.detail
    return d.get("message", str(d)) if isinstance(d, dict) else str(d)


async def refresh_device_address(db: Session, device: Device) -> dict:
    """기기 한 대의 `server_url`을 지금 주소로 맞춘다. 결과는 항상 dict(예외를 던지지 않는다)."""
    out = {"device_id": device.id, "ok": False, "changed": False, "skipped": False,
           "server_url": None, "previous": None, "error": None}

    if not device.control_key:
        out.update(skipped=True, error="기기에 신원이 심어지지 않았습니다 — 먼저 신원 재주입을 하세요")
        return out
    expected = public_url_for(device.ip)
    if not expected:
        out["error"] = "서버 주소를 정할 수 없습니다 (PUBLIC_BASE_URL을 확인하세요)"
        return out
    out["server_url"] = expected

    client = PiClient(device.ip)
    try:
        ident = await client.get_identity()
    except HTTPException as exc:
        out["error"] = f"기기에 연결하지 못했습니다: {_detail(exc)}"
        return out
    if not ident.get("registered"):
        out.update(skipped=True, error="기기에 신원이 없습니다 — 신원 재주입이 필요합니다")
        return out
    if ident.get("device_id") != device.id:
        # 같은 IP를 다른 기기가 쓰게 된 경우 — 엉뚱한 기기에 우리 키를 심으면 안 된다.
        out.update(skipped=True, error=f"이 주소의 기기는 {ident.get('device_id')}입니다 "
                                       f"(등록된 id: {device.id})")
        return out

    current = (ident.get("server_url") or "").rstrip("/")
    out["previous"] = current
    if current == expected:
        out["ok"] = True
        return out

    # 보내기 전에 이 기기가 정말 우리 키를 아는지 증명한다 — 다른 서버로 옮겨 간 기기를 빼앗지 않으려는 것.
    try:
        owned = await client.prove_identity(device.control_key)
    except HTTPException as exc:
        if exc.status_code == 404:
            owned = None            # 증명 엔드포인트가 없는 구버전 — 예전 동작(키가 틀리면 Pi가 인수로 기록·부저)
        else:
            out["error"] = f"기기의 소속을 확인하지 못했습니다: {_detail(exc)}"
            return out
    if owned is False:
        out.update(skipped=True, error="이 기기는 다른 서버 소속입니다(키가 맞지 않음) — 서버 주소를 바꾸지 않았습니다. "
                                       "이 서버로 가져오려면 기기가 올리는 승인 대기에서 승인하세요")
        return out

    try:
        await client.post_identity(
            device_id=device.id, api_key=device.control_key, server_url=expected,
            name=ident.get("name") or device.name or "",
            location=ident.get("location") or device.location or "",
            registered_at=ident.get("registered_at") or utcnow().isoformat(timespec="seconds") + "Z",
            current_key=device.control_key,
        )
    except HTTPException as exc:
        out["error"] = f"서버 주소를 바꾸지 못했습니다: {_detail(exc)}"
        return out
    logger.info("기기 %s의 서버 주소를 갱신했습니다: %s -> %s", device.id, current or "(없음)", expected)
    out.update(ok=True, changed=True)
    return out


async def reconcile_addresses(now: Optional[float] = None) -> int:
    """하트비트가 끊긴 기기들의 서버 주소를 맞춘다. 고친 기기 수를 돌려준다.

    정상 기기에는 호출하지 않는다 — 하트비트가 오고 있다면 주소는 맞다.
    """
    now = time.monotonic() if now is None else now
    db: Session = SessionLocal()
    fixed = 0
    try:
        for device in db.query(Device).filter(Device.control_key.isnot(None)).all():
            hb = await get_buffered_status(device.id)
            if hb and not is_stale(hb.get("updated_at")):
                _retry.pop(device.id, None)
                continue
            due, interval = _retry.get(device.id, (0.0, _BASE_RETRY_SEC))
            if now < due:
                continue
            result = await refresh_device_address(db, device)
            if result["ok"]:
                _retry.pop(device.id, None)
                fixed += 1 if result["changed"] else 0
            else:
                nxt = min(interval * 2, _MAX_RETRY_SEC) if device.id in _retry else interval
                _retry[device.id] = (now + nxt, nxt)
                logger.debug("기기 %s 주소 점검 실패: %s", device.id, result["error"])
    finally:
        db.close()
    return fixed
