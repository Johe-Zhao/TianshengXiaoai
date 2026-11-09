@echo off
REM One-time Xiaomi login (asks for SMS code, saves token to ~/.mi.token)
cd /d "%~dp0"
if not exist config.json (
    echo [ERROR] config.json not found. Fill your Xiaomi account first.
    pause
    exit /b 1
)
echo Xiaomi one-time login. Watch your phone for the SMS code.
echo.
venv\Scripts\python.exe login.py
echo.
pause
