"""Standalone AS4432-SMD/SI4432 RF reception test with a GPIO buzzer."""

from __future__ import annotations

import argparse
import signal
import threading
import time
from pathlib import Path

from gpiozero import Buzzer

from kics_protocol import KicsPacket
from rf_audio_trigger import DETECTION_MODES, RFConfig, RFAudioTrigger, load_rf_config


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
    config.audio_files = ()
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
        "--detection",
        choices=DETECTION_MODES,
        default=None,
        help="override detection_mode: kics (pulse decode) or rssi (carrier burst)",
    )
    parser.add_argument("--rssi-threshold", type=int, default=None,
                        help="override rssi_threshold (raw 0-255) in rssi mode")
    parser.add_argument(
        "--debug-edges",
        action="store_true",
        help="report raw GPIO2 edge counts (kics) or the last RSSI value (rssi)",
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
    if args.detection is not None:
        config.detection_mode = args.detection
    if args.rssi_threshold is not None:
        config.rssi_threshold = args.rssi_threshold
    buzzer = Buzzer(args.buzzer_pin)
    stop = threading.Event()

    def stop_test(_signum, _frame) -> None:
        stop.set()

    def on_packet(packet: KicsPacket) -> None:
        print(f"[RF-TEST] valid KICS packet: {packet.kind} (0x{packet.data:02x})")

    def on_trigger(kind: str) -> None:
        print(f"[RF-TEST] trigger ({kind}) -> buzzer beep-beep", flush=True)
        _beep_twice(buzzer, args.on_time, args.gap)

    signal.signal(signal.SIGINT, stop_test)
    signal.signal(signal.SIGTERM, stop_test)
    trigger = RFAudioTrigger(
        config,
        _SilentRouter(),
        packet_callback=on_packet,
        trigger_callback=on_trigger,
    )
    try:
        trigger.start()
        print(
            f"[RF-TEST] listening at {config.frequency_mhz:.4f} MHz "
            f"({config.detection_mode} mode); "
            f"GPIO{args.buzzer_pin} buzzer test active (Ctrl+C to stop)",
            flush=True,
        )
        next_edge_report = time.monotonic() + 2.0
        while not stop.wait(0.5):
            if args.debug_edges and time.monotonic() >= next_edge_report:
                if trigger.rssi_mode:
                    print(f"[RF-TEST] last RSSI raw: {trigger.last_rssi}", flush=True)
                else:
                    print(f"[RF-TEST] raw GPIO2 edges: {trigger.edge_count}", flush=True)
                next_edge_report = time.monotonic() + 2.0
    finally:
        trigger.close()
        buzzer.off()
        buzzer.close()
        print("[RF-TEST] stopped")


if __name__ == "__main__":
    main()
