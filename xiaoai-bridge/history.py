# -*- coding: utf-8 -*-
"""
小爱音箱对话记录查看器
======================
从云端拉取对话记录，生成本地网页（聊天气泡样式）并自动打开。
双击 history.bat 或运行: venv/Scripts/python.exe history.py
"""

import asyncio
import html
import json
import ssl
import time
import webbrowser
from datetime import datetime, date
from pathlib import Path

import aiohttp

TOKEN_PATH = Path.home() / ".mi.token"
OUT_FILE = Path(__file__).parent / "history.html"
SPEAKER_DEVICE_ID = "d6e848d0-4a79-429c-adb4-55c0d6fcf6a2"  # 音箱 deviceID
MAX_RECORDS = 300


def make_ssl_ok():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    _orig = aiohttp.TCPConnector.__init__

    def _p(self, *a, **k):
        k["ssl"] = ctx
        return _orig(self, *a, **k)

    aiohttp.TCPConnector.__init__ = _p


async def fetch_all_records() -> list:
    token = json.loads(TOKEN_PATH.read_text())
    _, st = token["micoapi"]
    cookies = {
        "deviceId": SPEAKER_DEVICE_ID,
        "serviceToken": st,
        "userId": str(token["userId"]),
    }

    all_records = []
    ts = int(time.time() * 1000)
    async with aiohttp.ClientSession(cookies=cookies) as s:
        for _ in range(60):  # 最多 60 页
            url = (
                "https://userprofile.mina.mi.com/device_profile/v2/conversation"
                f"?source=dialogu&hardware=LX05&timestamp={ts}&limit=20"
            )
            async with s.get(url) as r:
                body = await r.text()
            try:
                data = json.loads(json.loads(body)["data"])
            except Exception:
                break
            recs = data.get("records", [])
            if not recs:
                break
            all_records.extend(recs)
            if len(all_records) >= MAX_RECORDS:
                break
            ts = recs[-1]["time"] - 1  # 继续往前翻
            if ts <= 0:
                break
    return all_records


