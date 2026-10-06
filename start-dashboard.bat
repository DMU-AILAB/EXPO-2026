@echo off
setlocal

set "ROOT=%~dp0"
set "BACKEND_DIR=%ROOT%dashboard\backend"
set "FRONTEND_DIR=%ROOT%dashboard\frontend"
rem Backend deps (fastapi, sqlalchemy, ...) live only in the visionguide-dashboard
rem conda env. Call its python.exe directly so this works from a double-click,
rem where conda is not on PATH and CONDA_PREFIX may be unset or another env.
set "DASH_PY=%USERPROFILE%\anaconda3\envs\visionguide-dashboard\python.exe"
if not exist "%DASH_PY%" (
    echo [ERROR] Backend Python was not found:
    echo         %DASH_PY%
    echo.
    echo Create it with: conda create -n visionguide-dashboard python=3.10 ^&^& pip install -r dashboard\backend\requirements.txt
    pause
    exit /b 1
)
if not exist "%FRONTEND_DIR%\node_modules" (
    echo [ERROR] Frontend dependencies were not found:
    echo         %FRONTEND_DIR%\node_modules
    echo.
    echo Install them with: cd dashboard\frontend ^&^& npm ci
    pause
    exit /b 1
)

echo Starting VisionGuide dashboard...
start "VisionGuide Backend :8000" /D "%BACKEND_DIR%" cmd /k ""%DASH_PY%" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1"
start "VisionGuide Frontend :5173" /D "%FRONTEND_DIR%" cmd /k "npm.cmd run dev -- --host 0.0.0.0"

echo.
echo Dashboard: http://localhost:5173
echo Backend:   http://localhost:8000
echo.
timeout /t 3 /nobreak >nul
start "" "http://localhost:5173"

endlocal
