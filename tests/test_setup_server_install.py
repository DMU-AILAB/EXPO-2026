"""setup-server.ps1의 도구 설치 로직 — winget 우선, 없거나 실패하면 공식 설치 파일을 직접 받는다.

실제 PowerShell에서 **ps1의 함수 정의만 AST로 꺼내** mock과 함께 실행한다(스크립트 전체는 관리자 권한·winget·방화벽을 건드려
돌릴 수 없다). 지키는 것:
- winget이 성공하면 직접 설치를 하지 않는다. 실패하거나 없거나 `-NoWinget`이면 직접 설치로 넘어간다
- 받은 파일은 **실행하기 전에** 디지털 서명(유효성 + 게시자)과 체크섬으로 검증하고, 틀리면 실행하지 않고 멈춘다
- Node.js LTS·Git 설치 파일을 올바른 변형으로 고른다
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PS1 = ROOT / "deploy" / "setup-server.ps1"
POWERSHELL = shutil.which("powershell.exe")

pytestmark = pytest.mark.skipif(POWERSHELL is None, reason="powershell.exe가 없다(Windows/WSL 밖)")


def _win(path: Path) -> str:
    out = subprocess.run(["wslpath", "-w", str(path)], capture_output=True, text=True).stdout.strip()
    return out or str(path)


def _run(tmp_path: Path, functions: list[str], body: str, timeout: int = 120) -> str:
    """ps1에서 `functions`만 꺼내 정의하고 `body`를 실행한다. 출력(stdout)을 돌려준다."""
    names = ",".join(f"'{n}'" for n in functions)
    script = f"""$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$NoWinget = $false; $DryRun = $false
$script:log = New-Object System.Collections.ArrayList
function Write-Info {{ param($m) [void]$script:log.Add("INFO:$m") }}
function Write-Warn {{ param($m) [void]$script:log.Add("WARN") }}
function Write-Ok   {{ param($m) [void]$script:log.Add("OK") }}
function Fail       {{ param($m) throw "FAIL" }}
$ast = [System.Management.Automation.Language.Parser]::ParseFile('{_win(PS1)}', [ref]$null, [ref]$null)
foreach ($n in @({names})) {{
    $f = $ast.Find({{ param($x) $x -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $x.Name -eq $n }}, $true)
    if (-not $f) {{ throw "함수 없음: $n" }}
    Invoke-Expression $f.Extent.Text
}}
{body}
"""
    f = tmp_path / "t.ps1"
    f.write_text("﻿" + script, encoding="utf-8")
    res = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", _win(f)],
                         capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    return res.stdout + res.stderr


# --- Install-Tool: 어떤 경로를 타는가 --------------------------------------------------------

_INSTALL_TOOL = ["Act", "Install-Tool"]

_MOCKS = r"""
$script:directCount = 0
$script:wingetCount = 0
function Update-SessionPath { }
$directBlock = { $script:directCount++ }
"""


def _tool(tmp_path, *, winget: str, no_winget=False, dry_run=False):
    """winget: 'ok'(종료 코드 0) | 'fail'(1) | 'absent'(명령 없음)."""
    wg = {"ok": "function winget.exe { $script:wingetCount++; $global:LASTEXITCODE = 0 }",
          "fail": "function winget.exe { $script:wingetCount++; $global:LASTEXITCODE = 1 }",
          "absent": ""}[winget]
    body = f"""{_MOCKS}
{wg}
$NoWinget = ${str(no_winget).lower()}; $DryRun = ${str(dry_run).lower()}
Install-Tool 'X.Y' 'Tool' $directBlock
"RESULT winget=$($script:wingetCount) direct=$($script:directCount) warn=$(@($script:log | Where-Object {{ $_ -eq 'WARN' }}).Count)"
"""
    out = _run(tmp_path, _INSTALL_TOOL, body)
    m = re.search(r"RESULT winget=(\d+) direct=(\d+) warn=(\d+)", out)
    assert m, out
    return tuple(int(x) for x in m.groups())


def test_winget이_성공하면_직접_설치하지_않는다(tmp_path):
    assert _tool(tmp_path, winget="ok") == (1, 0, 0)


def test_winget이_실패하면_직접_설치로_넘어가고_알린다(tmp_path):
    assert _tool(tmp_path, winget="fail") == (1, 1, 1)


def test_winget이_없으면_직접_설치한다(tmp_path):
    winget, direct, warn = _tool(tmp_path, winget="absent")
    assert (winget, direct) == (0, 1) and warn == 1


def test_NoWinget이면_winget이_있어도_쓰지_않는다(tmp_path):
    winget, direct, _ = _tool(tmp_path, winget="ok", no_winget=True)
    assert (winget, direct) == (0, 1)


def test_DryRun은_winget이_있으면_아무것도_실행하지_않는다(tmp_path):
    assert _tool(tmp_path, winget="ok", dry_run=True)[:2] == (0, 0)


# --- 서명 검증 ---------------------------------------------------------------------------

def _sign_body(tmp_path, publisher: str, target: str) -> str:
    return f"""
