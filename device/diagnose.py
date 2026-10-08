"""diagnose.py — 기기가 **스스로** 확인할 수 있는 연결·전원·시계 상태.

대시보드의 "연결 진단"이 서버 쪽에서는 알 수 없는 것을 기기에게 묻는 용도다.
- 기기 → 서버 접속: 서버가 기기에 닿는다고 해서 기기가 서버에 닿는 것은 아니다(이번에
  Windows 방화벽이 한쪽만 막고 있었다). 단계(DNS → TCP → HTTP)를 나눠 어디서 끊기는지 낸다.
- 전원: `vcgencmd get_throttled`. 저전압 여부를 화면에서 답한다.
- 시계: NTP 동기 여부. 이벤트 타임스탬프를 기기가 찍으므로 어긋나면 통계가 엉킨다.

표준 라이브러리만 쓴다. 외부 호출(소켓·서브프로세스)은 인자로 주입해 테스트한다.
**`GET /api/device/status`의 스키마는 늘리지 않는다**(기기 탐색이 그 바디로 판별) —
그래서 별도 엔드포인트(`/api/diagnose`)로 낸다.
"""

from __future__ import annotations

import importlib.util
import socket
import subprocess
import time
import urllib.error
import urllib.request
from typing import Callable, Optional
from urllib.parse import urlsplit

__all__ = ["parse_throttled", "read_throttled", "check_server", "ntp_synchronized",
           "REQUIRED_MODULES", "check_python_modules"]

# 기기에 **설치돼 있어야 하는 파이썬 패키지** — import 이름 → (pip 패키지, 없으면 멈추는 기능).
# ★ `deploy/install.sh`의 PIP_PACKAGES와 같은 집합이어야 한다(`tests/test_pi_dependencies.py`가 대조하고, 코드가
# import하는 서드파티 모듈이 여기나 선택 목록에 빠져 있으면 실패한다).
# 이 점검이 있는 이유: **푸시 업데이트는 코드만 올리고 pip를 실행하지 않는다.** 새 기능이 패키지를 추가해도(예: ESP32
# 중계의 bleak) 푸시로 갱신된 기기에는 설치되지 않고, 그 기능만 조용히 "Pi 패키지가 없습니다"로 멈춘다.
REQUIRED_MODULES: dict[str, tuple[str, str]] = {
    "ai_edge_litert": ("ai-edge-litert", "CPU TFLite 추론"),
    "cv2": ("opencv-python-headless", "카메라·영상 처리"),
    "numpy": ("numpy", "추론·영상 처리"),
    "shapely": ("shapely", "ROI 판정"),
    "PIL": ("pillow", "한글 오버레이"),
    "spidev": ("spidev", "RF 수신(SI4432)"),
    "gpiozero": ("gpiozero", "버튼·LED·팬·부저"),
    "lgpio": ("lgpio", "GPIO 백엔드"),
    "dbus_next": ("dbus-next", "BLE Wi-Fi 페어링"),
    "bleak": ("bleak", "ESP32 BLE 중계"),
    "fastapi": ("fastapi", "Pi API(:5000)"),
    "uvicorn": ("uvicorn[standard]", "Pi API(:5000)"),
    "multipart": ("python-multipart", "오디오 업로드"),
}
# 패키지가 import 이름을 바꾼 경우의 대체 이름 — 하나라도 있으면 설치된 것이다.
_ALIASES = {"multipart": ("python_multipart",)}


def check_python_modules(find_spec: Callable = importlib.util.find_spec) -> dict:
    """필수 모듈 중 **설치되지 않은 것**을 낸다: `{"checked": N, "missing": [{module, package, feature}]}`.

    import하지 않고 `find_spec`으로만 찾는다 — cv2·ai_edge_litert를 불러오면 느리고 메모리를 쓴다.
    """
    def present(name: str) -> bool:
        try:
            return find_spec(name) is not None
        except (ImportError, ValueError, AttributeError):
            return False

    missing = []
    for module, (package, feature) in REQUIRED_MODULES.items():
        if not any(present(n) for n in (module, *_ALIASES.get(module, ()))):
            missing.append({"module": module, "package": package, "feature": feature})
    return {"checked": len(REQUIRED_MODULES), "missing": missing}


