# -*- coding: utf-8 -*-
import asyncio, json, ssl
from pathlib import Path
import aiohttp
from aiohttp import CookieJar
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
    async with aiohttp.ClientSession(cookie_jar=CookieJar()) as s:
        account = MiAccount(s, cfg.get("account", ""), cfg.get("password", ""), str(TOKEN_PATH))
        mina = MiNAService(account)
        for h in await mina.device_list():
            print(f"hardware={h.get('hardware','?'):8} name={h.get('name','?'):20} deviceID={h.get('deviceID','?')} miotDID={h.get('miotDID','?')}")

asyncio.run(main())
