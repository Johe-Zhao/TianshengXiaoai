# -*- coding: utf-8 -*-
"""
无窗口 CodeBuddy 服务守护进程（含登录控制接口）
================================================
原理：用 Windows ConPTY 伪终端运行 CodeBuddy TUI —— 它有完整终端语义
（能正常启动、保留登录态、暴露 REST API），但没有任何可见窗口。

额外提供控制通道（:55101）供天声小爱调用：
  GET  /status  -> {"ready": bool, "logged_in": bool, "pid": int}
  GET  /screen  -> 纯文本屏幕快照（去 ANSI）
  POST /login   -> 向 TUI 发送 /login 指令
  POST /input   -> {"text": "..."} 向 TUI 写入任意输入
"""

import json
import os
import re
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from winpty import PtyProcess

from paths import bridge_dir, codebuddy_cli, node_exe

NODE = str(node_exe())
CBC = str(codebuddy_cli())
WORKDIR = str(bridge_dir())
PORT = 55100          # CodeBuddy REST API
CTL_PORT = 55101      # 控制接口
PORT_FILE = Path(WORKDIR) / "tui_port.txt"
LOG_FILE = Path(WORKDIR) / "codebuddy_hidden.log"

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# 全局状态（供控制接口读取）
_buf = {"screen": "", "proc": None, "started": time.time()}
_auth_cache = {"at": 0.0, "value": False, "source": ""}
_ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][B0]|\r")

# 未登录 / 等待授权时 TUI 屏幕上可能出现的提示（仅在官方端点不可用时兜底）
_NOT_LOGGED_IN = (
    "选择登录", "sign in", "login with", "登录方式", "授权登录",
    "not logged in", "authentication required", "waiting for auth",
    "failed to login", "press esc to return to login", "select login",
    "login method", "please log in", "登录后使用",
)


def log(msg: str):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def drain(proc: PtyProcess, stop: threading.Event):
    """持续读取 PTY 输出，防止缓冲区填满阻塞子进程；同时留存屏幕快照。"""
    pty_log = Path(WORKDIR) / "codebuddy_pty.log"
    while not stop.is_set():
        try:
            if not proc.isalive():
                break
            chunk = proc.read(4096)
            if chunk:
                with open(pty_log, "a", encoding="utf-8", errors="replace") as f:
                    f.write(chunk)
                # 维护最近屏幕文本（去 ANSI，限制长度）
                clean = _ANSI.sub("", chunk)
                _buf["screen"] = (_buf["screen"] + clean)[-12000:]
        except EOFError:
            break
        except Exception:
            time.sleep(0.5)


def health(port: int) -> bool:
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/v1/health",
            headers={"x-codebuddy-request": "1"},
        )
        with _opener.open(req, timeout=4) as r:
            return json.loads(r.read()).get("data", {}).get("status") == "ok"
    except Exception:
        return False


def account_logged_in():
    """用 CodeBuddy 官方端点查**账号**登录态，返回 True/False；不可用时返回 None。

    注意区分两个端点：
      /api/v1/auth/status         -> 本地 REST API 的鉴权开关，恒为 authenticated=true，
                                     与账号是否登录无关（不能拿它判断）
      /api/v1/auth/account/status -> 账号登录态，data.authenticated 才是真值
    """
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{PORT}/api/v1/auth/account/status",
            headers={"x-codebuddy-request": "1"},
        )
        with _opener.open(req, timeout=4) as r:
            data = json.loads(r.read())
        val = (data.get("data") or {}).get("authenticated")
        return bool(val) if isinstance(val, bool) else None
    except Exception:
        return None


def _screen_logged_in() -> bool:
    """兜底：官方端点不可用（旧版本/未就绪）时，用屏幕关键词判断。"""
    s = _buf["screen"]
    if not s:
        return False
    tail = s[-3000:].lower()
    return not any(m in tail for m in _NOT_LOGGED_IN)


def auth_state():
    """返回 (是否登录, 判据来源)。2 秒缓存，避免前端高频轮询打爆 API。"""
    now = time.time()
    if now - _auth_cache["at"] < 2.0:
        return _auth_cache["value"], _auth_cache["source"]
    api = account_logged_in()
    if api is not None:
        val, src = api, "api"
    else:
        val, src = _screen_logged_in(), "screen"
    _auth_cache.update(at=now, value=val, source=src)
    return val, src


def looks_logged_in() -> bool:
    return auth_state()[0]


class CtlHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/status"):
            logged, src = auth_state()
            self._json({
                "ready": health(PORT),
                "logged_in": logged,
                "auth_source": src,       # api=官方端点实测 / screen=屏幕关键词兜底
                "pid": _buf["proc"].pid if _buf["proc"] else 0,
                "uptime": int(time.time() - _buf["started"]),
            })
        elif self.path.startswith("/screen"):
            self._json({"screen": _buf["screen"][-6000:]})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            body = json.loads(raw or "{}")
        except Exception:
            body = {}

        proc = _buf["proc"]
        if not proc or not proc.isalive():
            return self._json({"ok": False, "msg": "CodeBuddy 未运行"}, 400)

        if self.path.startswith("/login"):
            # 优先用官方 REST API 发起登录：不依赖 TUI 当前停在哪一屏。
            # 退出登录后 TUI 常停在 "Failed to login" 界面，此时发 /login 命令可能无效。
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{PORT}/api/v1/auth/account/login",
                    data=b"{}",
                    headers={"x-codebuddy-request": "1",
                             "Content-Type": "application/json"},
                    method="POST",
                )
                with _opener.open(req, timeout=25) as r:
                    data = json.loads(r.read())
                auth_url = (data.get("data") or {}).get("authUrl") or ""
                if auth_url:
                    self._json({"ok": True, "auth_url": auth_url, "source": "api"})
                    return
                log("官方登录 API 未返回 authUrl，回退 TUI /login")
            except Exception as e:
                log(f"官方登录 API 调用失败，回退 TUI /login: {e}")

            # 兜底：向 TUI 发 /login（先清屏幕，避免抓到上一次的过期链接）
            try:
                _buf["screen"] = ""
                proc.write("/login\r")
                time.sleep(1.2)
                proc.write("\r")          # 有些版本需要回车确认第一项
                self._json({"ok": True, "source": "tui",
                            "screen": _buf["screen"][-1500:]})
            except Exception as e:
                self._json({"ok": False, "msg": str(e)}, 500)

        elif self.path.startswith("/logout"):
            # 官方退出登录端点；清掉状态缓存让界面立刻反映未登录
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{PORT}/api/v1/auth/account/logout",
                    data=b"{}",
                    headers={"x-codebuddy-request": "1",
                             "Content-Type": "application/json"},
                    method="POST",
                )
                with _opener.open(req, timeout=10) as r:
                    data = json.loads(r.read())
                _auth_cache["at"] = 0.0
                ok = bool((data.get("data") or {}).get("success"))
                self._json({"ok": ok, "logged_in": auth_state()[0]})
            except Exception as e:
                self._json({"ok": False, "msg": str(e)}, 500)

        elif self.path.startswith("/input"):
            text = body.get("text", "")
            try:
                proc.write(text)
                self._json({"ok": True})
            except Exception as e:
                self._json({"ok": False, "msg": str(e)}, 500)
        else:
            self._json({"error": "not found"}, 404)


def start_ctl_server():
    try:
        ThreadingHTTPServer.allow_reuse_address = True
        srv = ThreadingHTTPServer(("127.0.0.1", CTL_PORT), CtlHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        log(f"控制接口已启动: http://127.0.0.1:{CTL_PORT}")
    except Exception as e:
        # 端口被占（多为上一个实例尚未释放）：不影响 CodeBuddy 本体运行
        log(f"控制接口启动失败（端口 {CTL_PORT} 被占用）: {e}")


def main():
    from paths import watch_parent

    watch_parent()
    log("启动 CodeBuddy（ConPTY 伪终端，无窗口）...")
    # 阻止 CLI 自动打开系统浏览器：授权页由天声小爱的 WebView 弹窗承载，
    # 否则点一次「登录」会同时冒出弹窗 + 系统浏览器两个窗口。
    # CodeBuddy 的 UrlUtils.openUrl() 在 win32 下会读 process.env.BROWSER：
    # 有值就把它当命令执行；指向一个不存在的程序 → openUrl 返回 false，
    # 而 CLI 对此的处理是「keep polling so manual login can still complete」，
    # 即继续轮询登录态，WebView 里完成授权后照样能拿到 token。
    env = dict(os.environ)
    env["BROWSER"] = "__tiansheng_no_external_browser__"
    proc = PtyProcess.spawn(
        [NODE, CBC, "--port", str(PORT)],
        dimensions=(36, 140),
        env=env,
    )
    _buf["proc"] = proc
    log(f"已启动, PTY pid={proc.pid}")

    stop = threading.Event()
    threading.Thread(target=drain, args=(proc, stop), daemon=True).start()
    start_ctl_server()

    # 等待服务就绪（固定端口），成功后告知网关
    for _ in range(45):
        time.sleep(1)
        if health(PORT):
            PORT_FILE.write_text(str(PORT), encoding="utf-8")
            log(f"服务就绪, API 端口={PORT}（已写入 {PORT_FILE.name}）")
            break
    else:
        log("警告：等待服务就绪超时，进程仍在运行")

    # 常驻守护：进程退出则重启
    while True:
        time.sleep(5)
        if not proc.isalive():
            log("CodeBuddy 进程退出，5 秒后重启")
            stop.set()
            time.sleep(5)
            return main()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
