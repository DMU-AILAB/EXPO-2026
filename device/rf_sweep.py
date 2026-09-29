"""Coarse SI4432 RSSI sweep for locating an unknown carrier."""

from __future__ import annotations

import argparse
import time

from si4432_radio import Si4432Radio


RSSI_REGISTER = 0x26


def main() -> None:
    parser = argparse.ArgumentParser(description="Sweep SI4432 RSSI over a frequency range")
    parser.add_argument("--start", type=float, default=335.5)
    parser.add_argument("--stop", type=float, default=435.5)
    parser.add_argument("--step", type=float, default=0.5)
    parser.add_argument("--dwell", type=float, default=0.08)
    args = parser.parse_args()
    if not args.start < args.stop or args.step <= 0 or args.dwell <= 0:
        raise SystemExit("invalid sweep range or timing")

    radio = Si4432Radio()
    readings: list[tuple[float, int]] = []
    try:
        radio.open()
        frequency = args.start
        total = int((args.stop - args.start) / args.step) + 1
        index = 0
        while frequency <= args.stop + 1e-9:
            radio.configure_kics(frequency)
            time.sleep(args.dwell)
            samples = [radio.read_register(RSSI_REGISTER) for _ in range(3)]
            raw = max(samples)
            readings.append((frequency, raw))
            index += 1
            print(f"{index:3d}/{total} {frequency:8.3f} MHz RSSI raw={raw:3d} ({raw / 2.0 - 120.0:6.1f} dBm)", flush=True)
            frequency += args.step
    finally:
        radio.close()

    print("--- strongest RSSI points ---")
    for frequency, raw in sorted(readings, key=lambda item: item[1], reverse=True)[:10]:
        print(f"{frequency:8.3f} MHz raw={raw:3d} ({raw / 2.0 - 120.0:6.1f} dBm)")


if __name__ == "__main__":
    main()
