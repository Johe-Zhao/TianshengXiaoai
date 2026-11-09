@echo off
REM Generate and open xiaoai conversation history page
cd /d "%~dp0"
venv\Scripts\python.exe history.py
if %errorlevel% neq 0 pause
