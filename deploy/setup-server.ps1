<#
.SYNOPSIS
    VisionGuide 대시보드 서버 한 줄 설치 (Windows 네이티브)

.DESCRIPTION
    새 PC에서 이 스크립트 하나로 서버를 세운다:
      Git/Python/Node 설치(winget) -> venv·의존성 -> .env·DB -> 프런트 빌드 ->
      방화벽 -> 로그온 시 자동 시작(작업 스케줄러) -> 시작 -> 접속 주소 안내

    서버는 **WSL이 아니라 Windows 네이티브**로 돌린다. WSL(NAT/미러)에서는 Pi가 서버에 닿지 못하거나
    Hyper-V 방화벽이 막는다(docs/dashboard-pc-setup.md). 개발(코드 편집·테스트)은 WSL에서 해도 된다.

    판단 로직(.env 채우기·관리자 비밀번호)은 deploy/server_setup.py에 있다 — 이 스크립트는 OS 일만 한다.
    모든 단계는 멱등이다. 다시 실행해도 기존 .env 값과 DB는 그대로다.

.PARAMETER Port          서버 포트 (기본 8000)
.PARAMETER NoAutostart   로그온 시 자동 시작 작업을 만들지 않는다
.PARAMETER NoFirewall    방화벽 규칙을 건드리지 않는다
.PARAMETER Rebuild       프런트(dist)가 있어도 다시 빌드한다
.PARAMETER ResetEnv      기존 .env를 새로 만든다 (JWT 키가 바뀌어 로그인이 풀린다)
.PARAMETER Uninstall     자동 시작 작업과 방화벽 규칙을 제거한다 (파일·DB는 지우지 않는다)
.PARAMETER DryRun        아무것도 바꾸지 않고 할 일만 출력한다
.PARAMETER NoWinget      winget을 쓰지 않고 공식 설치 파일을 직접 받아 설치한다 (winget이 없거나 고장 난 PC)
.PARAMETER VerifyDownloads  Python·Node.js·Git 공식 설치 파일을 받아 서명·체크섬만 검증하고 설치하지 않는다

.EXAMPLE
    .\setup-server.ps1
    .\setup-server.ps1 -Port 8080 -DryRun
    .\setup-server.ps1 -Uninstall
#>

[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,
    [switch]$NoAutostart,
    [switch]$NoFirewall,
    [switch]$Rebuild,
    [switch]$ResetEnv,
    [switch]$Uninstall,
    [switch]$DryRun,
    [switch]$NoWinget,
    [switch]$VerifyDownloads,
    # 저장소 밖에서 실행할 때(원격 한 줄) 받아올 곳과 위치
    [string]$RepoUrl = 'https://github.com/DMU-AILAB/EXPO-2026.git',
    [string]$InstallDir = (Join-Path $env:USERPROFILE 'VisionGuide'),
    # 내부용 — 관리자 권한으로 다시 띄운 창이 끝나도 요약을 읽을 수 있게 멈춘다
    [switch]$Elevated
)

$ErrorActionPreference = 'Stop'

# 이 스크립트가 띄우는 모든 파이썬(pip·venv·server_setup·init_db)을 UTF-8 모드로 돌린다. Windows의 기본 인코딩은
# 로케일(한국어 Windows는 cp949)이라, UTF-8 한글이 든 파일을 인코딩 지정 없이 읽는 곳에서
# "'cp949' codec can't decode byte 0xeb"로 죽는다(pip install -r requirements.txt에서 실제로 겪음).
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$TaskName         = 'VisionGuide Dashboard'
$FirewallHttp     = 'VisionGuide Dashboard'
$FirewallDiscover = 'VisionGuide Discovery'
$DiscoveryPort    = 48555      # backend/app/config.py discovery_port와 같아야 한다

# ── 출력 ────────────────────────────────────────────────────────────
function Write-Step { param($m) Write-Host "`n[*] $m" -ForegroundColor Cyan }
function Write-Ok   { param($m) Write-Host "[OK] $m" -ForegroundColor Green }
function Write-Info { param($m) Write-Host "    $m" -ForegroundColor Gray }
function Write-Warn { param($m) Write-Host "[!] $m" -ForegroundColor Yellow }
function Fail       { param($m) Write-Host "`n[ERROR] $m" -ForegroundColor Red; Finish 1 }

