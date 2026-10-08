"""서버 설치 스크립트(setup-server.ps1 / .bat / .sh)의 정적·dry-run 점검.

이 스크립트들은 winget·방화벽·작업 스케줄러를 만져서 pytest가 실제로 돌릴 수 없다. 대신
**조용히 깨지는 지점**을 고정한다 — 실제로 한 번씩 겪었거나 겪기 쉬운 것들이다:

- PowerShell 5.1은 BOM이 없는 UTF-8을 CP949로 읽어 한글이 깨진다.
- `Do`는 PowerShell 예약어라 함수 이름으로 쓰면 파싱 오류다.
- `.bat`은 LF 줄바꿈이면 `cmd`가 오동작한다.
- 방화벽 UDP 포트가 백엔드 설정과 어긋나면 기기가 서버를 못 찾는다.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PS1 = ROOT / "deploy" / "setup-server.ps1"
SH = ROOT / "deploy" / "setup-server.sh"
BAT = ROOT / "setup-server.bat"
POWERSHELL = shutil.which("powershell.exe")


def test_ps1은_utf8_bom으로_시작한다():
    assert PS1.read_bytes()[:3] == b"\xef\xbb\xbf"


def test_ps1은_예약어_Do를_함수로_쓰지_않는다():
    text = PS1.read_text(encoding="utf-8-sig")
    assert not re.search(r"^\s*function\s+Do\b", text, re.M)
    assert not re.search(r'^\s*Do\s+["\']', text, re.M)


def test_ps1은_python_명령에_기대지_않는다():
    # Microsoft Store 스텁이 성공 코드를 돌려주며 아무것도 하지 않는다 — py 런처/설치 경로만 쓴다
    text = PS1.read_text(encoding="utf-8-sig")
    assert not re.search(r"&\s+python(\.exe)?\s", text)


def test_ps1은_네이티브_stderr_리다이렉트를_Continue로_감싼다():
    # Stop + `2>$null`은 PowerShell 5.1에서 NativeCommandError로 중단된다(실제로 겪음)
    text = PS1.read_text(encoding="utf-8-sig")
    offset = 0
    for line in text.splitlines(keepends=True):
        if "2>$null" in line and not line.lstrip().startswith("#"):       # 주석에 적힌 것은 제외
            window = text[max(0, offset - 600):offset]
            assert "ErrorActionPreference = 'Continue'" in window, f"2>$null이 Continue 구간 밖에 있다: {line.strip()}"
        offset += len(line)


def test_ps1_방화벽_udp_포트는_백엔드_설정과_같다():
    port = int(re.search(r"\$DiscoveryPort\s*=\s*(\d+)", PS1.read_text(encoding="utf-8-sig")).group(1))
    config = (ROOT / "dashboard" / "backend" / "app" / "config.py").read_text(encoding="utf-8")
    m = re.search(r"discovery_port:\s*int\s*=\s*(\d+)", config)
    if m is None:
        pytest.skip("discovery_port는 2단계에서 추가된다")
    assert int(m.group(1)) == port


def test_ps1_기본_포트는_8000이다():
    text = PS1.read_text(encoding="utf-8-sig")
    assert re.search(r"\[int\]\$Port\s*=\s*8000", text)
    # 8001은 8000이 WSL의 다른 프로세스에 점유돼 우연히 쓴 값이었다
    assert "8001" not in text


def test_ps1은_백엔드를_reload나_workers로_띄우지_않는다():
    # 하트비트 버퍼와 APScheduler가 한 프로세스 안에 있다
    for line in PS1.read_text(encoding="utf-8-sig").splitlines():
        if "uvicorn" in line and not line.strip().startswith("#"):
            assert "--reload" not in line and "--workers" not in line, line


def test_bat은_crlf이고_ps1을_부른다():
    raw = BAT.read_bytes()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    text = raw.decode("ascii")                                  # 코드페이지 문제를 피하려고 ASCII만
    assert r"deploy\setup-server.ps1" in text and "-ExecutionPolicy Bypass" in text
    assert "endlocal & exit /b %RC%" in text                     # `^&`는 문자 그대로가 되어 틀린 구문


@pytest.mark.skipif(POWERSHELL is None, reason="powershell.exe가 없다(Windows/WSL 밖)")
def test_ps1은_실제_파서를_통과한다():
    win = subprocess.run(["wslpath", "-w", str(PS1)], capture_output=True, text=True).stdout.strip() or str(PS1)
    cmd = ("$e=$null;$t=$null;[void][System.Management.Automation.Language.Parser]::ParseFile("
           f"'{win}',[ref]$t,[ref]$e); if($e.Count){{$e|%{{\"$($_.Extent.StartLineNumber): $($_.Message)\"}};exit 1}}")
    res = subprocess.run([POWERSHELL, "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stdout + res.stderr


@pytest.mark.skipif(POWERSHELL is None, reason="powershell.exe가 없다(Windows/WSL 밖)")
def test_ps1_dry_run은_아무것도_바꾸지_않고_끝까지_간다():
    win = subprocess.run(["wslpath", "-w", str(PS1)], capture_output=True, text=True).stdout.strip() or str(PS1)
    res = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", win, "-DryRun"],
                         capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert res.returncode == 0, res.stdout + res.stderr
    out = res.stdout
    for needle in ("[dry-run] TCP 8000", "[dry-run] UDP 48555", "[dry-run] 작업 스케줄러 등록",
                   "아무것도 바꾸지 않았습니다"):
        assert needle in out, needle


def test_sh_구문과_dry_run():
    assert subprocess.run(["bash", "-n", str(SH)]).returncode == 0
    venv = ROOT / "dashboard" / "backend" / ".venv"
    existed = venv.exists()
    res = subprocess.run(["bash", str(SH), "--dry-run"], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    assert "아무것도 바꾸지 않았습니다" in res.stdout and "UDP 48555" in res.stdout
    assert venv.exists() == existed                              # dry-run이 venv를 만들지 않았다


def test_sh는_잘못된_옵션과_포트를_거부한다():
    assert subprocess.run(["bash", str(SH), "--bogus"], capture_output=True).returncode == 2
    assert subprocess.run(["bash", str(SH), "--port", "abc"], capture_output=True).returncode == 2


# --- Windows 인코딩(cp949) ------------------------------------------------------------------
# Windows의 파이썬·pip 기본 인코딩은 로케일이다(한국어 Windows는 cp949). UTF-8 한글이 든 파일을 인코딩 지정 없이 읽으면
# "'cp949' codec can't decode byte 0xeb"로 죽는다 — 리눅스·WSL은 UTF-8이 기본이라 개발 중에는 드러나지 않는다.
# 새 Windows PC의 setup-server가 `pip install -r requirements.txt`에서 실제로 이렇게 죽었다.

def test_설치가_pip로_읽는_requirements는_ASCII뿐이다():
    """pip는 requirements를 로케일 인코딩으로 읽는다 — 한글 주석 한 줄이면 Windows 설치가 멈춘다."""
    raw = (ROOT / "dashboard" / "backend" / "requirements.txt").read_bytes()
    bad = [(i + 1, line[:40]) for i, line in enumerate(raw.splitlines()) if any(b > 127 for b in line)]
    assert not bad, f"비ASCII 줄: {bad}"


def test_ps1은_파이썬을_UTF8_모드로_돌린다():
    text = PS1.read_text(encoding="utf-8-sig")
    assert re.search(r"\$env:PYTHONUTF8\s*=\s*'1'", text)
    assert re.search(r"\$env:PYTHONIOENCODING\s*=\s*'utf-8'", text)


def test_자동_시작_작업의_인자에도_UTF8_모드가_있다():
    text = PS1.read_text(encoding="utf-8-sig")
    assert re.search(r'\$uvArgs\s*=\s*"-X utf8 ', text)
    assert "'-X', 'utf8'" in text                                  # -NoAutostart의 직접 시작


def _text_io_without_encoding(path: Path):
    """인코딩 없이 텍스트로 파일을 읽고 쓰는 호출(`read_text()`·`write_text(x)`·텍스트 모드 `open`)."""
    import ast
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        kw = {k.arg for k in node.keywords}
        f = node.func
        name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
        if name in ("read_text", "write_text") and "encoding" not in kw:
            found.append((node.lineno, name))
        elif name == "open" and "encoding" not in kw:
            mode = next((a.value for a in node.args[1:2] if isinstance(a, ast.Constant)), None)
            mode = mode or next((k.value.value for k in node.keywords if k.arg == "mode" and isinstance(k.value, ast.Constant)), "r")
            if "b" not in str(mode) and isinstance(f, ast.Name):     # 내장 open(...) 텍스트 모드만
                found.append((node.lineno, "open"))
    return found


def test_서버_설치_코드는_파일을_UTF8로_명시해_읽고_쓴다():
    """Windows 설치 경로가 실행하는 파이썬 — 인코딩을 로케일에 맡기지 않는다."""
    files = [ROOT / "deploy" / "server_setup.py"]
    files += [p for p in (ROOT / "dashboard" / "backend" / "app").rglob("*.py") if "__pycache__" not in p.parts]
    offenders = {p.relative_to(ROOT).as_posix(): hits for p in files if (hits := _text_io_without_encoding(p))}
    assert not offenders, offenders


def test_bat은_성공해도_끝에서_멈춘다():
    """이미 관리자 콘솔이면 ps1이 새 창을 열지 않아, 멈추지 않으면 설치가 끝나는 순간 창이 닫혀 접속 주소와 관리자
    비밀번호 요약을 읽을 수 없다(성공했는데 '바로 종료된다'는 보고)."""
    text = BAT.read_bytes().decode("ascii")
    lines = [l.strip() for l in text.splitlines()]
    pause = next(i for i, l in enumerate(lines) if l.startswith("if /i not") and "pause" in l)
    assert 'VG_NO_PAUSE' in lines[pause]                          # 자동화에서는 끌 수 있다
    exit_line = next(i for i, l in enumerate(lines) if l.startswith("endlocal"))
    assert pause < exit_line                                      # 종료 직전에 멈춘다
    assert not any(l.startswith("if not") and "pause" in l and "ERROR" not in l for l in lines)   # 실패일 때만 멈추던 옛 구조가 아니다
