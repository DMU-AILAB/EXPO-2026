"""rf_config.json 핫 리로드 — 음성 목록만 바뀌면 무선 모듈을 다시 열지 않는다."""

import json

import camera_live_pi as m


class FakeTrigger:
    instances = []

    def __init__(self, config, announcements):
        self.config = config
        self.started = False
        self.closed = False
        self.audio_updates = []
        FakeTrigger.instances.append(self)

    def start(self):
        self.started = True

    def close(self):
        self.closed = True

    def update_audio(self, config):
        self.audio_updates.append(config.audio_files)
        self.config.audio_files = config.audio_files


def _write(path, **values):
    path.write_text(json.dumps(values), encoding="utf-8")


def test_reload_swaps_playlist_without_restarting_and_restarts_on_radio_change(tmp_path):
    FakeTrigger.instances = []
    path = tmp_path / "rf_config.json"
    _write(path, enabled=True, detection_mode="rssi", frequency_mhz=356.635)

    trigger = m._reload_rf_trigger(None, path, object(), FakeTrigger)
    assert trigger.started and len(FakeTrigger.instances) == 1

    _write(path, enabled=True, detection_mode="rssi", frequency_mhz=356.635, audio_files=["/a/1.mp3"])
    same = m._reload_rf_trigger(trigger, path, object(), FakeTrigger)
    assert same is trigger
    assert trigger.audio_updates == [("/a/1.mp3",)]
    assert len(FakeTrigger.instances) == 1

    _write(path, enabled=True, detection_mode="rssi", frequency_mhz=358.5, audio_files=["/a/1.mp3"])
    restarted = m._reload_rf_trigger(trigger, path, object(), FakeTrigger)
    assert trigger.closed and restarted is not trigger and restarted.started

    _write(path, enabled=False)
    assert m._reload_rf_trigger(restarted, path, object(), FakeTrigger) is None
    assert restarted.closed


def test_reload_keeps_trigger_on_invalid_config(tmp_path):
    path = tmp_path / "rf_config.json"
    _write(path, enabled=True, detection_mode="rssi")
    trigger = m._reload_rf_trigger(None, path, object(), FakeTrigger)

    path.write_text("{broken", encoding="utf-8")
    assert m._reload_rf_trigger(trigger, path, object(), FakeTrigger) is trigger
    assert not trigger.closed