function Finish {
    param([int]$Code = 0)
    if ($script:DownloadDir -and (Test-Path $script:DownloadDir)) { Remove-Item -Recurse -Force $script:DownloadDir -ErrorAction SilentlyContinue }
    if ($Elevated) { Read-Host "`nEnter를 누르면 이 창을 닫습니다" | Out-Null }
    exit $Code
}

# ('Do'는 PowerShell 예약어라 함수 이름으로 못 쓴다)
# 시스템을 바꾸는 일은 전부 여기를 거친다 — -DryRun이 한 곳에서 막는다.
function Act {
    param([string]$Desc, [scriptblock]$Block)
    if ($DryRun) { Write-Host "    [dry-run] $Desc" -ForegroundColor DarkYellow; return }
    Write-Info $Desc
    & $Block
}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return ([Security.Principal.WindowsPrincipal]$id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Update-SessionPath {
    # winget이 PATH를 바꿔도 이미 떠 있는 세션은 모른다 — 레지스트리에서 다시 읽는다.
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'User')
}

# ── 설치: winget 우선, 없거나 실패하면 공식 설치 파일을 직접 받는다 ──────────────────────────
# 받은 파일은 **실행하기 전에 검증한다.** 해시를 코드에 박아 두지 않고(버전이 바뀌면 낡는다) 디지털 서명(Authenticode)의
# 유효성과 게시자를 확인하고, Node.js는 공식 SHASUMS256.txt와도 대조한다. 검증에 실패하면 실행하지 않고 멈춘다.
# 모두 HTTPS이고 인증서 검사를 끄지 않는다.
$script:DownloadDir = $null
$script:ToolInstalled = $false

function Get-Arch {
    $a = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    switch ($a) { 'AMD64' { return 'x64' } 'ARM64' { return 'arm64' } default { Fail "지원하지 않는 CPU 구조입니다: $a (x64·ARM64만 지원)" } }
}

