"""SI4432 noise monitor: show GPIO2 edge rate on the status LED."""

from __future__ import annotations

import argparse
import signal
import time
from pathlib import Path

from gpiozero import PWMLED

from rf_audio_trigger import RFAudioTrigger, load_rf_config


class _SilentRouter:
    def submit(self, announcement, on_done=None) -> None:
        if on_done is not None:
            on_done()


def main() -> None:
    parser = argparse.ArgumentParser(description="Show SI4432 GPIO2 noise on LED brightness")
    parser.add_argument("--rf-config", default="rf_config.json")
    parser.add_argument("--frequency", type=float, default=358.5)
    parser.add_argument("--led-pin", type=int, default=27)
    parser.add_argument("--full-scale", type=float, default=6000.0,
                        help="edge rate that maps to full LED brightness")
    args = parser.parse_args()
    if args.full_scale <= 0:
        raise SystemExit("--full-scale must be positive")

    config_path = Path(args.rf_config)
    config = load_rf_config(config_path if config_path.exists() else None)
    config.enabled = True
    config.frequency_mhz = args.frequency
    config.expected_address = None
    config.valid_data_codes = tuple(range(64))
    led = PWMLED(args.led_pin)
    trigger = RFAudioTrigger(config, _SilentRouter())
    stop = False

    def request_stop(_signum, _frame) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    try:
        trigger.start()
        previous = trigger.edge_count
        previous_time = time.monotonic()
        report_at = previous_time
        while not stop:
            time.sleep(0.1)
            now = time.monotonic()
            elapsed = now - previous_time
            if elapsed < 0.05:
                continue
            current = trigger.edge_count
            rate = max(0.0, (current - previous) / elapsed)
            led.value = min(1.0, rate / args.full_scale)
            previous, previous_time = current, now
            if now >= report_at:
                print(f"[RF-LED] {args.frequency:.4f} MHz edges={rate:7.1f}/s brightness={led.value:.2f}", flush=True)
                report_at = now + 1.0
    finally:
        led.off()
        led.close()
        trigger.close()


if __name__ == "__main__":
    main()
