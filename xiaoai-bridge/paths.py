# -*- coding: utf-8 -*-
"""
天声小爱 — 统一路径解析（后端脚本副本）
========================================
与 tiansheng-app/core/paths.py 功能一致，供 bridge 目录下的脚本
（codebuddy_hidden.py / box_bridge.py 等）独立 import。

本文件放在 bridge/ 目录里，bridge_dir() 即 __file__ 所在目录。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    if getattr(sys, "frozen", False):
        return True
    return os.environ.get("TIANSHENG_BUNDLED") == "1"


def bridge_dir() -> Path:
    """后端脚本根目录。本文件就在 bridge/ 下，直接用 __file__ 定位。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "bridge"
    return Path(__file__).resolve().parent


def _bundle_root() -> Path:
    """便携包根目录（app/ 与 bridge/ 的上一级）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return bridge_dir().parent


def app_root() -> Path:
    """GUI 程序根目录（含 web/ 与 core/）。"""
    if is_frozen():
        return _bundle_root() / "app"
    return bridge_dir().parent / "tiansheng-app"


def web_dir() -> Path:
    return app_root() / "web"


def runtime_dir() -> Path:
    if is_frozen():
        return _bundle_root() / "runtime"
    # 开发模式：优先用包内自带的 runtime，没有再回退到用户目录
    local = _bundle_root() / "runtime"
    if local.exists():
        return local
    return Path.home() / ".workbuddy" / "binaries"


def python_exe() -> Path:
    """后端脚本使用的 Python 解释器。优先包内自带，开发模式回退 venv。"""
    bundled = runtime_dir() / "python" / "python.exe"
    if bundled.exists():
        return bundled
    return bridge_dir() / "venv" / "Scripts" / "python.exe"


def site_packages_dir() -> Path:
    bundled = runtime_dir() / "python" / "Lib" / "site-packages"
    if bundled.exists():
        return bundled
    return python_exe().parent.parent / "Lib" / "site-packages"


def adb_exe() -> Path:
    return site_packages_dir() / "adbutils" / "binaries" / "adb.exe"


def _node_install() -> Path:
    """定位 node 安装目录：兼容「包内 runtime/node」与「用户目录按版本存放」。"""
    base = runtime_dir() / "node"
    if (base / "node.exe").exists():
        return base
    versions = base / "versions"
    found = sorted(versions.glob("*/node.exe")) if versions.exists() else []
    return found[-1].parent if found else base


def node_exe() -> Path:
    return _node_install() / "node.exe"


def codebuddy_cli() -> Path:
    return (
        _node_install() / "node_modules" / "@tencent-ai"
        / "codebuddy-code" / "dist" / "codebuddy.js"
    )


def config_file() -> Path:
    return bridge_dir() / "config.json"


def tui_port_file() -> Path:
    return bridge_dir() / "tui_port.txt"


def watch_parent(interval: float = 5.0) -> None:
    """看门狗：主程序（GUI）退出后，子服务自动退出，避免孤儿进程常驻。

    由 supervisor 启动子进程时注入环境变量 TIANSHENG_PARENT_PID，
    子进程在后台线程里定期检查父进程是否还活着；父进程没了就自杀。
    未注入该变量时（例如手工命令行调试）不做任何事。
    """
    import os
    import threading
    import time as _time

    ppid = os.environ.get("TIANSHENG_PARENT_PID", "").strip()
    if not ppid.isdigit():
        return
    parent = int(ppid)

    def _alive(pid: int) -> bool:
        try:
            import psutil

            return psutil.pid_exists(pid)
        except Exception:
            # psutil 不可用时退回 os.kill 探测（Windows 下 0 号信号不可用）
            return True

    def _watch():
        while True:
            _time.sleep(interval)
            if not _alive(parent):
                try:
                    print(f"[看门狗] 主程序({parent})已退出，本进程随之结束", flush=True)
                except Exception:
                    pass
                os._exit(0)

    threading.Thread(target=_watch, daemon=True).start()
