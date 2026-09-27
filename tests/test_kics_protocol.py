from kics_protocol import (
    KICS_LOCATION_CODE,
    KICS_SIGNAL_CODE,
    KicsPulseDecoder,
    decode_pulse_train,
)
from si4432_radio import Si4432Radio, frequency_registers


def _bit(bit: int, scale: float = 1.0) -> list[float]:
    base = [500, 1500, 500, 1500] if bit == 0 else [1500, 500, 1500, 500]
    return [value * scale for value in base]


def _frame(address: int = 0, data: int = KICS_LOCATION_CODE, scale: float = 1.0) -> list[float]:
    bits = [(address >> shift) & 1 for shift in range(5, -1, -1)]
    bits.extend((data >> shift) & 1 for shift in range(5, -1, -1))
    pulses = [duration * scale for bit in bits for duration in _bit(bit)]
    return pulses + [500 * scale, 15000 * scale]


def test_358_5_frequency_registers_are_low_band():
    assert frequency_registers(358.5000) == (11, 0xD4, 0x80)


def test_high_band_frequency_registers_set_hbsel():
    assert frequency_registers(480.0) == (0x20, 0x00, 0x00)
    assert frequency_registers(915.0) == (0x35, 0xBB, 0x80)


def test_frequency_registers_rejects_out_of_range_values():
    for frequency in (239.999, 930.001):
        try:
            frequency_registers(frequency)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted unsupported frequency {frequency}")


def test_si4432_configures_frequency_and_direct_rx_data():
    class FakeSpi:
        def __init__(self):
            self.writes = []

        def xfer2(self, data):
            if data[0] & 0x80:
                self.writes.append(tuple(data))
                return [0, 0]
            return [0, 0x08]

        def close(self):
            pass

    spi = FakeSpi()
    radio = Si4432Radio(spi=spi)
    radio.open()
    radio.configure_kics()
    assert (0xF5, 0x0B) in spi.writes
    assert (0xF6, 0xD4) in spi.writes
    assert (0xF7, 0x80) in spi.writes
    assert (0x8B, 0x00) in spi.writes  # GPIO0 is unconnected
    assert (0x8D, 0x14) in spi.writes  # GPIO2 = direct RX data
    assert (0xAA, 0x01) in spi.writes  # AFC pull-in covers +/-500 Hz
    assert (0xF1, 0x02) in spi.writes  # FSK direct mode
    radio.close()


def test_decodes_location_and_signal_codes():
    assert decode_pulse_train(_frame(data=KICS_LOCATION_CODE)).kind == "location"
    assert decode_pulse_train(_frame(data=KICS_SIGNAL_CODE)).kind == "signal"


def test_accepts_standard_fifteen_percent_timing_tolerance():
    packet = decode_pulse_train(_frame(scale=1.10))
    assert packet is not None


def test_rejects_non_common_address_and_unknown_data():
    assert decode_pulse_train(_frame(address=1)) is None
    assert decode_pulse_train(_frame(data=0x01)) is None


def test_can_decode_any_address_and_data_for_hardware_diagnostics():
    packet = decode_pulse_train(
        _frame(address=17, data=0x03),
        expected_address=None,
        valid_data_codes=frozenset(range(64)),
    )
    assert packet is not None
    assert packet.address == 17
    assert packet.data == 0x03


def test_stream_decoder_flushes_final_sync_without_waiting_for_next_edge():
    decoder = KicsPulseDecoder()
    timestamp = 0.0
    packet = None
    decoder.feed_edge(timestamp)
    for duration in _frame()[:-1]:
        timestamp += duration
        packet = decoder.feed_edge(timestamp)
        if packet is not None:
            break
    assert packet is None
    packet = decoder.flush(timestamp + 15000)
    assert packet is not None
    assert packet.data == KICS_LOCATION_CODE
