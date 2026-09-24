"""Shared audio announcement routing for camera and RF event sources."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
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
    # 서버 전송용 추가 정보(백엔드 명세 §6의 ingest 페이로드). 로컬 표시에는 쓰지
    # 않으므로 기본값을 둬서 기존 호출부가 그대로 동작한다.
    camera_id: str = ""
    confidence: float | None = None
    event_type: str = "ANNOUNCEMENT"


class AnnouncementRouter:
    """Send all announcement sources through one shared AudioPlayer.

    The camera dispatcher still owns ROI debounce/cooldown state. RF edge
    detection owns its signal latch. This class only provides the common
    queue and event-log boundary, so audio playback can never overlap.
    """

    def __init__(self, audio_player, event_logger: Callable | None = None,
                 outbox: Callable | None = None) -> None:
        self.audio_player = audio_player
        self.event_logger = event_logger
        # `outbox`는 서버 전송 대기열(`event_logger.queue_event`)이다. `event_logger`가
        # 로컬 화면용 기록이라면 이쪽은 밖으로 나갈 몫 — 둘은 보관 기간과 정리 정책이
        # 달라 같은 테이블을 쓸 수 없다(event_logger.py 헤더 참고).
        self.outbox = outbox

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

        if self.outbox is not None and announcement.event_db:
            try:
                self.outbox(
                    announcement.event_db,
                    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    announcement.camera_id,
                    announcement.event_class,
                    announcement.trigger_id,
                    announcement.confidence,
                    announcement.event_type,
                )
            except Exception as exc:
                print(f"[WARN] announcement outbox failed: {exc}")

        if self.audio_player is not None:
            self.audio_player.play(announcement.audio_file, on_done=on_done)
        elif on_done is not None:
            on_done()