function Get-DownloadDir {
    if (-not $script:DownloadDir) {
        $script:DownloadDir = Join-Path $env:TEMP ('vg-setup-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $script:DownloadDir | Out-Null
    }
    return $script:DownloadDir
}

function Save-Url {
    param([string]$Url, [string]$Name)
    # Windows PowerShell 5.1은 기본이 TLS 1.0이라 python.org·nodejs.org에 못 붙는다.
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    $dest = Join-Path (Get-DownloadDir) $Name
    $prev = $ProgressPreference
    $ProgressPreference = 'SilentlyContinue'                 # 5.1의 진행률 표시는 다운로드를 수십 배 느리게 한다
    try { Invoke-WebRequest -Uri $Url -OutFile $dest -UseBasicParsing }
    catch { Fail "다운로드에 실패했습니다: $Url ($($_.Exception.Message))" }
    finally { $ProgressPreference = $prev }
    return $dest
}

function Assert-Signed {
    param([string]$Path, [string]$Publisher)
    $sig = Get-AuthenticodeSignature -FilePath $Path
    if ($sig.Status -ne 'Valid') { Fail "설치 파일의 서명이 유효하지 않습니다 ($($sig.Status)): $Path — 실행하지 않았습니다" }
    $subject = $sig.SignerCertificate.Subject
    if ($subject -notmatch $Publisher) { Fail "설치 파일의 게시자가 예상과 다릅니다: $subject (예상: $Publisher) — 실행하지 않았습니다" }
    Write-Info "서명 확인: $(($subject -split ',')[0])"
}

function Get-ExpectedSha256 {
    # SHASUMS256.txt("<해시>  <파일명>" 줄들)에서 `Name`의 해시. 없으면 $null.
    param([string]$SumsPath, [string]$Name)
    $line = Get-Content $SumsPath | Where-Object { $_ -match ('^[0-9a-fA-F]{64}\s+\*?' + [regex]::Escape($Name) + '$') } | Select-Object -First 1
    if (-not $line) { return $null }
    return (($line -split '\s+')[0]).ToUpperInvariant()
}

function Assert-Sha256 {
    param([string]$Path, [string]$Expected)
    $actual = (Get-FileHash -Path $Path -Algorithm SHA256).Hash.ToUpperInvariant()
    if ($actual -ne $Expected.ToUpperInvariant()) { Fail "설치 파일의 SHA-256이 공식 값과 다릅니다 — 실행하지 않았습니다 (받은 값 $($actual.Substring(0, 16))..., 공식 값 $($Expected.Substring(0, 16))...)" }
    Write-Info "SHA-256 확인: $($actual.Substring(0, 16))..."
}

function Select-NodeLts {
    # 공식 index.json(최신순)에서 LTS이면서 해당 구조의 MSI가 있는 첫 릴리스.
    param($Index, [string]$Arch)
    return $Index | Where-Object { $_.lts -and ($_.files -contains "win-$Arch-msi") } | Select-Object -First 1
}

function Select-GitAsset {
    # Git for Windows 릴리스의 설치 파일(.exe). 포터블·MinGit·busybox 변형은 고르지 않는다.
    param($Release, [string]$Arch)
    $pattern = if ($Arch -eq 'arm64') { '^Git-[\d.]+-arm64\.exe$' } else { '^Git-[\d.]+-64-bit\.exe$' }
    return $Release.assets | Where-Object { $_.name -match $pattern } | Select-Object -First 1
}

function Invoke-Installer {
    param([string]$File, [string[]]$Arguments, [string]$Name)
    $p = if ($File -like '*.msi') {
        Start-Process msiexec.exe -ArgumentList (@('/i', "`"$File`"") + $Arguments) -Wait -PassThru
    } else {
        Start-Process $File -ArgumentList $Arguments -Wait -PassThru
    }
    if ($p.ExitCode -notin 0, 3010) { Fail "$Name 설치 프로그램이 실패했습니다 (종료 코드 $($p.ExitCode))" }   # 3010 = 재부팅 필요(설치는 성공)
    $script:ToolInstalled = $true
}

# Python: 3.11.9가 3.11의 마지막 바이너리 설치 파일이다. 3.10~3.12면 이 프로젝트가 인정한다.
function Install-PythonDirect {
    param([switch]$VerifyOnly)
    $ver = '3.11.9'
    $arch = if ((Get-Arch) -eq 'arm64') { 'arm64' } else { 'amd64' }
    $url = "https://www.python.org/ftp/python/$ver/python-$ver-$arch.exe"
    Act "Python $ver 공식 설치 파일 받기·검증$(if (-not $VerifyOnly) { '·설치' }): $url" {
        $f = Save-Url $url "python-$ver-$arch.exe"
        Assert-Signed $f 'Python Software Foundation'
        if ($VerifyOnly) { Write-Ok "Python 설치 파일 검증 완료 (설치하지 않음)"; return }
        Invoke-Installer $f @('/quiet', 'InstallAllUsers=1', 'PrependPath=1', 'Include_launcher=1', 'Include_test=0', 'Include_doc=0') 'Python'
    }
}

# Node.js: 최신 LTS를 공식 index.json에서 찾고, SHASUMS256.txt의 해시와 서명을 모두 확인한다.
function Install-NodeDirect {
    param([switch]$VerifyOnly)
    $arch = Get-Arch
    Act "Node.js LTS 공식 설치 파일 받기·검증$(if (-not $VerifyOnly) { '·설치' }): https://nodejs.org/dist/index.json" {
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        try { $index = Invoke-RestMethod -Uri 'https://nodejs.org/dist/index.json' -UseBasicParsing }
        catch { Fail "Node.js 버전 목록을 받지 못했습니다: $($_.Exception.Message)" }
        $rel = Select-NodeLts $index $arch
        if (-not $rel) { Fail "Node.js LTS의 Windows $arch MSI를 찾지 못했습니다" }
        $ver = $rel.version
        $name = "node-$ver-$arch.msi"
        Write-Info "Node.js LTS $ver ($($rel.lts))"
        $f = Save-Url "https://nodejs.org/dist/$ver/$name" $name
        $sums = Save-Url "https://nodejs.org/dist/$ver/SHASUMS256.txt" 'SHASUMS256.txt'
        $expected = Get-ExpectedSha256 $sums $name
        if (-not $expected) { Fail "SHASUMS256.txt에 $name 항목이 없습니다" }
        Assert-Sha256 $f $expected
        Assert-Signed $f 'OpenJS Foundation|Node\.js Foundation'
        if ($VerifyOnly) { Write-Ok "Node.js 설치 파일 검증 완료 (설치하지 않음)"; return }
        Invoke-Installer $f @('/qn', '/norestart') 'Node.js'
    }
}

# Git: Git for Windows의 최신 릴리스를 GitHub API에서 찾는다.
function Install-GitDirect {
    param([switch]$VerifyOnly)
    $arch = Get-Arch
    Act "Git for Windows 공식 설치 파일 받기·검증$(if (-not $VerifyOnly) { '·설치' }): github.com/git-for-windows/git" {
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        try { $rel = Invoke-RestMethod -Uri 'https://api.github.com/repos/git-for-windows/git/releases/latest' -UseBasicParsing -Headers @{ 'User-Agent' = 'VisionGuide-setup' } }
        catch { Fail "Git for Windows 릴리스를 확인하지 못했습니다: $($_.Exception.Message)" }
        $asset = Select-GitAsset $rel $arch
        if (-not $asset) { Fail "Git for Windows $arch 설치 파일을 찾지 못했습니다" }
        Write-Info "Git for Windows $($rel.tag_name)"
        $f = Save-Url $asset.browser_download_url $asset.name
        Assert-Signed $f 'Johannes Schindelin|Git for Windows'
        if ($VerifyOnly) { Write-Ok "Git 설치 파일 검증 완료 (설치하지 않음)"; return }
        Invoke-Installer $f @('/VERYSILENT', '/NORESTART', '/NOCANCEL', '/SP-') 'Git'
    }
}

function Install-Tool {
    param([string]$WingetId, [string]$Name, [scriptblock]$Direct)
    $useWinget = (-not $NoWinget) -and [bool](Get-Command winget.exe -ErrorAction SilentlyContinue)
    $script:ToolInstalled = $false
    if ($useWinget) {
        Act "winget install $WingetId" {
            & winget.exe install --id $WingetId -e --silent --accept-package-agreements --accept-source-agreements
            if ($LASTEXITCODE -eq 0) { $script:ToolInstalled = $true }
        }
        if ($DryRun) { return }
        if (-not $script:ToolInstalled) { Write-Warn "winget으로 $Name 을(를) 설치하지 못했습니다 — 공식 설치 파일을 직접 받아 설치합니다" }
    } else {
        Write-Warn "$(if ($NoWinget) { '-NoWinget' } else { 'winget이 없어' }) $Name 은(는) 공식 설치 파일을 직접 받아 설치합니다"
    }
    if (-not $script:ToolInstalled) { & $Direct }
    if (-not $DryRun) { Update-SessionPath }
}

# 설치 없이 다운로드·서명·체크섬만 검증한다 — 새 PC에서 직접 설치 경로가 동작할지 미리 본다(관리자 권한 불필요).
if ($VerifyDownloads) {
    Write-Step '공식 설치 파일 검증 (설치하지 않습니다)'
    Install-PythonDirect -VerifyOnly
    Install-NodeDirect -VerifyOnly
    Install-GitDirect -VerifyOnly
    Finish 0
}

# ── 1. 권한 ─────────────────────────────────────────────────────────
if (-not $DryRun -and -not (Test-Admin)) {
    # 방화벽·작업 스케줄러에 관리자 권한이 필요하다 — UAC 한 번으로 스스로 다시 띄운다.
    if (-not $PSCommandPath) {
        Fail "관리자 PowerShell에서 실행하세요 (원격 한 줄로 실행한 경우 자동 승격할 수 없습니다)."
    }
    Write-Info '관리자 권한이 필요합니다 — 권한 확인 창이 뜨면 허용하세요.'
    $fwd = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"", '-Elevated')
    foreach ($key in $PSBoundParameters.Keys) {
        $val = $PSBoundParameters[$key]
        if ($val -is [switch]) { if ($val) { $fwd += "-$key" } }
        else { $fwd += "-$key"; $fwd += "`"$val`"" }
    }
    $p = Start-Process powershell.exe -Verb RunAs -ArgumentList $fwd -Wait -PassThru
    exit $p.ExitCode
}

# ── 제거 ────────────────────────────────────────────────────────────
if ($Uninstall) {
    Write-Step '자동 시작 작업과 방화벽 규칙을 제거합니다 (파일·DB는 그대로 둡니다)'
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Act "작업 중지·제거: $TaskName" {
            Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        }
    } else { Write-Info "작업 없음: $TaskName" }
    foreach ($rule in @($FirewallHttp, $FirewallDiscover)) {
        if (Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue) {
            Act "방화벽 규칙 제거: $rule" { Remove-NetFirewallRule -DisplayName $rule }
        } else { Write-Info "규칙 없음: $rule" }
    }
    Write-Ok '제거 완료. 실행 중인 서버 프로세스가 남아 있으면 작업 관리자에서 pythonw.exe를 종료하세요.'
    Finish 0
}

# ── 2. 저장소 위치 ───────────────────────────────────────────────────
Write-Step '저장소 확인'
$Repo = $null
if ($PSScriptRoot) {
    $candidate = Split-Path $PSScriptRoot -Parent
    if (Test-Path (Join-Path $candidate 'dashboard\backend\app\main.py')) { $Repo = $candidate }
}
if (-not $Repo) {
    if (-not (Get-Command git.exe -ErrorAction SilentlyContinue)) { Install-Tool 'Git.Git' 'Git' { Install-GitDirect } }
    if (Test-Path (Join-Path $InstallDir 'dashboard\backend\app\main.py')) {
        $Repo = $InstallDir
        Act "git pull ($InstallDir)" { & git.exe -C $InstallDir pull --ff-only }
    } else {
        Act "git clone $RepoUrl -> $InstallDir" {
            & git.exe clone $RepoUrl $InstallDir
            if ($LASTEXITCODE -ne 0) { Fail "저장소를 받지 못했습니다. 저장소가 비공개이면 직접 clone한 뒤 그 안의 setup-server.bat을 실행하세요." }
        }
        $Repo = $InstallDir
    }
}
Write-Info "저장소: $Repo"
$Backend  = Join-Path $Repo 'dashboard\backend'
$Frontend = Join-Path $Repo 'dashboard\frontend'
$SetupPy  = Join-Path $Repo 'deploy\server_setup.py'

# ── 3. Python · Node ────────────────────────────────────────────────
function Find-Python {
    # 'python' 명령은 쓰지 않는다 — Microsoft Store 스텁이 아무것도 하지 않고 성공처럼 보일 수 있다.
    $found = $null
    if (Get-Command py.exe -ErrorAction SilentlyContinue) {
        # ★ Stop 상태에서 네이티브 명령의 stderr를 2>$null로 돌리면 Windows PowerShell 5.1이 그것을
        # 종료 오류(NativeCommandError)로 취급한다 — py.exe는 없는 버전을 stderr로 알리므로 여기서 멈춘다.
        $prev = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            foreach ($v in '3.11', '3.12', '3.10') {
                $exe = & py.exe "-$v" -c 'import sys;print(sys.executable)' 2>$null
                if ($LASTEXITCODE -eq 0 -and $exe -and (Test-Path $exe)) { return $exe }
            }
        } finally { $ErrorActionPreference = $prev }
    }
    foreach ($dir in 'Python311', 'Python312', 'Python310') {
        foreach ($base in (Join-Path $env:LOCALAPPDATA 'Programs\Python'), $env:ProgramFiles) {
            $exe = Join-Path (Join-Path $base $dir) 'python.exe'
            if (Test-Path $exe) { return $exe }
        }
    }
    return $found
}

Write-Step 'Python 확인'
$Python = Find-Python
if (-not $Python) {
    Install-Tool 'Python.Python.3.11' 'Python 3.11' { Install-PythonDirect }
    $Python = Find-Python
    if (-not $Python -and -not $DryRun) { Fail 'Python 3.10~3.12를 찾지 못했습니다. 설치 후 PowerShell을 새로 열고 다시 실행하세요.' }
}
Write-Info "Python: $(if ($Python) { $Python } else { '(설치 예정)' })"

$NpmCmd = $null
$distIndex = Join-Path $Frontend 'dist\index.html'
$needBuild = $Rebuild -or -not (Test-Path $distIndex)
if ($needBuild) {
    Write-Step 'Node.js 확인'
    $NpmCmd = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
    if (-not $NpmCmd) {
        Install-Tool 'OpenJS.NodeJS.LTS' 'Node.js LTS' { Install-NodeDirect }
        $NpmCmd = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
        if (-not $NpmCmd) { $NpmCmd = Join-Path $env:ProgramFiles 'nodejs\npm.cmd' }
    }
    Write-Info "npm: $NpmCmd"
}

# ── 4. venv · 의존성 · .env · DB ─────────────────────────────────────
Write-Step '백엔드 환경 (venv · 의존성)'
$Venv   = Join-Path $Backend '.venv'
$VenvPy = Join-Path $Venv 'Scripts\python.exe'
$VenvPyW = Join-Path $Venv 'Scripts\pythonw.exe'
$RunServer = Join-Path $Backend 'run_server.py'
$LogFile = Join-Path $Backend 'data\logs\server.log'
if (-not (Test-Path $VenvPy)) { Act "venv 생성: $Venv" { & $Python -m venv $Venv; if ($LASTEXITCODE -ne 0) { Fail 'venv를 만들지 못했습니다' } } }
else { Write-Info "venv 있음: $Venv" }
Act 'pip install -r requirements.txt' {
    & $VenvPy -m pip install --disable-pip-version-check -q -r (Join-Path $Backend 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { Fail '백엔드 의존성을 설치하지 못했습니다' }
}

Write-Step '.env · 관리자 계정 · DB'
$EnvResult = $null
$envArgs = @($SetupPy, 'env', '--backend', $Backend, '--port', "$Port")
if ($ResetEnv) { $envArgs += '--reset' }
if ($DryRun) { Write-Info "[dry-run] python server_setup.py env --backend ... --port $Port$(if ($ResetEnv) { ' --reset' })" }
else {
    $json = & $VenvPy @envArgs | Out-String
    if ($LASTEXITCODE -ne 0) { Fail '.env를 만들지 못했습니다' }
    $EnvResult = $json | ConvertFrom-Json
    Write-Info ".env: $($EnvResult.status)"
}
Act 'python -m app.db.init_db' {
    Push-Location $Backend
    try { & $VenvPy -m app.db.init_db; if ($LASTEXITCODE -ne 0) { Fail 'DB를 초기화하지 못했습니다' } }
    finally { Pop-Location }
}

# ── 5. 프런트 빌드 ──────────────────────────────────────────────────
Write-Step '프런트 빌드'
if (-not $needBuild) { Write-Info 'dist가 이미 있습니다 (-Rebuild로 다시 빌드)' }
else {
    Act 'npm ci && npm run build' {
        Push-Location $Frontend
        try {
            & $NpmCmd ci
            if ($LASTEXITCODE -ne 0) { Fail 'npm ci에 실패했습니다' }
            & $NpmCmd run build
            if ($LASTEXITCODE -ne 0) { Fail '프런트 빌드에 실패했습니다' }
        } finally { Pop-Location }
    }
}

# ── 6. 방화벽 ───────────────────────────────────────────────────────
Write-Step '방화벽'
if ($NoFirewall) { Write-Info '-NoFirewall — 건너뜁니다' }
else {
    # 프로필 Any: 공용 Wi-Fi로 분류돼도 기기가 닿게 한다(편의 우선, 통제된 LAN 전제).
    Act "TCP $Port 인바운드 허용 ($FirewallHttp)" {
        Get-NetFirewallRule -DisplayName $FirewallHttp -ErrorAction SilentlyContinue | Remove-NetFirewallRule
        New-NetFirewallRule -DisplayName $FirewallHttp -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow -Profile Any | Out-Null
    }
    Act "UDP $DiscoveryPort 인바운드 허용 ($FirewallDiscover)" {
        Get-NetFirewallRule -DisplayName $FirewallDiscover -ErrorAction SilentlyContinue | Remove-NetFirewallRule
        New-NetFirewallRule -DisplayName $FirewallDiscover -Direction Inbound -Protocol UDP -LocalPort $DiscoveryPort -Action Allow -Profile Any | Out-Null
    }
}

# ── 7. 자동 시작 · 시작 ─────────────────────────────────────────────
function Test-Health {
    try { return (Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/health" -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200 }
    catch { return $false }
}

Write-Step '서버 시작'
if (Test-Health) {
    Write-Info "이미 실행 중입니다 (http://localhost:$Port) — 새 코드·.env를 반영하려면 작업을 다시 시작하세요"
    $alreadyUp = $true
} else { $alreadyUp = $false }

if ($NoAutostart) {
    Write-Info '-NoAutostart — 로그온 시 자동 시작을 만들지 않습니다'
    if (-not $alreadyUp) {
        Act "서버 시작 (이 창을 닫아도 계속 실행): pythonw run_server.py --port $Port" {
            Start-Process -FilePath $VenvPyW -WorkingDirectory $Backend -WindowStyle Hidden `
                -ArgumentList @('-X', 'utf8', "`"$RunServer`"", '--port', "$Port", '--log-file', "`"$LogFile`"")
        }
    }
} else {
    # --reload/--workers 없음: 하트비트 버퍼와 APScheduler가 한 프로세스 안에 있다.
    # ★ `pythonw -m uvicorn`을 직접 띄우면 안 된다: pythonw는 콘솔이 없어 sys.stderr가 None이고 uvicorn의 로그 설정이
    # 시작 직후 죽는다(창이 숨겨져 있어 아무 메시지도 없이 서버가 안 뜬다). run_server.py가 출력을 로그 파일로 돌린다.
    # -X utf8: 작업 스케줄러는 이 세션의 환경변수를 물려받지 않으므로 인자로 UTF-8 모드를 켠다.
    $uvArgs = "-X utf8 `"$RunServer`" --port $Port --log-file `"$LogFile`""
    Act "작업 스케줄러 등록: $TaskName (로그온 시, 숨김)" {
        $action    = New-ScheduledTaskAction -Execute $VenvPyW -Argument $uvArgs -WorkingDirectory $Backend
        $trigger   = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
        $settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                        -StartWhenAvailable -Hidden -ExecutionTimeLimit ([TimeSpan]::Zero) `
                        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
        $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
            -Principal $principal -Description 'VisionGuide 대시보드 서버' -Force | Out-Null
    }
    if ($alreadyUp) {
        Act "작업 다시 시작: $TaskName" {
            Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 1
            Start-ScheduledTask -TaskName $TaskName
        }
    } else {
        Act "작업 시작: $TaskName" { Start-ScheduledTask -TaskName $TaskName }
    }
}

# ── 8. 확인 · 요약 ──────────────────────────────────────────────────
$lan = $null
if ($DryRun) { $lan = '<이 PC의 LAN 주소>' }
else {
    $lan = ((& $VenvPy $SetupPy lan-ip | Out-String) | ConvertFrom-Json).lan_ip
    Write-Step '서버 응답 대기 (최대 60초)'
    $up = $false
    for ($i = 0; $i -lt 60; $i++) { if (Test-Health) { $up = $true; break }; Start-Sleep -Seconds 1 }
    if (-not $up) {
        Write-Warn "서버 로그($LogFile)의 마지막 줄:"
        if (Test-Path $LogFile) { Get-Content $LogFile -Tail 25 -Encoding UTF8 | ForEach-Object { Write-Host "    $_" } }
        else { Write-Info '(로그 파일이 없습니다 — 서버 프로세스가 시작되지 못했습니다. 작업 스케줄러에서 VisionGuide Dashboard의 마지막 실행 결과를 확인하세요)' }
        Fail "60초 안에 서버가 응답하지 않습니다. 콘솔에서 직접 확인: cd $Backend; .venv\Scripts\python.exe run_server.py --port $Port"
    }
    Write-Ok "서버 응답 확인: http://localhost:$Port"
}
$url = "http://$(if ($lan) { $lan } else { 'localhost' }):$Port"

Write-Host "`n====================================================" -ForegroundColor Green
Write-Host ' VisionGuide 대시보드 서버 준비 완료' -ForegroundColor Green
Write-Host '====================================================' -ForegroundColor Green
Write-Host "  접속 주소 : $url   (이 PC에서는 http://localhost:$Port)"
Write-Host '  관리자 ID : admin'
if ($EnvResult -and $EnvResult.admin_password) {
    Write-Host "  비밀번호  : $($EnvResult.admin_password)" -ForegroundColor Yellow
    Write-Host "              (저장 위치: $($EnvResult.password_file))"
} elseif ($DryRun) {
    Write-Host '  비밀번호  : (새로 만들면 여기에 표시됩니다)'
} else {
    Write-Host "  비밀번호  : 기존 값을 유지했습니다 ($Backend\data\admin-password.txt 에 처음 만든 값이 있을 수 있습니다)"
}
Write-Host ''
Write-Host '  다음 단계 : 대시보드 로그인 -> "기기 추가" -> "새 기기 설치" 명령을 Pi에 붙여 넣기'
if (-not $NoAutostart) { Write-Host '  자동 시작 : 이 PC에 로그온할 때 서버가 시작됩니다.' }
Write-Host "  서버 로그 : $LogFile"
Write-Warn '이 PC가 절전·최대 절전에 들어가면 서버가 멈추고 기기 연결이 끊깁니다 (전원 옵션에서 끄세요).'
Write-Host ''

if (-not $DryRun) { Start-Process "http://localhost:$Port" }
if ($DryRun) { Write-Host '(dry-run — 아무것도 바꾸지 않았습니다)' }
Finish 0
