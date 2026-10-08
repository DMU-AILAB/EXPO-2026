"""server_discovery.py — LAN에서 VisionGuide 서버를 **스스로 찾는다** (UDP 브로드캐스트).

Pi는 서버 주소를 모르는 채로 켜질 수 있다(새 기기, 서버 PC 교체). 서버를 찾는 데 `.local` 이름이나
고정 IP에 기대지 않는다 — mDNS는 기기마다 해석 여부가 다르고 방화벽에서도 막힌다.

    요청:  b"VISIONGUIDE?" [+ JSON 부가 정보]      → 브로드캐스트, UDP 48555
    응답:  {"v":1,"service":"visionguide","url":"http://<서버>:8000","name":"<호스트명>"}   ← 서버가 유니캐스트로

**두 군데로 보낸다**: 전역 브로드캐스트(`255.255.255.255`)와 기본 경로 인터페이스의 /24 지향 브로드캐스트.
NIC가 여러 개(유선+Wi-Fi)면 전역 브로드캐스트는 라우팅 테이블이 고른 하나로만 나가서, 서버가 있는 쪽 망에
닿지 않는 일이 있다.

★ 서버(`dashboard/backend/app/services/discovery_responder.py`)와 **프로토콜 상수가 같아야 한다**.
`tests/test_server_discovery.py`가 두 파일을 맞대어 본다. 표준 라이브러리만 쓴다(Pi 의존성을 늘리지 않는다).
소켓·시간을 주입할 수 있다.
"""

from __future__ import annotations

import json
import socket
import time
from typing import Callable, Optional
from urllib.parse import urlparse

__all__ = ["DISCOVERY_PORT", "REQUEST_PREFIX", "SERVICE", "PROTOCOL_VERSION",
           "discover", "default_route_ip", "broadcast_targets", "parse_response", "url_key"]

DISCOVERY_PORT = 48555
REQUEST_PREFIX = b"VISIONGUIDE?"
SERVICE = "visionguide"
PROTOCOL_VERSION = 1
_MAX_RESPONSE_BYTES = 4096
_MAX_URL_LEN = 200


def url_key(url: Optional[str]) -> str:
    """서버 주소를 `host:port`로 정규화한다 — 스킴·끝 슬래시·대소문자 차이를 무시하고 비교하려는 것.

    서버 쪽 `enrollment.url_key`와 같은 규칙이다.
    """
    if not url:
        return ""
    parsed = urlparse(url if "://" in url else f"http://{url}")
    host = (parsed.hostname or "").lower()
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return ""
    return f"{host}:{port}" if host else ""


def default_route_ip() -> Optional[str]:
    """기본 경로로 나가는 인터페이스의 IPv4. 패킷은 보내지 않는다(UDP connect는 라우팅만 정한다)."""
    for target in (("192.0.2.1", 9), ("8.8.8.8", 80)):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(target)
                ip = s.getsockname()[0]
        except OSError:
            continue
        if ip and ip != "0.0.0.0":
            return ip
    return None


def broadcast_targets(local_ip: Optional[str]) -> list[str]:
    """요청을 보낼 브로드캐스트 주소들 — 전역 + (알면) 로컬 /24 지향."""
    targets = ["255.255.255.255"]
    if local_ip and local_ip.count(".") == 3:
        directed = local_ip.rsplit(".", 1)[0] + ".255"
        if directed not in targets:
            targets.append(directed)
    return targets


def parse_response(data: bytes) -> Optional[dict]:
    """응답 한 건을 검증한다. 서버가 아니거나 형식이 틀리면 None."""
    if not data or len(data) > _MAX_RESPONSE_BYTES:
        return None
    try:
        obj = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(obj, dict) or obj.get("service") != SERVICE:
        return None
    url = obj.get("url")
    if not isinstance(url, str) or not url or len(url) > _MAX_URL_LEN or any(c.isspace() for c in url):
        return None
    parsed = urlparse(url)
    try:
        _ = parsed.port
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    name = obj.get("name")
    return {"url": url.rstrip("/"), "name": name if isinstance(name, str) else "", "v": obj.get("v")}


def discover(timeout: float = 2.0, tries: int = 3, port: int = DISCOVERY_PORT, *,
             hostname: Optional[str] = None,
             sock_factory: Callable[..., socket.socket] = socket.socket,
             local_ip_fn: Callable[[], Optional[str]] = default_route_ip,
             clock: Callable[[], float] = time.monotonic) -> list[dict]:
    """서버를 찾아 `[{url, name, v}, …]`로 돌려준다 (없으면 빈 목록, 중복 제거, 응답 순서 유지).

    한 번 시도할 때마다 `timeout`초 동안 응답을 모은다(여러 서버가 답할 수 있다). **응답이 하나라도
    모이면 거기서 멈추고** 다음 시도를 하지 않는다 — 요청이 유실될 수 있는 UDP라서 `tries`번까지 재시도한다.
    """
    payload = REQUEST_PREFIX + json.dumps(
        {"v": PROTOCOL_VERSION, "hostname": hostname or socket.gethostname()}, ensure_ascii=False).encode("utf-8")
    targets = broadcast_targets(local_ip_fn())

    try:
        sock = sock_factory(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError:
        return []
    found: dict[str, dict] = {}
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            sock.bind(("", 0))
        except OSError:
            return []
        for _ in range(max(1, tries)):
            sent = 0
            for target in targets:
                try:
                    sock.sendto(payload, (target, port))
                    sent += 1
                except OSError:
                    continue                                # 그 방향 망이 없을 수 있다 — 다른 쪽으로 계속
            if sent == 0:
                continue
            deadline = clock() + timeout
            while True:
                remaining = deadline - clock()
                if remaining <= 0:
                    break
                sock.settimeout(remaining)
                try:
                    data, _addr = sock.recvfrom(_MAX_RESPONSE_BYTES + 1)
                except (socket.timeout, TimeoutError):
                    break
                except OSError:
                    break
                server = parse_response(data)
                if server is None:
                    continue
                found.setdefault(url_key(server["url"]), server)
            if found:
                break
    finally:
        try:
            sock.close()
        except OSError:
            pass
    return list(found.values())
