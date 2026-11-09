@echo off
REM Remove auto-start tasks
schtasks /Delete /F /TN "TianshengVoice" >nul 2>&1
schtasks /Delete /F /TN "TianshengCodebuddy" >nul 2>&1
echo [OK] Auto-start tasks removed.
pause
