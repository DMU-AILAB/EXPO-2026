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

# 이탈 히스테리시스 — subject가 ROI 밖에 이만큼 머물러야 "방문이 끝났다"로 본다.
# 경계에 걸친 박스는 EMA 스무딩 뒤에도 한두 프레임씩 안팎을 오가는데, 이게 없으면
# 그때마다 방문이 리셋돼 같은 사람에게 안내가 반복된다.
_DEFAULT_EXIT_GRACE_SEC = 1.0

# ROI 단위 최소 간격 — 주체가 누구든 이 간격 안에는 같은 ROI에서 두 번 안내하지 않는다.
# 쿨다운(주체별 재안내 금지)과 역할이 다르다: 이쪽은 유동인구가 많을 때 안내가
# 연달아 터지는 것을 막는 스팸 방지용이다. 오디오 재생 시간 위에 더해지므로
# 실제 간격은 이 값 + 오디오 길이다.
_DEFAULT_MIN_GAP_SEC = 3.0

# 주체를 구분하지 않는 호출부(레거시 경로·시뮬레이터)가 쓰는 단일 주체 키.
_LEGACY_SUBJECT = "__any__"


class StandaloneDispatcher:
    """ROI별 안내 발사 게이트 — 디바운스 · 쿨다운 · **주체(subject)별 방문 판정**.

    주체는 `pedestrian_entity`의 `entity_id`(= 사람 한 명)다. 주체를 구분하기 전에는
    상태가 **ROI 이름 하나로만** 묶여 있어서 두 가지 문제가 있었다.

    1. **지나가는 사람이 안내를 통째로 놓친다.** A가 안내를 받으면 ROI 전체가 쿨다운에
       들어가는데, 그 사이 B가 ROI를 통과해 **나가버리면** 디바운스 기준점이 리셋되어
       B에게는 아무것도 나가지 않는다. 걸어서 지나가는 보행자가 정확히 이 경우다.
       (계속 서 있으면 쿨다운이 풀릴 때 나가긴 한다 — 즉 누락이 아니라 최대 쿨다운만큼
       지연되는 경우와, 완전히 누락되는 경우가 섞여 있었다.)
    2. **머물러 있는 사람에게 쿨다운마다 같은 안내가 반복된다.** ROI 단위 키의 부작용이지
       의도된 설계가 아니다.

    주체별 상태기계로 둘 다 닫는다.

        OUT  --(연속 debounce초 이상 ROI 안)-->  PENDING
        PENDING --(ROI 쿨다운이 풀림)-->  ANNOUNCED   ← 여기서 발사
        PENDING/ANNOUNCED --(연속 exit_grace초 이상 ROI 밖)-->  OUT (방문 종료)

    **PENDING은 쿨다운에 걸려도 버리지 않는다** — 쿨다운이 풀리는 즉시, 그 주체가
    아직 ROI 안에 있을 때만 발사된다. 나간 뒤에 안내해봐야 소용이 없기 때문이다.
    이것이 위 1번의 수정이다.

    **발사되면 그 순간 ROI 안에 있는 주체를 전부 ANNOUNCED로 표시한다.** 안내는
    스피커로 공간에 나가는 것이라 한 번이면 그 자리의 모두가 듣는다 — 주체별로
    쿨다운만 풀어주면 3명이 동시에 들어올 때 같은 안내가 3번 큐에 쌓인다.

    **두 개의 시간 제한이 서로 다른 일을 한다.**

    | 값 | 범위 | 막는 것 |
    |---|---|---|
    | `cooldown` (`rois.json`, 기본 10초) | **주체별** | 같은 사람에게 반복 안내 (나갔다 다시 들어와도) |
    | `min_gap` (기본 3초) | **ROI별** | 유동인구가 많을 때 안내가 연달아 터지는 스팸 |

    쿨다운을 ROI가 아니라 주체에 건 이유: ROI에 걸면 **먼저 온 사람의 쿨다운이 뒤에
    오는 사람의 안내를 잡아먹는다.** A가 안내를 받은 뒤 10초 안에 B가 ROI를 통과해
    나가버리면 B는 아무것도 못 듣는데, 걸어서 지나가는 보행자가 정확히 그 경우다.
    주체별 래치가 이미 반복을 막아주므로 ROI에 긴 쿨다운을 둘 이유가 없어졌다.

    `min_gap`은 `cooldown`보다 크지 않게 잘린다 — 사용자가 쿨다운을 1초로 낮췄는데
    스팸 방지값이 3초로 남아 더 둔해지는 역전을 막기 위해서다.
    """

    def __init__(self, debounce: float = 0.5, cooldown: float = 10.0,
                 exit_grace: float = _DEFAULT_EXIT_GRACE_SEC,
                 min_gap: float = _DEFAULT_MIN_GAP_SEC) -> None:
        self.debounce = debounce
        self.cooldown = cooldown
        self.exit_grace = exit_grace
        self.min_gap = min(min_gap, cooldown)
        # roi_name -> {"last_triggered": float,
        #              "subjects": {subject: {...}},      # 진행 중인 방문
        #              "recent":   {subject: 안내 시각}}  # 방문이 끝난 뒤에도 남는 기록
        self._state: dict[str, dict] = {}

    # ------------------------------------------------------------------ #

    def update(self, roi_name: str, subjects, now: float):
        """이번 프레임에 `roi_name` 안에 있는 주체 집합을 넘긴다.

        지금 안내를 발사해야 하면 그 원인이 된 주체를, 아니면 None을 반환한다.
        **ROI마다 프레임당 한 번만 호출할 것** — 이탈 판정이 "이번 프레임에 없었다"에
        달려 있어서, 같은 ROI를 두 번 부르면 두 번째 호출이 첫 번째의 주체들을
        이탈로 오인한다.
        """
        entry = self._state.setdefault(roi_name, {"last_triggered": 0.0,
                                                  "subjects": {}, "recent": {}})
        subs = entry["subjects"]
        recent = entry["recent"]
        present = set(subjects)

        for sid in present:
            st = subs.get(sid)
            if st is None:
                st = subs[sid] = {"since": now, "gone_since": None, "announced": False}
            # 이탈 유예 중에 돌아왔으면 같은 방문으로 잇는다(경계 깜빡임 흡수).
            st["gone_since"] = None

        for sid, st in list(subs.items()):
            if sid in present:
                continue
            if st["gone_since"] is None:
                st["gone_since"] = now
            elif now - st["gone_since"] >= self.exit_grace:
                del subs[sid]              # 방문 종료 — 다시 들어오면 새 방문이다

        # 주체별 쿨다운 기록은 방문이 끝나도 남아야 한다(나갔다 바로 다시 들어오는
        # 사람에게 재안내하지 않기 위해). 대신 쿨다운이 지나면 지운다 — 장시간
        # 가동 시 무한히 쌓이면 안 된다.
        for sid, t in list(recent.items()):
            if now - t >= self.cooldown:
                del recent[sid]

        last = entry["last_triggered"]
        # inf는 오디오 재생 중 — 종료 콜백(update_last_triggered)이 올 때까지 차단.
        # 그 뒤에는 ROI 단위 최소 간격만 본다(주체별 쿨다운은 아래에서 따로 본다).
        if last == float("inf") or (last > 0 and now - last < self.min_gap):
            return None

        ready = [sid for sid in present
                 if not subs[sid]["announced"]
                 and now - subs[sid]["since"] >= self.debounce
                 and (sid not in recent or now - recent[sid] >= self.cooldown)]
        if not ready:
            return None

        # 가장 오래 기다린 주체를 안내의 원인으로 삼는다(쿨다운에 밀린 순서 보존).
        winner = min(ready, key=lambda sid: subs[sid]["since"])
        entry["last_triggered"] = float("inf")   # 오디오 종료까지 무한 차단
        for sid in present:
            subs[sid]["announced"] = True        # 같은 공간에 있으면 다 들었다
            recent[sid] = now
        return winner

    def states(self, roi_name: str) -> dict:
        """주체별 상태 스냅샷 — 디버그 오버레이/로그용 (판정에는 쓰지 않는다)."""
        subs = self._state.get(roi_name, {}).get("subjects", {})
        return {sid: ("ANNOUNCED" if st["announced"] else "PENDING")
                for sid, st in subs.items() if st["gone_since"] is None}

    def update_last_triggered(self, roi_name: str, t: float) -> None:
        """오디오 재생 완료 후 호출 — 쿨다운 기산점을 오디오 종료 시각으로 갱신."""
        if roi_name in self._state:
            self._state[roi_name]["last_triggered"] = t

    def cooldown_remaining(self, roi_name: str, now: float) -> float:
        """이 ROI에서 **다음 안내가 가능해지기까지** 남은 시간(초).

        주체별 쿨다운이 아니라 ROI 단위 최소 간격(`min_gap`) 기준이다 — UI가 알고
        싶은 것은 "이 구역에서 언제 다시 소리가 날 수 있나"이기 때문이다.
        """
        if roi_name not in self._state:
            return 0.0
        last = self._state[roi_name].get("last_triggered", 0.0)
        if last == float("inf"):
            return self.min_gap   # 재생 중 — 최대값 표시
        return max(0.0, self.min_gap - (now - last))

    # --- 주체를 구분하지 않는 레거시 호출부용 -------------------------- #

    def on_detected(self, roi_name: str, now: float) -> bool:
        """주체 구분 없이 "이번 프레임 ROI 안에 무언가 있다"를 알린다.

        `update()`를 단일 주체로 호출하는 얇은 래퍼다. 주체 구분이 없으므로
        "머무는 동안 재안내 없음"이 ROI 전체에 적용된다 — 새 코드는 `update()`를 쓸 것.
        """
        return self.update(roi_name, {_LEGACY_SUBJECT}, now) is not None

    def on_not_detected(self, roi_name: str, now: float | None = None) -> None:
        """주체 구분 없이 "이번 프레임 ROI가 비었다"를 알린다."""
        if roi_name in self._state:
            self.update(roi_name, (), now if now is not None else time.time())

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
