"""블루투스(BLE)로 Wi-Fi를 알려주는 페어링 서비스 — 대시보드의 Web Bluetooth 상대.

중앙 대시보드는 **같은 망에 있는 기기만** 다룰 수 있다. 처음 켠 기기는 아직 그 망에
없으므로, 대시보드 화면(브라우저)이 블루투스로 직접 기기에 SSID·비밀번호를 건넨다.
연결되면 새 IP를 돌려주고 대시보드가 그 IP로 바로 등록까지 한다.

허용 조건(둘 다 만족해야 **광고 자체를** 한다):
  1. wlan0이 Wi-Fi(station)에 붙어 있지 않다 — 운영 중인 기기는 건드릴 수 없다.
  2. 페어링 창이 열려 있다 — Wi-Fi 버튼(GPIO17)을 3초 누르면 3분 열린다
     (`gpio_controls.py`가 `ble_window.json`을 쓴다). 반경 10m 안의 누구나 Wi-Fi를
     바꾸지 못하도록 **기기를 만질 수 있는 사람**만 열 수 있게 했다.

광고 이름은 `VG-<블루투스 주소 끝 4자리>`다(광고 패킷 31바이트 제약).

GATT 구성 (`SERVICE_UUID` 아래 4개):
  info    read    {product, host, ver, wifi:{mode,ssid}}
  command write   {"op":"scan"} | {"op":"connect","ssid":..,"psk":..}
  result  read    마지막 결과 JSON + 순번 n — 스캔 목록 또는 {state, ip, error}
  event   notify  1바이트 순번 — 바뀌면 클라이언트가 result를 다시 읽는다

결과를 notify에 직접 싣지 않는 이유: notify는 MTU-3바이트(기본 20바이트)를 넘을
수 없는데 스캔 목록은 수백 바이트다. read는 BlueZ가 offset으로 나눠 읽어준다.

알려진 한계: BLE 구간에 앱 수준 암호화가 없다 — 창이 열린 3분 동안 근거리에서
비밀번호를 엿들을 수 있다. 전시·데모 범위에서 수용한 위험이다.

★ `ble_beacon.py`(iBeacon)와 같은 블루투스 칩의 광고를 쓴다. 비콘을 켜게 되면
   `pairing_window_open()`이 참인 동안은 비콘 광고를 멈춰야 둘이 서로 덮지 않는다.

★ 이 파일에 `from __future__ import annotations`를 넣지 말 것 — dbus-next가 메서드
   주석('a{sv}' 등)을 D-Bus 서명으로 읽는데, 지연 평가되면 서명이 깨진다.
"""

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Optional

_HERE = Path(__file__).parent
# 평면(Pi ~/visionguide/) vs 중첩(PC device/) — CLAUDE.md의 배치 관용구
_BASE = _HERE if (_HERE / "runs").is_dir() else _HERE.parent

SERVICE_UUID = "6f1e0001-5a1b-4c7d-9e2f-56495347554e"
INFO_UUID = "6f1e0002-5a1b-4c7d-9e2f-56495347554e"
COMMAND_UUID = "6f1e0003-5a1b-4c7d-9e2f-56495347554e"
RESULT_UUID = "6f1e0004-5a1b-4c7d-9e2f-56495347554e"
EVENT_UUID = "6f1e0005-5a1b-4c7d-9e2f-56495347554e"

WINDOW_FILE = "ble_window.json"
WINDOW_SEC = 180            # 버튼 한 번에 열리는 시간
MAX_VALUE = 512             # ATT 속성값 상한 — Web Bluetooth도 이 이상은 못 읽는다
VERSION = "1"


# ====================================================================== 페어링 창
# gpio_controls(버튼)와 이 서비스는 **다른 프로세스**다. rois.json과 같은 원칙으로
# 기기 루트에 작은 파일 하나를 두고 주고받는다.

def window_path(base: Optional[Path] = None) -> Path:
    return (base or _HERE) / WINDOW_FILE


def open_window(path: Optional[Path] = None, seconds: float = WINDOW_SEC,
                now: Optional[float] = None) -> float:
    """창을 연다(원자적 쓰기). 닫히는 시각을 돌려준다."""
    p = path or window_path()
    until = (time.time() if now is None else now) + seconds
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".ble_window.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"until": until}, f)
        # 쓰는 쪽(gpio_controls, root)과 읽는 쪽(이 서비스, ailab)의 사용자가 다르다.
        # mkstemp 기본값 0600이면 ailab이 못 읽어 창이 영영 닫힌 것으로 보인다.
        os.chmod(tmp, 0o644)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return until


