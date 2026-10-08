"""dashboard/backend/run_server.py — 콘솔 없이(pythonw) 서버를 띄우는 진입점.

새 Windows PC의 setup-server가 "60초 안에 응답하지 않습니다"로 실패했다. 설치 스크립트가 서버를 `pythonw -m uvicorn`으로
띄우는데, pythonw는 콘솔이 없어 `sys.stdout`/`sys.stderr`가 `None`이고 uvicorn의 로그 설정이 시작 직후 죽는다.
창이 숨겨져 있어 아무 메시지도 안 남아 원인을 알 수 없었다. 같은 앱을 `python.exe`로 띄우면 정상이었다.

여기서는 **`sys.stdout`/`sys.stderr`를 `None`으로 만들어** 그 조건을 리눅스에서도 재현한다.
"""

import importlib.util
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "dashboard" / "backend"
RUN = BACKEND / "run_server.py"

_spec = importlib.util.spec_from_file_location("run_server", RUN)
rs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rs)

# 콘솔이 없는 환경을 흉내 낸다 — pythonw에서는 stdout/stderr가 None이다
_NO_CONSOLE = "import sys, runpy; sys.stdout = None; sys.stderr = None; sys.argv = {argv!r}; runpy.{call}"


def _env(tmp_path):
    return {**os.environ, "DATABASE_URL": f"sqlite:///{tmp_path / 'db.sqlite'}", "AUDIO_DIR": str(tmp_path / "audio"),
            "AUTO_ENROLL": "false", "PYTHONUTF8": "1"}


