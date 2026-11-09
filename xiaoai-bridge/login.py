# -*- coding: utf-8 -*-
"""
小米账号一次性登录（解决短信验证码）
====================================
运行后会提示输入手机短信验证码，成功后 token 存入 ~/.mi.token，
之后 xiaogpt 直接复用，不再要求验证码。
"""

import asyncio
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


async def async_main():
    import aiohttp
    import json
    from miservice import MiAccount

    cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))
    token_path = str(Path.home() / ".mi.token")

    async with aiohttp.ClientSession() as session:
        async def otp_callback(method: str) -> str:
            print(f"\n小米已发送验证码（{method}），请查看短信。")
            return input("输入验证码: ").strip()

        account = MiAccount(
            session,
            cfg.get("account", ""),
            cfg.get("password", ""),
            token_path,
            otp_callback=otp_callback,
        )
        await account.login("micoapi")
        print("\n登录成功！token 已保存到", token_path)
        print("现在可以运行 start.bat 了。")


if __name__ == "__main__":
    patch_aiohttp_no_verify()
    asyncio.run(async_main())
