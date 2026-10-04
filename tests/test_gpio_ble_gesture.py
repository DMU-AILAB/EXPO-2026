"""Wi-Fi 버튼의 짧게/길게 누름 구분과 페어링 LED 패턴.

`gpio_controls`는 맨 위에서 gpiozero를 import하므로 가짜 모듈을 끼워 넣는다.
`main()`이 버튼에 건 콜백을 가로채 사람이 누르는 순서대로 불러 본다.
"""

import sys
import types

import pytest


class _FakeDev:
    def __init__(self, *a, **kw):
        self.kw = kw
        self.value = 0
        self.when_pressed = self.when_held = self.when_released = None

    def on(self): pass
    def off(self): pass
    def close(self): pass


@pytest.fixture
def gc(monkeypatch, tmp_path):
    created = {}

    class Button(_FakeDev):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            created["button"] = self

    fake = types.SimpleNamespace(Button=Button, Buzzer=_FakeDev, LED=_FakeDev)
    monkeypatch.setitem(sys.modules, "gpiozero", fake)
    sys.modules.pop("gpio_controls", None)
    import gpio_controls as mod

    import ble_provisioning as bp
    win = tmp_path / "ble_window.json"
    monkeypatch.setattr(mod, "open_ble_window", lambda: bp.open_window(win))
    monkeypatch.setattr(mod, "pairing_window_open", lambda: bp.pairing_window_open(win))
    beeps = []
    monkeypatch.setattr(mod, "_beep", lambda _bz, times, **k: beeps.append(times))
    conn = {"now": "VisionGuide-AP"}               # 기본: 핫스팟(페어링 가능)
    monkeypatch.setattr(mod, "_active_connection", lambda: conn["now"])
    monkeypatch.setattr(mod, "pause", lambda: None)          # main()이 바로 돌아오게
    monkeypatch.setattr(mod, "_status_led_loop", lambda *a, **k: None)
    toggles = []
    monkeypatch.setattr(mod, "handle_button_press", lambda *a, **k: toggles.append(1))
    mod.main()
    mod._test_beeps, mod._test_conn = beeps, conn
    yield mod, created["button"], toggles, win
    sys.modules.pop("gpio_controls", None)


def test_button_has_hold_time(gc):
    mod, button, _, _ = gc
    assert button.kw["hold_time"] == mod.BLE_HOLD_SEC


def test_short_press_toggles_wifi_on_release(gc):
    _, button, toggles, win = gc
    button.when_pressed()
    assert toggles == []                 # 누르는 순간에는 아무것도 하지 않는다
    button.when_released()
    assert toggles == [1]
    assert not win.exists()


def test_long_press_opens_window_without_toggle(gc):
    _, button, toggles, win = gc
    button.when_pressed()
    button.when_held()
    button.when_released()
    assert toggles == []                 # 길게 눌렀으면 Wi-Fi는 건드리지 않는다
    assert win.exists()


def test_next_short_press_after_long_press_toggles(gc):
    _, button, toggles, _ = gc
    button.when_pressed(); button.when_held(); button.when_released()
    button.when_pressed(); button.when_released()
    assert toggles == [1]


def _kind(mod):
    import threading
    return mod._status_kind(threading.Event(), {"kind": None, "until": 0.0}, threading.Lock())


@pytest.mark.parametrize("connection", ["", "VisionGuide-AP"])
def test_led_shows_pairing_while_window_open(gc, connection):
    mod, button, _, _ = gc
    mod._test_conn["now"] = connection
    button.when_pressed(); button.when_held()
    assert _kind(mod) == "ble_pairing"
    assert mod._test_beeps == [1]                  # 긴 비프 한 번
    assert "ble_pairing" in mod._PATTERNS


def test_long_press_on_home_wifi_refuses(gc):
    # 홈 Wi-Fi에 붙어 있으면 광고하지 않으므로 창을 열지 않고 짧게 3번 울린다.
    mod, button, toggles, win = gc
    mod._test_conn["now"] = "enjoy"
    button.when_pressed(); button.when_held(); button.when_released()
    assert not win.exists()
    assert mod._test_beeps == [3]
    assert toggles == []                           # 길게 누른 것이니 Wi-Fi 전환도 하지 않는다


def test_led_ignores_stale_window_on_home_wifi(gc, monkeypatch):
    # 핫스팟에서 창을 연 뒤 짧게 눌러 홈으로 돌아가면 창 파일이 남아 있어도 페어링 패턴이 아니다.
    mod, button, _, _ = gc
    button.when_pressed(); button.when_held()
    mod._test_conn["now"] = "enjoy"
    monkeypatch.setattr(mod, "_service_active", lambda: True)
    monkeypatch.setattr(mod, "_camera_pipeline_active", lambda: True)
    assert _kind(mod) == "home"
