# -*- coding: utf-8 -*-
"""
天声小爱 - 服务进程管理器
=========================
统一管理三个子进程：CodeBuddy（ConPTY 无窗口）/ 语音网关 / 小米云监听。

关键设计：
  - 所有子进程 CREATE_NO_WINDOW 启动，退出时用进程树杀
  - 启动前清理同类残留进程，停止后确认真正退出
    （否则换音色重启时会堆积多个 xiaogpt，它们用不同音色同时播报，
      表现为"前后音色不一致"）
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

import psutil

from core.paths import adb_exe, bridge_dir, config_file, python_exe

BRIDGE_DIR = bridge_dir()
PY = python_exe()
CONFIG = config_file()
TUI_PORT_FILE = BRIDGE_DIR / "tui_port.txt"

CREATE_NO_WINDOW = 0x08000000
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _http_get(url: str, timeout: float = 3.0, headers: dict | None = None) -> dict | None:
    try:
        req = urllib.request.Request(url, headers=headers or {})
        with _opener.open(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _ps(script: str) -> str:
    """执行一段 PowerShell 并返回 stdout（仅本机内部使用）。"""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            creationflags=CREATE_NO_WINDOW, timeout=25,
        )
        return (r.stdout or "").strip()
    except Exception:
        return ""


class Service:
    def __init__(self, name: str, args: list[str], label: str, env_provider=None):
        self.name = name
        self.args = args
        self.label = label
        self.proc: subprocess.Popen | None = None
        self.started_at: float | None = None
        self.env_provider = env_provider  # () -> dict，启动时动态取额外环境变量

    # ---------- 命令行特征匹配 ----------
    @property
    def pattern(self) -> str:
        return self.args[0].replace(".py", "")

    def _matching_pids(self) -> list[int]:
        """枚举真正运行本服务的 python 进程。

        严格匹配：python 解释器之后的第一个参数必须是目标脚本文件名。
        这样可以把 `python -c "...run_xiaogpt..."` 之类的内联脚本排除在外。
        """
        pids = []
        script = self.args[0].lower()
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                name = (proc.info.get("name") or "").lower()
                if not name.startswith("python"):
                    continue
                cmdline = proc.info.get("cmdline") or []
                if len(cmdline) < 2:
                    continue
                first_arg = cmdline[1].replace("\\", "/").split("/")[-1].lower()
                if first_arg == script:
                    pids.append(proc.info["pid"])
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        return pids

    def kill_orphans(self) -> int:
        """杀掉所有命令行匹配本服务的进程（含游离残留），返回数量。"""
        pids = self._matching_pids()
        for pid in pids:
            try:
                proc = psutil.Process(pid)
                for child in proc.children(recursive=True):
                    try:
                        child.kill()
                    except Exception:
                        pass
                proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            except Exception:
                pass
        return len(pids)

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc and self.proc.poll() is None else None

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    # ---------- 生命周期 ----------
    def start(self) -> bool:
        if self.alive():
            return True
        # 启动前清残留：绝不允许多实例并存
        self.kill_orphans()
        time.sleep(0.6)

        log = BRIDGE_DIR / f"{self.name}_ts.log"
        try:
            f = open(log, "a", encoding="utf-8", errors="replace")
            f.write(f"\n===== start {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
            f.flush()
            env = dict(os.environ)
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"
            env["PYTHONUNBUFFERED"] = "1"
            # 看门狗：主程序退出后子服务自动结束，避免孤儿进程常驻
            env["TIANSHENG_PARENT_PID"] = str(os.getpid())
            if self.env_provider:
                try:
                    env.update(self.env_provider() or {})
                except Exception as e:
                    print(f"[supervisor] {self.name} 环境变量注入失败: {e}")
            self.proc = subprocess.Popen(
                [str(PY)] + self.args,
                cwd=str(BRIDGE_DIR),
                stdout=f,
                stderr=subprocess.STDOUT,
                env=env,
                creationflags=CREATE_NO_WINDOW,
            )
            self.started_at = time.time()
            return True
        except Exception as e:
            print(f"[supervisor] {self.name} 启动失败: {e}")
            return False

    def stop(self, verify: bool = True) -> None:
        if self.proc:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                    capture_output=True, creationflags=CREATE_NO_WINDOW,
                )
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.proc = None
        self.started_at = None

        # 兜底清理 + 确认死亡（最多等 3 秒）
        self.kill_orphans()
        if verify:
            for _ in range(10):
                if not self._matching_pids():
                    break
                time.sleep(0.3)


class Supervisor:
    def __init__(self):
        self.codebuddy = Service("codebuddy", ["codebuddy_hidden.py"], "CodeBuddy 后端")
        self.gateway = Service("gateway", ["gateway.py"], "语音网关")
        self.xiaogpt = Service(
            "xiaogpt",
            ["run_xiaogpt.py", "--config", "config.json"],
            "小米云监听",
            env_provider=self._xiaogpt_env,
        )
        self.boxbridge = Service("boxbridge", ["box_bridge.py"], "米盒语音")
        self._order = [self.codebuddy, self.gateway, self.xiaogpt, self.boxbridge]

    @staticmethod
    def _xiaogpt_env() -> dict:
        """把可配置项从 config.json 注入 xiaogpt 子进程。"""
        cfg = load_config()
        transition = cfg.get("transition", "正在问天声小爱，请稍等")
        return {"TIANSHENG_TRANSITION": transition if transition is not None else ""}

    # ---------- 生命周期 ----------
    def start_all(self) -> dict:
        result = {}
        result["codebuddy"] = self.codebuddy.start()
        for _ in range(45):
            if self.codebuddy_ready():
                break
            time.sleep(1)
        result["gateway"] = self.gateway.start()
        time.sleep(2)
        result["xiaogpt"] = self.xiaogpt.start()
        # 盒子桥接依赖网关，最后启动；盒子没连上也只是它自己报错，不影响其他服务
        result["boxbridge"] = self.boxbridge.start()
        return result

    def stop_all(self) -> None:
        for svc in reversed(self._order):
            svc.stop()

    def restart_listener(self) -> None:
        """改音色/唤醒词/人设后重启监听（严格保证只有一个实例）。"""
        self.xiaogpt.stop(verify=True)
        time.sleep(1)
        self.xiaogpt.start()

    # ---------- 状态 ----------
    def codebuddy_port(self) -> int:
        try:
            return int(TUI_PORT_FILE.read_text(encoding="utf-8").strip())
        except Exception:
            return 0

    def codebuddy_ready(self) -> bool:
        port = self.codebuddy_port()
        if not port:
            return False
        data = _http_get(
            f"http://127.0.0.1:{port}/api/v1/health",
            headers={"x-codebuddy-request": "1"},
        )
        return bool(data and data.get("data", {}).get("status") == "ok")

    def gateway_ready(self) -> bool:
        data = _http_get("http://127.0.0.1:8008/health")
        return bool(data and data.get("gateway") == "ok")

    def listener_count(self) -> int:
        """实际运行的监听服务个数（去重父子进程）。

        venv 的 python.exe 是 shim，会再 spawn 一个真实解释器，
        所以同一服务会出现"父子两个同命令行进程"，这里只算顶层。
        """
        pids = set(self.xiaogpt._matching_pids())
        if not pids:
            return 0
        top = 0
        for pid in pids:
            try:
                ppid = psutil.Process(pid).ppid()
            except Exception:
                continue
            if ppid not in pids:
                top += 1
        return top

    def status(self) -> dict:
        return {
            "running": any(s.alive() for s in self._order),
            "services": [
                {
                    "name": s.name,
                    "label": s.label,
                    "alive": s.alive(),
                    "pid": s.pid,
                    "uptime": int(time.time() - s.started_at) if s.started_at and s.alive() else 0,
                }
                for s in self._order
            ],
            "codebuddy_api": self.codebuddy_ready(),
            "gateway_api": self.gateway_ready(),
            "listener_count": self.listener_count(),
            "box": self.box_status(),
        }

    _box_cache: dict = {"at": 0.0, "connected": False}

    @staticmethod
    def box_serial() -> str:
        """盒子的 ADB 地址（从配置读，不再写死；空=尚未发现）。"""
        ip = str(load_config().get("box_ip") or "").strip()
        return f"{ip}:5555" if ip else ""

    def box_status(self) -> dict:
        """小米盒子状态：本机 ADB 连接 + 桥接进程（ADB 查询带 10 秒缓存）。"""
        connected = self._box_cache["connected"]
        if time.time() - self._box_cache["at"] > 10:
            connected = self._probe_box()
            self._box_cache = {"at": time.time(), "connected": connected}
        return {
            "alive": self.boxbridge.alive(),
            "adb_connected": connected,
            "serial": self.box_serial(),
        }

    def _probe_box(self) -> bool:
        """探测盒子是否真的可操作。

        注意：`adb devices` 只反映「连接记录」——盒子休眠/WiFi 断过之后，
        adb 仍会保留一条显示为 device 的僵尸连接，此时 shell 通道其实已关闭。
        所以列表显示在线后，必须再用 shell 实测一次。
        """
        SERIAL = self.box_serial()
        if not SERIAL:
            return False
        try:
            import subprocess as _sp

            adb_bin = adb_exe()
            if not adb_bin.exists():
                return False

            # 1) 先看列表（离线时能快速返回）
            r = _sp.run(
                [str(adb_bin), "devices"],
                capture_output=True, text=True, timeout=5,
                encoding="utf-8", errors="replace",
                creationflags=CREATE_NO_WINDOW,
            )
            if f"{SERIAL}\tdevice" not in (r.stdout or ""):
                return False

            # 2) 列表在线 → 用 shell 验证通道真的可用（僵尸连接会在这里失败）
            r2 = _sp.run(
                [str(adb_bin), "-s", SERIAL, "shell", "echo", "probe_ok"],
                capture_output=True, text=True, timeout=6,
                encoding="utf-8", errors="replace",
                creationflags=CREATE_NO_WINDOW,
            )
            return "probe_ok" in (r2.stdout or "")
        except Exception:
            return False


def load_config() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(updates: dict) -> dict:
    cfg = load_config()
    cfg.update(updates)
    CONFIG.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return cfg


supervisor = Supervisor()
