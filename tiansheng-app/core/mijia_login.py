# -*- coding: utf-8 -*-
"""
天声小爱 - 米家账号登录
======================
在独立 WebView 窗口里完成小米账号登录，自动提取凭据并换取长期 token。
用户不需要再用浏览器抓 cookie。

流程：
  1. 打开小米账号页面，用户自行扫码/密码登录
  2. 程序轮询 WebView cookie，检测到 passToken 即视为登录成功
  3. 用"浏览器特征模拟"方式静默换 micoapi / xiaomiio 长期 token
  4. 写入 ~/.mi.token（xiaogpt 使用）和 config.json（记录账号信息）
"""

from __future__ import annotations

import asyncio
import json
import ssl
import sys
import threading
import time
from http.cookies import SimpleCookie
from pathlib import Path

from core.paths import bridge_dir, config_file

BRIDGE_DIR = bridge_dir()
CONFIG = config_file()
TOKEN_PATH = Path.home() / ".mi.token"

LOGIN_URL = "https://account.xiaomi.com/"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

STATE = {"status": "idle", "message": "", "userId": ""}


def install_guard() -> None:
    """启用凭据守护（bridge/mi_guard.py）。

    miservice 在登录失败 / 401 时会 `save_token()` 无参数调用，
    把整个 ~/.mi.token 删掉（连带长期 passToken），导致"账号被退出"。
    守护模块把它改成"只清短期 token，保留长期凭据"。幂等，可重复调用。
    """
    p = str(BRIDGE_DIR)
    if p not in sys.path:
        sys.path.insert(0, p)
    try:
        import mi_guard

        mi_guard.apply()
    except Exception as e:
        print(f"[登录] 凭据守护未启用: {e}")


# ---------------- token 换取 ----------------
def _patch_ssl():
    import aiohttp

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    _orig = aiohttp.TCPConnector.__init__

    def _patched(self, *args, **kwargs):
        kwargs["ssl"] = ctx
        return _orig(self, *args, **kwargs)

    aiohttp.TCPConnector.__init__ = _patched


