"""Group control for RF announcements across several Pis on one LAN.

One remote press is heard by every guide device in range. KICS
KO-06.0046 3.3.2 (4)/(5) requires that they avoid overlapping sound and
play one after another by priority. Devices coordinate directly over UDP
broadcast, so no server is needed:

1. A device that detects a press broadcasts ``heard`` with its priority.
2. For ``window_s`` every device collects ``heard`` messages. All devices
   detect the same press within tens of milliseconds, so they end up with
   the same participant set.
3. Participants are ordered by (priority, device_id), lower first. The
   first plays immediately; each later one waits for its predecessor's
   ``done`` broadcast, or moves on after ``turn_timeout_s`` so one dead
   device cannot stall the round.
4. Presses during a round are ignored.

If the network drops messages every device believes it is alone and
plays, i.e. the behaviour falls back to the ungrouped one. An overlap is
safer than a guide that stays silent.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

PROTOCOL_VERSION = 1

PlayFn = Callable[[Callable[[], None]], None]


def default_device_id() -> str:
    # Hostnames are often identical on cloned Pi images; the MAC keeps it unique.
    return f"{socket.gethostname()}-{uuid.getnode():012x}"


@dataclass
class _Round:
    start: float
    participants: dict[str, int] = field(default_factory=dict)
    joined: bool = False
    play: PlayFn | None = None
    phase: str = "collect"          # collect -> wait -> playing -> after
    order: list[str] = field(default_factory=list)
    done: set[str] = field(default_factory=set)
    last_progress: float = 0.0


class GroupCoordinator:
    """Transport-agnostic state machine. Drive it with ``tick()``."""

    def __init__(self, device_id: str, priority: int, send: Callable[[dict], None], *,
                 window_s: float = 0.3, turn_timeout_s: float = 60.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.device_id = device_id
        self.priority = priority
        self.send = send
        self.window_s = window_s
        self.turn_timeout_s = turn_timeout_s
        self.clock = clock
        self._round: _Round | None = None
        self._lock = threading.Lock()

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._round is not None

    def local_press(self, play: PlayFn) -> bool:
        """Register a locally detected press. Returns False when it is ignored."""
        now = self.clock()
        with self._lock:
            rnd = self._round
            if rnd is not None and (rnd.phase != "collect" or rnd.joined):
                print("[RF-GROUP] round in progress; press ignored")
                return False
            if rnd is None:
                rnd = self._round = _Round(start=now)
            rnd.joined = True
            rnd.play = play
            rnd.participants[self.device_id] = self.priority
        self._send("heard")
        return True

    def on_message(self, msg: dict) -> None:
        if msg.get("v") != PROTOCOL_VERSION or msg.get("id") == self.device_id:
            return
        sender = str(msg.get("id", ""))
        if not sender:
            return
        now = self.clock()
        with self._lock:
            rnd = self._round
            if msg.get("type") == "heard":
                if rnd is None:
                    rnd = self._round = _Round(start=now)
                if rnd.phase == "collect":
                    rnd.participants[sender] = int(msg.get("prio", 0))
            elif msg.get("type") == "done" and rnd is not None:
                rnd.done.add(sender)
                rnd.last_progress = now

    def tick(self) -> None:
        now = self.clock()
        start_play: PlayFn | None = None
        with self._lock:
            rnd = self._round
            if rnd is None:
                return
            if rnd.phase == "collect" and now - rnd.start >= self.window_s:
                if not rnd.joined:
                    # Others heard a press we did not; this round is not ours.
                    self._round = None
                    return
                rnd.order = sorted(rnd.participants, key=lambda d: (rnd.participants[d], d))
                rnd.phase = "wait"
                rnd.last_progress = now
                position = rnd.order.index(self.device_id) + 1
                print(f"[RF-GROUP] turn {position}/{len(rnd.order)} order={rnd.order}")
            if rnd.phase == "wait":
                ahead = rnd.order[:rnd.order.index(self.device_id)]
                if all(d in rnd.done for d in ahead):
                    start_play = rnd.play
                elif now - rnd.last_progress >= self.turn_timeout_s:
                    print(f"[RF-GROUP] predecessor timed out; playing anyway (done={sorted(rnd.done)})")
                    start_play = rnd.play
                if start_play is not None:
                    rnd.phase = "playing"
            elif rnd.phase == "after":
                if rnd.order[-1] in rnd.done or now - rnd.last_progress >= self.turn_timeout_s:
                    self._round = None
        if start_play is not None:
            start_play(self._local_done)

    def _local_done(self) -> None:
        now = self.clock()
        with self._lock:
            rnd = self._round
            if rnd is not None:
                rnd.done.add(self.device_id)
                rnd.phase = "after"
                rnd.last_progress = now
        self._send("done")

    def _send(self, kind: str) -> None:
        try:
            self.send({"v": PROTOCOL_VERSION, "type": kind, "id": self.device_id,
                       "prio": self.priority})
        except Exception as exc:
            print(f"[WARN] RF group send failed: {exc}")


class UdpGroupBus:
    """UDP broadcast transport plus the thread that drives ``tick()``."""

    def __init__(self, coordinator: GroupCoordinator, port: int,
                 broadcast_addr: str = "255.255.255.255", tick_s: float = 0.02) -> None:
        self.coordinator = coordinator
        self.port = port
        self.broadcast_addr = broadcast_addr
        self.tick_s = tick_s
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._sock: socket.socket | None = None

    def send(self, msg: dict) -> None:
        if self._sock is not None:
            self._sock.sendto(json.dumps(msg).encode(), (self.broadcast_addr, self.port))

    def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("", self.port))
        sock.settimeout(0.2)
        self._sock = sock
        self._stop.clear()
        for target, name in ((self._recv_loop, "rf-group-rx"), (self._tick_loop, "rf-group-tick")):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)

    def close(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=1.0)
        self._threads = []
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def _recv_loop(self) -> None:
        while not self._stop.is_set():
            sock = self._sock
            if sock is None:
                return
            try:
                data, _addr = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                msg = json.loads(data.decode())
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(msg, dict):
                self.coordinator.on_message(msg)

    def _tick_loop(self) -> None:
        while not self._stop.wait(self.tick_s):
            try:
                self.coordinator.tick()
            except Exception as exc:
                print(f"[WARN] RF group tick failed: {exc}")
