@echo off
REM Tiansheng Xiaoai - start silently (tray icon only, no console window)
cd /d "%~dp0"

set "PYW=D:\WorkBuddy\2026-11-07-14-47-04\xiaoai-bridge\venv\Scripts\pythonw.exe"
if not exist "%PYW%" (
    echo [ERROR] pythonw not found: %PYW%
    pause
    exit /b 1
)

REM already running? check the console port
curl -s -m 2 --noproxy * http://127.0.0.1:8020/api/status >nul 2>&1
if not errorlevel 1 (
    echo Tiansheng Xiaoai is already running. Opening console ...
    start "" http://127.0.0.1:8020/
    timeout /t 2 /nobreak >nul
    exit /b 0
)

start "" "%PYW%" app.py
ping -n 4 127.0.0.1 >nul
exit
