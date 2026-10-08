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
    # 저장소 밖에서 실행할 때(원격 한 줄) 받아올 곳과 위치
    [string]$RepoUrl = 'https://github.com/DMU-AILAB/EXPO-2026.git',
    [string]$InstallDir = (Join-Path $env:USERPROFILE 'VisionGuide'),
    # 내부용 — 관리자 권한으로 다시 띄운 창이 끝나도 요약을 읽을 수 있게 멈춘다
    [switch]$Elevated
)

$ErrorActionPreference = 'Stop'
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

function Install-WithWinget {
    param([string]$Id, [string]$Name)
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        if ($DryRun) { Write-Warn "$Name 설치 예정 — 그런데 winget이 없습니다 (실제 실행 전에 App Installer가 필요합니다)"; return }
        Fail "$Name 이(가) 없고 winget도 없습니다. Microsoft Store의 'App Installer'를 설치한 뒤 다시 실행하세요."
    }
    Act "winget install $Id" {
        & winget.exe install --id $Id -e --silent --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) { Fail "$Name 설치에 실패했습니다 (winget 종료 코드 $LASTEXITCODE)" }
        Update-SessionPath
    }
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
    if (-not (Get-Command git.exe -ErrorAction SilentlyContinue)) { Install-WithWinget 'Git.Git' 'Git' }
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
    Install-WithWinget 'Python.Python.3.11' 'Python 3.11'
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
        Install-WithWinget 'OpenJS.NodeJS.LTS' 'Node.js LTS'
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
        Act "서버 시작 (이 창을 닫아도 계속 실행): pythonw -m uvicorn app.main:app --port $Port" {
            Start-Process -FilePath $VenvPyW -WorkingDirectory $Backend -WindowStyle Hidden `
                -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', "$Port")
        }
    }
} else {
    # --reload/--workers 없음: 하트비트 버퍼와 APScheduler가 한 프로세스 안에 있다.
    $uvArgs = "-m uvicorn app.main:app --host 0.0.0.0 --port $Port"
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
    if (-not $up) { Fail "60초 안에 서버가 응답하지 않습니다. 직접 확인: cd $Backend; .venv\Scripts\python.exe -m uvicorn app.main:app --port $Port" }
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
Write-Warn '이 PC가 절전·최대 절전에 들어가면 서버가 멈추고 기기 연결이 끊깁니다 (전원 옵션에서 끄세요).'
Write-Host ''

if (-not $DryRun) { Start-Process "http://localhost:$Port" }
if ($DryRun) { Write-Host '(dry-run — 아무것도 바꾸지 않았습니다)' }
Finish 0
