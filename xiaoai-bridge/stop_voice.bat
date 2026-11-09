@echo off
REM Stop background voice services
taskkill /F /IM pythonw.exe >nul 2>&1
echo [OK] pythonw services stopped.
timeout /t 2 /nobreak >nul
