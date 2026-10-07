# ESP32 S8050 output firmware

Arduino firmware for the VisionGuide output board. The Raspberry Pi handles camera inference,
ROI decisions, BLE discovery and trigger delivery. ESP32 Wi-Fi is configurable and is not used
for triggers; the Pi and ESP32 may be on different Wi-Fi networks. GPIO13 drives an S8050
transistor to switch a low-voltage DC load; there is no mechanical relay.

## Requirements

- ESP32 Arduino Core
- ArduinoJson **7.x**
- An S8050 NPN low-side driver, base resistor, and external supply rated for the DC load

## Configure and flash

1. Install Arduino IDE.
2. In **File > Preferences**, add Espressif's stable Boards Manager URL:
   `https://espressif.github.io/arduino-esp32/package_esp32_index.json`.
3. In **Tools > Board > Boards Manager**, install **esp32 by Espressif Systems**. Install
   **ArduinoJson 7.x** from **Sketch > Include Library > Manage Libraries**. BLE support comes
   with the ESP32 Arduino core.
4. Copy `secrets.example.h` to `secrets.h` in this directory. Set an optional initial Wi-Fi
   SSID/password and a unique random HTTP diagnostic API key of at least 32 characters. Wi-Fi can
   also be left unset and entered later from the central dashboard.
5. Open `esp32_relay.ino`, select the matching ESP32 board and serial port. Under
   **Tools > Partition Scheme**, select **Huge APP (3MB No OTA/1MB SPIFFS)**; the default app
   partition is too small for the built-in BLE + Wi-Fi firmware. For a generic ESP32-WROOM DevKit,
   select `ESP32 Dev Module`, then compile and upload.
6. After flashing, the ESP32 advertises its unique `VG-ESP32-<chip-id>` BLE identity even before
   it has Wi-Fi. In the central dashboard open the Pi's **Network** tab, approve the discovered
   ESP32 once, then enter or change its Wi-Fi there.

After upload, open Serial Monitor at **115200 baud** to see the IP address. Some boards require
holding **BOOT** while upload begins.

Espressif setup references: [Arduino IDE installation](https://docs.espressif.com/projects/arduino-esp32/en/latest/installing.html),
[board and port selection](https://docs.espressif.com/projects/arduino-esp32/en/latest/guides/tools_menu.html).

`secrets.h` is ignored by Git. Do not commit real Wi-Fi credentials or API keys.

## BLE control and Wi-Fi setup

The custom BLE service is the runtime link between Pi and ESP32. The info characteristic exposes
the stable chip ID, current Wi-Fi status and commanded output state. The encrypted command
characteristic accepts `pulse` and `configure_wifi` commands; the encrypted result characteristic
returns the matching `request_id`. Long JSON requests are split into 18-byte payload fragments so
they work with the default 23-byte ATT MTU.

The dashboard sends Wi-Fi credentials to the Pi over its registered device connection. The Pi
forwards them over the bonded, encrypted BLE link. The ESP32 stores the new SSID/password in NVS
only after joining the new network. If association fails, it tries the previously saved network
and reports the failure over BLE. Credentials are not printed to Serial or included in status.

The Pi selects only the ESP32 ID approved in the dashboard. Relay pulses continue over BLE while
the ESP32 changes Wi-Fi, including when the new network is different from the Pi's network.

## Local HTTP diagnostics

| Method | Path | Authentication | Purpose |
|---|---|---|---|
| `GET` | `/api/v1/health` | None | Basic uptime and Wi-Fi status |
| `GET` | `/api/v1/status` | `X-API-Key` | Output command state and remaining time |
| `POST` | `/api/v1/relays/1/pulse` | `X-API-Key` | Switch the logical output on for a timed pulse |
| `PUT` | `/api/v1/relays/1` | `X-API-Key` | Set or renew the logical output state |

HTTP remains available when the ESP32 is on the same LAN, for diagnostics and manual fallback.
The Pi does not use HTTP for normal triggering. `/relays/1` is a logical output channel name; it
does not mean a physical relay is present.

Pulse request:

```http
POST /api/v1/relays/1/pulse
X-API-Key: <configured key>
Content-Type: application/json

{"event_id":"pi01:front_gate:unique-id","roi":"front_gate","duration_ms":500}
```

`event_id` is required. The ESP32 deduplicates accepted IDs for ten minutes, with a 32-event
in-memory cache. A unique pulse request received while the output is on returns `409` with
`retry_after_ms`; the Pi should retry that same event ID after the current pulse completes.

Occupancy requests accept `{"state":"on","lease_ms":2000}` and `{"state":"off"}`.
Leases are limited to 10 seconds and must be renewed while the ROI remains occupied. An
authenticated `off` command immediately turns the output off, including during a pulse.

`/api/v1/health` intentionally omits output state. Use the authenticated `/api/v1/status` endpoint
to inspect the GPIO command state. This firmware does not sense whether current is actually flowing
through the load.

## S8050 wiring: GPIO13 low-side switch

The sketch uses `OUTPUT_PIN_1 = 13` and turns the S8050 on when GPIO13 is `HIGH`. Connect to the
ESP32 header pin marked **GPIO13** or **IO13** (not an Arduino Uno `D13` pin name).
On ESP32-WROOM-32, GPIO13 is also multiplexed with other peripheral functions, so leave JTAG
unused on that pin while driving the transistor. See the [Espressif module pin table](https://documentation.espressif.com/esp32-wroom-32_datasheet_en.html).

For an S8050 NPN low-side driver and a low-voltage DC load:

```text
ESP32 GPIO13 ──[R_BASE]── B
                           ├──[10 kΩ]── GND
Load +V ──────[DC LOAD]── C  S8050
                          E ─── GND
ESP32 GND ──────────────────── Load PSU GND
```

Connections:

- GPIO13 goes through a base resistor `R_BASE` to the S8050 base. Add a 10 kΩ resistor from base
  to emitter/GND so the transistor stays off while the ESP32 boots.
- S8050 emitter goes to GND; collector goes to the load's low side. Connect the load's other side
  to a supply with the load's rated voltage. ESP32 GND and the load supply's negative/GND must be
  common.
- If the DC load is inductive (for example, a motor or solenoid), add a flyback diode across it:
  **cathode/banded end to +Vload**, anode to the collector/load-negative side.
- Choose `R_BASE` from the load current and the datasheet for the exact S8050 part. The load and
  transistor manufacturer/package were not provided, so this sketch does not assume a resistor
  value or pin order. Check the transistor's ratings and pinout before connecting it.
- Do not power the load from GPIO13. The transistor does not provide relay-style isolation or
  separate COM/NO contacts. This wiring is for compatible low-voltage DC loads, not mains.

The API is intended for a trusted local network; do not expose port 80 to the internet.
