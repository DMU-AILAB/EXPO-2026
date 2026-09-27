@echo off
setlocal

set "ROOT=%~dp0"
set "BACKEND_DIR=%ROOT%dashboard\backend"
set "FRONTEND_DIR=%ROOT%dashboard\frontend"
set "BACKEND_PYTHON=%BACKEND_DIR%\.venv\Scripts\python.exe"

if not exist "%BACKEND_PYTHON%" (
    echo [ERROR] Backend virtual environment was not found:
    echo         %BACKEND_PYTHON%
    echo.
    echo Create it with: python -m venv dashboard\backend\.venv
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
start "VisionGuide Backend :8000" /D "%BACKEND_DIR%" cmd /k ""%BACKEND_PYTHON%" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1"
start "VisionGuide Frontend :5173" /D "%FRONTEND_DIR%" cmd /k "npm.cmd run dev -- --host 0.0.0.0"

echo.
echo Dashboard: http://localhost:5173
echo Backend:   http://localhost:8000
echo.
timeout /t 3 /nobreak >nul
start "" "http://localhost:5173"

endlocal