async def _exchange(cookies: dict) -> tuple[bool, str]:
    """用 WebView 拿到的 cookie 换长期 token（模拟浏览器特征，避免风控）。"""
    _patch_ssl()
    install_guard()          # 必须在 MiAccount 之前：登录失败时不能删凭据文件
    import aiohttp
    from aiohttp import CookieJar
    from miservice import MiAccount
    from yarl import URL

    pass_token = cookies.get("passToken", "")
    user_id = cookies.get("userId", "")
    device_id = cookies.get("deviceId", "")
    if not pass_token or not user_id:
        return False, "未获取到登录凭据（passToken/userId 缺失）"

    # 先写 passToken 到 token store，供 MiAccount 静默续期
    TOKEN_PATH.write_text(
        json.dumps({
            "deviceId": device_id,
            "passToken": pass_token,
            "userId": int(user_id),
        }),
        encoding="utf-8",
    )

    cfg = json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}

    try:
        async with aiohttp.ClientSession(cookie_jar=CookieJar()) as session:
            session.cookie_jar.update_cookies(cookies, response_url=URL(LOGIN_URL))

            account = MiAccount(
                session,
                cfg.get("account", ""),
                cfg.get("password", ""),
                str(TOKEN_PATH),
            )
            _orig_request = account.request

            def _ua_request(url, method="GET", **kwargs):
                headers = kwargs.pop("headers", {}) or {}
                headers.setdefault("User-Agent", BROWSER_UA)
                return _orig_request(url, method=method, headers=headers, **kwargs)

            account.request = _ua_request

            if not await account.login("micoapi"):
                return False, "换取语音服务 token 失败（登录态可能已失效，请重试）"
            await account.login("xiaomiio")
    except Exception as e:
        return False, f"换取 token 出错：{str(e)[:120]}"

    token = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
    if "micoapi" not in token:
        return False, "token 换取不完整，请重试"

    # 记录账号信息，供界面显示
    # 注意：**不再把完整 cookie 写进 config.json** —— 里面是明文登录态凭据
    # （passToken/serviceToken），config 常被打包分发，落盘等于泄露。
    # 主程序只用 ~/.mi.token，不读 config 里的 cookie。
    cfg["mi_user_id"] = str(user_id)
    cfg["mi_login_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return True, "登录成功"


# ---------------- WebView 登录窗口 ----------------
def _cookie_dict(window) -> dict:
    """把 pywebview 返回的 cookie 统一成 {name: value}。

    **这里是「扫码成功但程序一直等待登录」的根因**：
    WebView2 后端返回的是 `list[SimpleCookie]` —— 每个元素是一个 SimpleCookie，
    键是 cookie 名、值是 Morsel（`.value` 才是值），**它没有 `.name` 属性**。
    旧代码按 `c.name / c.value` 读取，全部被 except 吞掉，结果永远返回空字典，
    于是用户已经登录进小米主页，程序却一直「等待登录中」直到 5 分钟超时。

    兼容三种形态：SimpleCookie / 带 .name.value 的对象 / 原始 cookie 字符串。
    """
    try:
        raw = window.get_cookies() or []
    except Exception as e:
        print(f"[登录] 读取 cookie 失败: {e}")
        return {}

    out = {}
    for item in raw:
        try:
            if hasattr(item, "name") and hasattr(item, "value"):
                out[item.name] = item.value
            elif hasattr(item, "keys"):
                for k in item.keys():
                    morsel = item[k]
                    out[k] = getattr(morsel, "value", str(morsel))
            elif isinstance(item, str):
                sc = SimpleCookie(item)
                for k in sc.keys():
                    out[k] = sc[k].value
        except Exception:
            continue
    return out


def _close_window(window) -> None:
    """检测完成后关闭登录窗口（子线程调用，多试几种方式兜底）。"""
    for action in ("destroy", "hide"):
        try:
            getattr(window, action)()
            return
        except Exception as e:
            print(f"[登录] 关闭窗口({action})失败: {e}")
    try:
        window.evaluate_js("window.close()")
    except Exception:
        pass


def _watch(window, on_done):
    """轮询 WebView cookie，检测登录完成；成功/失败/超时都自动关闭窗口。"""
    deadline = time.time() + 300  # 5 分钟
    last_names = None

    while time.time() < deadline:
        if STATE["status"] == "cancelled":
            return
        time.sleep(2)
        cookies = _cookie_dict(window)

        # 只打印字段名、绝不打印值（诊断用，避免凭据进日志）
        names = tuple(sorted(cookies.keys()))
        if names and names != last_names:
            print(f"[登录] 检测到 cookie 字段: {list(names)}")
            last_names = names

        # 兜底：个别情况下 userId 只出现在 cUserId
        if cookies.get("passToken") and not cookies.get("userId"):
            if cookies.get("cUserId"):
                cookies["userId"] = cookies["cUserId"]

        # 未登录时小米会种一个空 passToken 占位，必须判非空
        if cookies.get("passToken") and cookies.get("userId"):
            STATE["message"] = "已检测到登录，正在换取长期凭据 …"
            ok, msg = asyncio.run(_exchange(cookies))
            STATE["status"] = "ok" if ok else "fail"
            STATE["message"] = msg
            STATE["userId"] = cookies.get("userId", "")
            _close_window(window)
            if on_done:
                try:
                    on_done(ok, msg)
                except Exception:
                    pass
            return

    STATE["status"] = "timeout"
    STATE["message"] = "登录超时：未检测到登录凭据，请确认已在窗口中完成扫码/登录"
    _close_window(window)


def start_login(on_done=None) -> bool:
    """打开登录窗口。返回是否成功创建（True 表示窗口已弹出）。"""
    import webview

    if STATE["status"] == "pending":
        return False

    STATE.update(status="pending", message="", userId="")
    try:
        window = webview.create_window(
            "小米账号登录 — 天声小爱",
            LOGIN_URL,
            width=720,
            height=800,
            background_color="#ffffff",
        )
    except Exception as e:
        STATE.update(status="fail", message=f"无法创建登录窗口：{e}")
        return False

    threading.Thread(target=_watch, args=(window, on_done), daemon=True).start()
    return True


def cancel_login():
    if STATE["status"] == "pending":
        STATE["status"] = "cancelled"
        STATE["message"] = "已取消"
