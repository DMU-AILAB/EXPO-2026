import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import audio_trigger as at


def test_usb_audio_power_runs_sudo_uhubctl(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(at.subprocess, "run", fake_run)
    power = at.UsbAudioPower("1-1", settle_seconds=0.0)

    assert power.power_on() is True
    assert power.power_off() is True
    assert [call[0] for call in calls] == [
        ["sudo", "-n", "uhubctl", "-l", "1-1", "-a", "on"],
        ["sudo", "-n", "uhubctl", "-l", "1-1", "-a", "off"],
    ]


def test_audio_player_powers_speaker_on_for_playback_and_off_after(monkeypatch):
    events = []
    finished = threading.Event()

    class FakePower:
        settle_seconds = 0.0

        def power_on(self):
            events.append("on")
            return True

        def power_off(self):
            events.append("off")
            return True

    def fake_play(path):
        events.append(("play", path))

    def done():
        events.append("done")
        finished.set()

    monkeypatch.setattr(at, "_CLI_PLAYER", None)
    player = at.AudioPlayer(usb_power=FakePower())
    monkeypatch.setattr(player, "_play_pygame", staticmethod(fake_play))

    player.play("announcement.mp3", on_done=done)

    assert finished.wait(1.0)
    assert events == ["off", "on", ("play", "announcement.mp3"), "off", "done"]


def test_audio_player_keeps_playing_when_usb_power_on_fails(monkeypatch):
    events = []
    finished = threading.Event()

    class FailedPower:
        settle_seconds = 0.0

        def power_on(self):
            events.append("on")
            return False

        def power_off(self):
            events.append("off")
            return True

    def fake_play(path):
        events.append(("play", path))

    monkeypatch.setattr(at, "_CLI_PLAYER", None)
    player = at.AudioPlayer(usb_power=FailedPower())
    monkeypatch.setattr(player, "_play_pygame", staticmethod(fake_play))
    player.play("announcement.mp3", on_done=finished.set)

    assert finished.wait(1.0)
    assert events == ["off", "on", ("play", "announcement.mp3")]
