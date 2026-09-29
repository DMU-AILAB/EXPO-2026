"""Live SI4432 RSSI monitor with LED, buzzer and a small web dashboard.

This is a diagnostic tool. It does not decode KICS packets; it shows RF energy
seen while the receiver is stepped through a configurable frequency range.
"""

from __future__ import annotations

import argparse
import json
import signal
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from gpiozero import Buzzer, PWMLED

from si4432_radio import Si4432Radio


RSSI_REGISTER = 0x26


class Monitor:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.radio = Si4432Radio()
        self.led = PWMLED(args.led_pin)
        self.buzzer = Buzzer(args.buzzer_pin)
        self.history: deque[dict] = deque(maxlen=240)
        self.current = {"frequency_mhz": None, "raw": 0, "dbm": -120.0,
                        "brightness": 0.0, "alert": False, "updated": 0.0}
        self.baseline = float(args.baseline_raw)
        self.last_alert = 0.0

    def start(self) -> None:
        self.radio.open()
        threading.Thread(target=self.run, name="rf-monitor", daemon=True).start()

    def run(self) -> None:
        try:
            while not self.stop_event.is_set():
                frequency = self.args.start
                while frequency <= self.args.stop + 1e-9 and not self.stop_event.is_set():
                    self.radio.configure_kics(frequency)
                    time.sleep(self.args.dwell)
                    raw = max(self.radio.read_register(RSSI_REGISTER) for _ in range(3))
                    # LED shows the complete measured range; only the buzzer uses the threshold.
                    brightness = min(1.0, max(0.0, (raw - self.args.led_min_raw) /
                                             (self.args.led_max_raw - self.args.led_min_raw)))
                    alert = raw >= self.baseline + self.args.alert_delta
                    now = time.time()
                    with self.lock:
                        self.current = {
                            "frequency_mhz": round(frequency, 3),
                            "raw": raw,
                            "dbm": round(raw / 2.0 - 120.0, 1),
                            "brightness": round(brightness, 3),
                            "alert": alert,
                            "updated": now,
                        }
                        self.history.append(self.current.copy())
                    self.led.value = brightness
                    if alert and now - self.last_alert >= self.args.buzzer_cooldown:
                        self.last_alert = now
                        self.buzzer.beep(on_time=0.12, off_time=0.08,
                                        n=2, background=True)
                        print(f"[RF-MONITOR] ALERT {frequency:.3f} MHz raw={raw}", flush=True)
                    frequency += self.args.step
        finally:
            self.stop()

    def snapshot(self) -> dict:
        with self.lock:
            return {"current": self.current.copy(), "baseline_raw": self.baseline,
                    "history": list(self.history)}

    def stop(self) -> None:
        if self.stop_event.is_set():
            return
        self.stop_event.set()
        self.led.off()
        self.buzzer.off()
        self.led.close()
        self.buzzer.close()
        self.radio.close()


def make_handler(monitor: Monitor):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def do_GET(self) -> None:
            if self.path == "/api/status":
                body = json.dumps(monitor.snapshot()).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            body = HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


HTML = """<!doctype html><meta charset=utf-8><title>VisionGuide RF Monitor</title>
<style>body{font:16px sans-serif;background:#101522;color:#eee;margin:24px}h1{color:#7dd3fc}.card{padding:18px;background:#1d2638;border-radius:12px;max-width:760px}.bar{height:28px;background:#303b50;border-radius:8px;overflow:hidden}.fill{height:100%;background:#22c55e;width:0;transition:.15s}pre{white-space:pre-wrap;color:#cbd5e1}</style>
<h1>VisionGuide RF Monitor</h1><div class=card><div id=main>?곌껐 以?..</div><div class=bar><div id=bar class=fill></div></div><pre id=log></pre></div>
<script>async function tick(){try{let d=await (await fetch('/api/status',{cache:'no-store'})).json(),c=d.current;document.querySelector('#main').innerHTML=`二쇳뙆??<b>${c.frequency_mhz??'-'} MHz</b> 쨌 RSSI raw <b>${c.raw}</b> (${c.dbm} dBm) 쨌 ${c.alert?'??媛먯?':'?湲?}`;document.querySelector('#bar').style.width=(c.brightness*100)+'%';document.querySelector('#log').textContent=d.history.slice(-12).reverse().map(x=>`${x.frequency_mhz} MHz  raw=${x.raw}  ${x.dbm} dBm`).join('\n')}catch(e){document.querySelector('#main').textContent='Pi ?곌껐 ?딄?'}}setInterval(tick,500);tick()</script>"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=float, default=335.5)
    parser.add_argument("--stop", type=float, default=435.5)
    parser.add_argument("--step", type=float, default=0.5)
    parser.add_argument("--dwell", type=float, default=0.15)
    parser.add_argument("--baseline-raw", type=float, default=45.0)
    parser.add_argument("--alert-delta", type=float, default=15.0)
    parser.add_argument("--led-min-raw", type=float, default=0.0,
                        help="RSSI raw value mapped to LED off")
    parser.add_argument("--led-max-raw", type=float, default=100.0,
                        help="RSSI raw value mapped to LED full brightness")
    parser.add_argument("--buzzer-cooldown", type=float, default=3.0)
    parser.add_argument("--led-pin", type=int, default=27)
    parser.add_argument("--buzzer-pin", type=int, default=25)
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()
    if not args.start < args.stop or args.step <= 0 or args.dwell <= 0:
        raise SystemExit("invalid frequency range or timing")
    monitor = Monitor(args)
    server = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(monitor))
    signal.signal(signal.SIGINT, lambda *_: monitor.stop_event.set())
    signal.signal(signal.SIGTERM, lambda *_: monitor.stop_event.set())
    monitor.start()
    print(f"[RF-MONITOR] http://0.0.0.0:{args.port} scanning {args.start}-{args.stop} MHz", flush=True)
    try:
        server.serve_forever()
    finally:
        server.shutdown()
        monitor.stop()


if __name__ == "__main__":
    main()



