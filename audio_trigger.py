"""audio_trigger.py — Streamlit 비의존 TriggerDispatcher + 논블로킹 AudioPlayer."""

from __future__ import annotations

import os
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
        """매 프레임 객체가 roi_name 안에 있을 때 호출. 트리거 발생 시 True 반환.

        트리거가 발생하면 last_triggered를 inf로 설정해 오디오가 끝날 때까지
        재트리거를 차단한다. 오디오 재생 완료 후 update_last_triggered()가
        실제 종료 시각으로 덮어써야 쿨다운 카운트다운이 시작된다.
        """
        s = self._state
        if roi_name not in s:
            s[roi_name] = {"first_seen": now, "last_triggered": 0.0}
            return False

        entry = s[roi_name]
        if entry["first_seen"] is None:
            entry["first_seen"] = now

        last = entry["last_triggered"]
        # inf는 오디오 재생 중 — 종료 콜백이 올 때까지 차단
        if last == float("inf") or (last > 0 and now - last < self.cooldown):
            return False

        if now - entry["first_seen"] >= self.debounce:
            entry["last_triggered"] = float("inf")  # 오디오 종료까지 무한 차단
            entry["first_seen"] = None
            return True

        return False

    def update_last_triggered(self, roi_name: str, t: float) -> None:
        """오디오 재생 완료 후 호출 — 쿨다운 기산점을 오디오 종료 시각으로 갱신."""
        if roi_name in self._state:
            self._state[roi_name]["last_triggered"] = t

    def on_not_detected(self, roi_name: str) -> None:
        """매 프레임 객체가 roi_name 밖에 있을 때 호출 (디바운스 리셋)."""
        if roi_name in self._state:
            self._state[roi_name]["first_seen"] = None

    def cooldown_remaining(self, roi_name: str, now: float) -> float:
        """roi_name 의 남은 쿨다운 시간(초). 쿨다운 중이 아니면 0."""
        if roi_name not in self._state:
            return 0.0
        last = self._state[roi_name].get("last_triggered", 0.0)
        if last == float("inf"):
            return self.cooldown  # 재생 중 — 최대값 표시
        elapsed = now - last
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


class UsbAudioPower:
    """Control the Raspberry Pi USB hub that supplies the speaker VBUS.

    The speaker uses USB for power and the 3.5 mm jack for audio.  On a Pi 4
    the native USB 2.0 ports are commonly ganged, so this intentionally
    controls the configured USB 2.0 hub group rather than pretending that one
    physical port can be isolated.
    """

    def __init__(
        self,
        location: str,
        settle_seconds: float = 1.0,
        command: str = "uhubctl",
        use_sudo: bool = True,
    ) -> None:
        if not location.strip():
            raise ValueError("USB audio hub location must not be empty")
        if settle_seconds < 0:
            raise ValueError("USB audio settle time must not be negative")
        self.location = location.strip()
        self.settle_seconds = settle_seconds
        self.command = command
        self.use_sudo = use_sudo

    @classmethod
    def from_environment(cls) -> "UsbAudioPower | None":
        """Create a controller only when USB speaker power is configured."""
        location = os.environ.get("VISIONGUIDE_USB_AUDIO_HUB", "").strip()
        if not location:
            return None

        raw_settle = os.environ.get("VISIONGUIDE_USB_AUDIO_SETTLE", "0.3")
        try:
            settle_seconds = float(raw_settle)
        except ValueError:
            print(
                "[WARN] 잘못된 VISIONGUIDE_USB_AUDIO_SETTLE 값: "
                f"{raw_settle!r}; 기본값 0.3초를 사용합니다"
            )
            settle_seconds = 0.3

        try:
            return cls(location=location, settle_seconds=settle_seconds)
        except ValueError as exc:
            print(f"[WARN] USB 오디오 전원 제어 비활성화: {exc}")
            return None

    def _set_power(self, enabled: bool) -> bool:
        action = "on" if enabled else "off"
        command = [self.command, "-l", self.location, "-a", action]
        if self.use_sudo:
            command = ["sudo", "-n", *command]

        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            print(f"[WARN] USB 오디오 전원 {action} 실패: {exc}")
            return False

        if result.returncode != 0:
            detail = (result.stdout or result.stderr or "").strip()
            suffix = f" ({detail})" if detail else ""
            print(f"[WARN] USB 오디오 전원 {action} 실패{suffix}")
            return False
        return True

    def power_on(self) -> bool:
        """Turn on VBUS and report whether uhubctl succeeded."""
        return self._set_power(True)

    def power_off(self) -> bool:
        """Turn off VBUS and report whether uhubctl succeeded."""
        return self._set_power(False)


class AudioPlayer:
    """논블로킹 MP3 파일 플레이어.

    재생 요청은 큐에 쌓여 워커 스레드 1개가 순차 재생한다 (겹쳐 재생/음성 뭉개짐 방지).
    카메라가 여러 대라도 하나의 AudioPlayer 인스턴스를 공유하면, 두 카메라가 거의 동시에
    트리거해도 안내가 무시되지 않고 대기했다가 순서대로 나온다. 큐가 max_queue를 넘기면
    (병리적으로 트리거가 계속 밀리는 상황 대비) 초과분은 경고 로그와 함께 버린다.
    백엔드 우선순위: mpg123 / ffplay (subprocess) → pygame (fallback).
    """

    def __init__(
        self,
        max_queue: int = 5,
        usb_power: UsbAudioPower | None = None,
    ) -> None:
        self._queue: "queue.Queue[tuple]" = queue.Queue(maxsize=max_queue)
        self._lock = threading.Lock()
        self._playing = False
        self._usb_power = usb_power if usb_power is not None else UsbAudioPower.from_environment()
        if self._usb_power is not None:
            # Leave the speaker unpowered until an announcement is actually queued.
            self._usb_power.power_off()
        threading.Thread(target=self._worker, daemon=True).start()

    @property
    def is_playing(self) -> bool:
        with self._lock:
            return self._playing

    def play(self, path: str, on_done: "callable | None" = None) -> None:
        """재생 큐에 추가. 재생 완료 후 on_done() 호출 (쿨다운 기산점 갱신용)."""
        if not path:
            if on_done:
                on_done()
            return
        try:
            self._queue.put_nowait((path, on_done))
        except queue.Full:
            print(f"[WARN] 오디오 대기열 초과 — 요청 무시: {path}")
            if on_done:
                on_done()

    def _worker(self) -> None:
        while True:
            path, on_done = self._queue.get()
            with self._lock:
                self._playing = True
            usb_powered = False
            try:
                if self._usb_power is not None:
                    usb_powered = self._usb_power.power_on()
                    if usb_powered and self._usb_power.settle_seconds:
                        time.sleep(self._usb_power.settle_seconds)
                if _CLI_PLAYER:
                    self._play_subprocess(path)
                else:
                    self._play_pygame(path)
            except Exception as exc:
                print(f"[WARN] AudioPlayer 재생 실패: {exc}")
            finally:
                if usb_powered:
                    self._usb_power.power_off()
                with self._lock:
                    self._playing = False
                if on_done:
                    try:
                        on_done()
                    except Exception as exc:
                        print(f"[WARN] AudioPlayer on_done 콜백 오류: {exc}")

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