# vcgencmd get_throttled 비트 (라즈베리파이 공식 문서)
_NOW = {0: "undervoltage", 1: "freq_capped", 2: "throttled", 3: "soft_temp_limit"}
_EVER = {16: "undervoltage", 17: "freq_capped", 18: "throttled", 19: "soft_temp_limit"}


def parse_throttled(raw: int) -> dict:
    """`get_throttled` 정수를 현재/과거 플래그로 푼다."""
    return {
        "raw": f"0x{raw:x}",
        "now": {name: bool(raw & (1 << bit)) for bit, name in _NOW.items()},
        "ever": {name: bool(raw & (1 << bit)) for bit, name in _EVER.items()},
    }


def read_throttled(run: Callable = subprocess.run) -> Optional[dict]:
    """`vcgencmd get_throttled`. 명령이 없거나 실패하면 None(라즈베리파이가 아닌 환경)."""
    try:
        res = run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode != 0:
        return None
    try:
        return parse_throttled(int(res.stdout.strip().split("=", 1)[1], 16))
    except (IndexError, ValueError):
        return None


def ntp_synchronized(run: Callable = subprocess.run) -> Optional[bool]:
    """시계가 NTP로 맞춰졌는가. 알 수 없으면 None."""
    try:
        res = run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                  capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode != 0:
        return None
    value = res.stdout.strip().lower()
    return True if value == "yes" else False if value == "no" else None


def _http_get(url: str, timeout: float) -> int:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:      # noqa: S310 (신원의 server_url)
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code                    # 4xx/5xx도 "서버에 닿았다"는 뜻이다


def check_server(server_url: str, timeout: float = 2.0,
                 resolve: Callable = socket.getaddrinfo,
                 connect: Callable = socket.create_connection,
                 http_get: Callable = _http_get) -> dict:
    """서버까지 DNS → TCP → HTTP 순으로 확인한다. 앞 단계가 실패하면 뒤는 건너뛴다.

    각 단계는 `{ok, ms?, detail?}`. 어느 단계에서 끊겼는지가 원인이다 —
    DNS 실패는 이름(`.local` 등)을 못 푼다는 뜻, TCP 타임아웃은 방화벽/경로, HTTP 실패는
    서버가 응답하지 않는다는 뜻이다.
    """
    out: dict = {"url": server_url, "dns": None, "tcp": None, "http": None}
    parts = urlsplit(server_url or "")
    host, port = parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)
    if not host:
        out["dns"] = {"ok": False, "detail": "서버 주소가 비어 있습니다"}
        return out

    t = time.monotonic()
    try:
        infos = resolve(host, port, type=socket.SOCK_STREAM)
        out["dns"] = {"ok": True, "ms": round((time.monotonic() - t) * 1000),
                      "detail": infos[0][4][0] if infos else None}
    except OSError as exc:
        out["dns"] = {"ok": False, "detail": f"이름을 해석하지 못했습니다: {exc}"}
        return out

    t = time.monotonic()
    try:
        sock = connect((host, port), timeout=timeout)
        sock.close()
        out["tcp"] = {"ok": True, "ms": round((time.monotonic() - t) * 1000)}
    except OSError as exc:
        out["tcp"] = {"ok": False, "detail": f"{host}:{port}에 연결하지 못했습니다: {exc}"}
        return out

    t = time.monotonic()
    try:
        status = http_get(f"{parts.scheme}://{parts.netloc}/", timeout)
        out["http"] = {"ok": status < 500, "ms": round((time.monotonic() - t) * 1000), "detail": f"HTTP {status}"}
    except (OSError, ValueError) as exc:
        out["http"] = {"ok": False, "detail": f"HTTP 요청이 실패했습니다: {exc}"}
    return out