$r = try {{ Assert-Signed {target} '{publisher}'; 'PASSED' }} catch {{ 'REJECTED' }}
"RESULT $r"
"""


def test_유효한_서명과_맞는_게시자는_통과한다(tmp_path):
    out = _run(tmp_path, ["Assert-Signed"], _sign_body(tmp_path, "Microsoft", "(Join-Path $PSHOME 'powershell.exe')"))
    assert "RESULT PASSED" in out, out


def test_게시자가_다르면_실행하지_않는다(tmp_path):
    out = _run(tmp_path, ["Assert-Signed"], _sign_body(tmp_path, "Python Software Foundation", "(Join-Path $PSHOME 'powershell.exe')"))
    assert "RESULT REJECTED" in out, out


def test_서명이_없는_파일은_거부한다(tmp_path):
    f = tmp_path / "unsigned.exe"
    f.write_bytes(b"MZ not really signed")
    out = _run(tmp_path, ["Assert-Signed"], _sign_body(tmp_path, "Microsoft", f"'{_win(f)}'"))
    assert "RESULT REJECTED" in out, out


def test_서명이_깨진_파일도_거부한다(tmp_path):
    """서명된 바이너리에 한 바이트라도 덧붙이면 해시가 어긋나 서명이 무효가 된다 — 변조 탐지."""
    src = Path("/mnt/c/Windows/System32/notepad.exe")
    if not src.exists():
        pytest.skip("notepad.exe가 없다")
    f = tmp_path / "tampered.exe"
    f.write_bytes(src.read_bytes() + b"\x00" * 16)
    out = _run(tmp_path, ["Assert-Signed"], _sign_body(tmp_path, "Microsoft", f"'{_win(f)}'"))
    assert "RESULT REJECTED" in out, out


def _mock_signature(tmp_path, status: str, subject: str, publisher: str = "Python Software Foundation") -> str:
    """`Get-AuthenticodeSignature`를 mock해 **서명 상태와 게시자를 따로** 시험한다."""
    f = tmp_path / "any.exe"
    f.write_bytes(b"x")
    body = f"""
