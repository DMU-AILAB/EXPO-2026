#!/usr/bin/env python3
"""VisionGuide Pi 자동 탐색 도구

로컬 서브넷을 병렬 스캔해서 포트 5000에서 실행 중인 VisionGuide 대시보드를 찾습니다.
표준 라이브러리만 사용 — 별도 설치 불필요.

사용법:
    python discover.py            # 탐색 후 URL 출력
    python discover.py --open     # 탐색 후 브라우저 자동 실행
    python discover.py --subnet 10.0.0  # 서브넷 직접 지정
"""
import argparse
import concurrent.futures
import json
import socket
import sys
import urllib.request
from urllib.error import URLError

PORT = 5000
TIMEOUT = 0.4       # 포트 연결 타임아웃 (초)
API_TIMEOUT = 1.5   # API 확인 타임아웃 (초)
MAX_WORKERS = 80    # 병렬 스캔 수


def _get_local_subnet() -> str:
    """현재 PC의 기본 게이트웨이 방향 IP에서 서브넷 추출."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return ".".join(local_ip.split(".")[:3])
    except Exception:
        return "192.168.0"


def _check(ip: str) -> dict | None:
    """포트 열려 있고 /api/network/status 응답이 VisionGuide인지 확인."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(TIMEOUT)
        if s.connect_ex((ip, PORT)) != 0:
            s.close()
            return None
        s.close()
    except Exception:
        return None

    # 포트가 열렸으면 API 확인
    try:
        url = f"http://{ip}:{PORT}/api/network/status"
        with urllib.request.urlopen(url, timeout=API_TIMEOUT) as r:
            data = json.loads(r.read().decode())
        return {"ip": ip, **data}
    except Exception:
        # /api/network/status 없어도 포트 5000이 열려있으면 후보로 포함
        return {"ip": ip, "mode": "unknown", "ssid": None,
                "hostname": None, "url": f"http://{ip}:{PORT}"}


def scan(subnet: str) -> list[dict]:
    results = []
    ips = [f"{subnet}.{i}" for i in range(1, 255)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(_check, ip): ip for ip in ips}
        done = 0
        for f in concurrent.futures.as_completed(futures):
            done += 1
            # 진행 표시
            bar_len = 30
            filled = int(bar_len * done / 254)
            bar = "█" * filled + "░" * (bar_len - filled)
            print(f"\r  [{bar}] {done}/254", end="", flush=True)
            result = f.result()
            if result:
                results.append(result)
    print()  # 줄바꿈
    return results


def main():
    parser = argparse.ArgumentParser(description="VisionGuide Pi 자동 탐색")
    parser.add_argument("--open", action="store_true", help="찾은 후 브라우저 자동 실행")
    parser.add_argument("--subnet", default=None,
                        help="서브넷 지정 (예: 192.168.1). 미지정 시 자동 감지")
    args = parser.parse_args()

    subnet = args.subnet or _get_local_subnet()
    print(f"\n🔍  VisionGuide Pi 탐색 중 — {subnet}.1-254 포트 {PORT}")
    print(f"    (mDNS가 동작하면 http://visionguide.local:{PORT} 도 시도해 보세요)\n")

    found = scan(subnet)

    if not found:
        print("\n  Pi를 찾지 못했습니다.")
        print("  확인 사항:")
        print(f"    • Pi와 이 PC가 같은 Wi-Fi(서브넷)에 있는지")
        print(f"    • Pi에서 visionguide-roi-editor 서비스가 실행 중인지")
        print(f"    • 서브넷이 {subnet}.x 가 맞는지  (--subnet 옵션으로 지정 가능)")
        sys.exit(1)

    print(f"\n  ✅  {len(found)}개 발견:\n")
    for d in found:
        url = f"http://{d['ip']}:{PORT}"
        hostname = d.get("hostname") or ""
        ssid     = d.get("ssid") or ""
        mode     = d.get("mode") or ""
        label = " | ".join(filter(None, [hostname, ssid, mode]))
        print(f"    {url}  {('(' + label + ')') if label else ''}")

    if args.open:
        import webbrowser
        target = f"http://{found[0]['ip']}:{PORT}"
        print(f"\n  브라우저에서 {target} 를 열고 있습니다...")
        webbrowser.open(target)


if __name__ == "__main__":
    main()
