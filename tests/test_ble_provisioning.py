"""BLE 페어링의 순수 로직 — 허용 판정·명령 파싱·긴 쓰기 조립·결과 상한·연결 흐름.

BlueZ가 없는 PC에서 돈다. D-Bus 객체는 **만들어지는지까지만** 본다 — dbus-next가
그 단계에서 메서드 서명('a{sv}' 등)을 검증하므로 서명 오타는 여기서 잡힌다.
"""

import asyncio
import json

import pytest

import ble_provisioning as bp


# ------------------------------------------------------------------ 페어링 창

def test_window_closed_when_missing(tmp_path):
    assert not bp.pairing_window_open(tmp_path / "w.json")


def test_window_open_then_expires(tmp_path):
    p = tmp_path / "w.json"
    bp.open_window(p, seconds=180, now=1000.0)
    assert bp.pairing_window_open(p, now=1100.0)
    assert bp.window_remaining(p, now=1100.0) == pytest.approx(80.0)
    assert not bp.pairing_window_open(p, now=1181.0)


def test_window_file_is_world_readable(tmp_path):
    # 버튼 서비스는 root, BLE 서비스는 ailab으로 돈다 — 0600이면 창이 안 열린 것으로 보인다.
    p = tmp_path / "w.json"
    bp.open_window(p)
    assert p.stat().st_mode & 0o044 == 0o044


def test_close_window(tmp_path):
    p = tmp_path / "w.json"
    bp.open_window(p, now=0)
    bp.close_window(p)
    bp.close_window(p)   # 두 번 닫아도 된다
    assert not p.exists()


def test_corrupt_window_file_counts_as_closed(tmp_path):
    p = tmp_path / "w.json"
    p.write_text("{not json", encoding="utf-8")
    assert not bp.pairing_window_open(p)


@pytest.mark.parametrize("mode,window,expected", [
    ("disconnected", True, True),
    ("ap", True, True),
    ("station", True, False),     # 운영 중인 기기는 창이 열려 있어도 거부
    ("disconnected", False, False),
])
def test_pairing_allowed(mode, window, expected):
    assert bp.pairing_allowed(mode, window) is expected


# ------------------------------------------------------------------ 명령

def _cmd(obj) -> bytes:
    return json.dumps(obj).encode()


def test_parse_scan():
    assert bp.parse_command(_cmd({"op": "scan"})) == {"op": "scan"}


def test_parse_connect_strips_ssid():
    got = bp.parse_command(_cmd({"op": "connect", "ssid": " Home ", "psk": "password1"}))
    assert got == {"op": "connect", "ssid": "Home", "psk": "password1"}


def test_parse_connect_open_network():
    assert bp.parse_command(_cmd({"op": "connect", "ssid": "Cafe"}))["psk"] == ""


@pytest.mark.parametrize("obj", [
    {"op": "connect", "ssid": ""},
    {"op": "connect", "ssid": "x" * 33},
    {"op": "connect", "ssid": "한" * 11},          # 33바이트
    {"op": "connect", "ssid": "a", "psk": "short"},
    {"op": "connect", "ssid": "a", "psk": "p" * 64},
    {"op": "reboot"},
    ["scan"],
])
def test_parse_rejects(obj):
    with pytest.raises(bp.CommandError):
        bp.parse_command(_cmd(obj))


def test_parse_rejects_non_json():
    with pytest.raises(bp.CommandError):
        bp.parse_command(b"\xff\xfe")


# ------------------------------------------------------------------ 긴 쓰기

def test_assembler_joins_chunks_by_offset():
    data = _cmd({"op": "connect", "ssid": "HomeNetwork", "psk": "averylongpassword"})
    a = bp.WriteAssembler()
    out = None
    for off in range(0, len(data), 18):            # MTU 23 → 18바이트씩(prepared write)
        out = a.feed(data[off:off + 18], off)
        if off + 18 < len(data):
            assert out is None
    assert out == data


def test_assembler_resets_on_offset_zero():
    a = bp.WriteAssembler()
    assert a.feed(b'{"op":"sc', 0) is None
    assert a.feed(b'{"op":"scan"}', 0) == b'{"op":"scan"}'


def test_assembler_drops_gap():
    a = bp.WriteAssembler()
    assert a.feed(b'{"op":', 0) is None
    assert a.feed(b'"scan"}', 40) is None          # 중간 조각을 잃었다


def test_assembler_rejects_oversized():
    a = bp.WriteAssembler()
    with pytest.raises(bp.CommandError):
        a.feed(b"[" + b"1," * 300, 0)


# ------------------------------------------------------------------ 결과 인코딩

def _res(prov) -> dict:
    """순번 `n`을 뺀 결과."""
    obj = json.loads(prov.result)
    obj.pop("n")
    return obj


