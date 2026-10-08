"""`device/diagnose.py` — 기기가 스스로 하는 연결·전원·시계 점검."""

import socket
import subprocess
from types import SimpleNamespace

import diagnose as d


def test_parse_throttled_flags():
    ok = d.parse_throttled(0x0)
    assert ok["raw"] == "0x0"
    assert not any(ok["now"].values()) and not any(ok["ever"].values())

    # 0x50005 = 지금 저전압+스로틀링, 과거에도 저전압+스로틀링
    bad = d.parse_throttled(0x50005)
    assert bad["now"] == {"undervoltage": True, "freq_capped": False, "throttled": True, "soft_temp_limit": False}
    assert bad["ever"]["undervoltage"] and bad["ever"]["throttled"] and not bad["ever"]["freq_capped"]

    # 과거에만 저전압(0x10000) — 지금은 정상
    past = d.parse_throttled(0x10000)
    assert past["ever"]["undervoltage"] and not past["now"]["undervoltage"]


def _run(stdout="", code=0, exc=None):
    def run(cmd, **kw):
        if exc:
            raise exc
        return SimpleNamespace(returncode=code, stdout=stdout, stderr="")
    return run


def test_read_throttled_parses_vcgencmd_output():
    assert d.read_throttled(_run("throttled=0x50005\n"))["now"]["undervoltage"] is True
    assert d.read_throttled(_run("throttled=0x0\n"))["raw"] == "0x0"


def test_read_throttled_is_none_when_unavailable():
    assert d.read_throttled(_run(exc=FileNotFoundError())) is None          # 라즈베리파이가 아님
    assert d.read_throttled(_run(code=1)) is None
    assert d.read_throttled(_run("garbage")) is None
    assert d.read_throttled(_run(exc=subprocess.TimeoutExpired("x", 3))) is None


def test_ntp_synchronized():
    assert d.ntp_synchronized(_run("yes\n")) is True
    assert d.ntp_synchronized(_run("no\n")) is False
    assert d.ntp_synchronized(_run(exc=FileNotFoundError())) is None


class _Sock:
    def close(self): pass


def test_check_server_all_stages_ok():
    r = d.check_server("http://B9.local:8001",
                       resolve=lambda h, p, type=None: [(0, 0, 0, "", ("192.168.0.105", p))],
                       connect=lambda addr, timeout: _Sock(),
                       http_get=lambda url, t: 200)
    assert r["dns"]["ok"] and r["dns"]["detail"] == "192.168.0.105"
    assert r["tcp"]["ok"] and r["http"]["ok"] and r["http"]["detail"] == "HTTP 200"


def test_check_server_dns_failure_skips_later_stages():
    def fail(*a, **k): raise socket.gaierror("Name or service not known")
    r = d.check_server("http://nope.local:8001", resolve=fail)
    assert not r["dns"]["ok"] and "해석" in r["dns"]["detail"]
    assert r["tcp"] is None and r["http"] is None


def test_check_server_tcp_timeout_is_firewall_symptom():
    """이번에 실제로 겪은 증상: 이름은 풀리는데 연결이 시간 초과 — 방화벽이다."""
    def timeout(addr, timeout): raise TimeoutError("timed out")
    r = d.check_server("http://192.168.0.105:8001",
                       resolve=lambda h, p, type=None: [(0, 0, 0, "", ("192.168.0.105", p))],
                       connect=timeout)
    assert r["dns"]["ok"] and not r["tcp"]["ok"] and "192.168.0.105:8001" in r["tcp"]["detail"]
    assert r["http"] is None


def test_check_server_http_5xx_is_not_ok_but_4xx_is():
    base = dict(resolve=lambda h, p, type=None: [(0, 0, 0, "", ("1.2.3.4", p))],
                connect=lambda addr, timeout: _Sock())
    assert d.check_server("http://x:1", http_get=lambda u, t: 404, **base)["http"]["ok"] is True
    assert d.check_server("http://x:1", http_get=lambda u, t: 502, **base)["http"]["ok"] is False


def test_check_server_empty_url():
    r = d.check_server("")
    assert not r["dns"]["ok"] and r["tcp"] is None
