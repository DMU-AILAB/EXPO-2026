@echo off
rem VisionGuide dashboard server one-click setup (Windows).
rem Double-click this file, or run it from a terminal. Extra arguments are passed through:
rem   setup-server.bat -Port 8080 -DryRun
rem The PowerShell script re-launches itself as administrator (one UAC prompt).
rem Real logic lives in deploy\setup-server.ps1 and deploy\server_setup.py.
rem
rem Always pauses at the end. If this console is already elevated the script does not open
rem a new window, so without a pause the window closes the moment setup finishes and the
rem summary (URL, admin password) is lost. Set VG_NO_PAUSE=1 to skip it (CI, automation).
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\setup-server.ps1" %*
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo [ERROR] Setup failed with exit code %RC%.
if /i not "%VG_NO_PAUSE%"=="1" pause
endlocal & exit /b %RC%
