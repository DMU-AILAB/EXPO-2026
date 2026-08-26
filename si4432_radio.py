"""Minimal Si4432 SPI/direct-mode receiver used by the KICS RF trigger."""

from __future__ import annotations

import time
from typing import Any

REG_DEVICE_TYPE = 0x00
REG_INT_STATUS_1 = 0x03
REG_INT_STATUS_2 = 0x04
REG_INT_ENABLE_1 = 0x05
REG_INT_ENABLE_2 = 0x06
REG_OP_MODE_1 = 0x07
REG_OP_MODE_2 = 0x08
REG_GPIO0 = 0x0B
REG_GPIO1 = 0x0C
REG_GPIO2 = 0x0D
REG_IO_PORT = 0x0E
REG_IF_FILTER = 0x1C
REG_AFC_OVERRIDE = 0x1D
REG_RX_OVERSAMPLE = 0x20
REG_RX_OFFSET_2 = 0x21
REG_RX_OFFSET_1 = 0x22
REG_RX_OFFSET_0 = 0x23
REG_RX_GAIN_1 = 0x24
REG_RX_GAIN_0 = 0x25
REG_DATA_ACCESS = 0x30
REG_MODULATION_1 = 0x70
REG_MODULATION_2 = 0x71
REG_FREQUENCY_DEVIATION = 0x72
REG_FREQUENCY_OFFSET_1 = 0x73
REG_FREQUENCY_OFFSET_2 = 0x74
REG_FREQUENCY_BAND = 0x75
REG_FREQUENCY_HIGH = 0x76
REG_FREQUENCY_LOW = 0x77

OP_READY = 0x01
OP_RX = 0x05
OP_RX_FIFO_CLEAR = 0x02
OP_SW_RESET = 0x80
GPIO_RX_DATA = 0x14
GPIO_RX_STATE = 0x15


def frequency_registers(frequency_mhz: float) -> tuple[int, int, int]:
    """Return (band, fc_high, fc_low) for the Si4432 low-band synthesizer."""

    if not 240.0 <= frequency_mhz < 480.0:
        raise ValueError("Si4432 low-band frequency must be in [240, 480) MHz")
    fb = int(frequency_mhz // 10) - 24
    fractional = (frequency_mhz / 10.0) - 24.0 - fb
    fc = round(fractional * 64_000)
    if fc >= 64_000:
        fb += 1
        fc = 0
    if not 0 <= fb <= 23:
        raise ValueError("frequency is outside the supported band")
    return fb, (fc >> 8) & 0xFF, fc & 0xFF


class Si4432Radio:
    """SPI register driver configured for direct RX data on GPIO0.

    The KICS standard's pulse timing is decoded by the host, so the chip is
    deliberately used in direct mode instead of the EZMAC packet handler.
    """

    def __init__(self, bus: int = 0, device: int = 0, speed_hz: int = 1_000_000, spi: Any = None) -> None:
        self.bus = bus
        self.device = device
        self.speed_hz = speed_hz
        self.spi = spi

    def open(self) -> None:
        if self.spi is None:
            try:
                import spidev  # type: ignore[import-not-found]
            except ImportError as exc:
                raise RuntimeError("spidev is required for the Si4432 RF receiver") from exc
            self.spi = spidev.SpiDev()
            self.spi.open(self.bus, self.device)
            self.spi.max_speed_hz = self.speed_hz
            self.spi.mode = 0

        device_type = self.read_register(REG_DEVICE_TYPE)
        if device_type not in (0x07, 0x08):
            raise RuntimeError(f"unexpected Si4432 device type: 0x{device_type:02x}")
        self.write_register(REG_OP_MODE_1, OP_SW_RESET)
        time.sleep(0.01)

    def close(self) -> None:
        if self.spi is not None and hasattr(self.spi, "close"):
            self.spi.close()
        self.spi = None

    def read_register(self, address: int) -> int:
        if self.spi is None:
            raise RuntimeError("Si4432 SPI is not open")
        return int(self.spi.xfer2([address & 0x7F, 0x00])[1])

    def write_register(self, address: int, value: int) -> None:
        if self.spi is None:
            raise RuntimeError("Si4432 SPI is not open")
        self.spi.xfer2([address | 0x80, value & 0xFF])

    def configure_kics(self, frequency_mhz: float = 358.5000, deviation_khz: float = 2.5) -> None:
        fb, fc_high, fc_low = frequency_registers(frequency_mhz)
        deviation = round(deviation_khz * 1000 / 625)
        if not 0 <= deviation <= 0xFF:
            raise ValueError("deviation is outside the Si4432 register range")

        # Disable interrupt sources: the host reads direct demodulated data
        # from GPIO0 and does not use the FIFO packet handler.
        self.write_register(REG_INT_ENABLE_1, 0x00)
        self.write_register(REG_INT_ENABLE_2, 0x00)
        self.read_register(REG_INT_STATUS_1)
        self.read_register(REG_INT_STATUS_2)
        self.write_register(REG_OP_MODE_1, OP_READY)
        self.write_register(REG_OP_MODE_2, OP_RX_FIFO_CLEAR)
        self.write_register(REG_OP_MODE_2, 0x00)

        self.write_register(REG_GPIO0, GPIO_RX_DATA)
        self.write_register(REG_GPIO1, GPIO_RX_STATE)
        self.write_register(REG_GPIO2, 0x00)
        self.write_register(REG_IO_PORT, 0x00)

        # KICS uses a 2.5 kHz peak FSK deviation with very slow pulse
        # timing. Use the 11.5 kHz filter setting to leave room for crystal
        # error and the specified +/-500 Hz carrier tolerance.
        self.write_register(REG_IF_FILTER, 0x33)
        self.write_register(REG_AFC_OVERRIDE, 0x44)

        # FSK, direct mode via GPIO, MSB-first/no host-side Manchester.
        self.write_register(REG_MODULATION_1, 0x00)
        self.write_register(REG_MODULATION_2, 0x02)
        self.write_register(REG_FREQUENCY_DEVIATION, deviation)
        self.write_register(REG_FREQUENCY_OFFSET_1, 0x00)
        self.write_register(REG_FREQUENCY_OFFSET_2, 0x00)
        self.write_register(REG_FREQUENCY_BAND, fb)
        self.write_register(REG_FREQUENCY_HIGH, fc_high)
        self.write_register(REG_FREQUENCY_LOW, fc_low)
        self.write_register(REG_OP_MODE_1, OP_RX)
