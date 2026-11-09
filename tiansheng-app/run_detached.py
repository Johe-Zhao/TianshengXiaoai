# -*- coding: utf-8 -*-
"""以脱离会话方式启动 app.py（仅用于测试）"""
import subprocess
from pathlib import Path

PY = r"D:/WorkBuddy/2026-11-07-14-47-04/xiaoai-bridge/venv/Scripts/python.exe"
APP = r"D:/WorkBuddy/2026-11-07-14-47-04/tiansheng-app/app.py"
DETACHED = 0x00000008
NO_WINDOW = 0x08000000

log = open(Path(__file__).parent / "app_test.log", "a", encoding="utf-8")
p = subprocess.Popen(
    [PY, APP], cwd=str(Path(__file__).parent),
    stdout=log, stderr=subprocess.STDOUT,
    creationflags=DETACHED | NO_WINDOW,
)
print(f"已脱离启动 app.py, PID={p.pid}")
