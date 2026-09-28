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
from rf_group import GroupCoordinator, UdpGroupBus, default_device_id
from si4432_radio import MAX_FREQUENCY_MHZ, MIN_FREQUENCY_MHZ, Si4432Radio


@dataclass
class RFConfig:
    enabled: bool = False
    frequency_mhz: float = KICS_FREQUENCY_MHZ
    audio_file: str = "audio/rf_voice_guide.mp3"
    # Played in order on one press. When empty, audio_file alone is played.
    audio_files: tuple[str, ...] = ()
    event_db: str = "foot_traffic.db"
    spi_bus: int = 0
    spi_device: int = 0
    spi_speed_hz: int = 1_000_000
    data_pin: int = 23
    quiet_timeout_sec: float = 1.0
    pulse_tolerance: float = 0.15
    expected_address: int | None = 0
    valid_data_codes: tuple[int, ...] = (0x20, 0x10)
    # "kics" decodes the pulse protocol from GPIO2; "rssi" treats any carrier
    # burst above rssi_threshold lasting min_burst_ms as one button press.
    detection_mode: str = "kics"
    rssi_threshold: int = 110
    min_burst_ms: float = 150.0
    rssi_poll_ms: float = 5.0
    # Group control (rf_group.py): devices on one LAN that hear the same press
    # play one after another by priority (lower first) instead of all at once.
    group_enabled: bool = False
    group_priority: int = 100
    group_port: int = 47600
    group_window_ms: float = 300.0
    group_turn_timeout_sec: float = 60.0
    group_device_id: str = ""
    # BLE iBeacon — each device advertises so phones can detect proximity.
    ble_enabled: bool = False
    ble_uuid: str = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    ble_major: int = 1
    ble_minor: int = 1
    ble_tx_power: int = -59
    _extra: dict = field(default_factory=dict, repr=False)


    def playlist(self) -> tuple[str, ...]:
        if self.audio_files:
            return self.audio_files
        return (self.audio_file,) if self.audio_file else ()


DETECTION_MODES = ("kics", "rssi")
# Fields that require reopening the radio when they change at runtime.
RADIO_FIELDS = (
    "enabled", "frequency_mhz", "spi_bus", "spi_device", "spi_speed_hz", "data_pin",
    "detection_mode", "rssi_threshold", "min_burst_ms", "rssi_poll_ms",
    "pulse_tolerance", "expected_address", "valid_data_codes",
    "group_enabled", "group_priority", "group_port", "group_window_ms",
    "group_turn_timeout_sec", "group_device_id",
)


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
        if "audio_files" in values:
            files = values["audio_files"]
            if not isinstance(files, list) or not all(isinstance(f, str) and f for f in files):
                raise ValueError("audio_files must be a list of non-empty strings")
            values["audio_files"] = tuple(files)
        config = RFConfig(**values)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid RF config fields: {path}") from exc
    if not MIN_FREQUENCY_MHZ <= config.frequency_mhz <= MAX_FREQUENCY_MHZ:
        raise ValueError(
            f"RF frequency must be in [{MIN_FREQUENCY_MHZ:g}, "
            f"{MAX_FREQUENCY_MHZ:g}] MHz"
        )
    if config.quiet_timeout_sec <= 0:
        raise ValueError("quiet_timeout_sec must be positive")
    if config.detection_mode not in DETECTION_MODES:
        raise ValueError(f"detection_mode must be one of {DETECTION_MODES}")
    if not 0 < config.rssi_threshold < 256:
        raise ValueError("rssi_threshold must be in [1, 255]")
    if config.min_burst_ms <= 0 or config.rssi_poll_ms <= 0:
        raise ValueError("min_burst_ms and rssi_poll_ms must be positive")
    if not 1 <= config.group_port <= 65535:
        raise ValueError("group_port must be in [1, 65535]")
    if config.group_window_ms <= 0 or config.group_turn_timeout_sec <= 0:
        raise ValueError("group_window_ms and group_turn_timeout_sec must be positive")
    return config


