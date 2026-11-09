# -*- coding: utf-8 -*-
import asyncio, json, ssl, time
from pathlib import Path
import aiohttp

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
    _, service_token = token["micoapi"]
    cookies = {
        "deviceId": token["deviceId"],
        "serviceToken": service_token,
        "userId": str(token["userId"]),
    }
    print("cookies:", {k: v[:12]+"..." for k, v in cookies.items()})

    url = ("https://userprofile.mina.mi.com/device_profile/v2/conversation"
           f"?source=dialogu&hardware=LX05&timestamp={int(time.time()*1000)}&limit=2")
    async with aiohttp.ClientSession(cookies=cookies) as s:
        async with s.get(url) as r:
            text = await r.text()
            print(f"HTTP {r.status}, body head: {text[:400]}")

asyncio.run(main())
