"""Background Si4432 receiver and global KICS audio trigger."""

from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from announcement_router import Announcement, AnnouncementRouter
from kics_protocol import KICS_FREQUENCY_MHZ, KicsPacket, KicsPulseDecoder
from si4432_radio import Si4432Radio


@dataclass
class RFConfig:
    enabled: bool = False
    frequency_mhz: float = KICS_FREQUENCY_MHZ
    audio_file: str = "audio/rf_voice_guide.mp3"
    event_db: str = "foot_traffic.db"
    spi_bus: int = 0
    spi_device: int = 0
    spi_speed_hz: int = 1_000_000
    data_pin: int = 23
    quiet_timeout_sec: float = 1.0
    pulse_tolerance: float = 0.15
    expected_address: int = 0
    valid_data_codes: tuple[int, ...] = (0x20, 0x10)
    _extra: dict = field(default_factory=dict, repr=False)


def load_rf_config(path: str | Path | None) -> RFConfig:
    if path is None:
        return RFConfig()
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        if isinstance(exc, FileNotFoundError):
            return RFConfig()
        raise ValueError(f"invalid RF config: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"RF config must contain a JSON object: {path}")

    known = {field_name for field_name in RFConfig.__dataclass_fields__ if field_name != "_extra"}
    values = {key: value for key, value in data.items() if key in known}
    try:
        if "valid_data_codes" in values:
            values["valid_data_codes"] = tuple(
                int(value, 0) if isinstance(value, str) else int(value)
                for value in values["valid_data_codes"]
            )
        config = RFConfig(**values)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid RF config fields: {path}") from exc
    if config.frequency_mhz != KICS_FREQUENCY_MHZ:
        raise ValueError("KICS RF frequency must remain 358.5000 MHz")
    if config.quiet_timeout_sec <= 0:
        raise ValueError("quiet_timeout_sec must be positive")
    return config


class _DataPin:
    """Small GPIO adapter kept optional so the protocol remains unit-testable."""

    def __init__(self, pin: int, on_edge: Callable[[float], None]) -> None:
        try:
            from gpiozero import DigitalInputDevice  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("gpiozero is required for Si4432 data GPIO capture") from exc
        self.device = DigitalInputDevice(pin, pull_up=False)
        self.device.when_changed = lambda _value: on_edge(time.monotonic_ns() / 1000.0)

    def close(self) -> None:
        self.device.close()


class RFAudioTrigger:
    """Decode KICS packets and submit one fixed global audio announcement."""

    event_name = "RF:KICS-358.5000"

    def __init__(
        self,
        config: RFConfig,
        router: AnnouncementRouter,
        *,
        radio_factory: Callable[..., Si4432Radio] = Si4432Radio,
        data_pin_factory: Callable[..., _DataPin] = _DataPin,
    ) -> None:
        self.config = config
        self.router = router
        self.radio_factory = radio_factory
        self.data_pin_factory = data_pin_factory
        self.radio: Si4432Radio | None = None
        self.data_pin = None
        self.decoder = KicsPulseDecoder(
            tolerance=config.pulse_tolerance,
            expected_address=config.expected_address,
            valid_data_codes=frozenset(config.valid_data_codes),
        )
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._edges: queue.Queue[float] = queue.Queue(maxsize=512)
        self._lock = threading.Lock()
        self._last_valid_packet = 0.0
        self._active = False

    def start(self) -> bool:
        if not self.config.enabled:
            return False
        try:
            self.radio = self.radio_factory(
                self.config.spi_bus, self.config.spi_device, self.config.spi_speed_hz
            )
            self.radio.open()
            self.radio.configure_kics(self.config.frequency_mhz)
            self.data_pin = self.data_pin_factory(self.config.data_pin, self._on_edge)
        except Exception:
            self.close()
            raise
        self._stop.clear()
        self._thread = threading.Thread(target=self._watchdog, name="si4432-rf", daemon=True)
        self._thread.start()
        print(f"[RF] KICS receiver enabled at {self.config.frequency_mhz:.4f} MHz")
        return True

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self._thread = None
        if self.data_pin is not None:
            try:
                self.data_pin.close()
            finally:
                self.data_pin = None
        if self.radio is not None:
            try:
                self.radio.close()
            finally:
                self.radio = None

    def _on_edge(self, timestamp_us: float) -> None:
        try:
            self._edges.put_nowait(float(timestamp_us))
        except queue.Full:
            print("[WARN] SI4432 edge queue full; dropping RF edge")

    def _on_packet(self, packet: KicsPacket) -> None:
        now = time.monotonic()
        with self._lock:
            self._last_valid_packet = now
            if self._active:
                return
            self._active = True

        print(f"[TRIGGER][RF] KICS {packet.kind} -> audio={self.config.audio_file}")
        self.router.submit(Announcement(
            source="rf",
            trigger_id=self.event_name,
            audio_file=self.config.audio_file,
            event_db=self.config.event_db,
            event_class="rf_kics",
        ))

    def _watchdog(self) -> None:
        while not self._stop.is_set():
            try:
                timestamp_us = self._edges.get(timeout=0.05)
            except queue.Empty:
                timestamp_us = None
            if timestamp_us is not None:
                packet = self.decoder.feed_edge(timestamp_us)
                if packet is not None:
                    self._on_packet(packet)
            packet = self.decoder.flush(time.monotonic_ns() / 1000.0)
            if packet is not None:
                self._on_packet(packet)
            now = time.monotonic()
            with self._lock:
                if self._active and now - self._last_valid_packet >= self.config.quiet_timeout_sec:
                    self._active = False
                    self.decoder.reset()
