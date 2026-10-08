"""audio_trigger.py 단위 테스트 — AudioPlayer 큐 기반 순차 재생."""
import sys
import time
from pathlib import Path


import audio_trigger as at


def test_play_requests_run_sequentially_not_overlapping(monkeypatch):
    order = []

    def fake_play(path):
        order.append(("start", path))
        time.sleep(0.05)
        order.append(("end", path))

    monkeypatch.setattr(at, "_CLI_PLAYER", None)  # force pygame branch
    player = at.AudioPlayer()
    monkeypatch.setattr(player, "_play_pygame", staticmethod(fake_play))

    player.play("a.mp3")
    player.play("b.mp3")
    player.play("c.mp3")
    time.sleep(0.4)

    assert order == [
        ("start", "a.mp3"), ("end", "a.mp3"),
        ("start", "b.mp3"), ("end", "b.mp3"),
        ("start", "c.mp3"), ("end", "c.mp3"),
    ]


def test_play_ignores_empty_path():
    player = at.AudioPlayer()
    player.play("")  # should not raise, should not enqueue
    assert player._queue.qsize() == 0


def test_queue_overflow_drops_excess_requests(monkeypatch, capsys):
    def slow_play(path):
        time.sleep(0.3)

    monkeypatch.setattr(at, "_CLI_PLAYER", None)
    player = at.AudioPlayer(max_queue=1)
    monkeypatch.setattr(player, "_play_pygame", staticmethod(slow_play))

    player.play("first.mp3")   # picked up by worker almost immediately
    time.sleep(0.02)           # let worker start "first.mp3"
    player.play("second.mp3")  # queued
    player.play("third.mp3")   # queue full (maxsize=1) -> dropped with a warning

    captured = capsys.readouterr()
    assert "대기열 초과" in captured.out


def test_muted_skips_playback_but_still_calls_on_done(monkeypatch, tmp_path):
    settings = tmp_path / "audio_settings.json"
    settings.write_text('{"muted": true}', encoding="utf-8")
    monkeypatch.setattr(at, "AUDIO_SETTINGS_PATH", settings)
    played, done = [], []

    monkeypatch.setattr(at, "_CLI_PLAYER", None)
    player = at.AudioPlayer()
    monkeypatch.setattr(player, "_play_pygame", staticmethod(played.append))

    player.play("a.mp3", on_done=lambda: done.append("a"))
    time.sleep(0.2)
    assert played == [] and done == ["a"]

    # 해제는 재시작 없이 다음 재생부터 반영된다.
    settings.write_text('{"muted": false}', encoding="utf-8")
    player.play("b.mp3", on_done=lambda: done.append("b"))
    time.sleep(0.2)
    assert played == ["b.mp3"] and done == ["a", "b"]


def test_read_muted_defaults_to_audible(tmp_path):
    assert at.read_muted(tmp_path / "missing.json") is False
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert at.read_muted(bad) is False
    bad.write_text("[1]", encoding="utf-8")
    assert at.read_muted(bad) is False
    bad.write_text('{"muted": "yes"}', encoding="utf-8")
    assert at.read_muted(bad) is False
