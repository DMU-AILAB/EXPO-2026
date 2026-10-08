"""server_join.py — Pi가 서버를 스스로 찾아 **등록을 요청**하는 참가 에이전트.

`roi_editor`가 `EventSender`/`HeartbeatSender`를 띄우는 자리에서 함께 시작한다(새 systemd 유닛·sudoers 변경
없음). **모든 Pi에서** 동작한다 — 신원이 없는 새 기기는 물론, 이미 다른 서버에 등록된 기기도 서버 PC를 바꿨을
때 새 서버가 승인 대기 목록에서 알아볼 수 있게 한다.

동작
- `server_discovery.discover()`가 찾은 서버 중 **자기 신원의 `server_url`과 다른 서버에만**
  `POST {서버}/api/bootstrap/enroll`을 보낸다. 자기 서버와 같으면 아무것도 하지 않는다.
- 서버가 **그 Pi의 :5000을 직접 읽어** 판정한다(신원 없음 → 등록 / 우리 소속 → 주소 갱신 / 다른 서버 소속
  → 승인 대기). 이 에이전트는 자기 신고를 하지 않는다.
- ★ **Pi 스스로는 신원을 바꾸지 않는다.** 신원이 바뀌는 때는 서버가 `:5000/api/identity`로 심을 때
  (자동 등록·승인·주소 갱신)뿐이다. 이 모듈은 `save_identity`를 부르지 않는다.
- 여러 서버가 답하면 전부 기록한다. 신원이 없을 때는 **등록에 성공할 때까지** 차례로 시도한다. 다른 서버가
  `pending`이라고 하면 "승인 대기 중"만 기록하고 **계속 자기 서버로 동작한다.**

주기 — 서버 승인 대기는 **15분 무요청이면 만료**되므로 `pending`은 5분마다 다시 알린다
  신원 없음: 30초에서 시작해 최대 5분까지 백오프 · 신원 있음: 10분(승인 대기가 있으면 5분)
  같은 서버에는 결과별 간격으로만 다시 묻는다(`known`·`enrolled` 30분, `pending` 5분, `rejected` 1시간).

첫 시도는 `START_DELAY_SEC` 뒤에 한다 — 이 스레드는 `uvicorn.run` **이전**에 시작되는데, 서버가 등록 요청을
받으면 이 Pi의 :5000을 되불러 확인하므로 그때 이미 떠 있어야 한다.

표준 라이브러리만 쓴다(`urllib`). 네트워크 작업은 이 스레드에만 있고 탐지 루프와 무관하다.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional, Union

from device_identity import load_identity
from server_discovery import discover, url_key

__all__ = ["JoinAgent", "post_enroll"]

START_DELAY_SEC = 10.0
NO_IDENTITY_MIN_SEC = 30.0
NO_IDENTITY_MAX_SEC = 300.0
IDENTITY_PERIOD_SEC = 600.0
PENDING_PERIOD_SEC = 300.0
# 같은 서버에 다시 묻는 최소 간격 — 서버의 응답 상태별.
_REASK_SEC = {"enrolled": 1800.0, "known": 1800.0, "pending": PENDING_PERIOD_SEC,
              "rejected": 3600.0, "failed": 300.0, "error": 300.0}


def post_enroll(server_url: str, hostname: str, timeout: float = 20.0) -> dict:
    """`POST {server_url}/api/bootstrap/enroll`. 항상 dict를 돌려준다 — 실패는 `{"status":"error",…}`."""
    body = json.dumps({"hostname": hostname}).encode("utf-8")
    req = urllib.request.Request(f"{server_url.rstrip('/')}/api/bootstrap/enroll", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8"))
            msg = detail.get("message") or detail.get("error") or str(detail)
        except Exception:                                     # noqa: BLE001
            msg = exc.reason
        return {"status": "error", "error": f"HTTP {exc.code}: {msg}"}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"status": "error", "error": str(getattr(exc, "reason", exc))}
    data = payload.get("data") if isinstance(payload, dict) else None
    return data if isinstance(data, dict) and "status" in data else {"status": "error", "error": "알 수 없는 응답"}


class JoinAgent(threading.Thread):
    """서버를 찾아 등록을 요청하는 백그라운드 스레드. `step()`이 한 번의 점검이고 테스트가 직접 부른다."""

    def __init__(self, identity_path: Union[str, Path], *,
                 discover_fn: Callable[[], list] = discover,
                 post_fn: Callable[[str, str], dict] = post_enroll,
                 hostname_fn: Callable[[], str] = socket.gethostname,
                 clock: Callable[[], float] = time.monotonic,
                 log: Callable[[str], None] = print,
                 start_delay: float = START_DELAY_SEC) -> None:
        super().__init__(daemon=True, name="JoinAgent")
        self.identity_path = Path(identity_path)
        self._discover = discover_fn
        self._post = post_fn
        self._hostname = hostname_fn
        self._clock = clock
        self._log = log
        self.start_delay = start_delay
        # ★ `_stop`이 아니다 — threading.Thread의 내부 메서드 이름이라 덮어쓰면 join()이 깨진다.
        self._stop_event = threading.Event()
        self._next_ask: dict[str, float] = {}       # url_key -> 이 시각 전에는 다시 묻지 않는다
        self._status: dict[str, str] = {}           # url_key -> 마지막 응답 상태
        self._backoff = NO_IDENTITY_MIN_SEC
        self._last_note = ""
        self.last_status: Optional[str] = None
        self.attempts = 0

    # ---------------------------------------------------------------- 한 번의 점검

    def _say(self, msg: str) -> None:
        # 같은 메시지를 계속 반복해 로그를 채우지 않는다.
        if msg != self._last_note:
            self._last_note = msg
            self._log(f"[JOIN] {msg}")

    def step(self) -> float:
        """한 번 점검하고 **다음 점검까지의 초**를 돌려준다."""
        try:
            ident = load_identity(self.identity_path)
        except Exception as exc:                                  # noqa: BLE001
            self._say(f"신원 파일을 읽지 못했습니다: {exc}")
            return NO_IDENTITY_MAX_SEC
        registered = ident is not None
        own = url_key(ident.server_url) if ident else ""

        try:
            found = list(self._discover() or [])
        except Exception as exc:                                  # noqa: BLE001
            self._say(f"서버 탐색 실패: {exc}")
            found = []
        if not found:
            self._say("VisionGuide 서버를 찾지 못했습니다" + ("" if registered else " — 계속 찾는 중"))
            return self._delay(registered)

        others = [s for s in found if url_key(s.get("url")) != own]
        if own and not others:
            self._say(f"자기 서버({ident.server_url})만 응답합니다 — 할 일 없음")
            return self._delay(registered)
        if len(found) > 1:
            self._say("응답한 서버: " + ", ".join(s.get("url", "?") for s in found))

        joined = False
        for server in others:
            key = url_key(server["url"])
            if self._clock() < self._next_ask.get(key, 0.0):
                continue
            self.attempts += 1
            result = self._post(server["url"], self._hostname())
            status = str(result.get("status", "error"))
            self._status[key] = status
            self.last_status = status
            self._next_ask[key] = self._clock() + _REASK_SEC.get(status, _REASK_SEC["error"])
            self._report(server["url"], status, result)
            if status in ("enrolled", "known"):
                joined = True
                if not registered:
                    break                                          # 신원이 없었다면 여기서 끝
        if joined:
            # 서버가 곧 신원을 심는다 — 짧게 뒤에 다시 보고(자기 서버와 같아졌는지), 백오프는 올리지 않는다.
            self._backoff = NO_IDENTITY_MIN_SEC
            return NO_IDENTITY_MIN_SEC if not registered else self._delay(registered)
        return self._delay(registered)

    def _report(self, url: str, status: str, result: dict) -> None:
        if status == "enrolled":
            self._log(f"[JOIN] 서버에 등록되었습니다: {url} (기기 {result.get('device_id')})")
        elif status == "known":
            self._log(f"[JOIN] 이미 이 서버 소속입니다: {url}")
        elif status == "pending":
            self._log(f"[JOIN] 승인 대기 중: {url} — 대시보드에서 승인하면 이 서버로 옮겨 갑니다 "
                      f"(그동안 현재 서버로 계속 동작합니다)")
        elif status == "rejected":
            self._log(f"[JOIN] 서버가 요청을 거절했습니다: {url} (1시간 동안 다시 묻지 않습니다)")
        else:
            self._log(f"[JOIN] 등록 요청 실패: {url} — {result.get('error') or status}")

    def _delay(self, registered: bool) -> float:
        if not registered:
            delay = self._backoff
            self._backoff = min(self._backoff * 2, NO_IDENTITY_MAX_SEC)
            return delay
        # 승인 대기가 있으면 15분 만료 전에 다시 알려야 한다.
        return PENDING_PERIOD_SEC if "pending" in self._status.values() else IDENTITY_PERIOD_SEC

    # ---------------------------------------------------------------- 스레드

    def run(self) -> None:
        if self._stop_event.wait(self.start_delay):
            return
        while not self._stop_event.is_set():
            try:
                delay = self.step()
            except Exception as exc:                              # noqa: BLE001
                # 에이전트의 실패가 roi_editor를 멈추면 안 된다 — 기록하고 다음 주기에 계속한다.
                self._log(f"[JOIN] 점검 중 오류: {exc!r}")
                delay = NO_IDENTITY_MAX_SEC
            self._stop_event.wait(delay)

    def stop(self) -> None:
        self._stop_event.set()
