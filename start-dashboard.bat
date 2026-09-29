@echo off
setlocal

set "ROOT=%~dp0"
set "BACKEND_DIR=%ROOT%dashboard\backend"
set "FRONTEND_DIR=%ROOT%dashboard\frontend"
rem Use the already active Conda environment. If this script is launched
rem without an activated environment, fall back to the named environment.
if not exist "%FRONTEND_DIR%\node_modules" (
    echo [ERROR] Frontend dependencies were not found:
    echo         %FRONTEND_DIR%\node_modules
    echo.
    echo Install them with: cd dashboard\frontend ^&^& npm ci
    pause
    exit /b 1
)

echo Starting VisionGuide dashboard...
if defined CONDA_PREFIX (
    start "VisionGuide Backend :8000" /D "%BACKEND_DIR%" cmd /k ""%CONDA_PREFIX%\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1"
) else (
    start "VisionGuide Backend :8000" /D "%BACKEND_DIR%" cmd /k "conda run --no-capture-output -n visionguide-dashboard python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1"
)
start "VisionGuide Frontend :5173" /D "%FRONTEND_DIR%" cmd /k "npm.cmd run dev -- --host 0.0.0.0"

echo.
echo Dashboard: http://localhost:5173
echo Backend:   http://localhost:8000
echo.
timeout /t 3 /nobreak >nul
start "" "http://localhost:5173"

endlocal
