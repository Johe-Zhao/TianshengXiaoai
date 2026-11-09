# -*- coding: utf-8 -*-
"""
天声小爱 - 进程清理
==================
杀掉所有相关进程（应用自身、网关、监听、无窗口 CodeBuddy、PTY 内的 node）。
被 stop.bat 和 start.bat 调用，确保每次启动都是干净环境。
"""

import sys
import time
from pathlib import Path

import psutil

SCRIPTS = {"app.py", "gateway.py", "run_xiaogpt.py", "codebuddy_hidden.py"}
NODE_MARK = "codebuddy-code"


def targets() -> list:
    found = []
    me = psutil.Process().pid
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if proc.info["pid"] == me:
                continue
            name = (proc.info.get("name") or "").lower()
            cmdline = proc.info.get("cmdline") or []
            if name.startswith("python") and len(cmdline) >= 2:
                arg1 = cmdline[1].replace("\\", "/").split("/")[-1].lower()
                if arg1 in SCRIPTS:
                    found.append(proc)
                    continue
            if name == "node.exe":
                joined = " ".join(cmdline)
                if NODE_MARK in joined:
                    found.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return found


def main() -> int:
    procs = targets()
    if not procs:
        print("没有发现运行中的天声小爱进程")
        return 0

    print(f"发现 {len(procs)} 个相关进程，正在停止 …")
    for p in procs:
        try:
            for child in p.children(recursive=True):
                try:
                    child.kill()
                except Exception:
                    pass
            p.kill()
            print(f"  已停止 PID {p.pid}")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        except Exception:
            pass

    # 等待真正退出
    for _ in range(15):
        time.sleep(0.3)
        if not targets():
            print("全部进程已退出")
            return 0
    print("警告：仍有残留进程")
    return 1


if __name__ == "__main__":
    sys.exit(main())
