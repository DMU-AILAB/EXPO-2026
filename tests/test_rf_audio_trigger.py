import time

from kics_protocol import KicsPacket
from rf_audio_trigger import RFConfig, RFAudioTrigger


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