function Get-AuthenticodeSignature {{ param($FilePath) [pscustomobject]@{{ Status = '{status}'; SignerCertificate = [pscustomobject]@{{ Subject = '{subject}' }} }} }}
$r = try {{ Assert-Signed '{_win(f)}' '{publisher}'; 'PASSED' }} catch {{ 'REJECTED' }}
"RESULT $r"
"""
    return _run(tmp_path, ["Assert-Signed"], body)


def test_게시자가_맞아도_서명이_깨졌으면_거부한다(tmp_path):
    """변조된 설치 파일 — 인증서(게시자)는 그대로 남아 있지만 서명 검증은 실패한다. 게시자만 보면 통과해 버린다."""
    for status in ("HashMismatch", "NotSigned", "UnknownError", "NotTrusted", "Incompatible"):
        out = _mock_signature(tmp_path, status, "CN=Python Software Foundation, O=Python Software Foundation, C=US")
        assert "RESULT REJECTED" in out, (status, out)


def test_서명이_유효하고_게시자가_맞아야_통과한다(tmp_path):
    out = _mock_signature(tmp_path, "Valid", "CN=Python Software Foundation, O=Python Software Foundation, C=US")
    assert "RESULT PASSED" in out, out


def test_서명이_유효해도_게시자가_다르면_거부한다(tmp_path):
    """다른 회사가 서명한 정상 파일 — 공식 배포처의 파일이 아니다."""
    out = _mock_signature(tmp_path, "Valid", "CN=Some Other Vendor, O=Some Other Vendor, C=US")
    assert "RESULT REJECTED" in out, out


def test_게시자_이름의_일부만_같아서는_통과하지_않는다(tmp_path):
    out = _mock_signature(tmp_path, "Valid", "CN=Totally Unrelated Corp", publisher="Python Software Foundation")
    assert "RESULT REJECTED" in out, out


# --- 체크섬 ---------------------------------------------------------------------------------

_HASH_A = "a" * 64
_HASH_B = "B" * 64


def test_SHASUMS에서_파일명으로_해시를_찾는다(tmp_path):
    sums = tmp_path / "SHASUMS256.txt"
    sums.write_text(f"{_HASH_A}  node-v1.0.0-x64.msi\n{_HASH_B}  node-v1.0.0-arm64.msi\n{'c' * 64}  node-v1.0.0-x64.msi.sig\n",
                    encoding="utf-8")
    body = f"""
"X64=$(Get-ExpectedSha256 '{_win(sums)}' 'node-v1.0.0-x64.msi')"
"ARM=$(Get-ExpectedSha256 '{_win(sums)}' 'node-v1.0.0-arm64.msi')"
"NONE=[$(Get-ExpectedSha256 '{_win(sums)}' 'node-v9-x64.msi')]"
"""
    out = _run(tmp_path, ["Get-ExpectedSha256"], body)
    assert f"X64={_HASH_A.upper()}" in out and f"ARM={_HASH_B.upper()}" in out
    assert "NONE=[]" in out                                             # 없는 파일은 해시가 없다
    assert "C" * 64 not in out                                          # `.sig`가 붙은 다른 파일과 섞이지 않는다


def test_체크섬이_맞으면_통과하고_다르면_멈춘다(tmp_path):
    f = tmp_path / "file.bin"
    f.write_bytes(b"hello")
    import hashlib
    good = hashlib.sha256(b"hello").hexdigest()
    body = f"""
$ok = try {{ Assert-Sha256 '{_win(f)}' '{good}'; 'PASSED' }} catch {{ 'REJECTED' }}
$bad = try {{ Assert-Sha256 '{_win(f)}' '{"0" * 64}'; 'PASSED' }} catch {{ 'REJECTED' }}
$lower = try {{ Assert-Sha256 '{_win(f)}' '{good.lower()}'; 'PASSED' }} catch {{ 'REJECTED' }}
"GOOD=$ok BAD=$bad LOWER=$lower"
"""
    out = _run(tmp_path, ["Assert-Sha256"], body)
    assert "GOOD=PASSED BAD=REJECTED LOWER=PASSED" in out, out


# --- 설치 파일 선택 -------------------------------------------------------------------------

def test_Node는_LTS이면서_해당_구조의_MSI가_있는_첫_릴리스를_고른다(tmp_path):
    body = r"""
