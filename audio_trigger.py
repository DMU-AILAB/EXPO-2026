"""audio_trigger.py — Streamlit 비의존 TriggerDispatcher + 논블로킹 AudioPlayer."""

from __future__ import annotations

import platform
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path


def _detect_alsa_device() -> str | None:
    """Pi에서 3.5mm 이어폰 잭(bcm2835 Headphones 카드)을 명시적으로 지정.

    ALSA에 asound.conf 설정이 없으면 카드 번호가 가장 낮은 쪽(보통 HDMI)이 기본
    출력이 되는데, 헤드리스(모니터 미연결) 운영 시 그쪽으로 나가면 아무 데도
    안 들린다. /proc/asound/cards에 Headphones 카드가 있을 때만 강제 지정하고,
    PC(Windows/카드 구성이 다른 환경)에서는 조용히 None을 반환해 기본 동작을 유지한다.
    """
    if platform.system() != "Linux":
        return None
    try:
        cards = Path("/proc/asound/cards").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    return "plughw:Headphones,0" if "Headphones" in cards else None


_ALSA_DEVICE = _detect_alsa_device()


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

    재생 요청은 큐에 쌓여 워커 스레드 1개가 순차 재생한다 (겹쳐 재생/음성 뭉개짐 방지).
    카메라가 여러 대라도 하나의 AudioPlayer 인스턴스를 공유하면, 두 카메라가 거의 동시에
    트리거해도 안내가 무시되지 않고 대기했다가 순서대로 나온다. 큐가 max_queue를 넘기면
    (병리적으로 트리거가 계속 밀리는 상황 대비) 초과분은 경고 로그와 함께 버린다.
    백엔드 우선순위: mpg123 / ffplay (subprocess) → pygame (fallback).
    """

    def __init__(self, max_queue: int = 5) -> None:
        self._queue: "queue.Queue[str]" = queue.Queue(maxsize=max_queue)
        self._lock = threading.Lock()
        self._playing = False
        threading.Thread(target=self._worker, daemon=True).start()

    @property
    def is_playing(self) -> bool:
        with self._lock:
            return self._playing

    def play(self, path: str) -> None:
        """재생 큐에 추가. 워커 스레드가 순서대로 재생한다."""
        if not path:
            return
        try:
            self._queue.put_nowait(path)
        except queue.Full:
            print(f"[WARN] 오디오 대기열 초과 — 요청 무시: {path}")

    def _worker(self) -> None:
        while True:
            path = self._queue.get()
            with self._lock:
                self._playing = True
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
            # -o alsa를 명시하지 않으면 mpg123가 JACK 출력 모듈을 먼저 시도하다
            # "jack server is not running" 에러로 조용히 실패하는 경우가 있다
            # (systemd 시스템 서비스는 로그인 세션의 PipeWire/JACK에 붙을 수 없음).
            cmd = ["mpg123", "-q", "-o", "alsa", path]
            if _ALSA_DEVICE:
                cmd = ["mpg123", "-q", "-o", "alsa", "-a", _ALSA_DEVICE, path]
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
