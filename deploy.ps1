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
    [ValidateSet('setup-ssh', 'deploy', 'sync', 'deps', 'install-edgetpu-py39', 'coral-setup', 'coral-compile', 'run', 'run-headless', 'ping', 'help')]
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
    "detect.py",
    "edgetpu_infer.py"
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

    # Edge TPU model is optional — ship it only if it has been compiled.
    $edgetpuModel = "runs/white_cane_v1-2/weights/best_int8_edgetpu.tflite"
    if (Test-Path $edgetpuModel) {
        Write-Step "Transferring Edge TPU model (best_int8_edgetpu.tflite)..."
        scp $edgetpuModel "${Target}:${RemoteDir}/runs/white_cane_v1-2/weights/"
        if ($?) { Write-Ok "best_int8_edgetpu.tflite" } else { Write-Fail "Edge TPU model transfer failed" }
    }
}

# ── install-edgetpu-py39: Install Python 3.9 EdgeTPU packages on Pi ──
#   Run this AFTER Python 3.9 is compiled on the Pi:
#     cd ~ && wget https://www.python.org/ftp/python/3.9.21/Python-3.9.21.tgz
#     tar xf Python-3.9.21.tgz && cd Python-3.9.21
#     ./configure --prefix=$HOME/.python39 && make -j4 && make install
function Invoke-InstallEdgeTPUPy39 {
    Write-Step "Installing Python 3.9 EdgeTPU packages on Pi..."
    Write-Info "Requires ~/.python39 — build it first if missing:"
    Write-Info "  cd ~ && wget https://www.python.org/ftp/python/3.9.21/Python-3.9.21.tgz"
    Write-Info "  tar xf Python-3.9.21.tgz && cd Python-3.9.21"
    Write-Info "  ./configure --prefix=`$HOME/.python39 && make -j4 && make install"

    $whl = "https://github.com/google-coral/pycoral/releases/download/v2.0.0/tflite_runtime-2.5.0.post1-cp39-cp39-linux_aarch64.whl"
    ssh $Target "~/.python39/bin/pip3 install -q '$whl' 'numpy<2' opencv-python-headless"
    if ($?) {
        Write-Ok "Python 3.9 EdgeTPU packages installed"
        Write-Info "Test: ssh $Target '~/.python39/bin/python3.9 ~/visionguide/edgetpu_infer.py'"
        Write-Info "  => should print READY and wait (Ctrl+C to exit)"
    } else {
        Write-Fail "Install failed. Ensure ~/.python39 exists on the Pi."
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

# ── coral-setup: Install Edge TPU RUNTIME on Pi (runtime only!) ───────
#   NOTE: edgetpu_compiler is x86-64 ONLY and cannot run on the Pi (aarch64).
#         The Pi needs the runtime (libedgetpu1-std) so it can USE a model
#         that was compiled elsewhere. Compile with:  .\deploy.ps1 coral-compile
function Invoke-CoralSetup {
    # Write a bash script with LF line endings and upload via scp
    # (ssh -t is needed so sudo can prompt for password interactively)
    $scriptLines = @(
        '#!/bin/bash',
        'set -e',
        'echo "[1/3] Adding Coral apt repository (Bookworm signed-by)..."',
        'curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg | sudo gpg --dearmor -o /usr/share/keyrings/coral-edgetpu.gpg',
        'echo "deb [signed-by=/usr/share/keyrings/coral-edgetpu.gpg] https://packages.cloud.google.com/apt coral-edgetpu-stable main" | sudo tee /etc/apt/sources.list.d/coral-edgetpu.list',
        'sudo apt-get update -q',
        'echo "[2/3] Installing libedgetpu1-std runtime (NO compiler — aarch64)..."',
        'sudo apt-get install -y libedgetpu1-std',
        'echo "[3/3] Checking USB Accelerator..."',
        'lsusb | grep -i "1a6e\|18d1\|global unichip\|google" && echo "  -> Coral USB detected" || echo "  -> WARNING: Coral USB NOT detected. Plug it in (a USB3/blue port is best)."'
    )
    $scriptBody = $scriptLines -join "`n"
    $tmpScript  = "$env:TEMP\vg_coral_setup.sh"
    [System.IO.File]::WriteAllText($tmpScript, $scriptBody, [System.Text.UTF8Encoding]::new($false))

    Write-Step "Uploading Coral runtime setup script to Pi..."
    scp $tmpScript "${Target}:~/vg_coral_setup.sh"
    if (-not $?) { Write-Fail "Failed to upload setup script."; return }

    Write-Step "Installing Coral runtime on Pi (sudo password will be required)..."
    ssh -t $Target "bash ~/vg_coral_setup.sh; rm -f ~/vg_coral_setup.sh"

    Remove-Item $tmpScript -ErrorAction SilentlyContinue

    if ($?) {
        Write-Ok "Coral runtime installed on Pi."
        Write-Info "Next: compile the model with  .\deploy.ps1 coral-compile  (needs WSL),"
        Write-Info "then  .\deploy.ps1 sync  to push best_int8_edgetpu.tflite to the Pi."
    } else {
        Write-Fail "Coral runtime setup failed. Check the output above."
    }
}

# ── coral-compile: Compile TFLite model for Edge TPU in WSL (x86-64) ──
#   edgetpu_compiler only runs on x86-64 Linux, so we use WSL. The output
#   best_int8_edgetpu.tflite is written next to best_int8.tflite locally,
#   then `sync` ships it to the Pi.
function Invoke-CoralCompile {
    $wslCheck = wsl --status 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "WSL is not installed."
        Write-Info "Install it (admin PowerShell, then reboot):  wsl --install -d Ubuntu"
        return
    }

    $modelDir = "runs/white_cane_v1-2/weights"
    if (-not (Test-Path "$modelDir/best_int8.tflite")) {
        Write-Fail "Model not found: $modelDir/best_int8.tflite"
        return
    }

    # Bash script run inside WSL: install compiler (x86-64) if missing, then compile.
    $scriptLines = @(
        '#!/bin/bash',
        'set -e',
        'if ! command -v edgetpu_compiler >/dev/null 2>&1; then',
        '  echo "[1/2] Installing edgetpu-compiler in WSL (x86-64)..."',
        '  curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg | sudo gpg --dearmor -o /usr/share/keyrings/coral-edgetpu.gpg',
        '  echo "deb [signed-by=/usr/share/keyrings/coral-edgetpu.gpg] https://packages.cloud.google.com/apt coral-edgetpu-stable main" | sudo tee /etc/apt/sources.list.d/coral-edgetpu.list',
        '  sudo apt-get update -q',
        '  sudo apt-get install -y edgetpu-compiler',
        'fi',
        'echo "[2/2] Compiling for Edge TPU..."',
        'cd "$(dirname "$0")"',
        'edgetpu_compiler -s best_int8.tflite',
        'ls -lh best_int8_edgetpu.tflite'
    )
    $scriptBody = $scriptLines -join "`n"
    $tmpScript  = Join-Path (Resolve-Path $modelDir) "vg_compile.sh"
    [System.IO.File]::WriteAllText($tmpScript, $scriptBody, [System.Text.UTF8Encoding]::new($false))

    Write-Step "Compiling model for Edge TPU in WSL (sudo password may be required)..."
    # Translate the Windows path to a WSL path and run the script there.
    $wslPath = (wsl wslpath -a ("'" + (Resolve-Path "$modelDir/vg_compile.sh").Path + "'")) 2>$null
    if (-not $wslPath) {
        # Fallback: cd into the dir via wslpath of the directory
        $wslDir = wsl wslpath -a ("'" + (Resolve-Path $modelDir).Path + "'")
        wsl bash -c "cd $wslDir && bash vg_compile.sh"
    } else {
        wsl bash "$wslPath"
    }
    $ok = $?

    Remove-Item $tmpScript -ErrorAction SilentlyContinue

    if ($ok -and (Test-Path "$modelDir/best_int8_edgetpu.tflite")) {
        Write-Ok "Compiled: $modelDir/best_int8_edgetpu.tflite"
        Write-Info "Now push it to the Pi:  .\deploy.ps1 sync -PI $PI"
    } else {
        Write-Fail "Compilation failed or output missing. Check the output above."
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
  .\deploy.ps1 install-edgetpu-py39 [-PI <ip>]   Install Python 3.9 EdgeTPU packages (after py3.9 build)
  .\deploy.ps1 coral-setup  [-PI <ip>]   Install Edge TPU RUNTIME on Pi (libedgetpu1-std)
  .\deploy.ps1 coral-compile             Compile model for Edge TPU in WSL (x86-64)
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
    'setup-ssh'           { Invoke-SetupSSH }
    'deploy'              { Invoke-Deploy }
    'sync'                { Invoke-Sync }
    'deps'                { Invoke-Deps }
    'install-edgetpu-py39' { Invoke-InstallEdgeTPUPy39 }
    'coral-setup'         { Invoke-CoralSetup }
    'coral-compile'       { Invoke-CoralCompile }
    'run-headless' { Invoke-RunHeadless }
    'run'          { Invoke-Run }
    'ping'         { Invoke-Ping }
    default        { Show-Help }
}
