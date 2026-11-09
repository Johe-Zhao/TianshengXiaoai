# -*- coding: utf-8 -*-
import asyncio, json, ssl
from pathlib import Path
import aiohttp
from miservice import MiAccount, MiIOService

TOKEN_PATH = Path.home() / ".mi.token"

async def main():
    ctx = ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
    _orig = aiohttp.TCPConnector.__init__
    def _p(self,*a,**k):
        k["ssl"]=ctx; return _orig(self,*a,**k)
    aiohttp.TCPConnector.__init__=_p
    cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))
    async with aiohttp.ClientSession() as s:
        account = MiAccount(s, cfg.get("account", ""), cfg.get("password", ""), str(TOKEN_PATH))
        iot = MiIOService(account)
        try:
            devs = await iot.device_list()
            print(f"共 {len(devs)} 台设备:")
            for d in devs:
                print(f"  model={d.get('model','?'):24} name={d.get('name','?'):18} did={d.get('did','?')}")
        except Exception as e:
            print("iot 接口失败:", str(e)[:200])

asyncio.run(main())
