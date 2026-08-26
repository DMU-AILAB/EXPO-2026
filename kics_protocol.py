"""KICS.KO-06.0046/R3 358.5000 MHz pulse protocol decoder.

The standard frame is six MSB-first address bits, six MSB-first data bits,
and a SYNC pulse. A logical 0 is 500/1500/500/1500 us; a logical 1 is the
reverse. The SYNC pulse is 500/15000 us. The decoder intentionally accepts
only the common address and the two standard data codes.
"""

from __future__ import annotations

from dataclasses import dataclass

KICS_FREQUENCY_MHZ = 358.5000
KICS_COMMON_ADDRESS = 0
KICS_LOCATION_CODE = 0b100000
KICS_SIGNAL_CODE = 0b010000
KICS_VALID_DATA_CODES = frozenset((KICS_LOCATION_CODE, KICS_SIGNAL_CODE))
PULSE_TOLERANCE = 0.15
FRAME_PULSE_COUNT = 50  # 12 data bits * 4 pulses + 2 SYNC pulses


@dataclass(frozen=True)
class KicsPacket:
    address: int
    data: int
    kind: str


def _near(actual: float, expected: float, tolerance: float) -> bool:
    return abs(actual - expected) <= expected * tolerance


def _decode_bit(pulses: list[float], tolerance: float) -> int | None:
    if len(pulses) != 4:
        return None
    zero = (500.0, 1500.0, 500.0, 1500.0)
    one = (1500.0, 500.0, 1500.0, 500.0)
    if all(_near(a, e, tolerance) for a, e in zip(pulses, zero)):
        return 0
    if all(_near(a, e, tolerance) for a, e in zip(pulses, one)):
        return 1
    return None


def decode_pulse_train(
    durations_us: list[float] | tuple[float, ...],
    *,
    tolerance: float = PULSE_TOLERANCE,
    expected_address: int = KICS_COMMON_ADDRESS,
    valid_data_codes: frozenset[int] = KICS_VALID_DATA_CODES,
) -> KicsPacket | None:
    """Decode one frame, allowing leading/trailing GPIO edge noise."""

    if not 0 <= expected_address < 64:
        raise ValueError("expected_address must be a 6-bit value")
    if not 0 < tolerance < 1:
        raise ValueError("tolerance must be between 0 and 1")

    pulses = [float(value) for value in durations_us]
    if len(pulses) < FRAME_PULSE_COUNT:
        return None

    for start in range(len(pulses) - FRAME_PULSE_COUNT + 1):
        frame = pulses[start:start + FRAME_PULSE_COUNT]
        if not (_near(frame[-2], 500.0, tolerance)
                and _near(frame[-1], 15000.0, tolerance)):
            continue

        bits: list[int] = []
        valid = True
        for offset in range(0, 48, 4):
            bit = _decode_bit(frame[offset:offset + 4], tolerance)
            if bit is None:
                valid = False
                break
            bits.append(bit)
        if not valid:
            continue

        address = 0
        for bit in bits[:6]:
            address = (address << 1) | bit
        data = 0
        for bit in bits[6:]:
            data = (data << 1) | bit

        if address != expected_address or data not in valid_data_codes:
            continue
        kind = "location" if data == KICS_LOCATION_CODE else "signal"
        return KicsPacket(address=address, data=data, kind=kind)
    return None


class KicsPulseDecoder:
    """Collect GPIO transition timestamps and emit validated KICS packets."""

    def __init__(
        self,
        *,
        tolerance: float = PULSE_TOLERANCE,
        timeout_us: float = 25_000.0,
        expected_address: int = KICS_COMMON_ADDRESS,
        valid_data_codes: frozenset[int] = KICS_VALID_DATA_CODES,
    ) -> None:
        self.tolerance = tolerance
        self.timeout_us = timeout_us
        self.expected_address = expected_address
        self.valid_data_codes = valid_data_codes
        self._last_edge_us: float | None = None
        self._durations: list[float] = []

    def reset(self) -> None:
        self._last_edge_us = None
        self._durations.clear()

    def feed_edge(self, timestamp_us: float) -> KicsPacket | None:
        timestamp_us = float(timestamp_us)
        if self._last_edge_us is None:
            self._last_edge_us = timestamp_us
            return None

        duration = timestamp_us - self._last_edge_us
        self._last_edge_us = timestamp_us
        if duration <= 0 or duration > self.timeout_us:
            self._durations.clear()
            return None

        self._durations.append(duration)
        if len(self._durations) > FRAME_PULSE_COUNT * 2:
            del self._durations[:-FRAME_PULSE_COUNT]
        packet = decode_pulse_train(
            self._durations,
            tolerance=self.tolerance,
            expected_address=self.expected_address,
            valid_data_codes=self.valid_data_codes,
        )
        if packet is not None:
            self.reset()
        return packet

    def flush(self, timestamp_us: float) -> KicsPacket | None:
        """Complete a frame whose final 15 ms SYNC level has no next edge."""

        if self._last_edge_us is None:
            return None
        duration = float(timestamp_us) - self._last_edge_us
        if len(self._durations) == FRAME_PULSE_COUNT - 1 and duration >= 15_000 * (1 - self.tolerance):
            self._durations.append(15_000.0)
            packet = decode_pulse_train(
                self._durations,
                tolerance=self.tolerance,
                expected_address=self.expected_address,
                valid_data_codes=self.valid_data_codes,
            )
            self.reset()
            return packet
        if duration > self.timeout_us:
            self.reset()
        return None
