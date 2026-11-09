# -*- coding: utf-8 -*-
"""
xiaogpt 启动器（SSL 兼容补丁 + mi_did 自动补全）
================================================
本机代理（TUN/全局模式）会拦截到小米服务器的 TLS 连接，导致
CERTIFICATE_VERIFY_FAILED。这里在启动 xiaogpt 前给 aiohttp 打补丁，
跳过证书校验（仅本机使用，风险可接受）。

另外，xiaogpt 在 mi_did 为空时会调用 xiaomiio 的 /home/device_list 自动发现设备，
而该接口容易被小米风控拦截（code -127 client withdraw）。因此这里先用
micoapi(MiNA) 的 device_list 拿到 miotDID 写回配置，让 xiaogpt 直接使用，
既绕开风控，也让用户无需手填设备 ID。
"""

import asyncio
import json
import os
import ssl
from pathlib import Path


def patch_aiohttp_no_verify():
    import aiohttp

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    _orig = aiohttp.TCPConnector.__init__

    def patched(self, *args, **kwargs):
        kwargs["ssl"] = ctx
        return _orig(self, *args, **kwargs)

    aiohttp.TCPConnector.__init__ = patched


def _ensure_mi_did():
    """确保配置里有音箱标识（mi_did 给 xiaogpt、device_id 给播报）。

    xiaogpt 在 mi_did 为空时会调 xiaomiio 的 /home/device_list 自动发现设备，
    而该接口容易被小米风控拦截（code -127 client withdraw）。所以先用
    micoapi(MiNA) 的 device_list 拿到 miotDID 与 deviceID 写回配置，
    既绕开风控，也让用户无需手填设备信息。
    """
    try:
        from devices import load_cfg, resolve_speaker
    except Exception as e:
        print(f"[did] 设备模块不可用: {e}")
        return

    cfg = load_cfg()
    if cfg.get("mi_did") and cfg.get("speaker_device_id"):
        return
    resolve_speaker(cfg)


if __name__ == "__main__":
    from paths import watch_parent

    watch_parent()
    patch_aiohttp_no_verify()

    # 凭据守护：xiaogpt 长期运行，401 一旦触发就会把 ~/.mi.token 删掉，
    # 表现为"用着用着账号被退出"。必须先于任何小米会话建立打上补丁。
    try:
        import mi_guard

        mi_guard.apply()
    except Exception as e:
        print(f"[凭据守护] 启用失败: {e}")

    # 未登录时绝不启动：xiaogpt 会拿 config 里的账号密码去登录，触发小米短信验证码，
    # 而后台进程没有 stdin 读不到验证码，只会崩溃 + 白白骚扰用户手机。
    if not (Path.home() / ".mi.token").exists():
        print("[监听] 小米账号未登录（~/.mi.token 缺失），跳过启动；请在界面点「重新登录」")
        raise SystemExit(0)

    _ensure_mi_did()
    from xiaogpt.cli import main

    main()
