<#
.SYNOPSIS
    VisionGuide Pi deployment automation (Windows PowerShell)

.DESCRIPTION
    Covers the full workflow: SSH key setup, file transfer,
    dependency install, and remote run — using built-in Windows OpenSSH.

.EXAMPLE
    .\deploy.ps1 setup-ssh -PI 192.168.0.89   # First time only
    .\deploy.ps1 deploy    -PI 192.168.0.89   # Full deploy
    .\deploy.ps1 sync      -PI 192.168.0.89   # Re-send files after code change
    .\deploy.ps1 run-headless -PI 192.168.0.89
    .\deploy.ps1 ping      -PI 192.168.0.89   # Check connection
#>

param(
    [Parameter(Position = 0)]
    [ValidateSet('setup-ssh', 'deploy', 'sync', 'deps', 'coral-setup', 'run', 'run-headless', 'ping', 'help')]
    [string]$Action = 'help',

    # Pi IP or hostname (raspberrypi.local works when mDNS is available)
    [string]$PI   = 'raspberrypi.local',
    [string]$User = 'ailab'
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# Run from project root regardless of where the script is called from
Set-Location $PSScriptRoot

$Target    = "${User}@${PI}"
$RemoteDir = "~/visionguide"

# Files to deploy — add new Pi scripts here
$PyFiles   = @(
    "camera_live_pi.py",
    "detect.py"
)
$ModelFile = "runs/white_cane_v1-2/weights/best_int8.tflite"

# ── Output helpers ────────────────────────────────────────────────────
function Write-Step { param($msg) Write-Host "`n[*] $msg" -ForegroundColor Cyan }
function Write-Ok   { param($msg) Write-Host "[OK] $msg"    -ForegroundColor Green }
function Write-Fail { param($msg) Write-Host "[ERROR] $msg" -ForegroundColor Red }
function Write-Info { param($msg) Write-Host "[INFO] $msg"  -ForegroundColor Gray }

# ── setup-ssh: Generate SSH key + register on Pi ─────────────────────
function Invoke-SetupSSH {
    $keyPath = "$env:USERPROFILE\.ssh\id_ed25519"
    $pubPath = "$keyPath.pub"

    # Create .ssh directory if it does not exist
    $sshDir = "$env:USERPROFILE\.ssh"
    if (-not (Test-Path $sshDir)) {
        New-Item -ItemType Directory -Path $sshDir | Out-Null
        Write-Info ".ssh directory created: $sshDir"
    }

    # Generate SSH key if missing
    if (Test-Path $pubPath) {
        Write-Info "Using existing SSH key: $pubPath"
    } else {
        Write-Step "Generating SSH key (press Enter twice for no passphrase)"
        ssh-keygen -t ed25519 -C "visionguide" -f $keyPath
        if (-not $?) {
            Write-Fail "SSH key generation failed"
            return
        }
        Write-Ok "SSH key created: $pubPath"
    }

    # Register public key on Pi via scp (avoids CRLF corruption from PowerShell pipe)
    Write-Step "Registering public key on Pi... (enter Pi password when prompted)"
    ssh $Target "mkdir -p ~/.ssh && chmod go-w ~ && chmod 700 ~/.ssh"
    if (-not $?) { Write-Fail "Failed. Verify PI=$PI and User=$User are correct."; return }

    scp $pubPath "${Target}:~/.ssh/temp_vg.pub"
    if (-not $?) { Write-Fail "Failed to copy public key."; return }

    ssh $Target "cat ~/.ssh/temp_vg.pub >> ~/.ssh/authorized_keys && rm ~/.ssh/temp_vg.pub && chmod 600 ~/.ssh/authorized_keys"
    if (-not $?) {
        Write-Fail "Failed to register public key."
        return
    }
    Write-Ok "Public key registered."

    # Verify passwordless auth actually works
    Write-Step "Verifying passwordless login..."
    $test = ssh -o "BatchMode=yes" -o "ConnectTimeout=5" $Target "echo ok" 2>&1
    if ($test -eq "ok") {
        Write-Ok "Passwordless login confirmed. No password needed from now on."
    } else {
        Write-Fail "Key registered but passwordless login failed."
        Write-Info "Check Pi sshd config: PubkeyAuthentication yes"
        Write-Info "Or try: ssh -v $Target"
    }
}

# ── sync: Transfer files to Pi ───────────────────────────────────────
function Invoke-Sync {
    Write-Step "Creating remote directory..."
    ssh $Target "mkdir -p $RemoteDir/runs/white_cane_v1-2/weights"
    if (-not $?) { Write-Fail "SSH connection failed ($Target)"; return }

    Write-Step "Transferring Python files..."
    foreach ($f in $PyFiles) {
        if (Test-Path $f) {
            scp $f "${Target}:${RemoteDir}/"
            if ($?) { Write-Ok $f } else { Write-Fail "Failed: $f" }
        } else {
            Write-Info "Skipped (not found): $f"
        }
    }

    Write-Step "Transferring model (best_int8.tflite)..."
    if (Test-Path $ModelFile) {
        scp $ModelFile "${Target}:${RemoteDir}/runs/white_cane_v1-2/weights/"
        if ($?) { Write-Ok "best_int8.tflite" } else { Write-Fail "Model transfer failed" }
    } else {
        Write-Fail "Model file not found: $ModelFile"
    }
}

# ── deps: Install Python dependencies on Pi ──────────────────────────
function Invoke-Deps {
    Write-Step "Installing dependencies on Pi..."
    ssh $Target "sudo apt-get install -y python3-picamera2 2>/dev/null; true"
    ssh $Target "pip install -q --break-system-packages ai-edge-litert opencv-python-headless numpy"
    if ($?) {
        Write-Ok "Dependencies installed"
    } else {
        Write-Fail "Dependency install encountered errors"
    }
}

# ── coral-setup: Install Edge TPU runtime + compile model on Pi ──────
function Invoke-CoralSetup {
    # Write a bash script with LF line endings and upload via scp
    # (ssh -t is needed so sudo can prompt for password interactively)
    $scriptLines = @(
        '#!/bin/bash',
        'set -e',
        'echo "[1/4] Adding Coral apt repository..."',
        'echo "deb https://packages.cloud.google.com/apt coral-edgetpu-stable main" | sudo tee /etc/apt/sources.list.d/coral-edgetpu.list',
        'curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg | sudo gpg --dearmor -o /usr/share/keyrings/coral-edgetpu.gpg 2>/dev/null',
        'sudo apt-get update -q',
        'echo "[2/4] Installing libedgetpu1-std and edgetpu-compiler..."',
        'sudo apt-get install -y libedgetpu1-std edgetpu-compiler',
        'echo "[3/4] Compiling TFLite model for Edge TPU..."',
        "cd ~/visionguide/runs/white_cane_v1-2/weights",
        'edgetpu_compiler best_int8.tflite',
        'echo "[4/4] Done! best_int8_edgetpu.tflite is ready."'
    )
    $scriptBody = $scriptLines -join "`n"
    $tmpScript  = "$env:TEMP\vg_coral_setup.sh"
    [System.IO.File]::WriteAllText($tmpScript, $scriptBody, [System.Text.UTF8Encoding]::new($false))

    Write-Step "Uploading Coral setup script to Pi..."
    scp $tmpScript "${Target}:~/vg_coral_setup.sh"
    if (-not $?) { Write-Fail "Failed to upload setup script."; return }

    Write-Step "Running Coral setup (sudo password will be required)..."
    Write-Info "This may take a few minutes."
    ssh -t $Target "bash ~/vg_coral_setup.sh; rm -f ~/vg_coral_setup.sh"

    Remove-Item $tmpScript -ErrorAction SilentlyContinue

    if ($?) {
        Write-Ok "Coral setup complete."
        Write-Info "Restart run-headless — Edge TPU backend will be selected automatically."
    } else {
        Write-Fail "Coral setup failed. Check the output above."
    }
}

# ── deploy: sync + deps ──────────────────────────────────────────────
function Invoke-Deploy {
    Invoke-Sync
    Invoke-Deps
    Write-Host "`n[DONE] Deploy complete: $PI" -ForegroundColor Green
}

# ── ping: Check Pi connection and environment ─────────────────────────
function Invoke-Ping {
    Write-Step "Checking connection: $Target"
    ssh $Target "python3 --version && echo '--- deployed files ---' && ls $RemoteDir/ 2>/dev/null || echo '(not deployed yet)'"
}

# ── run-headless: Start MJPEG streaming on Pi ─────────────────────────
function Invoke-RunHeadless {
    Write-Host "`n[RUN] Open in browser: http://${PI}:8080/stream.mjpg" -ForegroundColor Yellow
    Write-Info "Press Ctrl+C to stop"
    ssh -t $Target "cd $RemoteDir && python camera_live_pi.py --headless --port 8080"
}

# ── run: Display mode on Pi ───────────────────────────────────────────
function Invoke-Run {
    Write-Info "Starting display mode (requires monitor on Pi)"
    ssh -t $Target "cd $RemoteDir && python camera_live_pi.py"
}

# ── help ─────────────────────────────────────────────────────────────
function Show-Help {
    Write-Host @"

VisionGuide Pi Deploy Script
-----------------------------------------------------
  .\deploy.ps1 setup-ssh    [-PI <ip>]   SSH key setup (first time only)
  .\deploy.ps1 deploy       [-PI <ip>]   Transfer files + install deps
  .\deploy.ps1 sync         [-PI <ip>]   Re-send files only
  .\deploy.ps1 deps         [-PI <ip>]   Install deps only
  .\deploy.ps1 coral-setup  [-PI <ip>]   Install Edge TPU runtime + compile model for Coral
  .\deploy.ps1 run-headless [-PI <ip>]   Start MJPEG stream on Pi
  .\deploy.ps1 run          [-PI <ip>]   Start display mode on Pi
  .\deploy.ps1 ping         [-PI <ip>]   Check Pi connection

Defaults: PI=$PI  User=$User

If you get an execution policy error:
  Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
-----------------------------------------------------
"@
}

# ── Router ───────────────────────────────────────────────────────────
switch ($Action) {
    'setup-ssh'    { Invoke-SetupSSH }
    'deploy'       { Invoke-Deploy }
    'sync'         { Invoke-Sync }
    'deps'         { Invoke-Deps }
    'coral-setup'  { Invoke-CoralSetup }
    'run-headless' { Invoke-RunHeadless }
    'run'          { Invoke-Run }
    'ping'         { Invoke-Ping }
    default        { Show-Help }
}
