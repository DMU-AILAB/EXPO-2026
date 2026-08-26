"""Shared audio announcement routing for camera and RF event sources."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class Announcement:
    """One logical announcement request."""

    source: str
    trigger_id: str
    audio_file: str
    event_db: str | Path | None = None
    event_class: str = "announcement"


class AnnouncementRouter:
    """Send all announcement sources through one shared AudioPlayer.

    The camera dispatcher still owns ROI debounce/cooldown state. RF edge
    detection owns its signal latch. This class only provides the common
    queue and event-log boundary, so audio playback can never overlap.
    """

    def __init__(self, audio_player, event_logger: Callable | None = None) -> None:
        self.audio_player = audio_player
        self.event_logger = event_logger

    def submit(self, announcement: Announcement, on_done: Callable[[], None] | None = None) -> None:
        if self.event_logger is not None and announcement.event_db:
            try:
                self.event_logger(
                    announcement.event_db,
                    datetime.now().isoformat(),
                    announcement.event_class,
                    announcement.trigger_id,
                )
            except Exception as exc:
                print(f"[WARN] announcement event log failed: {exc}")

        if self.audio_player is not None:
            self.audio_player.play(announcement.audio_file, on_done=on_done)
        elif on_done is not None:
            on_done()
