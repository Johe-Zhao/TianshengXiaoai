@echo off
REM Stop Tiansheng Xiaoai and all its services (reliable psutil-based cleanup)
cd /d "%~dp0"
set "PY=D:\WorkBuddy\2026-11-07-14-47-04\xiaoai-bridge\venv\Scripts\python.exe"

if not exist "%PY%" (
    echo [ERROR] Python not found: %PY%
    pause
    exit /b 1
)

echo Stopping Tiansheng Xiaoai ...
"%PY%" cleanup.py
del /q ".instance.lock" 2>nul
timeout /t 2 /nobreak >nul