def test_scan_payload_fits_limit_with_seq_and_keeps_strongest():
    nets = [{"ssid": f"network-{i:02d}-padding", "signal_pct": 100 - i, "security": "WPA2"}
            for i in range(60)]
    prov = bp.Provisioner(FakeNM())
    prov._set(bp.scan_payload(nets))
    prov.seq = 255                                   # 가장 긴 순번으로도 상한 안이어야 한다
    assert len(prov.result) <= bp.MAX_VALUE
    obj = _res(prov)
    assert obj["state"] == "scanned"
    assert obj["networks"][0] == {"s": "network-00-padding", "q": 100, "l": 1}
    assert 0 < len(obj["networks"]) < 60          # 잘렸지만 비지 않았다


# ------------------------------------------------------------------ 연결 흐름

class FakeNM:
    def __init__(self, mode="disconnected"):
        self.mode = mode
        self.in_progress = False
        self.result = None
        self.connected_with = None

    def get_status(self):
        return {"mode": self.mode, "ssid": None, "ip": None, "hostname": "pi"}

    def scan_networks(self):
        return [{"ssid": "Home", "signal_pct": 80, "security": "WPA2"}]

    def scan_networks_fresh(self):
        self.fresh_scans = getattr(self, "fresh_scans", 0) + 1
        return self.scan_networks()

    def connect_wifi(self, ssid, password, delay_seconds=3, release_ap=False):
        self.connected_with = (ssid, password)
        self.release_ap = release_ap
        self.in_progress = True

    def get_connect_result(self):
        return self.result

    def is_connect_in_progress(self):
        return self.in_progress


def test_scan_sets_result_and_bumps_seq(tmp_path):
    changes = []
    prov = bp.Provisioner(FakeNM(), on_change=lambda: changes.append(1), window=tmp_path / "w")
    prov.handle({"op": "scan"})
    assert _res(prov)["networks"][0]["s"] == "Home"
    assert prov.seq == 2 and len(changes) == 2      # scanning → scanned
    assert json.loads(prov.result)["n"] == 2        # 클라이언트는 n이 바뀐 결과만 이번 것으로 본다


def test_connect_success_reports_ip_and_closes_window(tmp_path):
    w = tmp_path / "w.json"
    bp.open_window(w)
    nm = FakeNM()
    prov = bp.Provisioner(nm, window=w)
    prov.handle({"op": "connect", "ssid": "Home", "psk": "password1"})
    assert nm.connected_with == ("Home", "password1")
    assert _res(prov)["state"] == "connecting"
    assert prov.poll_connect() is False             # 아직 진행 중

    nm.in_progress = False
    nm.result = {"status": "ok", "ip": "192.168.0.50"}
    assert prov.poll_connect() is True
    assert _res(prov) == {"state": "connected", "ip": "192.168.0.50"}
    assert not w.exists()                            # 목적을 이뤘으니 창을 닫는다
    assert prov.poll_connect() is False              # 같은 결과를 두 번 싣지 않는다


def test_connect_failure_keeps_window(tmp_path):
    w = tmp_path / "w.json"
    bp.open_window(w)
    nm = FakeNM()
    prov = bp.Provisioner(nm, window=w)
    prov.handle({"op": "connect", "ssid": "Home", "psk": "wrongpass1"})
    nm.in_progress = False
    nm.result = {"status": "error", "error": "Secrets were required"}
    assert prov.poll_connect() is True
    assert _res(prov)["state"] == "failed"
    assert w.exists()                                # 비밀번호를 다시 시도할 수 있다


def test_stale_result_is_ignored_without_command(tmp_path):
    # 기기 화면(5000)에서 했던 예전 연결 결과를 BLE 결과로 오인하면 안 된다.
    nm = FakeNM()
    nm.result = {"status": "ok", "ip": "10.0.0.9"}
    prov = bp.Provisioner(nm, window=tmp_path / "w")
    assert prov.poll_connect() is False


def test_connect_while_in_progress_rejected(tmp_path):
    nm = FakeNM()
    nm.in_progress = True
    prov = bp.Provisioner(nm, window=tmp_path / "w")
    with pytest.raises(bp.CommandError):
        prov.handle({"op": "connect", "ssid": "Home", "psk": "password1"})


def test_allowed_uses_wifi_mode_and_window(tmp_path):
    w = tmp_path / "w.json"
    nm = FakeNM(mode="disconnected")
    prov = bp.Provisioner(nm, window=w)
    assert not prov.allowed()
    bp.open_window(w)
    assert prov.allowed()
    nm.mode = "station"
    assert not prov.allowed()


def test_info_payload(tmp_path):
    info = json.loads(bp.Provisioner(FakeNM(), window=tmp_path / "w").info())
    assert info["product"] == "VisionGuide" and info["wifi"]["mode"] == "disconnected"


# ------------------------------------------------------------------ D-Bus 객체

