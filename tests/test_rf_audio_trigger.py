import sys
import time
import types

from kics_protocol import KicsPacket
from rf_audio_trigger import RFConfig, RFAudioTrigger, _DataPin


class FakeRouter:
    def __init__(self):
        self.calls = []

    def submit(self, announcement, on_done=None):
        self.calls.append(announcement)
        # Behave like a finished playback unless a test holds it open.
        if on_done is not None and not getattr(self, "hold", False):
            on_done()
        else:
            self.pending = on_done


class FakeRadio:
    def __init__(self, *args):
        self.opened = False
        self.configured = False
        self.closed = False

    def open(self):
        self.opened = True

    def configure_kics(self, frequency):
        self.configured = frequency

    def close(self):
        self.closed = True


class FakeDataPin:
    def __init__(self, pin, callback):
        self.pin = pin
        self.callback = callback
        self.closed = False

    def close(self):
        self.closed = True


def test_rf_trigger_is_global_and_latches_repeated_packets():
    router = FakeRouter()
    radio = FakeRadio()
    data_pin = FakeDataPin
    service = RFAudioTrigger(
        RFConfig(enabled=True, audio_file="rf.mp3"),
        router,
        radio_factory=lambda *args: radio,
        data_pin_factory=data_pin,
    )
    try:
        assert service.start() is True
        assert radio.configured == 358.5
        packet = KicsPacket(address=0, data=0x20, kind="location")
        service._on_packet(packet)
        service._on_packet(packet)
        assert len(router.calls) == 1
        assert router.calls[0].trigger_id == "RF:KICS-358.5000"

        service._last_valid_packet = time.monotonic() - 2.0
        service._active = False
        service._on_packet(packet)
        assert len(router.calls) == 2
    finally:
        service.close()
        assert radio.closed is True


def test_rf_trigger_invokes_packet_callback_without_changing_audio_flow():
    router = FakeRouter()
    packets = []
    service = RFAudioTrigger(
        RFConfig(enabled=True, audio_file="rf.mp3"),
        router,
        radio_factory=FakeRadio,
        data_pin_factory=FakeDataPin,
        packet_callback=packets.append,
    )
    packet = KicsPacket(address=0, data=0x10, kind="signal")
    try:
        service.start()
        service._on_packet(packet)
    finally:
        service.close()

    assert packets == [packet]
    assert len(router.calls) == 1


def test_data_pin_uses_gpiozero_activation_callbacks(monkeypatch):
    callbacks = {}

    class FakeDigitalInputDevice:
        def __init__(self, pin, pull_up):
            self.pin = pin
            self.pull_up = pull_up
            self.when_activated = None
            self.when_deactivated = None

        def close(self):
            pass

    monkeypatch.setitem(
        sys.modules,
        "gpiozero",
        types.SimpleNamespace(DigitalInputDevice=FakeDigitalInputDevice),
    )
    timestamps = []
    pin = _DataPin(23, timestamps.append)
    pin.device.when_activated()
    pin.device.when_deactivated()
    pin.close()

    assert len(timestamps) == 2
    assert all(isinstance(timestamp, float) for timestamp in timestamps)


def test_rssi_detector_fires_once_per_long_burst_and_ignores_spikes():
    from rf_audio_trigger import RssiBurstDetector

    detector = RssiBurstDetector(threshold=110, min_burst_ms=150)
    # A 20 ms noise spike never reaches min_burst.
    assert not any(detector.feed(150, t / 1000) for t in range(0, 20, 5))
    assert detector.feed(50, 0.100) is False

    # A 480 ms carrier burst fires exactly once, even across one missed sample.
    fired = []
    for t in range(200, 680, 5):
        rssi = 40 if t == 400 else 150
        fired.append(detector.feed(rssi, t / 1000))
    assert fired.count(True) == 1
    assert 30 <= fired.index(True) <= 31  # ~150 ms after the burst started

    # After the carrier drops, the next press fires again.
    detector.feed(50, 0.800)
    assert any(detector.feed(150, t / 1000) for t in range(2000, 2300, 5))


def test_load_rf_config_validates_detection_mode(tmp_path):
    import json

    import pytest

    from rf_audio_trigger import load_rf_config

    path = tmp_path / "rf.json"
    path.write_text(json.dumps({"detection_mode": "rssi", "rssi_threshold": 120}), encoding="utf-8")
    config = load_rf_config(path)
    assert config.detection_mode == "rssi"
    assert config.rssi_threshold == 120

    path.write_text(json.dumps({"detection_mode": "ook"}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_rf_config(path)


def test_rssi_mode_triggers_once_per_press_without_data_pin():
    readings = []

    class RssiRadio(FakeRadio):
        def read_rssi(self):
            return readings.pop(0) if readings else 40

    def no_data_pin(*args):
        raise AssertionError("rssi mode must not claim the GPIO2 data pin")

    router = FakeRouter()
    kinds = []
    service = RFAudioTrigger(
        RFConfig(enabled=True, audio_file="rf.mp3", detection_mode="rssi",
                 frequency_mhz=356.635, rssi_poll_ms=1.0, quiet_timeout_sec=0.05),
        router,
        radio_factory=RssiRadio,
        data_pin_factory=no_data_pin,
        trigger_callback=kinds.append,
    )
    readings.extend([150] * 400)  # a burst well over min_burst_ms at 1 ms polling
    try:
        assert service.start() is True
        deadline = time.monotonic() + 3.0
        while (readings or service._active) and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        service.close()

    assert kinds == ["rssi"]
    assert len(router.calls) == 1
    assert router.calls[0].event_class == "rf_rssi"


def test_rf_playlist_is_one_announcement_and_skips_presses_while_playing(tmp_path):
    import json

    from rf_audio_trigger import load_rf_config

    path = tmp_path / "rf.json"
    path.write_text(json.dumps({"enabled": True, "audio_files": ["/a/1.mp3", "/a/2.mp3"]}), encoding="utf-8")
    config = load_rf_config(path)
    assert config.playlist() == ("/a/1.mp3", "/a/2.mp3")

    router = FakeRouter()
    router.hold = True
    service = RFAudioTrigger(config, router, radio_factory=FakeRadio, data_pin_factory=FakeDataPin)
    service._on_rssi_press(150)
    assert router.calls[0].audio_file == "/a/1.mp3"
    assert router.calls[0].playlist == ("/a/2.mp3",)

    # A new press while the playlist is still playing is ignored.
    service._active = False
    service._on_rssi_press(150)
    assert len(router.calls) == 1

    router.pending()
    service._active = False
    service._on_rssi_press(150)
    assert len(router.calls) == 2


def test_rf_audio_files_validation_and_update_audio(tmp_path):
    import json

    import pytest

    from rf_audio_trigger import RFConfig, load_rf_config

    path = tmp_path / "rf.json"
    path.write_text(json.dumps({"audio_files": "one.mp3"}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_rf_config(path)

    assert RFConfig(audio_file="").playlist() == ()
    service = RFAudioTrigger(RFConfig(enabled=True), FakeRouter(), radio_factory=FakeRadio,
                             data_pin_factory=FakeDataPin)
    service.update_audio(RFConfig(audio_files=("x.mp3",)))
    assert service.config.playlist() == ("x.mp3",)
