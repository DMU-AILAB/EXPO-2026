"""Wi-Fi / NetworkManager nmcli 래퍼

대시보드 Wi-Fi 온보딩 기능에서 사용.
ailab 유저로 실행되며, nmcli/iptables는 NOPASSWD sudoers 규칙을 통해 호출한다.
(/etc/sudoers.d/visionguide-network 에 설정됨)
"""
import socket
import subprocess
import threading
import time

AP_CONNECTION = "VisionGuide-AP"
AP_IP = "192.168.4.1"
WIFI_DEVICE = "wlan0"
CONNECT_DELAY_S = 3
_DASHBOARD_PORT = "5000"
_DNSMASQ_CONF = "/etc/NetworkManager/dnsmasq.d/visionguide-captive.conf"

_connect_lock = threading.Lock()
_connect_result: dict | None = None
_connect_in_progress: bool = False


def _run(cmd: list[str], timeout: int = 10) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, check=False, timeout=timeout
    )


def _sudo(cmd: list[str], timeout: int = 10) -> subprocess.CompletedProcess:
    """NOPASSWD sudoers 규칙을 이용한 권한 필요 명령 실행."""
    return _run(["sudo"] + cmd, timeout=timeout)


def get_status() -> dict:
    """현재 wlan0 연결 상태 반환.

    Returns:
        {mode: "ap"|"station"|"disconnected", ssid: str|None, ip: str|None, hostname: str}
    """
    try:
        r_conn = _run(["nmcli", "-g", "GENERAL.CONNECTION", "device", "show", WIFI_DEVICE])
        conn = r_conn.stdout.strip() if r_conn.returncode == 0 else ""

        r_ip = _run(["nmcli", "-g", "IP4.ADDRESS", "device", "show", WIFI_DEVICE])
        raw_ip = r_ip.stdout.strip().split("\n")[0] if r_ip.returncode == 0 else ""
        ip = raw_ip.split("/")[0] if raw_ip and raw_ip != "--" else None

        if not conn or conn == "--":
            mode = "disconnected"
            ssid = None
        elif conn == AP_CONNECTION:
            mode = "ap"
            ssid = conn
            if not ip:
                ip = AP_IP
        else:
            mode = "station"
            ssid = conn

    except Exception:
        mode = "disconnected"
        ssid = None
        ip = None

    return {
        "mode": mode,
        "ssid": ssid,
        "ip": ip,
        "hostname": socket.gethostname(),
    }


def scan_networks() -> list[dict]:
    """주변 Wi-Fi 네트워크 목록 반환.

    Returns:
        [{"ssid": str, "signal_pct": int, "security": str}, ...]
        신호 강도 내림차순, SSID 중복 제거(최고 신호만 유지).
    """
    try:
        r = _run(
            ["nmcli", "-f", "SSID,SIGNAL,SECURITY", "--terse", "device", "wifi", "list"],
            timeout=15,
        )
        lines = r.stdout.strip().splitlines() if r.stdout else []
    except Exception:
        return []

    own = _ap_ssid()
    seen: dict[str, dict] = {}
    for line in lines:
        parts = line.split(":")
        if len(parts) < 2:
            continue
        ssid = parts[0].strip()
        if not ssid or ssid == "--" or ssid == own:
            continue   # 자기 핫스팟은 연결 대상이 아니다
        try:
            signal_pct = int(parts[1].strip())
        except (ValueError, IndexError):
            signal_pct = 0
        security = parts[2].strip() if len(parts) > 2 else ""
        if ssid not in seen or seen[ssid]["signal_pct"] < signal_pct:
            seen[ssid] = {"ssid": ssid, "signal_pct": signal_pct, "security": security}

    return sorted(seen.values(), key=lambda x: x["signal_pct"], reverse=True)


_ap_ssid_cache: str | None = None


def _ap_ssid() -> str | None:
    """핫스팟 프로필의 실제 SSID. 프로필 이름(`VisionGuide-AP`)과 다르다(예: `VisionGuide-Pi`)."""
    global _ap_ssid_cache
    if _ap_ssid_cache is None:
        try:
            r = _run(["nmcli", "-g", "802-11-wireless.ssid", "connection", "show", AP_CONNECTION])
            _ap_ssid_cache = r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            _ap_ssid_cache = ""
    return _ap_ssid_cache or None