class RssiBurstDetector:
    """Report one press when RSSI stays above a threshold long enough.

    A KICS remote keys the carrier for up to ~500 ms per press, while noise
    spikes last a few milliseconds. Dips shorter than ``gap_ms`` are bridged
    so a single missed sample does not split one burst into two.
    """

    def __init__(self, threshold: int, min_burst_ms: float, gap_ms: float = 30.0) -> None:
        self.threshold = threshold
        self.min_burst_s = min_burst_ms / 1000.0
        self.gap_s = gap_ms / 1000.0
        self._start: float | None = None
        self._last_above = 0.0
        self._fired = False

    def feed(self, rssi: int, now: float) -> bool:
        if rssi >= self.threshold:
            if self._start is None or now - self._last_above > self.gap_s:
                self._start = now
                self._fired = False
            self._last_above = now
            if not self._fired and now - self._start >= self.min_burst_s:
                self._fired = True
                return True
        elif self._start is not None and now - self._last_above > self.gap_s:
            self._start = None
            self._fired = False
        return False


class _DataPin:
    """Small GPIO adapter kept optional so the protocol remains unit-testable."""

    def __init__(self, pin: int, on_edge: Callable[[float], None]) -> None:
        try:
            from gpiozero import DigitalInputDevice  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("gpiozero is required for Si4432 data GPIO capture") from exc
        self.device = DigitalInputDevice(pin, pull_up=False)

        # gpiozero exposes separate callbacks for the two edge directions.
        # ``when_changed`` is not available on DigitalInputDevice in the
        # Raspberry Pi gpiozero version used by the deployed image.
        def edge_callback() -> None:
            on_edge(time.monotonic_ns() / 1000.0)

        self.device.when_activated = edge_callback
        self.device.when_deactivated = edge_callback

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
        group_bus_factory: Callable[..., UdpGroupBus] = UdpGroupBus,
        packet_callback: Callable[[KicsPacket], None] | None = None,
        trigger_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self.router = router
        self.radio_factory = radio_factory
        self.data_pin_factory = data_pin_factory
        self.group_bus_factory = group_bus_factory
        self.group: GroupCoordinator | None = None
        self.group_bus: UdpGroupBus | None = None
        self.packet_callback = packet_callback
        self.trigger_callback = trigger_callback
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
        self.edge_count = 0
        self.rssi_detector = RssiBurstDetector(config.rssi_threshold, config.min_burst_ms)
        self.last_rssi = 0
        self._playing = False

    def update_audio(self, config: RFConfig) -> None:
        """Swap the announcement playlist without reopening the radio."""

        self.config.audio_file = config.audio_file
        self.config.audio_files = config.audio_files
        print(f"[RF] announcement playlist updated: {list(self.config.playlist()) or '(none)'}")

    @property
    def rssi_mode(self) -> bool:
        return self.config.detection_mode == "rssi"

    def start(self) -> bool:
        if not self.config.enabled:
            return False
        try:
            self.radio = self.radio_factory(
                self.config.spi_bus, self.config.spi_device, self.config.spi_speed_hz
            )
            self.radio.open()
            self.radio.configure_kics(self.config.frequency_mhz)
            if not self.rssi_mode:
                self.data_pin = self.data_pin_factory(self.config.data_pin, self._on_edge)
            if self.config.group_enabled:
                self._start_group()
        except Exception:
            self.close()
            raise
        self._stop.clear()
        target = self._rssi_loop if self.rssi_mode else self._watchdog
        self._thread = threading.Thread(target=target, name="si4432-rf", daemon=True)
        self._thread.start()
        if self.rssi_mode:
            print(f"[RF] RSSI press detector enabled at {self.config.frequency_mhz:.4f} MHz "
                  f"(threshold={self.config.rssi_threshold}, min_burst={self.config.min_burst_ms:g} ms)")
        else:
            print(f"[RF] KICS receiver enabled at {self.config.frequency_mhz:.4f} MHz")
        return True

    def _start_group(self) -> None:
        device_id = self.config.group_device_id or default_device_id()
        bus_ref: list[UdpGroupBus] = []
        self.group = GroupCoordinator(
            device_id, self.config.group_priority, lambda msg: bus_ref[0].send(msg),
            window_s=self.config.group_window_ms / 1000.0,
            turn_timeout_s=self.config.group_turn_timeout_sec,
        )
        self.group_bus = self.group_bus_factory(self.group, self.config.group_port)
        bus_ref.append(self.group_bus)
        self.group_bus.start()
        print(f"[RF-GROUP] enabled id={device_id} priority={self.config.group_priority} "
              f"udp={self.config.group_port}")

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self._thread = None
        if self.group_bus is not None:
            try:
                self.group_bus.close()
            finally:
                self.group_bus = None
                self.group = None
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
        self.edge_count += 1
        try:
            self._edges.put_nowait(float(timestamp_us))
        except queue.Full:
            print("[WARN] SI4432 edge queue full; dropping RF edge")

    def _on_packet(self, packet: KicsPacket) -> None:
        if not self._latch():
            return
        print(f"[TRIGGER][RF] KICS {packet.kind} -> audio={list(self.config.playlist())}")
        if self.packet_callback is not None:
            try:
                self.packet_callback(packet)
            except Exception as exc:
                print(f"[WARN] RF packet callback failed: {exc}")
        self._announce(packet.kind, "rf_kics")

    def _on_rssi_press(self, rssi: int) -> None:
        if not self._latch():
            return
        print(f"[TRIGGER][RF] RSSI burst (raw={rssi}) -> audio={list(self.config.playlist())}")
        self._announce("rssi", "rf_rssi")

    def _latch(self) -> bool:
        """Return True only for the first trigger of one button press."""

        with self._lock:
            self._last_valid_packet = time.monotonic()
            if self._active:
                return False
            self._active = True
            return True

    def _announce(self, kind: str, event_class: str) -> None:
        if self.trigger_callback is not None:
            try:
                self.trigger_callback(kind)
            except Exception as exc:
                print(f"[WARN] RF trigger callback failed: {exc}")
        files = self.config.playlist()
        if self.group is not None:
            # The coordinator decides when (and whether) this device plays.
            self.group.local_press(lambda on_done: self._submit(files, event_class, on_done))
            return
        with self._lock:
            if self._playing:
                print("[RF] previous announcement still playing; press ignored")
                return
            self._playing = bool(files)
        self._submit(files, event_class, self._on_playlist_done)

    def _submit(self, files: tuple[str, ...], event_class: str, on_done) -> None:
        self.router.submit(Announcement(
            source="rf",
            trigger_id=self.event_name,
            audio_file=files[0] if files else "",
            event_db=self.config.event_db,
            event_class=event_class,
            playlist=tuple(files[1:]),
        ), on_done=on_done)

    def _on_playlist_done(self) -> None:
        with self._lock:
            self._playing = False

    def _rssi_loop(self) -> None:
        poll_s = self.config.rssi_poll_ms / 1000.0
        while not self._stop.wait(poll_s):
            radio = self.radio
            if radio is None:
                return
            try:
                rssi = radio.read_rssi()
            except Exception as exc:
                print(f"[WARN] SI4432 RSSI read failed: {exc}")
                continue
            self.last_rssi = rssi
            now = time.monotonic()
            if self.rssi_detector.feed(rssi, now):
                self._on_rssi_press(rssi)
            with self._lock:
                if rssi >= self.config.rssi_threshold and self._active:
                    # Keep the latch while the carrier is still present.
                    self._last_valid_packet = now
                elif self._active and now - self._last_valid_packet >= self.config.quiet_timeout_sec:
                    self._active = False

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
