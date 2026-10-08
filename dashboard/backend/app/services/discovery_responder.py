"""discovery_responder.py — Pi가 서버를 **스스로 찾게** 해 주는 UDP 응답기.

Pi는 서버 주소를 모르는 채로 켜질 수 있다(새 기기, 서버 PC 교체). 그래서 Pi가 LAN에 브로드캐스트로
"VisionGuide 서버 있나?"를 묻고, 서버가 **그 Pi에 닿는 자기 주소**를 알려 준다.

    요청:  b"VISIONGUIDE?" [+ JSON 부가 정보]            (Pi → 브로드캐스트, UDP 48555)
    응답:  {"v":1,"service":"visionguide","url":"http://<서버>:8000","name":"<호스트명>"}   (서버 → Pi 유니캐스트)

- **브로드캐스트 요청 · 유니캐스트 응답.** 서버가 여러 인터페이스(유선+Wi-Fi 등)를 가질 수 있으므로
  응답의 `url`은 요청자 IP 기준으로 계산한다(`public_url_for` — 그 기기에 닿는 경로의 자기 IP).
- **사설 LAN에서 온 요청만** 답한다(`lan_ipv4` — HTTP 등록과 같은 기준). 형식이 다르거나 크면 무시한다.
- `AUTO_ENROLL=false`면 **침묵**한다 — 응답하지 않으면 Pi는 서버가 없다고 본다.
- **포트가 이미 쓰이면 경고만 하고 서버는 계속 뜬다.** 발견은 편의 기능이고, 같은 PC에서 서버를 둘
  띄운 개발 환경이 흔하다.
- `public_url_for`가 `auto`일 때 포트는 서버가 **실제로 받은 요청의 포트**를 쓰는데, 기동 직후에는
  아직 요청이 없어 `.env`의 `PORT`를 쓴다. `uvicorn --port`와 `PORT`를 같게 둘 것.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import time
from typing import Callable, Optional

from ..config import settings
from .device_address import lan_ipv4
from .server_address import public_url_for

logger = logging.getLogger(__name__)

__all__ = ["REQUEST_PREFIX", "SERVICE", "PROTOCOL_VERSION", "build_response", "start_responder", "stop_responder"]

# ★ device/server_discovery.py와 같아야 한다 — 계약 테스트가 두 파일을 맞대어 본다.
REQUEST_PREFIX = b"VISIONGUIDE?"
SERVICE = "visionguide"
PROTOCOL_VERSION = 1
MAX_REQUEST_BYTES = 1024
# 출발지마다 이 간격 안의 요청은 무시한다 — 12바이트 요청에 ~150바이트로 답하므로(증폭), 출발지를 속인 요청이
# 계속 오면 서버가 피해자에게 트래픽을 쏘게 된다. 정상 Pi는 요청을 몇 초에 한 번만 보낸다.
MIN_INTERVAL_SEC = 1.0
_MAX_SOURCES = 1024


def build_response(peer_ip: str, *, url_for: Optional[Callable[[str], str]] = None,
                   name: Optional[str] = None) -> Optional[bytes]:
    """`peer_ip`(요청자)에게 보낼 응답. 보낼 주소를 정할 수 없으면 None.

    `url_for`의 기본값을 **호출 시점에** 정한다 — 기본 인자로 붙잡으면 정의 시점의 함수에 묶여 모듈의
    `public_url_for`를 바꿔도(테스트·설정 변경) 반영되지 않는다.
    """
    url = (url_for or public_url_for)(peer_ip)
    if not url:
        return None
    return json.dumps({"v": PROTOCOL_VERSION, "service": SERVICE, "url": url,
                       "name": name if name is not None else socket.gethostname()},
                      ensure_ascii=False).encode("utf-8")


class _Responder(asyncio.DatagramProtocol):
    def __init__(self, lan_check: Callable[[Optional[str]], Optional[str]],
                 min_interval: float = MIN_INTERVAL_SEC,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.transport: Optional[asyncio.DatagramTransport] = None
        self._lan_check = lan_check
        self._min_interval = min_interval
        self._clock = clock
        self._last: dict[str, float] = {}

    def _rate_limited(self, peer: str) -> bool:
        now = self._clock()
        if len(self._last) > _MAX_SOURCES:
            self._last = {k: t for k, t in self._last.items() if now - t < self._min_interval}
        last = self._last.get(peer)
        if last is not None and now - last < self._min_interval:
            return True
        self._last[peer] = now
        return False

    def connection_made(self, transport) -> None:           # noqa: D401
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        # 요청은 짧다. 크기·접두사·출발지를 모두 통과한 것만 답한다.
        if not settings.auto_enroll or self.transport is None:
            return
        if len(data) > MAX_REQUEST_BYTES or not data.startswith(REQUEST_PREFIX):
            return
        peer = self._lan_check(addr[0])
        if peer is None or self._rate_limited(peer):
            return
        reply = build_response(peer)
        if reply is not None:
            self.transport.sendto(reply, addr)

    def error_received(self, exc: Exception) -> None:
        logger.debug("발견 응답기 소켓 오류: %s", exc)


async def start_responder(port: Optional[int] = None, host: str = "0.0.0.0",
                          lan_check: Callable[[Optional[str]], Optional[str]] = lan_ipv4,
                          min_interval: float = MIN_INTERVAL_SEC):
    """UDP 응답기를 시작한다. 바인딩에 실패하면 경고만 남기고 None을 돌려준다(서버는 계속 뜬다)."""
    port = settings.discovery_port if port is None else port
    loop = asyncio.get_running_loop()
    try:
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _Responder(lan_check, min_interval), local_addr=(host, port), allow_broadcast=True)
    except OSError as exc:
        logger.warning("발견 응답기를 시작하지 못했습니다 (UDP %s): %s — 기기 자동 발견은 동작하지 않습니다 "
                       "(수동 등록·설치 토큰은 영향 없음)", port, exc)
        return None
    logger.info("발견 응답기 시작: UDP %s", transport.get_extra_info("sockname"))
    return transport


def stop_responder(transport) -> None:
    if transport is not None:
        transport.close()