def _free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def db(tmp_path):
    env = _env(tmp_path)
    res = subprocess.run([sys.executable, "-m", "app.db.init_db"], cwd=BACKEND, env=env, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    return env


def _health(port, timeout=25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except OSError:
            time.sleep(0.3)
    return False


def _stop(proc):
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


# --- 근본 원인과 수정 -----------------------------------------------------------------------

def test_콘솔이_없으면_python_m_uvicorn은_시작하자마자_죽는다(db):
    """근본 원인의 대조군 — 이것이 setup-server가 서버를 못 띄운 이유다."""
    port = _free_port()
    code = _NO_CONSOLE.format(argv=["uvicorn", "app.main:app", "--port", str(port)], call="run_module('uvicorn', run_name='__main__')")
    proc = subprocess.Popen([sys.executable, "-c", code], cwd=BACKEND, env=db,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert proc.wait(timeout=30) != 0
        assert not _health(port, timeout=1)
    finally:
        _stop(proc)


def test_콘솔이_없어도_run_server는_뜨고_응답한다(db, tmp_path):
    port, log = _free_port(), tmp_path / "logs" / "server.log"
    code = _NO_CONSOLE.format(argv=["run_server.py", "--port", str(port), "--log-file", str(log)],
                              call=f"run_path({str(RUN)!r}, run_name='__main__')")
    proc = subprocess.Popen([sys.executable, "-c", code], cwd=tmp_path, env=db,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert _health(port), "서버가 응답하지 않는다: " + (log.read_text(encoding="utf-8") if log.exists() else "(로그 없음)")
        assert proc.poll() is None
        text = log.read_text(encoding="utf-8")
        assert "=== 서버 시작" in text and "Application startup complete" in text    # 기동 과정이 파일에 남는다
    finally:
        _stop(proc)


def _run_failing(tmp_path, app):
    log = tmp_path / "server.log"
    env = {**_env(tmp_path), "PYTHONPATH": str(tmp_path)}
    res = subprocess.run([sys.executable, str(RUN), "--app", app, "--port", str(_free_port()), "--log-file", str(log)],
                         cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    return res, log.read_text(encoding="utf-8")


def test_앱을_찾지_못하면_이유가_로그에_남고_종료_코드가_0이_아니다(tmp_path):
    """창이 숨겨져 있어도 왜 안 떴는지 알 수 있어야 한다."""
    res, text = _run_failing(tmp_path, "no_such_module_xyz:app")
    assert res.returncode != 0
    assert "=== 서버 시작" in text and "no_such_module_xyz" in text


def test_앱_import_중_예외는_트레이스백으로_로그에_남는다(tmp_path):
    """모듈은 있는데 import 중에 죽는 경우(예: 의존성 누락) — 원인을 알 수 있는 트레이스백이 있어야 한다."""
    (tmp_path / "boom_mod.py").write_text("raise RuntimeError('의존성이 없습니다: boom')\napp = None\n", encoding="utf-8")
    res, text = _run_failing(tmp_path, "boom_mod:app")
    assert res.returncode != 0
    assert "Traceback" in text and "RuntimeError" in text and "의존성이 없습니다: boom" in text


def test_log_file이_없으면_콘솔로_나온다(db, tmp_path):
    """개발 중에는 평소처럼 쓴다 — 기본 동작이 바뀌면 안 된다."""
    port = _free_port()
    proc = subprocess.Popen([sys.executable, str(RUN), "--port", str(port)], cwd=BACKEND, env=db,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        assert _health(port)
    finally:
        _stop(proc)
    out = proc.stdout.read()
    assert "Application startup complete" in out and "=== 서버 시작" not in out


# --- 로그 보조 함수 -------------------------------------------------------------------------

def test_로그가_커지면_하나만_남기고_새로_시작한다(tmp_path):
    log = tmp_path / "server.log"
    log.write_bytes(b"x" * 100)
    rs.rotate(log, max_bytes=50)
    assert not log.exists() and (tmp_path / "server.log.1").read_bytes() == b"x" * 100
    log.write_bytes(b"y" * 100)
    rs.rotate(log, max_bytes=50)
    assert (tmp_path / "server.log.1").read_bytes() == b"y" * 100          # 이전 백업은 덮어쓴다(무한히 쌓이지 않는다)
    assert not (tmp_path / "server.log.2").exists()


def test_작은_로그는_그대로_이어_쓴다(tmp_path):
    log = tmp_path / "server.log"
    log.write_text("keep", encoding="utf-8")
    rs.rotate(log, max_bytes=1000)
    assert log.read_text(encoding="utf-8") == "keep"


def test_rotate는_실패해도_예외를_던지지_않는다(tmp_path):
    rs.rotate(tmp_path / "없는파일.log")                                       # 없는 파일
    rs.rotate(tmp_path)                                                        # 디렉터리


def test_redirect_output은_한글을_UTF8로_한_줄씩_바로_쓴다(tmp_path):
    code = (f"import importlib.util as u, sys; s=u.spec_from_file_location('r', {str(RUN)!r}); m=u.module_from_spec(s); "
            f"s.loader.exec_module(m); m.redirect_output(__import__('pathlib').Path({str(tmp_path / 'a' / 'b.log')!r})); "
            "print('안녕 로그'); import os; os._exit(0)")                     # 정상 종료(flush) 없이 죽어도 남아야 한다
    subprocess.run([sys.executable, "-c", code], check=True, env={**os.environ, "PYTHONUTF8": "1"})
    assert (tmp_path / "a" / "b.log").read_text(encoding="utf-8").strip() == "안녕 로그"


# --- 설치 스크립트가 이 진입점을 쓴다 ----------------------------------------------------------

PS1 = (ROOT / "deploy" / "setup-server.ps1").read_text(encoding="utf-8-sig")


def test_ps1은_서버를_pythonw_m_uvicorn으로_직접_띄우지_않는다():
    for line in PS1.splitlines():
        if line.strip().startswith("#"):
            continue
        assert not ("-m uvicorn" in line and "Fail" not in line and "Write-" not in line), line.strip()
        assert "'-m', 'uvicorn'" not in line, line.strip()


def test_ps1은_run_server와_로그_파일로_시작한다():
    assert "$RunServer = Join-Path $Backend 'run_server.py'" in PS1
    assert "data\\logs\\server.log" in PS1
    assert re.search(r'\$uvArgs\s*=\s*"-X utf8 `"\$RunServer`" --port \$Port --log-file `"\$LogFile`"', PS1)
    assert "'--log-file'" in PS1                                                # -NoAutostart의 직접 시작


def test_ps1은_응답이_없으면_서버_로그를_보여_준다():
    assert "Get-Content $LogFile -Tail" in PS1 and "서버 로그" in PS1


import re  # noqa: E402  (맨 아래 테스트가 사용)
