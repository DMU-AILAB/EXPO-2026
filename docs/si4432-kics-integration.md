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

The driver uses SPI0 CE0 and the module's GPIO0 direct RX-data output:

| AS4432-SMD | Raspberry Pi | Purpose |
| --- | --- | --- |
| SCLK | physical 23 / BCM11 | SPI clock |
| SDI/MOSI | physical 19 / BCM10 | SPI controller output |
| SDO/MISO | physical 21 / BCM9 | SPI controller input |
| nSEL/CS | physical 24 / BCM8 | SPI0 CE0 |
| GPIO0/RX DATA | physical 16 / BCM23 | demodulated FSK pulse input |
| VCC/GND | 3.3V / GND | power and common ground |

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
python camera_live_pi.py --headless --port 8080 --rf-config rf_config.json
```

Confirm one `RF:KICS-358.5000` log entry and one audio playback per button
press. Camera ROI announcements must continue to work independently.
