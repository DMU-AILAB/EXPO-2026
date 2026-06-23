<#
.SYNOPSIS
    VisionGuide Pi 배포 자동화 (Windows PowerShell)

.DESCRIPTION
    SSH 키 초기 설정부터 파일 전송, 의존성 설치, 실행까지 한 번에 처리합니다.
    Windows 기본 OpenSSH(scp/ssh)를 사용하므로 별도 도구 설치 불필요.

.EXAMPLE
    # 처음 Pi 연결 시 (SSH 키 등록)
    .\deploy.ps1 setup-ssh -PI 192.168.0.89

    # 전체 배포 (파일 전송 + 의존성 설치)
    .\deploy.ps1 deploy -PI 192.168.0.89

    # 코드만 수정됐을 때 빠른 재배포
    .\deploy.ps1 sync -PI 192.168.0.89

    # Pi에서 스트리밍 시작
    .\deploy.ps1 run-headless -PI 192.168.0.89

    # PI 기본값을 스크립트 상단에서 수정하면 -PI 생략 가능
#>

# 한글 깨짐 방지
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

param(
    [Parameter(Position = 0)]
    [ValidateSet('setup-ssh', 'deploy', 'sync', 'deps', 'run', 'run-headless', 'ping', 'help')]
    [string]$Action = 'help',

    # Pi IP 또는 호스트명 — mDNS가 작동하면 raspberrypi.local, 아니면 IP 직접 지정
    [string]$PI   = 'raspberrypi.local',
    [string]$User = 'pi'
)

# 스크립트 위치를 기준으로 실행 (경로 문제 방지)
Set-Location $PSScriptRoot

$Target    = "${User}@${PI}"
$RemoteDir = "~/visionguide"

# Pi에 배포할 파일 목록 — 새 파일 추가 시 여기에 추가
$PyFiles   = @(
    "camera_live_pi.py",
    "detect.py"
)
$ModelFile = "runs/white_cane_v1-2/weights/best_int8.tflite"

# ── 출력 헬퍼 ────────────────────────────────────────────────────────
function Write-Step { param($msg) Write-Host "`n[*] $msg" -ForegroundColor Cyan }
function Write-Ok   { param($msg) Write-Host "[OK] $msg"    -ForegroundColor Green }
function Write-Fail { param($msg) Write-Host "[ERROR] $msg" -ForegroundColor Red }
function Write-Info { param($msg) Write-Host "[INFO] $msg"  -ForegroundColor Gray }

# ── setup-ssh: SSH 키 생성 + Pi에 공개키 등록 ───────────────────────
function Invoke-SetupSSH {
    $keyPath = "$env:USERPROFILE\.ssh\id_ed25519"
    $pubPath = "$keyPath.pub"

    # SSH 키 생성
    if (Test-Path $pubPath) {
        Write-Info "기존 SSH 키 사용: $pubPath"
    } else {
        Write-Step "SSH 키 생성 (passphrase 없으려면 Enter 두 번)"
        ssh-keygen -t ed25519 -C "visionguide" -f $keyPath
        if (-not $?) {
            Write-Fail "SSH 키 생성 실패"
            return
        }
        Write-Ok "SSH 키 생성: $pubPath"
    }

    # Pi에 공개키 등록
    Write-Step "Pi에 공개키 등록 중... (이 단계에서만 Pi 비밀번호 입력)"
    Get-Content $pubPath | ssh $Target "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
    if ($?) {
        Write-Ok "등록 완료 — 이후 배포 시 비밀번호 없이 자동 진행됩니다."
    } else {
        Write-Fail "등록 실패. Pi IP($PI)와 사용자명($User)을 확인하세요."
    }
}

