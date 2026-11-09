# -*- coding: utf-8 -*-
"""
天声小爱 — 统一路径解析（GUI 侧）
==================================
所有模块（GUI / 后端脚本）只通过这里拿路径，杜绝硬编码。

两种运行模式：
  1. 开发模式：tiansheng-app（GUI）+ xiaoai-bridge（后端）平级目录。
  2. 打包模式：launcher(exe) 注入环境变量 TIANSHENG_BUNDLED=1，
     便携目录结构为：
       天声小爱.exe
         ├── app/       （app.py + core/ + web/）
         ├── bridge/    （后端脚本 + config.json）
         └── runtime/{python,node}

关键：本文件由便携 Python 以 .py 形式运行（非 PyInstaller 冻结），
因此 __file__ 始终真实，app_root() 用 __file__ 推导即可。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    """是否运行在打包后的便携包环境里。"""
    if getattr(sys, "frozen", False):
        return True
    return os.environ.get("TIANSHENG_BUNDLED") == "1"


def app_root() -> Path:
    """GUI 程序根目录（含 web/ 与 core/）。

    __file__ = app_root/core/paths.py，故上两级即 app_root。
    开发模式= tiansheng-app，打包模式= 便携包/app。公式一致。
    """
    return Path(__file__).resolve().parent.parent


def _bundle_root() -> Path:
    """便携包根目录（app/ 与 bridge/ 的上一级）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return app_root().parent


def bridge_dir() -> Path:
    """后端脚本根目录（gateway/run_xiaogpt/box_bridge/codebuddy_hidden + config.json）。"""
    if is_frozen():
        return _bundle_root() / "bridge"
    return app_root().parent / "xiaoai-bridge"


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
    """看门狗：主程序退出后子服务自动结束（GUI 侧不使用，仅为与 bridge 侧一致）。"""
    import os
    import threading
    import time as _time

    ppid = os.environ.get("TIANSHENG_PARENT_PID", "").strip()
    if not ppid.isdigit():
        return
    parent = int(ppid)

    def _watch():
        while True:
            _time.sleep(interval)
            try:
                import psutil

                alive = psutil.pid_exists(parent)
            except Exception:
                alive = True
            if not alive:
                os._exit(0)

    threading.Thread(target=_watch, daemon=True).start()
