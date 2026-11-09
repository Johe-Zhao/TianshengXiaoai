@echo off
REM Tiansheng voice bridge launcher (gateway + xiaogpt)
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo [ERROR] venv not found. Run setup.bat first.
    pause
    exit /b 1
)
if not exist "config.json" (
    echo [ERROR] config.json not found. Copy config.example.json and fill your Xiaomi account.
    pause
    exit /b 1
)

echo [1/2] Starting tiansheng gateway on port 8008 ...
start "tiansheng-gateway" /min cmd /c "venv\Scripts\python.exe gateway.py > gateway.log 2>&1"
timeout /t 2 /nobreak >nul

echo [2/2] Starting xiaogpt, listening to your speaker ...
echo (Press Ctrl+C to stop. Keep this window open.)
echo.
venv\Scripts\python.exe run_xiaogpt.py --config config.json

echo.
echo ===== xiaogpt exited with code %errorlevel% =====
pause