$index = @(
  [pscustomobject]@{ version='v25.0.0'; lts=$false;   files=@('win-x64-msi') },
  [pscustomobject]@{ version='v24.9.0'; lts='Krypton'; files=@('win-x64-zip') },
  [pscustomobject]@{ version='v24.8.0'; lts='Krypton'; files=@('win-x64-msi','win-arm64-msi') },
  [pscustomobject]@{ version='v22.1.0'; lts='Jod';     files=@('win-x64-msi') }
)
"X64=$((Select-NodeLts $index 'x64').version)"
"ARM=$((Select-NodeLts $index 'arm64').version)"
"NONE=[$((Select-NodeLts $index 'riscv').version)]"
"""
    out = _run(tmp_path, ["Select-NodeLts"], body)
    # 최신이어도 LTS가 아니면(v25) 건너뛰고, LTS여도 MSI가 없는 릴리스(v24.9.0)는 건너뛴다
    assert "X64=v24.8.0" in out and "ARM=v24.8.0" in out and "NONE=[]" in out, out


def test_Git은_설치용_exe만_고른다(tmp_path):
    body = r"""
$rel = [pscustomobject]@{ assets = @(
  [pscustomobject]@{ name='MinGit-2.50.0-64-bit.zip' },
  [pscustomobject]@{ name='PortableGit-2.50.0-64-bit.7z.exe' },
  [pscustomobject]@{ name='Git-2.50.0-64-bit.tar.bz2' },
  [pscustomobject]@{ name='Git-2.50.0-arm64.exe' },
  [pscustomobject]@{ name='Git-2.50.0-64-bit.exe' }
) }
"X64=$((Select-GitAsset $rel 'x64').name)"
"ARM=$((Select-GitAsset $rel 'arm64').name)"
"""
    out = _run(tmp_path, ["Select-GitAsset"], body)
    assert "X64=Git-2.50.0-64-bit.exe" in out and "ARM=Git-2.50.0-arm64.exe" in out, out


# --- 정적 점검 ------------------------------------------------------------------------------

TEXT = PS1.read_text(encoding="utf-8-sig")


def _download_section() -> str:
    """설치 파일을 받는 함수들(`Get-Arch` ~ `Install-Tool` 앞) — 요약 화면의 로컬 서버 주소 등은 제외한다."""
    return TEXT[TEXT.index("function Get-Arch"):TEXT.index("function Install-Tool")]


def test_다운로드는_HTTPS만_쓰고_인증서_검사를_끄지_않는다():
    urls = re.findall(r"https?://[^\s'\"`)]+", _download_section())
    assert len(urls) >= 5, urls                                   # Python·Node(2)·Git(API)·... 를 실제로 찾았다
    assert all(u.startswith("https://") for u in urls), [u for u in urls if not u.startswith("https://")]
    assert "SkipCertificateCheck" not in TEXT and "ServerCertificateValidationCallback" not in TEXT
    # 받는 곳은 공식 배포처뿐이다
    assert {re.match(r"https://([^/]+)", u).group(1) for u in urls} <= {"www.python.org", "nodejs.org", "api.github.com"}


def test_받은_파일은_실행하기_전에_검증한다():
    """각 Install-*Direct에서 Assert-Signed가 Invoke-Installer보다 먼저 와야 한다."""
    for name in ("Install-PythonDirect", "Install-NodeDirect", "Install-GitDirect"):
        body = TEXT[TEXT.index(f"function {name}"):]
        body = body[:body.index("\nfunction ")] if "\nfunction " in body else body
        assert body.index("Assert-Signed") < body.index("Invoke-Installer"), name


def test_TLS12를_명시한다():
    # Windows PowerShell 5.1의 기본은 TLS 1.0이라 python.org·nodejs.org에 붙지 못한다
    assert "Tls12" in TEXT


def test_설치_프로그램_종료_코드_3010은_성공으로_본다():
    assert "-notin 0, 3010" in TEXT                       # 3010 = 재부팅 필요(설치는 성공)


def test_VerifyDownloads는_설치하지_않는다():
    block = TEXT[TEXT.index("if ($VerifyDownloads)"):]
    block = block[:block.index("Finish 0")]
    assert "-VerifyOnly" in block and "Invoke-Installer" not in block
    assert block.count("-VerifyOnly") == 3                # Python·Node·Git 모두
