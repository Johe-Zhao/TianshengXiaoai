@echo off
REM Start gateway + xiaogpt in background (no windows) via pythonw
cd /d "%~dp0"

tasklist /FI "IMAGENAME eq pythonw.exe" 2>nul | find /I "pythonw.exe" >nul
if %errorlevel%==0 (
    echo [WARN] Services already running. Run stop_voice.bat first.
    pause
    exit /b 1
)

start /b "" venv\Scripts\pythonw.exe gateway.py > gateway_bg.log 2>&1
timeout /t 2 /nobreak >nul
start /b "" venv\Scripts\pythonw.exe run_xiaogpt.py --config config.json > xiaogpt_bg.log 2>&1

echo [OK] Tiansheng voice services started in background.
echo     gateway log: gateway_bg.log
echo     xiaogpt log: xiaogpt_bg.log
timeout /t 3 /nobreak >nul
