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