# 핫스팟(AP)으로 동작하는 동안 wlan0은 **새로 검색하지 못한다** — `--rescan yes`도 예전
# 기록을 그대로 돌려준다. 그 기록은 몇 분 뒤 사라져서, 오래 켜 둔 핫스팟에서는 목록에
# 자기 자신만 남고 연결도 "The Wi-Fi network could not be found"로 실패했다(실기기 실험).
# 칩을 잠깐 비우면(`device disconnect`) 실제로 다시 검색된다. `device disconnect`는
# 자동 재연결도 막아 주므로 그 사이 홈 Wi-Fi로 저절로 붙지 않는다.
#
# ★ 블루투스 페어링 경로에서만 쓴다. Pi 화면(:5000)은 휴대폰이 바로 그 핫스팟으로
#   붙어 있어 AP를 내리면 화면이 끊긴다.
_RESCAN_SETTLE_S = 4


def _release_ap_and_rescan() -> bool:
    """AP 모드면 내리고 다시 검색한다. AP였으면 True(호출부가 되돌릴지 정한다)."""
    was_ap = get_status().get("mode") == "ap"
    if was_ap:
        _sudo(["nmcli", "device", "disconnect", WIFI_DEVICE], timeout=10)
        time.sleep(1)
    _sudo(["nmcli", "device", "wifi", "rescan", "ifname", WIFI_DEVICE], timeout=10)
    time.sleep(_RESCAN_SETTLE_S)
    return was_ap


def _restore_ap() -> None:
    try:
        switch_to_ap()
    except Exception as e:   # noqa: BLE001 — 되돌리기 실패가 원래 결과를 가리면 안 된다
        print(f"[network] 핫스팟 복구 실패: {e}")


def scan_networks_fresh() -> list[dict]:
    """실제로 다시 검색한 목록. AP 모드였으면 끝난 뒤 AP를 다시 올린다(블루투스 경로용)."""
    was_ap = _release_ap_and_rescan()
    try:
        return scan_networks()
    finally:
        if was_ap:
            _restore_ap()


def _do_connect(ssid: str, password: str, delay_seconds: int, release_ap: bool = False) -> None:
    """백그라운드 스레드 — delay 후 실제 nmcli 연결 수행."""
    global _connect_result, _connect_in_progress

    time.sleep(delay_seconds)
    was_ap = False
    ok = False
    try:
        if release_ap:
            # 핫스팟에서는 대상 네트워크가 검색 기록에서 사라져 있을 수 있다 — 먼저 비우고 검색.
            was_ap = _release_ap_and_rescan()
        # 기존 프로필 존재 여부 확인 (읽기 전용 — sudo 불필요)
        r_list = _run(["nmcli", "-g", "NAME", "connection", "show"], timeout=5)
        existing = [n.strip() for n in r_list.stdout.splitlines()] if r_list.returncode == 0 else []

        if ssid in existing:
            # 프로필 존재 → 비밀번호 업데이트 후 연결
            if password:
                _sudo(["nmcli", "connection", "modify", ssid, "wifi-sec.psk", password], timeout=5)
            r = _sudo(["nmcli", "connection", "up", ssid], timeout=30)
        else:
            # 프로필 없음 → 새로 연결 (프로필 자동 생성)
            cmd = ["nmcli", "device", "wifi", "connect", ssid]
            if password:
                cmd += ["password", password]
            r = _sudo(cmd, timeout=30)

        if r.returncode != 0:
            err_text = (r.stderr or r.stdout or "nmcli 오류").strip()
            with _connect_lock:
                _connect_result = {"status": "error", "error": err_text}
            return

        # DHCP IP 할당 대기
        time.sleep(2)
        r_ip = _run(["nmcli", "-g", "IP4.ADDRESS", "device", "show", WIFI_DEVICE])
        raw = r_ip.stdout.strip().split("\n")[0] if r_ip.returncode == 0 else ""
        new_ip = raw.split("/")[0] if raw and raw != "--" else None

        _remove_captive_portal()
        ok = True
        with _connect_lock:
            _connect_result = {"status": "ok", "ip": new_ip}

    except subprocess.TimeoutExpired:
        with _connect_lock:
            _connect_result = {"status": "error", "error": "연결 시간 초과"}
    except Exception as e:
        with _connect_lock:
            _connect_result = {"status": "error", "error": str(e)}
    finally:
        # 핫스팟에서 시작했는데 실패하면 핫스팟으로 되돌린다 — 그대로 두면 어디에도 붙지
        # 않은 기기가 되어, 비밀번호를 고쳐 다시 시도할 길(핫스팟 화면)도 사라진다.
        if was_ap and not ok:
            _restore_ap()
        with _connect_lock:
            _connect_in_progress = False


