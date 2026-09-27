"""Standalone AS4432-SMD/SI4432 RF reception test with a GPIO buzzer."""

from __future__ import annotations

import argparse
import signal
import threading
import time
from pathlib import Path

from gpiozero import Buzzer

from kics_protocol import KicsPacket
from rf_audio_trigger import RFConfig, RFAudioTrigger, load_rf_config


class _SilentRouter:
    """Keep RF test mode independent from camera audio playback."""

    def submit(self, announcement, on_done=None) -> None:
        if on_done is not None:
            on_done()


def _beep_twice(buzzer: Buzzer, on_time: float, gap: float) -> None:
    for index in range(2):
        buzzer.on()
        time.sleep(on_time)
        buzzer.off()
        if index == 0:
            time.sleep(gap)


def _load_test_config(path: Path) -> RFConfig:
    config = load_rf_config(path if path.exists() else None)
    config.enabled = True
    config.audio_file = ""
    # Hardware test mode should prove reception independently of the
    # installed transmitter's address and button code.
    config.expected_address = None
    config.valid_data_codes = tuple(range(64))
    return config


def main() -> None:
    parser = argparse.ArgumentParser(description="AS4432-SMD/SI4432 KICS RF buzzer test")
    parser.add_argument("--rf-config", default="rf_config.json")
    parser.add_argument(
        "--frequency",
        type=float,
        default=None,
        help="override the receiver frequency in MHz (240-930)",
    )
    parser.add_argument(
        "--debug-edges",
        action="store_true",
        help="report raw GPIO2 edge counts while diagnosing reception",
    )
    parser.add_argument("--buzzer-pin", type=int, default=25,
                        help="BCM GPIO number (default: 25, physical pin 22)")
    parser.add_argument("--on-time", type=float, default=0.12)
    parser.add_argument("--gap", type=float, default=0.12)
    args = parser.parse_args()
    if args.on_time <= 0 or args.gap < 0:
        raise SystemExit("--on-time must be positive and --gap must not be negative")

    config = _load_test_config(Path(args.rf_config))
    if args.frequency is not None:
        config.frequency_mhz = args.frequency
    buzzer = Buzzer(args.buzzer_pin)
    stop = threading.Event()

    def stop_test(_signum, _frame) -> None:
        stop.set()

    def on_packet(packet: KicsPacket) -> None:
        print(f"[RF-TEST] valid KICS packet: {packet.kind} (0x{packet.data:02x}) -> buzzer beep-beep")
        _beep_twice(buzzer, args.on_time, args.gap)

    signal.signal(signal.SIGINT, stop_test)
    signal.signal(signal.SIGTERM, stop_test)
    trigger = RFAudioTrigger(
        config,
        _SilentRouter(),
        packet_callback=on_packet,
    )
    try:
        trigger.start()
        print(
            f"[RF-TEST] listening at {config.frequency_mhz:.4f} MHz; "
            f"GPIO{args.buzzer_pin} buzzer test active (Ctrl+C to stop)"
        )
        next_edge_report = time.monotonic() + 2.0
        while not stop.wait(0.5):
            if args.debug_edges and time.monotonic() >= next_edge_report:
                print(f"[RF-TEST] raw GPIO2 edges: {trigger.edge_count}")
                next_edge_report = time.monotonic() + 2.0
    finally:
        trigger.close()
        buzzer.off()
        buzzer.close()
        print("[RF-TEST] stopped")


if __name__ == "__main__":
    main()
