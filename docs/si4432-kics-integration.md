# SI4432 / KICS RF Integration

The RF path is a global trigger. It does not use camera coordinates or ROI
polygons. A valid KICS packet starts one fixed audio announcement; repeated
packets during the same button press are ignored until the receiver has been
quiet for `quiet_timeout_sec`.

## Configuration

Copy the example and set the audio path on the Raspberry Pi:

```bash
cp rf_config_example.json rf_config.json
```

Set `enabled` to `true` and replace `audio_file` with the required MP3. Start
the existing camera process as usual; it auto-loads `rf_config.json`, or pass
an explicit file with `--rf-config PATH`. Use `--disable-rf` to run camera-only.

## Raspberry Pi wiring

The driver uses SPI0 CE0 and the module's GPIO2 direct RX-data output. The
Si4432 synthesizer accepts 240-930MHz; the RF module's antenna and matching
network still determine which part of that range is practically sensitive.
The default KICS profile is fixed at 358.5000MHz and enables one low-band AFC
step, covering the specified +/-500Hz carrier tolerance.

| AS4432-SMD | Raspberry Pi | Purpose |
| --- | --- | --- |
| SCLK | physical 23 / BCM11 | SPI clock |
| SDI/MOSI | physical 19 / BCM10 | SPI controller output |
| SDO/MISO | physical 21 / BCM9 | SPI controller input |
| nSEL/CS | physical 24 / BCM8 | SPI0 CE0 |
| GPIO2/RX DATA | physical 16 / BCM23 | demodulated FSK pulse input |
| VCC (pad 8) | physical 17 / 3.3V | module power |
| GND (pad 1) | physical 30 | module ground; separate physical header pin from buttons, LED, and fan |
| SDN (pad 2) | physical 25 / GND | held low to keep the module enabled |

The Pi ground pins are electrically common. Physical 30 is chosen for the
module ground so it does not share the same header pin used by the buttons, LED, or fan.

Enable SPI with `raspi-config`. Confirm the exact module pin labels and logic
voltage from its supplied manual before powering it. Do not connect a module
signal directly to 5V. The Si4432 silicon covers the 240–930MHz range, but an
AS4432 module's matching network and supplied spring antenna may be optimized
for 433MHz; use an antenna/matching arrangement suitable for 358.5000MHz and
verify reception with a real KICS transmitter.

## Verification

Run `python -m pytest tests/ -v` on the development machine. On the Pi, first
verify SPI/device detection, then run:

```bash
python device/camera_live_pi.py --headless --port 8080 --rf-config rf_config.json
```

Confirm one `RF:KICS-358.5000` log entry and one audio playback per button
press. Camera ROI announcements must continue to work independently.

For a hardware test at another carrier frequency, override the receiver
frequency explicitly:

```bash
python3 /home/ailab/visionguide/rf_test_mode.py --frequency 433.0
```

This changes the Si4432 synthesizer setting only. The normal KICS test remains
at 358.5000MHz +/-500Hz. It does not retune the
module's antenna or matching network, so a module specified for 425-525MHz
may still have poor sensitivity at 358.5MHz.

## RF-only buzzer test

To test the AS4432-SMD reception without camera processing or voice playback,
stop the GPIO controls service first so it does not share BCM25 with the test:

```bash
sudo systemctl stop visionguide-controls
python3 /home/ailab/visionguide/rf_test_mode.py
# Send a KICS transmitter signal; a valid 358.5000 MHz packet produces two beeps.
sudo systemctl start visionguide-controls
```

The test listens on BCM23 (physical pin 16) and drives the active buzzer on
BCM25 (physical pin 22). Hardware test mode accepts any six-bit transmitter
address and button code so it can verify reception before the production code
list is known. `Ctrl+C` stops the receiver and turns the buzzer off.

## RSSI press detection (`detection_mode: "rssi"`)

Measured on 2026-09-28 with an AS4432-SMD module and a 한길에이치씨 HCR-2007A
remote. Full KICS pulse decoding never produced a valid packet with the
current Si4432 modem settings. The carrier itself, however, is unmistakable:

| Measurement | Value |
| --- | --- |
| Strongest response | 356.635 MHz with the 11.5 kHz filter (358.5 − 1.865 MHz, about 2 × the Si4432 IF, so most likely image-side reception) |
| Idle RSSI | raw 45–60 |
| RSSI while pressing (2–3 m) | raw 140–170 |
| Transmission length per press | 466–484 ms |
| Noise bursts | almost all shorter than 50 ms |

RSSI mode therefore counts one press whenever RSSI stays at or above
`rssi_threshold` for `min_burst_ms`. It does not use GPIO2. It will also react to
other KICS remotes on the same channel. Lowering `rssi_threshold` increases the
reception range.

The deployed devices (60, 89, 103) all use `rssi_threshold` 80. Measured at 2.5 m
on the 103 device the press peaked at 79–91 (noise floor 14–29, 10 cm from the
antenna 136–149), i.e. well below the 140–170 seen on the reference unit above.
At the old threshold of 110 that device never triggered, and the 60/89 units
triggered at a median raw value of only about 117. 80 sits between the noise
ceiling and the weakest measured press; re-measure before lowering it further.

```json
{
  "enabled": true,
  "detection_mode": "rssi",
  "frequency_mhz": 356.635,
  "rssi_threshold": 80,
  "min_burst_ms": 150,
  "audio_files": []
}
```

Buzzer check without the camera service:

```bash
python3 ~/visionguide/rf_test_mode.py --detection rssi --frequency 356.635 --debug-edges
```

### Announcement playlist from the dashboard

Use the dashboard's device page → **리모컨** tab to choose the audio files. You
can pick several, reorder them, and upload new files or generate them with TTS.
A single press plays every selected file in order. A press that arrives while
the playlist is still playing is ignored.

The dashboard stores the selection as absolute Pi paths in `rf_config.json`
(`audio_files`) through `PUT /api/rf/audio` on the Pi API. `camera_live_pi.py`
watches the file's mtime and applies playlist changes without restarting.
Changing radio fields (frequency, mode, threshold, SPI or pin) restarts only
the receiver.

### Group control (several guide devices in range)

KICS KO-06.0046 3.3.2 (4)/(5) requires devices within range of each other to
avoid overlapping sound and to play one after another by priority. With
`group_enabled: true`, devices on the same LAN coordinate over UDP broadcast
(`group_port`, default 47600). This is implemented in `device/rf_group.py`.

1. A device that detects a press broadcasts `heard`.
2. For `group_window_ms` (deployed value 600 ms, code default 300 ms) each
   device collects the `heard` messages from the other devices.
3. Participants are ordered by `group_priority` (lower first; ties break by
   device id). Each device plays after its predecessor broadcasts `done`.
   If the predecessor stays silent for `group_turn_timeout_sec`, the device
   plays anyway.
4. Presses during a round are ignored.

**Only devices that detected the press themselves take part.** A device on the
same LAN that did not detect the remote (for example one 100 m away) receives
the peers' `heard` messages but stays silent, so nothing sounds where nobody
can hear it. Set `group_follow_peers: true` to restore the old behaviour in
which a device that missed the press joins the round and plays after its
peers (it compensates for weak reception, but also sounds far-away devices).
The default is `false`. The LAN broadcast cannot tell how far away a peer is,
so two remotes pressed at nearly the same time in different places would still
be treated as one round.

If broadcasts are lost, each device assumes it is alone and plays. The
failure mode is an overlap, never a silent guide.

**The window must exceed the Wi-Fi broadcast latency, not just the detection
skew.** Measured between the 60/89/103 Pis on one access point, broadcast
packets arrived 97–100 % of the time but with a median latency of about
270–280 ms and a maximum of about 440 ms (unicast: under 10 ms median),
consistent with the AP holding broadcast frames until the DTIM beacon. With
`group_window_ms` 80 a `heard` message therefore arrived after the window had
closed, so every device believed it was alone and they played at the same time
(14 of 21 multi-device presses in the 12-hour log, 12 of them with detection
skew under 10 ms). 600 ms covers the measured maximum with margin. The cost
is that the first device plays about 0.6 s after the press. Re-measure when
the access point or its DTIM setting changes; replacing the broadcast with
unicast to known peers would remove the dependency on it. Set the priority per device
from the dashboard's **리모컨** tab (`PUT /api/rf/group` on the Pi API).
Changing it restarts only the RF receiver.