def close_window(path: Optional[Path] = None) -> None:
    try:
        (path or window_path()).unlink()
    except FileNotFoundError:
        pass


def window_remaining(path: Optional[Path] = None, now: Optional[float] = None) -> float:
    """남은 초. 닫혀 있거나 파일이 깨졌으면 0 — 모르면 닫힌 것으로 본다."""
    try:
        until = float(json.loads((path or window_path()).read_text(encoding="utf-8"))["until"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0.0
    return max(0.0, until - (time.time() if now is None else now))


def pairing_window_open(path: Optional[Path] = None, now: Optional[float] = None) -> bool:
    return window_remaining(path, now) > 0


def pairing_allowed(wifi_mode: str, window_is_open: bool) -> bool:
    """운영 중(station)인 기기는 창이 열려 있어도 받지 않는다."""
    return window_is_open and wifi_mode != "station"


# ====================================================================== 프로토콜

class CommandError(ValueError):
    """클라이언트에 그대로 보여줄 문장."""


def parse_command(raw: bytes) -> dict:
    try:
        obj = json.loads(bytes(raw).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise CommandError("명령이 JSON이 아닙니다") from exc
    if not isinstance(obj, dict):
        raise CommandError("명령은 객체여야 합니다")
    op = obj.get("op")
    if op == "scan":
        return {"op": "scan"}
    if op != "connect":
        raise CommandError(f"알 수 없는 명령: {op!r}")

    ssid = obj.get("ssid")
    psk = obj.get("psk", "")
    if not isinstance(ssid, str) or not ssid.strip():
        raise CommandError("SSID가 비어 있습니다")
    ssid = ssid.strip()
    if len(ssid.encode("utf-8")) > 32:
        raise CommandError("SSID는 32바이트를 넘을 수 없습니다")
    if not isinstance(psk, str):
        raise CommandError("비밀번호 형식이 잘못됐습니다")
    # WPA2-PSK는 8~63자. 개방형 네트워크는 빈 문자열.
    if psk and not (8 <= len(psk) <= 63):
        raise CommandError("비밀번호는 8~63자여야 합니다(개방형이면 비워 두세요)")
    return {"op": "connect", "ssid": ssid, "psk": psk}


def encode_json(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def scan_payload(networks: list[dict], limit: int = MAX_VALUE - 16) -> dict:
    """신호 순으로 들어온 목록을 **상한 안에 들어가는 만큼만** 싣는다.

    짧은 키를 쓴다(s=ssid, q=신호%, l=잠금) — 한 항목이라도 더 싣기 위해서다.
    기본 상한에서 16바이트를 남기는 것은 `Provisioner.result`가 붙이는 순번(`"n"`) 몫이다.
    """
    items = [{"s": n.get("ssid", ""), "q": int(n.get("signal_pct", 0)),
              "l": 1 if n.get("security") else 0} for n in networks]
    while True:
        obj = {"state": "scanned", "networks": items}
        if len(encode_json(obj)) <= limit or not items:
            return obj
        items.pop()


class WriteAssembler:
    """긴 쓰기(prepared write)를 offset으로 이어 붙인다.

    MTU가 작으면 BlueZ는 하나의 명령을 여러 번의 WriteValue(offset=…)로 나눠 준다.
    JSON이 완결되는 순간 명령 하나로 본다.
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, chunk: bytes, offset: int) -> Optional[bytes]:
        if offset == 0:
            self._buf = bytearray()
        if offset > len(self._buf):
            self._buf = bytearray()            # 앞 조각을 잃었다 — 새로 받는다
            return None
        self._buf[offset:offset + len(chunk)] = chunk
        if len(self._buf) > MAX_VALUE:
            self._buf = bytearray()
            raise CommandError("명령이 너무 깁니다")
        try:
            json.loads(bytes(self._buf).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None                          # 아직 덜 왔다
        done = bytes(self._buf)
        self._buf = bytearray()
        return done


class Provisioner:
    """명령을 받아 Wi-Fi를 조작하고 결과를 쌓는다 — BLE를 모르는 순수 로직.

    `nm`은 `apps/roi_editor/network_manager.py`와 같은 인터페이스
    (`get_status/scan_networks/connect_wifi/get_connect_result/is_connect_in_progress`).
    테스트는 가짜를 넣는다.
    """

    def __init__(self, nm: Any, on_change: Callable[[], None] = lambda: None,
                 window: Optional[Path] = None) -> None:
        self.nm = nm
        self.on_change = on_change
        self.window = window
        self._result: dict = {"state": "idle"}
        self.seq = 0
        self.awaiting_connect = False

    @property
    def result(self) -> bytes:
        """마지막 결과 + 순번 `n`. 클라이언트는 명령 전과 `n`이 **달라진** 결과만 이번
        명령의 것으로 본다 — 직전 명령의 결과(예: 틀린 비밀번호의 failed)를 오인하지 않게.
        진행 상태(scanning)는 캐시된 스캔이면 순식간에 지나가 관찰을 보장할 수 없다."""
        return encode_json({**self._result, "n": self.seq})

    def _set(self, obj: dict) -> None:
        self._result = obj
        self.seq = (self.seq + 1) % 256
        self.on_change()

    def info(self) -> bytes:
        st = self.nm.get_status()
        return encode_json({"product": "VisionGuide", "host": st.get("hostname"), "ver": VERSION,
                            "wifi": {"mode": st.get("mode"), "ssid": st.get("ssid")}})

    def allowed(self) -> bool:
        return pairing_allowed(self.nm.get_status().get("mode", ""),
                               pairing_window_open(self.window))

    def handle(self, cmd: dict) -> None:
        """동기 호출 — 스캔은 수 초 걸리므로 호출부가 실행기 스레드에서 부른다."""
        if cmd["op"] == "scan":
            self._set({"state": "scanning"})
            self._set(scan_payload(self.nm.scan_networks()))
            return
        if self.nm.is_connect_in_progress():
            raise CommandError("이미 연결을 시도하는 중입니다")
        self._set({"state": "connecting", "ssid": cmd["ssid"]})
        self.awaiting_connect = True
        # 기기 화면(5000)과 같은 경로 — nmcli 프로필 생성·캡티브 포털 해제까지 같다.
        self.nm.connect_wifi(cmd["ssid"], cmd["psk"], delay_seconds=1)

    def poll_connect(self) -> bool:
        """연결 결과가 나왔으면 결과를 싣고 True. 성공하면 창을 닫는다."""
        if not self.awaiting_connect or self.nm.is_connect_in_progress():
            return False
        res = self.nm.get_connect_result()
        if res is None:
            return False
        self.awaiting_connect = False
        if res.get("status") == "ok":
            self._set({"state": "connected", "ip": res.get("ip")})
            close_window(self.window)   # 목적을 이뤘다 — 남은 창으로 다른 사람이 바꾸지 못하게
        else:
            self._set({"state": "failed", "error": str(res.get("error", ""))[:200]})
        return True


def _import_network_manager():
    """roi_editor의 nmcli 래퍼를 쓴다 — Pi는 ~/visionguide/roi_editor/, PC는 apps/roi_editor/."""
    for d in (_BASE / "roi_editor", _BASE / "apps" / "roi_editor"):
        if (d / "network_manager.py").is_file() and str(d) not in sys.path:
            sys.path.insert(0, str(d))
    import network_manager   # noqa: E402
    return network_manager


# ====================================================================== BlueZ 접착부
# 아래는 실기기(BlueZ + D-Bus)에서만 돈다. dbus-next는 여기서만 import한다 —
# gpio_controls가 위의 창 함수만 쓰려고 이 모듈을 import해도 의존성이 늘지 않게.

def _build_bluez_objects(prov: Provisioner, loop: asyncio.AbstractEventLoop, local_name: str):
    from dbus_next import DBusError, Variant
    from dbus_next.service import ServiceInterface, dbus_property, method, signal
    from dbus_next.constants import PropertyAccess

    app_path = "/org/visionguide/ble"
    svc_path = f"{app_path}/service0"
    assembler = WriteAssembler()

    def _offset(options) -> int:
        v = options.get("offset")
        return int(v.value) if v is not None else 0

    class Characteristic(ServiceInterface):
        def __init__(self, index: int, uuid: str, flags: list[str]):
            super().__init__("org.bluez.GattCharacteristic1")
            self.path = f"{svc_path}/char{index}"
            self.uuid = uuid
            self.flags = flags
            self._value = b""

        @dbus_property(access=PropertyAccess.READ)
        def UUID(self) -> "s":
            return self.uuid

        @dbus_property(access=PropertyAccess.READ)
        def Service(self) -> "o":
            return svc_path

        @dbus_property(access=PropertyAccess.READ)
        def Flags(self) -> "as":
            return self.flags

        @dbus_property(access=PropertyAccess.READ)
        def Value(self) -> "ay":
            return self._value

        def read_bytes(self) -> bytes:
            return b""

        @method()
        def ReadValue(self, options: "a{sv}") -> "ay":
            return self.read_bytes()[_offset(options):]

        @method()
        def WriteValue(self, value: "ay", options: "a{sv}"):
            raise DBusError("org.bluez.Error.NotSupported", "쓰기 불가")

        @method()
        def StartNotify(self):
            raise DBusError("org.bluez.Error.NotSupported", "알림 불가")

        @method()
        def StopNotify(self):
            pass

        def props(self) -> dict:
            return {"UUID": Variant("s", self.uuid), "Service": Variant("o", svc_path),
                    "Flags": Variant("as", self.flags)}

    class InfoChar(Characteristic):
        def read_bytes(self) -> bytes:
            return prov.info()

    class ResultChar(Characteristic):
        def read_bytes(self) -> bytes:
            return prov.result

    class CommandChar(Characteristic):
        @method()
        def WriteValue(self, value: "ay", options: "a{sv}"):
            if not prov.allowed():
                raise DBusError("org.bluez.Error.NotPermitted",
                                "페어링 창이 닫혀 있습니다 — 기기의 Wi-Fi 버튼을 3초 누르세요")
            try:
                done = assembler.feed(bytes(value), _offset(options))
                if done is None:
                    return
                cmd = parse_command(done)
            except CommandError as exc:
                prov._set({"state": "error", "error": str(exc)})
                return
            # 스캔(최대 15초)이 D-Bus 응답을 붙잡지 않도록 실행기로 넘긴다.
            loop.run_in_executor(None, _safe_handle, cmd)

    def _safe_handle(cmd: dict) -> None:
        try:
            prov.handle(cmd)
        except CommandError as exc:
            prov._set({"state": "error", "error": str(exc)})
        except Exception as exc:   # noqa: BLE001 — 서비스가 죽으면 페어링 자체가 불가능해진다
            prov._set({"state": "error", "error": f"내부 오류: {exc}"[:200]})

    class EventChar(Characteristic):
        notifying = False

        @method()
        def StartNotify(self):
            self.notifying = True

        @method()
        def StopNotify(self):
            self.notifying = False

        def bump(self) -> None:
            self._value = bytes([prov.seq])
            if self.notifying:
                self.emit_properties_changed({"Value": self._value})

    class Service(ServiceInterface):
        def __init__(self, chars: list):
            super().__init__("org.bluez.GattService1")
            self.chars = chars

        @dbus_property(access=PropertyAccess.READ)
        def UUID(self) -> "s":
            return SERVICE_UUID

        @dbus_property(access=PropertyAccess.READ)
        def Primary(self) -> "b":
            return True

        def props(self) -> dict:
            return {"UUID": Variant("s", SERVICE_UUID), "Primary": Variant("b", True),
                    "Characteristics": Variant("ao", [c.path for c in self.chars])}

    class ObjectManager(ServiceInterface):
        def __init__(self, service: Service):
            super().__init__("org.freedesktop.DBus.ObjectManager")
            self.service = service

        @method()
        def GetManagedObjects(self) -> "a{oa{sa{sv}}}":
            out = {svc_path: {"org.bluez.GattService1": self.service.props()}}
            for c in self.service.chars:
                out[c.path] = {"org.bluez.GattCharacteristic1": c.props()}
            return out

    class Advertisement(ServiceInterface):
        path = "/org/visionguide/ble/adv0"

        def __init__(self):
            super().__init__("org.bluez.LEAdvertisement1")

        @dbus_property(access=PropertyAccess.READ)
        def Type(self) -> "s":
            return "peripheral"

        @dbus_property(access=PropertyAccess.READ)
        def ServiceUUIDs(self) -> "as":
            return [SERVICE_UUID]

        @dbus_property(access=PropertyAccess.READ)
        def LocalName(self) -> "s":
            return local_name

        # "LE General Discoverable" 플래그. 없으면 휴대폰 설정 화면·일부 스캐너가 광고를
        # 목록에서 뺀다(실기기에서 아무 데도 안 보였다). 플래그 3바이트는 31바이트 계산에
        # 이미 들어 있다. 이 속성을 모르는 구버전 BlueZ는 무시한다.
        @dbus_property(access=PropertyAccess.READ)
        def Discoverable(self) -> "b":
            return True

        @method()
        def Release(self):
            pass

    event = EventChar(3, EVENT_UUID, ["notify"])
    chars = [InfoChar(0, INFO_UUID, ["read"]), CommandChar(1, COMMAND_UUID, ["write"]),
             ResultChar(2, RESULT_UUID, ["read"]), event]
    service = Service(chars)
    return app_path, ObjectManager(service), service, chars, Advertisement(), event


async def _run(adapter: str = "hci0") -> None:
    from dbus_next import BusType, Variant
    from dbus_next.aio import MessageBus

    nm = _import_network_manager()
    loop = asyncio.get_running_loop()
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    adapter_path = f"/org/bluez/{adapter}"
    intro = await bus.introspect("org.bluez", adapter_path)
    obj = bus.get_proxy_object("org.bluez", adapter_path, intro)
    props = obj.get_interface("org.freedesktop.DBus.Properties")
    await props.call_set("org.bluez.Adapter1", "Powered", Variant("b", True))
    address = (await props.call_get("org.bluez.Adapter1", "Address")).value
    # 광고 패킷은 31바이트다: 플래그 3 + 128비트 UUID 18 + 이름(2+n). "VisionGuide-xxxx"면
    # 39바이트라 BlueZ가 등록을 거부한다 — 7자로 줄여 30바이트에 맞춘다.
    local_name = "VG-" + address.replace(":", "")[-4:]

    event_ref: dict = {}
    prov = Provisioner(nm, on_change=lambda: loop.call_soon_threadsafe(
        lambda: event_ref["e"].bump() if "e" in event_ref else None))

    app_path, om, service, chars, adv, event = _build_bluez_objects(prov, loop, local_name)
    event_ref["e"] = event
    bus.export(app_path, om)
    bus.export(f"{app_path}/service0", service)
    for c in chars:
        bus.export(c.path, c)
    bus.export(adv.path, adv)

    gatt = obj.get_interface("org.bluez.GattManager1")
    advm = obj.get_interface("org.bluez.LEAdvertisingManager1")
    await gatt.call_register_application(app_path, {})
    print(f"[BLE] GATT 등록 완료 — {local_name} (광고는 페어링 창이 열렸을 때만)", flush=True)

    advertising = False
    while True:
        allowed = await loop.run_in_executor(None, prov.allowed)
        if allowed and not advertising:
            await advm.call_register_advertisement(adv.path, {})
            advertising = True
            print("[BLE] 페어링 창 열림 — 광고 시작", flush=True)
        elif not allowed and advertising:
            try:
                await advm.call_unregister_advertisement(adv.path)
            except Exception:   # noqa: BLE001 — 이미 내려갔으면 그만이다
                pass
            advertising = False
            print("[BLE] 광고 중지", flush=True)

        if prov.awaiting_connect and await loop.run_in_executor(None, prov.poll_connect):
            print(f"[BLE] 연결 결과: {prov.result.decode('utf-8', 'replace')}", flush=True)
        await asyncio.sleep(1.0)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="VisionGuide BLE Wi-Fi 페어링")
    ap.add_argument("--adapter", default="hci0")
    ap.add_argument("--open-window", type=float, metavar="SEC",
                    help="테스트용 — 버튼 없이 페어링 창을 SEC초 연다")
    args = ap.parse_args()
    if args.open_window:
        open_window(seconds=args.open_window)
        print(f"[BLE] 페어링 창을 {args.open_window:.0f}초 열었습니다")
        return
    asyncio.run(_run(args.adapter))


if __name__ == "__main__":
    main()