def connect_wifi(ssid: str, password: str, delay_seconds: int = CONNECT_DELAY_S,
                 release_ap: bool = False) -> None:
    """Wi-Fi 연결을 백그라운드에서 시작. 즉시 반환.

    `release_ap=True`(블루투스 경로)면 핫스팟을 먼저 내려 다시 검색한 뒤 연결하고,
    실패하면 핫스팟으로 되돌린다.
    """
    global _connect_result, _connect_in_progress
    with _connect_lock:
        _connect_in_progress = True
        _connect_result = None
    t = threading.Thread(target=_do_connect, args=(ssid, password, delay_seconds, release_ap),
                         daemon=True)
    t.start()


def get_connect_result() -> dict | None:
    """마지막 connect_wifi() 결과 반환. None이면 미시도 또는 진행 중."""
    with _connect_lock:
        return dict(_connect_result) if _connect_result else None


def is_connect_in_progress() -> bool:
    with _connect_lock:
        return _connect_in_progress


def _apply_captive_portal() -> None:
    """AP 캡티브 포털용 dnsmasq 설정 + iptables 규칙 적용."""
    import pathlib
    try:
        # dnsmasq conf 디렉토리 생성 및 파일 작성은 sudo tee로
        conf_content = f"address=/#/{AP_IP}\n"
        subprocess.run(
            ["sudo", "tee", _DNSMASQ_CONF],
            input=conf_content, capture_output=True, text=True,
        )
        # 부모 디렉토리가 없을 수 있어 mkdir 먼저
        _sudo(["mkdir", "-p", str(pathlib.Path(_DNSMASQ_CONF).parent)])
        subprocess.run(
            ["sudo", "tee", _DNSMASQ_CONF],
            input=conf_content, capture_output=True, text=True,
        )
        _sudo(["pkill", "-HUP", "-f", "dnsmasq"])
    except Exception:
        pass
    # 중복 방지 후 iptables 규칙 추가
    chk = _sudo(["iptables", "-t", "nat", "-C", "PREROUTING",
                  "-i", WIFI_DEVICE, "-p", "tcp", "--dport", "80",
                  "-j", "REDIRECT", "--to-port", _DASHBOARD_PORT])
    if chk.returncode != 0:
        _sudo(["iptables", "-t", "nat", "-A", "PREROUTING",
               "-i", WIFI_DEVICE, "-p", "tcp", "--dport", "80",
               "-j", "REDIRECT", "--to-port", _DASHBOARD_PORT])


def _remove_captive_portal() -> None:
    """캡티브 포털 설정 제거 (Station 모드 전환 시)."""
    try:
        _sudo(["rm", "-f", _DNSMASQ_CONF])
        _sudo(["pkill", "-HUP", "-f", "dnsmasq"])
    except Exception:
        pass
    _sudo(["iptables", "-t", "nat", "-D", "PREROUTING",
           "-i", WIFI_DEVICE, "-p", "tcp", "--dport", "80",
           "-j", "REDIRECT", "--to-port", _DASHBOARD_PORT])


def switch_to_ap() -> None:
    """AP 모드로 동기 전환. 실패 시 RuntimeError."""
    r = _sudo(["nmcli", "connection", "up", AP_CONNECTION], timeout=15)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "AP 전환 실패").strip())
    _apply_captive_portal()


if __name__ == "__main__":
    import json
    print("=== network status ===")
    print(json.dumps(get_status(), ensure_ascii=False, indent=2))
    print("=== scan networks ===")
    print(json.dumps(scan_networks(), ensure_ascii=False, indent=2))