# ── sync: 파일만 Pi로 전송 ───────────────────────────────────────────
function Invoke-Sync {
    Write-Step "Pi 디렉토리 생성..."
    ssh $Target "mkdir -p $RemoteDir/runs/white_cane_v1-2/weights"
    if (-not $?) { Write-Fail "SSH 연결 실패 ($Target)"; return }

    Write-Step "Python 파일 전송..."
    foreach ($f in $PyFiles) {
        if (Test-Path $f) {
            scp $f "${Target}:${RemoteDir}/"
            if ($?) { Write-Ok $f } else { Write-Fail "$f 전송 실패" }
        } else {
            Write-Info "$f 없음 — 건너뜀"
        }
    }

    Write-Step "모델 파일 전송 (best_int8.tflite)..."
    if (Test-Path $ModelFile) {
        scp $ModelFile "${Target}:${RemoteDir}/runs/white_cane_v1-2/weights/"
        if ($?) { Write-Ok "best_int8.tflite" } else { Write-Fail "모델 전송 실패" }
    } else {
        Write-Fail "모델 파일 없음: $ModelFile"
    }
}

# ── deps: Pi 의존성 설치 ─────────────────────────────────────────────
function Invoke-Deps {
    Write-Step "Pi 의존성 설치 중..."
    ssh $Target "sudo apt-get install -y python3-picamera2 2>/dev/null; true"
    ssh $Target "pip install -q tflite-runtime opencv-python-headless numpy"
    if ($?) {
        Write-Ok "의존성 설치 완료"
    } else {
        Write-Fail "의존성 설치 중 오류 발생"
    }
}

# ── deploy: sync + deps ──────────────────────────────────────────────
function Invoke-Deploy {
    Invoke-Sync
    Invoke-Deps
    Write-Host "`n[완료] $PI 배포 완료" -ForegroundColor Green
}

# ── ping: 연결 및 환경 확인 ──────────────────────────────────────────
function Invoke-Ping {
    Write-Step "Pi 연결 확인: $Target"
    ssh $Target "python3 --version && echo '--- 배포된 파일 ---' && ls $RemoteDir/ 2>/dev/null || echo '(아직 배포 전)'"
}

# ── run-headless: MJPEG 스트리밍 시작 ───────────────────────────────
function Invoke-RunHeadless {
    Write-Host "`n[RUN] 브라우저에서 접속: http://${PI}:8080/stream.mjpg" -ForegroundColor Yellow
    Write-Info "종료: Ctrl+C"
    ssh -t $Target "cd $RemoteDir && python camera_live_pi.py --headless --port 8080"
}

# ── run: 디스플레이 모드 ─────────────────────────────────────────────
function Invoke-Run {
    Write-Info "Pi 디스플레이 모드 시작 (모니터 연결 필요)"
    ssh -t $Target "cd $RemoteDir && python camera_live_pi.py"
}

# ── help ─────────────────────────────────────────────────────────────
function Show-Help {
    Write-Host @"

VisionGuide Pi 배포 스크립트
─────────────────────────────────────────────────────
  .\deploy.ps1 setup-ssh    [-PI <ip>]   SSH 키 생성 + Pi 등록  ← 처음 한 번만
  .\deploy.ps1 deploy       [-PI <ip>]   파일 전송 + 의존성 설치
  .\deploy.ps1 sync         [-PI <ip>]   파일만 재전송 (코드 변경 후)
  .\deploy.ps1 deps         [-PI <ip>]   의존성만 설치
  .\deploy.ps1 run-headless [-PI <ip>]   Pi에서 MJPEG 스트리밍 시작
  .\deploy.ps1 run          [-PI <ip>]   Pi에서 디스플레이 모드 실행
  .\deploy.ps1 ping         [-PI <ip>]   Pi 연결 및 환경 확인

현재 기본값: PI=$PI  User=$User

실행 권한 오류 시:
  Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
─────────────────────────────────────────────────────
"@
}

# ── 라우터 ───────────────────────────────────────────────────────────
switch ($Action) {
    'setup-ssh'    { Invoke-SetupSSH }
    'deploy'       { Invoke-Deploy }
    'sync'         { Invoke-Sync }
    'deps'         { Invoke-Deps }
    'run-headless' { Invoke-RunHeadless }
    'run'          { Invoke-Run }
    'ping'         { Invoke-Ping }
    default        { Show-Help }
}
