from announcement_router import Announcement, AnnouncementRouter


class FakeAudioPlayer:
    def __init__(self):
        self.calls = []

    def play(self, path, on_done=None):
        self.calls.append((path, on_done))


def test_camera_and_rf_announcements_share_audio_player_and_log_boundary():
    player = FakeAudioPlayer()
    logged = []
    router = AnnouncementRouter(player, lambda *args: logged.append(args))

    router.submit(Announcement("camera", "ROI-1", "camera.mp3", "camera.db", "white_cane"))
    router.submit(Announcement("rf", "RF:KICS-358.5000", "rf.mp3", "rf.db", "rf_kics"))

    assert [call[0] for call in player.calls] == ["camera.mp3", "rf.mp3"]
    assert [call[3] for call in logged] == ["ROI-1", "RF:KICS-358.5000"]


def test_playlist_plays_files_in_order_after_each_finishes_and_logs_once():
    player = FakeAudioPlayer()
    logged = []
    finished = []
    router = AnnouncementRouter(player, lambda *args: logged.append(args))

    router.submit(
        Announcement("rf", "RF:KICS-358.5000", "a.mp3", "rf.db", "rf_rssi", playlist=("b.mp3", "c.mp3")),
        on_done=lambda: finished.append(True),
    )

    # Only the first file is queued until it finishes playing.
    assert [call[0] for call in player.calls] == ["a.mp3"]
    player.calls[0][1]()
    player.calls[1][1]()
    assert [call[0] for call in player.calls] == ["a.mp3", "b.mp3", "c.mp3"]
    assert finished == []
    player.calls[2][1]()
    assert finished == [True]
    assert len(logged) == 1
