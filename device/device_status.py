"""device_status.py — Pi의 하드웨어 상태를 표준 라이브러리만으로 읽는다.

`/proc`·`/sys` 파일만 쓴다. psutil 같은 패키지를 넣지 않는 이유는 기기에 무거운
의존성을 최소화하는 방침 때문이고, Pi가 3대로 늘면 설치·관리 부담도 3배가 된다.
Pi가 아닌 환경(개발 PC 등)에서는 해당 항목만 조용히 `None`이 된다 — 예외를 던져
호출부를 죽이지 않는다.

원래 `apps/roi_editor/server.py` 안에 있던 함수를 여기로 옮겼다. 하트비트
(`event_logger.HeartbeatSender`)가 같은 값을 필요로 하면서 **두 곳에 같은 코드가
생길 상황**이 됐기 때문이다.
"""

from __future__ import annotations

import os

__all__ = ["read_status", "CpuSampler"]


def read_status() -> dict:
    """가동시간 · CPU온도 · 부하 · 메모리. 읽지 못한 항목은 None."""
    status: dict = {"uptime_seconds": None, "cpu_temp_c": None, "load_avg": None,
                    "mem_used_mb": None, "mem_total_mb": None}

    try:
        with open("/proc/uptime") as f:
            status["uptime_seconds"] = float(f.read().split()[0])
    except OSError:
        pass

    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            status["cpu_temp_c"] = round(int(f.read().strip()) / 1000.0, 1)
    except (OSError, ValueError):
        pass

    try:
        status["load_avg"] = list(os.getloadavg())
    except (OSError, AttributeError):
        pass

    try:
        meminfo = {}
        with open("/proc/meminfo") as f:
            for line in f:
                key, _, rest = line.partition(":")
                meminfo[key] = int(rest.strip().split()[0])       # kB
        if "MemTotal" in meminfo and "MemAvailable" in meminfo:
            total = meminfo["MemTotal"] / 1024.0
            status["mem_total_mb"] = round(total, 1)
            status["mem_used_mb"] = round(total - meminfo["MemAvailable"] / 1024.0, 1)
    except (OSError, ValueError, KeyError):
        pass

    return status


class CpuSampler:
    """CPU 사용률(%). **두 시점의 차이**로만 구할 수 있어 상태를 들고 있어야 한다.

    `/proc/stat`의 누적 tick은 부팅 이후 총합이라 한 번 읽어서는 "지금 얼마나
    바쁜지"를 알 수 없다. 첫 호출은 기준점만 잡고 None을 반환한다.

    `load_avg`와 다른 값이다 — load는 실행 대기 중인 프로세스 수(코어 수에 따라
    해석이 달라짐)이고, 이쪽은 0~100%다. 백엔드 명세 §3이 "CPU 사용률(%)은 현재
    Pi가 산출하지 않는다"고 적은 것이 이 값이다.
    """

    def __init__(self) -> None:
        self._prev: tuple[int, int] | None = None

    def sample(self) -> float | None:
        cur = self._read()
        if cur is None:
            return None
        prev, self._prev = self._prev, cur
        if prev is None:
            return None                      # 첫 호출 — 기준점만 잡는다
        busy_delta = (cur[0] - prev[0])
        total_delta = (cur[1] - prev[1])
        if total_delta <= 0:
            return None
        return round(busy_delta / total_delta * 100.0, 1)

    @staticmethod
    def _read() -> tuple[int, int] | None:
        """(busy tick, total tick). idle과 iowait은 둘 다 놀고 있는 시간이다."""
        try:
            with open("/proc/stat") as f:
                parts = f.readline().split()
        except OSError:
            return None
        if not parts or parts[0] != "cpu":
            return None
        try:
            vals = [int(x) for x in parts[1:]]
        except ValueError:
            return None
        if len(vals) < 5:
            return None
        idle = vals[3] + vals[4]             # idle + iowait
        total = sum(vals)
        return total - idle, total