def _call(dbus_method, *args):
    """dbus-next의 @method 래퍼는 반환값을 버린다 — 원래 함수(__wrapped__)를 부른다."""
    return dbus_method.__wrapped__(*args)


def test_bluez_objects_build_with_valid_signatures(tmp_path):
    pytest.importorskip("dbus_next")
    from dbus_next import Variant
    prov = bp.Provisioner(FakeNM(), window=tmp_path / "w")
    loop = asyncio.new_event_loop()
    try:
        app_path, om, service, chars, adv, event = bp._build_bluez_objects(prov, loop, "VG-abcd")
    finally:
        loop.close()
    info, command, result, ev = chars
    assert [c.uuid for c in chars] == [bp.INFO_UUID, bp.COMMAND_UUID, bp.RESULT_UUID, bp.EVENT_UUID]

    objs = _call(type(om).GetManagedObjects, om)
    assert len(objs) == 5 and all(p.startswith(app_path) for p in objs)

    # 긴 읽기: BlueZ가 offset을 주면 그 뒤부터 돌려줘야 한다.
    whole = _call(type(result).ReadValue, result, {})
    assert whole == prov.result
    assert _call(type(result).ReadValue, result, {"offset": Variant("q", 3)}) == whole[3:]
    assert json.loads(_call(type(info).ReadValue, info, {}))["product"] == "VisionGuide"


def test_advertisement_is_discoverable(tmp_path):
    # 검색 가능 플래그가 없으면 휴대폰 설정 화면·일부 스캐너가 광고를 숨긴다.
    pytest.importorskip("dbus_next")
    prov = bp.Provisioner(FakeNM(), window=tmp_path / "w")
    loop = asyncio.new_event_loop()
    try:
        *_, adv, _ = bp._build_bluez_objects(prov, loop, "VG-abcd")
    finally:
        loop.close()
    props = {p.name: p for p in adv._ServiceInterface__properties} \
        if hasattr(adv, "_ServiceInterface__properties") else None
    getter = type(adv).Discoverable.prop_getter if hasattr(type(adv).Discoverable, "prop_getter") else None
    value = getter(adv) if getter else (props["Discoverable"].prop_getter(adv) if props else None)
    assert value is True


def test_command_write_refused_when_window_closed(tmp_path):
    pytest.importorskip("dbus_next")
    from dbus_next import DBusError
    prov = bp.Provisioner(FakeNM(), window=tmp_path / "w")   # 창 없음
    loop = asyncio.new_event_loop()
    try:
        _, _, _, chars, _, _ = bp._build_bluez_objects(prov, loop, "VG-abcd")
        command = chars[1]
        with pytest.raises(DBusError):
            _call(type(command).WriteValue, command, _cmd({"op": "scan"}), {})
    finally:
        loop.close()


def test_advert_fits_31_bytes():
    # 플래그(3) + 128비트 서비스 UUID(2+16) + 이름(2+n) ≤ 31
    name = "VG-" + "abcd"
    assert 3 + 18 + 2 + len(name.encode()) <= 31


def test_network_manager_import_path():
    nm = bp._import_network_manager()
    assert hasattr(nm, "connect_wifi") and hasattr(nm, "scan_networks")


# ------------------------------------------------------------------ 핫스팟에서의 검색·연결

def test_scan_uses_fresh_scan_when_available(tmp_path):
    nm = FakeNM()
    prov = bp.Provisioner(nm, window=tmp_path / "w")
    prov.handle({"op": "scan"})
    assert nm.fresh_scans == 1               # 핫스팟의 낡은 검색 기록을 쓰지 않는다


def test_connect_releases_ap(tmp_path):
    nm = FakeNM(mode="ap")
    prov = bp.Provisioner(nm, window=tmp_path / "w")
    prov.handle({"op": "connect", "ssid": "Home", "psk": "password1"})
    assert nm.release_ap is True


def test_extend_window_reopens_full_length(tmp_path):
    w = tmp_path / "w.json"
    bp.open_window(w, seconds=5)
    prov = bp.Provisioner(FakeNM(), window=w)
    prov.extend_window()
    assert bp.window_remaining(w) > bp.WINDOW_SEC - 5


def test_accepted_write_extends_window(tmp_path):
    pytest.importorskip("dbus_next")
    w = tmp_path / "w.json"
    bp.open_window(w, seconds=5)               # 거의 닫힌 창
    prov = bp.Provisioner(FakeNM(), window=w)
    loop = asyncio.new_event_loop()
    try:
        _, _, _, chars, _, _ = bp._build_bluez_objects(prov, loop, "VG-abcd")
        command = chars[1]
        _call(type(command).WriteValue, command, _cmd({"op": "connect", "ssid": "x"}), {})
    finally:
        loop.close()
    assert bp.window_remaining(w) > bp.WINDOW_SEC - 5
