@echo off
chcp 65001 >nul
title PLUXO - localhost
cd /d "%~dp0"

echo.
echo  Starting Pluxo - site + API at http://127.0.0.1:5000
echo  Keep this window open. Close it to stop the server.
echo.

REM Always run THIS folder's script (fixes wrong folder / 404)
set "PY=%~dp0pluxo_backend.py"
if not exist "%PY%" (
    echo ERROR: pluxo_backend.py not found next to this .bat
    pause
    exit /b 1
)

REM Open browser after Flask binds
start "" cmd /c "timeout /t 2 /nobreak >nul && start http://127.0.0.1:5000/"

py -3 "%PY%" 2>nul
if errorlevel 1 python "%PY%"
if errorlevel 1 (
    echo.
    echo  Python error - try: pip install -r requirements.txt
    pause
)
