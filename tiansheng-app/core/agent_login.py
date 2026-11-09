# -*- coding: utf-8 -*-
"""
天声小爱 — AI 后端（CodeBuddy / WorkBuddy）登录窗口
==================================================
仿小米登录：弹出 WebView 窗口加载授权页，后台轮询官方账号状态端点，
检测到登录成功即自动关闭窗口；超时或用户手动关窗也会正确收尾。

与 mijia_login 的区别：这里不靠 cookie 判断，而是由调用方传入 `check()`，
内部走 CodeBuddy 的 /api/v1/auth/account/status 实测登录态。
"""

from __future__ import annotations

import threading
import time

STATE = {"status": "idle", "message": "", "auth_url": ""}

_TIMEOUT = 300          # 5 分钟
_POLL = 2               # 2 秒轮询一次


def _close_window(window) -> None:
    for action in ("destroy", "hide"):
        try:
            getattr(window, action)()
            return
        except Exception:
            continue
    try:
        window.evaluate_js("window.close()")
    except Exception:
        pass


def start_login(auth_url: str, check, on_done=None) -> bool:
    """打开授权窗口。`check` 为无参回调，返回是否已登录。"""
    import webview

    if not auth_url or STATE["status"] == "pending":
        return False
    STATE.update(status="pending", message="", auth_url=auth_url)

    try:
        window = webview.create_window(
            "AI 后端登录 — 天声小爱",
            auth_url,
            width=720,
            height=800,
            background_color="#ffffff",
        )
    except Exception as e:
        STATE.update(status="fail", message=f"无法创建登录窗口：{e}")
        return False

    def on_closed():
        # 用户手动关窗 = 取消；自动关闭时 status 已是 ok/timeout，不覆盖
        if STATE["status"] == "pending":
            STATE["status"] = "cancelled"
            STATE["message"] = "已取消登录"

    try:
        window.events.closed += on_closed
    except Exception:
        pass

    threading.Thread(target=_watch, args=(window, check, on_done), daemon=True).start()
    return True


def _watch(window, check, on_done) -> None:
    deadline = time.time() + _TIMEOUT
    while time.time() < deadline:
        if STATE["status"] in ("cancelled", "ok"):
            return
        time.sleep(_POLL)
        try:
            if check():
                STATE.update(status="ok", message="登录成功，授权窗口已关闭")
                _close_window(window)
                if on_done:
                    try:
                        on_done(True)
                    except Exception:
                        pass
                return
        except Exception:
            pass
    STATE.update(status="timeout", message="登录超时（5 分钟未完成）")
    _close_window(window)


def cancel_login() -> None:
    if STATE["status"] == "pending":
        STATE.update(status="cancelled", message="已取消登录")
