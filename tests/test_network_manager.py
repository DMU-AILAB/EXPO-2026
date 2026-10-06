"""apps/roi_editor/network_manager.py — 핫스팟에서의 검색·연결 (nmcli는 가짜).

실기기 실험에서 확인한 것: 핫스팟(AP)인 동안에는 `--rescan yes`도 예전 기록을 돌려주고,
그 기록은 몇 분 뒤 사라져 목록에 자기 자신만 남고 연결이 "network could not be found"로
실패했다. `device disconnect` 후 다시 검색하면 실제 목록이 나온다.
"""

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "roi_editor"))
import network_manager as nm   # noqa: E402


class FakeNmcli:
    def __init__(self, mode="ap", up_ok=True, listing="Home:80:WPA2\nVisionGuide-Pi:0:WPA2\n:44:\nCafe:50:\n"):
        self.mode, self.up_ok, self.listing = mode, up_ok, listing
        self.calls: list[list[str]] = []

    def __call__(self, cmd, timeout=10):
        if cmd and cmd[0] == "sudo":
            cmd = cmd[1:]
        self.calls.append(cmd)
        ok = lambda out="": types.SimpleNamespace(returncode=0, stdout=out, stderr="")
        if cmd[:3] == ["nmcli", "-g", "802-11-wireless.ssid"]:
            return ok("VisionGuide-Pi\n")
        if cmd[:3] == ["nmcli", "-g", "GENERAL.CONNECTION"]:
            return ok({"ap": "VisionGuide-AP", "station": "Home", "off": ""}[self.mode] + "\n")
        if cmd[:3] == ["nmcli", "-g", "IP4.ADDRESS"]:
            return ok("192.168.0.50/24\n")
        if cmd[:4] == ["nmcli", "device", "disconnect", "wlan0"]:
            self.mode = "off"
            return ok()
        if cmd[:3] == ["nmcli", "-f", "SSID,SIGNAL,SECURITY"]:
            return ok(self.listing)
        if cmd[:3] == ["nmcli", "-g", "NAME"]:
            return ok("Home\nVisionGuide-AP\n")
        if cmd[:3] == ["nmcli", "connection", "up"]:
            target = cmd[3]
            if target == "VisionGuide-AP":
                self.mode = "ap"
                return ok()
            if self.up_ok:
                self.mode = "station"
                return ok()
            return types.SimpleNamespace(returncode=4, stdout="", stderr="network could not be found")
        return ok()

    def did(self, *prefix):
        return any(c[:len(prefix)] == list(prefix) for c in self.calls)


@pytest.fixture
def fake(monkeypatch):
    f = FakeNmcli()
    monkeypatch.setattr(nm, "_run", f)
    monkeypatch.setattr(nm, "_ap_ssid_cache", None)
    monkeypatch.setattr(nm, "_RESCAN_SETTLE_S", 0)
    monkeypatch.setattr(nm.time, "sleep", lambda s: None)
    monkeypatch.setattr(nm, "_apply_captive_portal", lambda: None)
    monkeypatch.setattr(nm, "_remove_captive_portal", lambda: None)
    return f


def test_scan_excludes_own_hotspot_and_blank(fake):
    ssids = [n["ssid"] for n in nm.scan_networks()]
    assert ssids == ["Home", "Cafe"]          # 자기 핫스팟(VisionGuide-Pi)·이름 없는 것 제외


def test_fresh_scan_releases_and_restores_ap(fake):
    nets = nm.scan_networks_fresh()
    assert [n["ssid"] for n in nets] == ["Home", "Cafe"]
    assert fake.did("nmcli", "device", "disconnect", "wlan0")
    assert fake.did("nmcli", "device", "wifi", "rescan")
    assert fake.mode == "ap"                   # 끝나면 핫스팟으로 되돌린다


def test_fresh_scan_on_station_does_not_touch_connection(fake):
    fake.mode = "station"
    nm.scan_networks_fresh()
    assert not fake.did("nmcli", "device", "disconnect", "wlan0")
    assert not fake.did("nmcli", "connection", "up", "VisionGuide-AP")


def test_connect_release_ap_success(fake):
    nm._do_connect("Home", "", 0, release_ap=True)
    assert fake.did("nmcli", "device", "disconnect", "wlan0")
    assert fake.mode == "station"
    assert nm.get_connect_result()["status"] == "ok"


def test_connect_failure_restores_ap(fake):
    fake.up_ok = False
    nm._do_connect("Home", "", 0, release_ap=True)
    assert nm.get_connect_result()["status"] == "error"
    assert fake.mode == "ap"                   # 어디에도 안 붙은 채로 남지 않는다


def test_connect_without_release_keeps_old_behavior(fake):
    # Pi 화면(:5000) 경로 — 휴대폰이 핫스팟에 붙어 있으므로 미리 내리지 않는다.
    nm._do_connect("Home", "", 0)
    assert not fake.did("nmcli", "device", "disconnect", "wlan0")
