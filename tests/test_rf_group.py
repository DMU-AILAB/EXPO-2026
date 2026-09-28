"""RF 군집 제어 — 같은 누름을 들은 기기들이 우선순위대로 한 대씩 재생한다."""

from rf_group import GroupCoordinator, PROTOCOL_VERSION


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class Lan:
    """In-memory broadcast: every message reaches every other coordinator."""

    def __init__(self, clock, drop=False):
        self.clock = clock
        self.nodes = []
        self.drop = drop

    def add(self, device_id, priority, **kw):
        node = GroupCoordinator(device_id, priority, self.broadcast, clock=self.clock, **kw)
        self.nodes.append(node)
        return node

    def broadcast(self, msg):
        if self.drop:
            return
        for node in self.nodes:
            node.on_message(dict(msg))

    def tick(self, until, step=0.01):
        while self.clock.t < until:
            self.clock.t = round(self.clock.t + step, 6)
            for node in self.nodes:
                node.tick()


def _player(log, name):
    """Record the start; keep on_done so the test decides when playback ends."""
    pending = {}

    def play(on_done):
        log.append(name)
        pending["done"] = on_done
    return play, pending


def test_devices_play_one_after_another_by_priority():
    clock = Clock()
    lan = Lan(clock)
    a, b, c = lan.add("pi-a", 2), lan.add("pi-b", 1), lan.add("pi-c", 3)
    log = []
    play_a, pa = _player(log, "a")
    play_b, pb = _player(log, "b")
    play_c, pc = _player(log, "c")

    # The same press is detected within a few ms on every device.
    assert a.local_press(play_a)
    clock.t = 0.02
    assert c.local_press(play_c)
    clock.t = 0.03
    assert b.local_press(play_b)

    lan.tick(0.5)
    assert log == ["b"]            # priority 1 first; the others wait
    pb["done"]()
    lan.tick(0.6)
    assert log == ["b", "a"]
    pa["done"]()
    lan.tick(0.7)
    assert log == ["b", "a", "c"]
    pc["done"]()
    lan.tick(0.8)
    assert not any(n.busy for n in lan.nodes)


def test_press_during_round_is_ignored_and_next_round_starts_after():
    clock = Clock()
    lan = Lan(clock)
    a, b = lan.add("pi-a", 1), lan.add("pi-b", 2)
    log = []
    play_a, pa = _player(log, "a")
    play_b, pb = _player(log, "b")
    a.local_press(play_a)
    b.local_press(play_b)
    lan.tick(0.5)
    assert log == ["a"]
    assert b.local_press(play_b) is False      # still inside the round
    pa["done"]()
    lan.tick(0.6)
    pb["done"]()
    lan.tick(0.7)
    assert log == ["a", "b"]

    play_b2, _ = _player(log, "b2")
    assert b.local_press(play_b2) is True
    lan.tick(1.2)
    assert log == ["a", "b", "b2"]             # alone this time, plays at once


def test_dead_predecessor_times_out():
    clock = Clock()
    lan = Lan(clock)
    a = lan.add("pi-a", 1, turn_timeout_s=5.0)
    b = lan.add("pi-b", 2, turn_timeout_s=5.0)
    log = []
    play_a, _ = _player(log, "a")              # never reports done
    play_b, _ = _player(log, "b")
    a.local_press(play_a)
    b.local_press(play_b)
    lan.tick(4.0)
    assert log == ["a"]
    lan.tick(5.5)
    assert log == ["a", "b"]


def test_without_network_every_device_plays_alone():
    clock = Clock()
    lan = Lan(clock, drop=True)
    a, b = lan.add("pi-a", 1), lan.add("pi-b", 2)
    log = []
    a.local_press(_player(log, "a")[0])
    b.local_press(_player(log, "b")[0])
    lan.tick(0.5)
    assert sorted(log) == ["a", "b"]


def test_device_that_did_not_hear_the_press_stays_silent_and_idle():
    clock = Clock()
    lan = Lan(clock)
    a, b = lan.add("pi-a", 1), lan.add("pi-b", 2)
    log = []
    a.local_press(_player(log, "a")[0])
    lan.tick(0.5)
    assert log == ["a"]
    assert not b.busy


def test_ignores_own_and_foreign_protocol_messages():
    node = GroupCoordinator("pi-a", 1, lambda msg: None, clock=Clock())
    node.on_message({"v": PROTOCOL_VERSION, "type": "heard", "id": "pi-a", "prio": 1})
    node.on_message({"v": 99, "type": "heard", "id": "pi-x", "prio": 0})
    assert not node.busy


def test_rf_trigger_routes_presses_through_group():
    from rf_audio_trigger import RFAudioTrigger, RFConfig

    class FakeBus:
        def __init__(self, coordinator, port):
            self.coordinator, self.port, self.sent, self.closed = coordinator, port, [], False

        def send(self, msg):
            self.sent.append(msg)

        def start(self):
            pass

        def close(self):
            self.closed = True

    class Radio:
        def __init__(self, *args):
            pass

        def open(self):
            pass

        def configure_kics(self, frequency):
            pass

        def close(self):
            pass

        def read_rssi(self):
            return 40

    class Router:
        def __init__(self):
            self.calls = []

        def submit(self, announcement, on_done=None):
            self.calls.append(announcement)
            if on_done is not None:
                on_done()

    router = Router()
    trigger = RFAudioTrigger(
        RFConfig(enabled=True, detection_mode="rssi", audio_files=("/a/1.mp3",),
                 group_enabled=True, group_priority=7, group_device_id="pi-test"),
        router, radio_factory=Radio, group_bus_factory=FakeBus)
    try:
        trigger.start()
        trigger._on_rssi_press(150)
        assert router.calls == []                  # waits for the collect window
        assert trigger.group_bus.sent[0]["type"] == "heard"
        assert trigger.group_bus.sent[0]["prio"] == 7
        trigger.group.clock = lambda: 1e9          # window elapsed
        trigger.group.tick()
        assert [c.audio_file for c in router.calls] == ["/a/1.mp3"]
        assert trigger.group_bus.sent[-1]["type"] == "done"
    finally:
        bus = trigger.group_bus
        trigger.close()
        assert bus.closed