def fmt_time(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S")


def day_label(ms: int) -> str:
    d = datetime.fromtimestamp(ms / 1000).date()
    today = date.today()
    delta = (today - d).days
    if delta == 0:
        return "今天"
    if delta == 1:
        return "昨天"
    return d.strftime("%Y年%m月%d日")


def load_local_log() -> list:
    """读取天声小爱本地问答日志 dialogue_log.jsonl。"""
    log_file = Path(__file__).parent / "dialogue_log.jsonl"
    entries = []
    if log_file.exists():
        for line in log_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    entries.sort(key=lambda e: e.get("time", 0))
    return entries


def match_assistant(records: list, local: list) -> dict:
    """把本地天声小爱回答按时间邻近（±20秒）挂到云端记录上。返回 {record_time: entry}。"""
    matched = {}
    used = set()
    for rec in records:
        rt = rec["time"]
        best, best_dt = None, 20001
        for i, e in enumerate(local):
            if i in used:
                continue
            dt = abs(e.get("time", 0) - rt)
            if dt < best_dt:
                best, best_dt = i, dt
        if best is not None and best_dt <= 20000:
            matched[rt] = local[best]
            used.add(best)
    return matched, [e for i, e in enumerate(local) if i not in used]


def build_html(records: list) -> str:
    records.sort(key=lambda r: r["time"])
    local = load_local_log()
    matched, orphans = match_assistant(records, local)
    today_n = sum(1 for r in records if datetime.fromtimestamp(r["time"] / 1000).date() == date.today())

    rows = []
    last_day = None
    for rec in records:
        t = rec["time"]
        day = day_label(t)
        if day != last_day:
            rows.append(f'<div class="day"><span>{day}</span></div>')
            last_day = day
        q = html.escape(rec.get("query", ""))
        answers = []
        for a in rec.get("answers", []):
            try:
                txt = a.get("tts", {}).get("text", "")
            except AttributeError:
                txt = ""
            if txt:
                answers.append(txt)
        ans_html = ""
        if answers:
            ans_html = "".join(
                f'<div class="bubble left"><div class="who">小爱</div>{html.escape(a)}</div>'
                for a in answers
            )
        jt = matched.get(t)
        if jt:
            ans_html += (
                f'<div class="bubble assistant"><div class="who assistant-who">天声小爱</div>'
                f'{html.escape(jt.get("answer", ""))}</div>'
            )
        rows.append(
            f'<div class="row"><div class="time">{fmt_time(t)}</div>'
            f'<div class="bubble right">{q}</div>{ans_html}</div>'
        )

    # 孤儿：本地有记录但云端没有对应条目（对话记录被删/未同步等）
    for e in orphans:
        t = e.get("time", 0)
        day = day_label(t)
        if day != last_day:
            rows.append(f'<div class="day"><span>{day}</span></div>')
            last_day = day
        rows.append(
            f'<div class="row"><div class="time">{fmt_time(t)}</div>'
            f'<div class="bubble right">{html.escape(e.get("query", ""))}</div>'
            f'<div class="bubble assistant"><div class="who assistant-who">天声小爱</div>'
            f'{html.escape(e.get("answer", ""))}</div></div>'
        )
    body = "\n".join(rows) or '<div class="empty">暂无对话记录</div>'

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>天声小爱语音对话记录</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:#f4f5f7; font-family:"Microsoft YaHei",sans-serif; color:#2b2b2b; }}
  .wrap {{ max-width:640px; margin:0 auto; padding:24px 16px 60px; }}
  .head {{ text-align:center; padding:18px 0 6px; }}
  .head h1 {{ font-size:19px; font-weight:600; color:#1a1a1a; }}
  .head .sub {{ font-size:12px; color:#8a8a8a; margin-top:6px; }}
  .stats {{ display:flex; gap:10px; justify-content:center; margin:14px 0 4px; }}
  .stat {{ background:#fff; border-radius:10px; padding:10px 22px; text-align:center;
          box-shadow:0 1px 3px rgba(0,0,0,.06); }}
  .stat b {{ display:block; font-size:20px; color:#0f6e56; }}
  .stat span {{ font-size:12px; color:#8a8a8a; }}
  .refresh {{ display:block; margin:16px auto; background:#0f6e56; color:#fff; border:none;
             border-radius:8px; padding:9px 30px; font-size:13px; cursor:pointer; }}
  .refresh:hover {{ background:#0d6249; }}
  .chat {{ margin-top:10px; }}
  .day {{ text-align:center; margin:22px 0 14px; }}
  .day span {{ background:#e2e4e8; color:#666; font-size:12px; padding:4px 14px; border-radius:20px; }}
  .row {{ margin:12px 0; }}
  .time {{ text-align:center; font-size:11px; color:#b0b0b0; margin-bottom:6px; }}
  .bubble {{ max-width:78%; padding:10px 14px; border-radius:14px; font-size:14px;
            line-height:1.55; box-shadow:0 1px 2px rgba(0,0,0,.05); word-break:break-word; }}
  .right {{ background:#95d4b8; margin-left:auto; border-bottom-right-radius:4px; }}
  .left {{ background:#fff; margin-right:auto; border-bottom-left-radius:4px; margin-top:8px; }}
  .who {{ font-size:11px; color:#9aa; margin-bottom:3px; }}
  .bubble.assistant {{ background:#d9f2ec; border-left:3px solid #0f6e56; }}
  .assistant-who {{ color:#0f6e56; font-weight:600; }}
  .empty {{ text-align:center; color:#999; padding:60px 0; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="head">
    <h1>天声小爱语音对话记录</h1>
    <div class="sub">小爱音箱 Play · 与天声小爱的对话（生成于 {datetime.now().strftime("%H:%M:%S")}）</div>
  </div>
  <div class="stats">
    <div class="stat"><b>{len(records)}</b><span>总条数</span></div>
    <div class="stat"><b>{today_n}</b><span>今天</span></div>
    <div class="stat"><b>{day_label(records[0]["time"]) if records else "-"}</b><span>最早记录</span></div>
  </div>
  <button class="refresh" onclick="location.reload()">刷新记录</button>
  <div class="chat">{body}</div>
</div>
</body>
</html>"""


async def main():
    make_ssl_ok()
    print("正在拉取云端对话记录 ...")
    records = await fetch_all_records()
    print(f"共 {len(records)} 条")
    OUT_FILE.write_text(build_html(records), encoding="utf-8")
    print(f"已生成: {OUT_FILE}")
    webbrowser.open(OUT_FILE.as_uri())


if __name__ == "__main__":
    asyncio.run(main())
