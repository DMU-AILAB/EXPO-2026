"""BLE iBeacon advertiser for Raspberry Pi (hcitool / BlueZ backend).

Each Pi advertises as an iBeacon so nearby smartphones can detect which
guide device is closest.  UUID identifies the VisionGuide deployment;
Major identifies the building/zone; Minor identifies the individual device.

A companion mobile app scans for this UUID and plays audio on the phone
when the RSSI is strong enough — no pairing required.

Activation: set ble_enabled=true in rf_config.json.  The field is picked up
by the hot-reload loop in camera_live_pi.py within 0.5 s.
"""

from __future__ import annotations

import struct
import subprocess
import uuid as _uuid_mod


# Project-wide iBeacon UUID — all VisionGuide devices share this UUID.
# Change it if you need to isolate from other iBeacon deployments.
VISIONGUIDE_UUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

# Apple Manufacturer ID + iBeacon sub-type/length
_APPLE_MFR = b'\x4c\x00'
_IBEACON   = b'\x02\x15'


def _uuid_bytes(uuid_str: str) -> bytes:
    return _uuid_mod.UUID(uuid_str).bytes


class BleBeacon:
    """iBeacon advertiser backed by hcitool raw HCI commands."""

    def __init__(
        self,
        uuid: str = VISIONGUIDE_UUID,
        major: int = 1,
        minor: int = 1,
        tx_power: int = -59,
        hci: str = "hci0",
    ) -> None:
        self.uuid = uuid
        self.major = major
        self.minor = minor
        self.tx_power = tx_power
        self.hci = hci
        self._running = False

    # ---------------------------------------------------------------- public

    def start(self) -> None:
        try:
            subprocess.run(["sudo", "hciconfig", self.hci, "up"],
                           check=True, capture_output=True)
            self._set_adv_data()
            subprocess.run(["sudo", "hciconfig", self.hci, "leadv", "3"],
                           check=True, capture_output=True)
            self._running = True
            print(f"[BLE] iBeacon 시작 — uuid={self.uuid} "
                  f"major={self.major} minor={self.minor}")
        except Exception as exc:
            print(f"[WARN] BLE beacon 시작 실패: {exc}")

    def stop(self) -> None:
        if not self._running:
            return
        try:
            subprocess.run(["sudo", "hciconfig", self.hci, "noleadv"],
                           check=False, capture_output=True)
            self._running = False
            print("[BLE] iBeacon 중지")
        except Exception as exc:
            print(f"[WARN] BLE beacon 중지 실패: {exc}")

    def update(self, *, major: int | None = None, minor: int | None = None,
               tx_power: int | None = None) -> None:
        """Change advertised values without stopping/starting the HCI adapter."""
        if major is not None:
            self.major = major
        if minor is not None:
            self.minor = minor
        if tx_power is not None:
            self.tx_power = tx_power
        if self._running:
            try:
                self._set_adv_data()
            except Exception as exc:
                print(f"[WARN] BLE beacon 업데이트 실패: {exc}")

    # ---------------------------------------------------------------- private

    def _set_adv_data(self) -> None:
        """Write iBeacon advertising data via HCI vendor command 0x08/0x0008."""
        uuid_b  = _uuid_bytes(self.uuid)
        major_b = struct.pack('>H', self.major)
        minor_b = struct.pack('>H', self.minor)
        tx_b    = struct.pack('b', self.tx_power)

        # AD payload (must be <= 31 bytes):
        #   02 01 1A              Flags (LE General Discoverable, no BR/EDR)
        #   1A FF                 Manufacturer Specific (26 bytes follow)
        #   4C 00                 Apple Company ID
        #   02 15                 iBeacon type + length (21 bytes follow)
        #   [16 UUID][2 Major][2 Minor][1 TxPower]
        payload = (
            bytes([0x02, 0x01, 0x1A, 0x1A, 0xFF])
            + _APPLE_MFR + _IBEACON
            + uuid_b + major_b + minor_b + tx_b
        )
        payload = payload.ljust(31, b'\x00')   # pad to 31

        hex_args = [f"{b:02X}" for b in payload]
        cmd = (["sudo", "hcitool", "-i", self.hci, "cmd",
                "0x08", "0x0008", f"{len(payload):02X}"]
               + hex_args)
        subprocess.run(cmd, check=True, capture_output=True)
