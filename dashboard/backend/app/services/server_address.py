"""server_address.py — 기기에 알려 줄 **이 서버의 주소**를 정한다.

Pi는 `server_url`을 IP로 기억한다. 노트북이 Wi-Fi를 옮기면 IP가 바뀌고, 그러면 하트비트가
사라진 주소로 나가 기기는 "알 수 없음"이 된다. `.env`의 `PUBLIC_BASE_URL`을 매번 고치지
않도록 `auto`를 지원한다 — **그 기기에 닿는 경로의 서버 쪽 IP**를 그때그때 계산한다.

`.local` 이름에 기대지 않는 이유: mDNS는 기기마다 해석되는지 다르고(`.60`은 확인하지
못했다) 방화벽에서도 막힌다. IP는 서버가 직접 알 수 있다.
"""

from __future__ import annotations

import socket
from typing import Optional

from ..config import settings

__all__ = ["AUTO", "is_auto", "local_ip_toward", "public_url_for", "remember_port"]

AUTO = "auto"

# 요청이 들어올 때 본 실제 포트. `.env`의 PORT는 uvicorn을 `--port`로 띄우면 어긋나기
# 쉬워(이번 세션에서도 .env는 8000, 실제는 8001) 믿을 수 있는 값이 아니다.
_seen_port: Optional[int] = None


def is_auto() -> bool:
    return settings.public_base_url.strip().lower() in ("", AUTO)


def remember_port(port: Optional[int]) -> None:
    """HTTP 요청에서 본 서버 포트를 기억한다(미들웨어가 호출)."""
    global _seen_port
    if port:
        _seen_port = int(port)


def _port() -> int:
    return _seen_port or settings.port


def local_ip_toward(device_ip: str) -> Optional[str]:
    """`device_ip`로 가는 경로에서 서버가 쓰는 자기 IP. 알 수 없으면 None.

    UDP 소켓의 `connect()`는 패킷을 보내지 않고 라우팅만 정하므로, `getsockname()`이
    그 기기에 닿는 인터페이스의 주소를 알려 준다. 기기가 꺼져 있어도 동작한다.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((device_ip, 9))
            ip = s.getsockname()[0]
    except OSError:
        return None
    return None if ip in ("0.0.0.0", "") else ip


def public_url_for(device_ip: str) -> str:
    """기기 `device_ip`에 심을 서버 주소.

    `PUBLIC_BASE_URL`이 명시돼 있으면 그대로(끝의 `/`만 정리). `auto`면 계산한다.
    계산에 실패하면 빈 문자열 — 호출부가 `server_url이 비었다`는 기존 오류를 낸다.
    """
    if not is_auto():
        return settings.public_base_url.strip().rstrip("/")
    ip = local_ip_toward(device_ip)
    return f"http://{ip}:{_port()}" if ip else ""
