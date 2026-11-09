# -*- coding: utf-8 -*-
"""实测 LX05 的 TTS 指令：依次试 5-3 / 5-1 / 5-5，听哪条出声。"""
import asyncio, json, ssl
from pathlib import Path
import aiohttp
from miservice import MiAccount, MiIOService, miio_command

TOKEN_PATH = Path.home() / ".mi.token"
DID = "295071332"

async def main():
    ctx = ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
    _orig = aiohttp.TCPConnector.__init__
    def _p(self,*a,**k):
        k["ssl"]=ctx; return _orig(self,*a,**k)
    aiohttp.TCPConnector.__init__=_p

    cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))
    async with aiohttp.ClientSession() as s:
        account = MiAccount(s, cfg.get("account", ""), cfg.get("password", ""), str(TOKEN_PATH))
        miot = MiIOService(account)
        for cmd, label in [("5-3", "5-3"), ("5-1", "5-1"), ("5-5", "5-5")]:
            try:
                r = await miio_command(miot, DID, f"{cmd} 测试声音{label}")
                print(f"{label}: OK -> {str(r)[:100]}")
            except Exception as e:
                print(f"{label}: FAIL -> {str(e)[:120]}")
            await asyncio.sleep(3)

asyncio.run(main())
