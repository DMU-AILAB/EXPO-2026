@echo off
rem VisionGuide dashboard server one-click setup (Windows).
rem Double-click this file, or run it from a terminal. Extra arguments are passed through:
rem   setup-server.bat -Port 8080 -DryRun
rem The PowerShell script re-launches itself as administrator (one UAC prompt).
rem Real logic lives in deploy\setup-server.ps1 and deploy\server_setup.py.
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\setup-server.ps1" %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [ERROR] Setup failed with exit code %RC%.
    pause
)
endlocal & exit /b %RC%
