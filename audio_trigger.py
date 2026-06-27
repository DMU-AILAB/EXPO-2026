"""audio_trigger.py — Streamlit 비의존 TriggerDispatcher + 논블로킹 AudioPlayer."""

from __future__ import annotations

import shutil
import subprocess
import threading
import time


# ---------------------------------------------------------------------------
# TriggerDispatcher (Streamlit-free)
# ---------------------------------------------------------------------------

class StandaloneDispatcher:
    """디바운싱 + 쿨다운 게이트 (st.session_state 미사용).

    simulator/trigger_dispatcher.py 의 동일 로직을 일반 dict 기반으로 구현.
    """

    def __init__(self, debounce: float = 0.5, cooldown: float = 10.0) -> None:
        self.debounce = debounce
        self.cooldown = cooldown
        self._state: dict[str, dict] = {}

    def on_detected(self, roi_name: str, now: float) -> bool:
        """매 프레임 객체가 roi_name 안에 있을 때 호출. 트리거 발생 시 True 반환."""
        s = self._state
        if roi_name not in s:
            s[roi_name] = {"first_seen": now, "last_triggered": 0.0}
            return False

        entry = s[roi_name]
        if entry["first_seen"] is None:
            entry["first_seen"] = now

        if now - entry["last_triggered"] < self.cooldown:
            return False

        if now - entry["first_seen"] >= self.debounce:
            entry["last_triggered"] = now
            entry["first_seen"] = None
            return True

        return False

    def on_not_detected(self, roi_name: str) -> None:
        """매 프레임 객체가 roi_name 밖에 있을 때 호출 (디바운스 리셋)."""
        if roi_name in self._state:
            self._state[roi_name]["first_seen"] = None

    def cooldown_remaining(self, roi_name: str, now: float) -> float:
        """roi_name 의 남은 쿨다운 시간(초). 쿨다운 중이 아니면 0."""
        if roi_name not in self._state:
            return 0.0
        elapsed = now - self._state[roi_name].get("last_triggered", 0.0)
        return max(0.0, self.cooldown - elapsed)

    def clear(self) -> None:
        self._state.clear()


# ---------------------------------------------------------------------------
# AudioPlayer
# ---------------------------------------------------------------------------

def _detect_player() -> str | None:
    """사용 가능한 커맨드라인 MP3 플레이어를 순서대로 탐색."""
    for cmd in ("mpg123", "ffplay", "mpg321", "cvlc"):
        if shutil.which(cmd):
            return cmd
    return None


_CLI_PLAYER: str | None = _detect_player()


class AudioPlayer:
    """논블로킹 MP3 파일 플레이어.

    재생 중에 새 요청이 오면 skip (동시 중복 재생 방지).
    백엔드 우선순위: mpg123 / ffplay (subprocess) → pygame (fallback).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._playing = False

    @property
    def is_playing(self) -> bool:
        with self._lock:
            return self._playing

    def play(self, path: str) -> None:
        """백그라운드 스레드에서 재생 시작. 이미 재생 중이면 무시."""
        if not path:
            return
        with self._lock:
            if self._playing:
                return
            self._playing = True
        threading.Thread(target=self._run, args=(path,), daemon=True).start()

    def _run(self, path: str) -> None:
        try:
            if _CLI_PLAYER:
                self._play_subprocess(path)
            else:
                self._play_pygame(path)
        except Exception as exc:
            print(f"[WARN] AudioPlayer 재생 실패: {exc}")
        finally:
            with self._lock:
                self._playing = False

    def _play_subprocess(self, path: str) -> None:
        if _CLI_PLAYER == "mpg123":
            cmd = ["mpg123", "-q", path]
        elif _CLI_PLAYER == "ffplay":
            cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path]
        elif _CLI_PLAYER == "cvlc":
            cmd = ["cvlc", "--play-and-exit", "-q", path]
        else:
            cmd = [_CLI_PLAYER, path]
        subprocess.run(cmd, check=False)

    @staticmethod
    def _play_pygame(path: str) -> None:
        try:
            import pygame  # type: ignore[import]
            if not pygame.get_init():
                pygame.init()
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            pygame.mixer.music.load(path)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                time.sleep(0.05)
        except ImportError:
            print(
                "[WARN] 오디오 재생 불가 — mpg123/ffplay 없고 pygame도 없음\n"
                "       Pi:  sudo apt install -y mpg123\n"
                "       PC:  pip install pygame"
            )
