# -*- coding: utf-8 -*-
"""手动调对话记录接口，验证 token 与数据。"""
import asyncio, json, ssl, time
from pathlib import Path
import aiohttp
from miservice import MiAccount, MiNAService

TOKEN_PATH = Path.home() / ".mi.token"

async def main():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    _orig = aiohttp.TCPConnector.__init__
    def _p(self, *a, **k):
        k["ssl"] = ctx
        return _orig(self, *a, **k)
    aiohttp.TCPConnector.__init__ = _p

    cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))
    token = json.loads(TOKEN_PATH.read_text())
    print("token keys:", sorted(token.keys()))

    async with aiohttp.ClientSession() as s:
        account = MiAccount(s, cfg.get("account", ""), cfg.get("password", ""), str(TOKEN_PATH))
        mina = MiNAService(account)

        # 1. 对话记录接口（xiaogpt 轮询用的同一个）
        url = ("https://userprofile.mina.mi.com/device_profile/v2/conversation"
               f"?source=dialogu&hardware=LX05&timestamp={int(time.time()*1000)}&limit=2")
        try:
            r = await mina.mina_request(f"/device_profile/v2/conversation?source=dialogu&hardware=LX05&timestamp={int(time.time()*1000)}&limit=2")
            print("conversation resp:", json.dumps(r, ensure_ascii=False)[:500])
        except Exception as e:
            print("conversation FAIL:", str(e)[:200])

asyncio.run(main())
