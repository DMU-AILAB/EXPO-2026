"""하트비트가 온 주소로 기기의 IP를 따라간다.

서버는 `Device.ip`로 Pi를 호출한다(스트림 프록시, 카메라·RF 설정, 재부팅). 그런데 Pi는
DHCP로 IP를 받기 때문에 공유기가 바뀌거나 임대가 갱신되면 값이 달라지고, 하트비트는 IP를
갱신하지 않아서 그 순간부터 서버→Pi 호출이 전부 끊겼다(Pi→서버 하트비트는 계속 오는데도
대시보드에는 연결이 안 되는 상태).

하트비트는 `X-API-Key`로 인증된 요청이고 출발지 주소는 TCP 연결의 상대 주소라 속일 수
없다. 그래서 그 주소를 새 IP로 받아들인다. 다만 서버는 이 IP로 **제어 키(`X-Device-Key`)를
실어 호출**하므로, 엉뚱한 곳으로 키가 가지 않게 다음을 지킨다.

- IPv4 사설 대역(RFC1918)과 링크 로컬(169.254/16)만 받는다 — 외부·루프백 주소는 무시.
- 다른 기기가 이미 쓰는 IP면 바꾸지 않는다 — 옛 기기 행의 낡은 IP가 새 기기로 넘어가 두
  기기가 같은 주소를 가리키는 일을 막는다. 이때는 경고만 남긴다.
"""

from __future__ import annotations

import ipaddress
import logging
from typing import Optional

from fastapi import Request
from sqlalchemy.orm import Session

from ..models.device import Device

logger = logging.getLogger(__name__)

# ipaddress의 is_private는 사설망 말고도 문서용(203.0.113.0/24 등)·벤치마크(198.18/15)·예약(240/4)
# 대역까지 True라서 쓰지 않는다. 제어 키를 보낼 주소이므로 실제 LAN 대역만 명시한다.
_LAN_NETWORKS = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
))


def lan_ipv4(host: Optional[str]) -> Optional[str]:
    """`host`를 서버가 믿고 쓸 수 있는 LAN IPv4 문자열로. 아니면 None.

    HTTP 요청의 상대 주소(`peer_ipv4`)와 UDP 발견 응답기가 **같은 기준**을 쓰도록 한 곳에 둔다 —
    기준이 둘이면 발견은 되는데 등록은 거부되는 기기가 생긴다.
    """
    if not host:
        return None
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return None
    if isinstance(addr, ipaddress.IPv6Address):
        addr = addr.ipv4_mapped  # ::ffff:192.168.0.5 형태만 IPv4로 본다
        if addr is None:
            return None
    if not any(addr in net for net in _LAN_NETWORKS):
        return None
    return str(addr)


def peer_ipv4(request: Request) -> Optional[str]:
    """요청의 TCP 상대 주소를 서버가 믿고 쓸 수 있는 IPv4 문자열로. 아니면 None.

    `X-Forwarded-For`는 보지 않는다 — 클라이언트가 마음대로 쓸 수 있는 헤더다.
    """
    return lan_ipv4(request.client.host if request.client else None)


def adopt_peer_ip(db: Session, device: Device, peer_ip: Optional[str]) -> bool:
    """`peer_ip`가 다르면 `device.ip`를 갱신한다. 갱신했으면 True."""
    if not peer_ip or peer_ip == device.ip:
        return False
    taken = (db.query(Device)
             .filter(Device.ip == peer_ip, Device.id != device.id)
             .first())
    if taken is not None:
        logger.warning("기기 %s의 하트비트가 %s에서 왔지만 이미 기기 %s가 쓰는 주소라 IP를 바꾸지 않습니다",
                       device.id, peer_ip, taken.id)
        return False
    logger.info("기기 %s의 IP가 바뀌었습니다: %s -> %s", device.id, device.ip, peer_ip)
    device.ip = peer_ip
    db.commit()
    return True
