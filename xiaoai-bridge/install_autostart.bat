@echo off
REM Register auto-start on login (no admin needed for current-user logon tasks)
REM NOTE: 便携版已由 supervisor 统一管理所有子服务（含无窗口 CodeBuddy 后端）。
REM       这个脚本是旧版遗留，正常情况下不需要再运行。
cd /d "%~dp0"

schtasks /Create /F /TN "TianshengVoice" /TR "\"%~dp0start_hidden.bat\"" /SC ONLOGON /RL LIMITED
if %errorlevel%==0 (echo [OK] TianshengVoice auto-start registered.) else (echo [ERROR] Failed to register TianshengVoice.)

REM 已废弃：TianshengCodebuddy 会在每次登录弹出一个可见的 CodeBuddy 黑窗口，
REM 且未登录时会拿账号密码触发小米短信验证码骚扰。现在登录走界面「重新登录」，
REM 后端走 codebuddy_hidden.py（无窗口），不再需要它。
REM schtasks /Create /F /TN "TianshengCodebuddy" /TR "\"%~dp0codebuddy.cmd\"" /SC ONLOGON /RL LIMITED

echo.
echo Done.
pause
