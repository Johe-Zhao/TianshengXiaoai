# -*- coding: utf-8 -*-
"""
用浏览器 cookie 静默换取 micoapi serviceToken（模拟浏览器特征版）
================================================================
关键点：
- deviceId / passToken / userId 等全部沿用浏览器抓包原值
- 请求带上完整浏览器 cookie + 浏览器 UA，特征与真浏览器一致
- serviceLogin(sid=micoapi) 携带 passToken 时应静默通过，无需短信
"""

import asyncio
import json
import ssl
from http.cookies import SimpleCookie
from pathlib import Path
from yarl import URL

import aiohttp
from aiohttp import CookieJar
from miservice import MiAccount

TOKEN_PATH = Path.home() / ".mi.token"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def make_ssl_patched_session() -> aiohttp.ClientSession:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    _orig = aiohttp.TCPConnector.__init__

    def _patched(self, *args, **kwargs):
        kwargs["ssl"] = ctx
        return _orig(self, *args, **kwargs)

    aiohttp.TCPConnector.__init__ = _patched
    return aiohttp.ClientSession(cookie_jar=CookieJar())


def _ua_wrapped(request_fn):
    def wrapper(url, method="GET", **kwargs):
        headers = kwargs.pop("headers", {}) or {}
        headers.setdefault("User-Agent", BROWSER_UA)
        return request_fn(url, method=method, headers=headers, **kwargs)
    return wrapper


async def main():
    cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))
    sc = SimpleCookie()
    sc.load(cfg.get("cookie", ""))
    browser_cookies = {k: m.value for k, m in sc.items()}
    print("浏览器 cookie 字段:", ", ".join(browser_cookies.keys()))

    token_path = str(TOKEN_PATH)
    # token store 里先放浏览器的设备指纹与 passToken
    TOKEN_PATH.write_text(json.dumps({
        "deviceId": browser_cookies.get("deviceId", ""),
        "passToken": browser_cookies["passToken"],
        "userId": int(browser_cookies["userId"]),
    }))
    print(f"已注入浏览器 passToken + deviceId 到 {TOKEN_PATH}")

    session = make_ssl_patched_session()
    try:
        # 预置完整浏览器 cookie 到 account.xiaomi.com 域
        session.cookie_jar.update_cookies(
            browser_cookies,
            response_url=URL("https://account.xiaomi.com/"),
        )

        account = MiAccount(
            session,
            cfg.get("account", ""),
            cfg.get("password", ""),
            token_path,
        )
        # 换浏览器 UA（miservice 默认 UA 可能被风控）
        account.request = _ua_wrapped(account.request)
        ok = await account.login("micoapi")
        if not ok:
            err = getattr(account, "_login_error", "")
            raise SystemExit(f"静默换取失败: {err}")

        token = json.loads(TOKEN_PATH.read_text())
        print("micoapi serviceToken 换取成功" if "micoapi" in token else "token 异常")
        if "micoapi" in token:
            cfg.pop("cookie", None)
            json.dump(cfg, open("config.json", "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)
            print("cookie 字段已移除，xiaogpt 将使用标准 token 模式")
    finally:
        await session.close()


if __name__ == "__main__":
    asyncio.run(main())
