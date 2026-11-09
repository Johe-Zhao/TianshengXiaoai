# -*- coding: utf-8 -*-
"""
天声小爱 - 小米盒子语音桥接（原型）
====================================
利用盒子已开的 ADB 调试，实现"盒子语音 → 助手 → 盒子播报"：

  输入：持续读取 logcat，解析 com.xiaomi.voicecontrol 的语音识别结果
        （日志特征：raw inst: SpeechRecognizer.RecognizeResult: [文本, isFinal]）
  处理：命中唤醒词后，把文本发给天声小爱网关（:8008），拿到回答
  输出：edge TTS 合成 mp3 → adb push 到盒子 → 调小米视频播放器播放

用法：python box_bridge.py [--keyword 天声小爱] [--no-play]
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import json
import re
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from paths import adb_exe, bridge_dir
import devices as devmod

BRIDGE_DIR = bridge_dir()
ADB = adb_exe()

# 运行时解析（main() 里填充）——不能写死：每个家庭的网段、每台音箱的 UUID 都不同
BOX_SERIAL = ""          # "ip:5555"
SPEAKER_DEVICE = ""      # 小爱音箱 deviceID（播报用）

GATEWAY = "http://127.0.0.1:8008/v1/chat/completions"
REMOTE_AUDIO = "/sdcard/tiansheng_reply.mp3"

# 凭据守护：未登录时禁止任何小米密码登录（否则后台会触发短信验证码骚扰用户）
try:
    import mi_guard

    mi_guard.apply()
except Exception as e:
    print(f"[凭据守护] 启用失败: {e}")


def _mi_ready() -> bool:
    """小米凭据是否存在（未登录时不发起任何小米请求）。"""
    return (Path.home() / ".mi.token").exists()

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
RECOG_RE = re.compile(r"RecognizeResult: \[(.*?), (true|false)\]")
ANSI_CLEAN = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


LOG_FILE = BRIDGE_DIR / "box_bridge.log"


def log(msg: str):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ---------------- ADB ----------------
def adb(*args: str, timeout: int = 30, retry: bool = True) -> tuple[int, str]:
    """执行 adb shell 命令（用 adbutils 管理连接，避免 daemon 重启导致断连）。"""
    try:
        from adbutils import adb as _adbcli

        _adbcli.connect(BOX_SERIAL, timeout=8)
        device = _adbcli.device(serial=BOX_SERIAL)
        if len(args) == 1:
            out = device.shell(args[0], timeout=timeout)
        else:
            out = device.shell(" ".join(args), timeout=timeout)
        return 0, out or ""
    except Exception as e:
        if retry:
            time.sleep(1.5)
            try:
                from adbutils import adb as _adbcli

                _adbcli.connect(BOX_SERIAL, timeout=8)
                device = _adbcli.device(serial=BOX_SERIAL)
                out = device.shell(" ".join(args), timeout=timeout)
                return 0, out or ""
            except Exception as e2:
                return -1, f"{e} / {e2}"
        return -1, str(e)


def adb_push(local: str, remote: str) -> bool:
    """推送文件到盒子。"""
    try:
        from adbutils import adb as _adbcli

        _adbcli.connect(BOX_SERIAL, timeout=8)
        device = _adbcli.device(serial=BOX_SERIAL)
        device.sync.push(local, remote)
        return True
    except Exception as e:
        log(f"推送失败: {str(e)[:100]}")
        return False


def ensure_connected(retries: int = 5) -> bool:
    """建立到盒子的 ADB 连接（带重试，盒子休眠/断连时能自动恢复）。"""
    for attempt in range(1, retries + 1):
        try:
            from adbutils import adb as _adbcli

            if attempt == 1:
                log("正在连接盒子 …")
            else:
                log(f"重试连接（第 {attempt}/{retries} 次）…")

            # 关键：先断开旧连接。盒子休眠/WiFi 断过之后，adb 会保留一条
            # 显示为 device 的僵尸连接（connect 会返回 already connected），
            # 但 shell 通道已关闭，不先断开就永远重建不出好连接。
            try:
                _adbcli.disconnect(BOX_SERIAL)
                time.sleep(0.8)
            except Exception:
                pass

            _adbcli.connect(BOX_SERIAL, timeout=10)
            device = _adbcli.device(serial=BOX_SERIAL)
            out = device.shell("echo tiansheng_ok", timeout=12)
            if "tiansheng_ok" in (out or ""):
                log(f"盒子连接成功: {BOX_SERIAL}")
                return True
        except Exception as e:
            if attempt == retries:
                log(f"连接失败: {str(e)[:100]}")
        time.sleep(3)
    log("盒子连接不上。请依次确认：1) 盒子已开机且非待机（用遥控器按一下唤醒）"
        " 2) 盒子设置里的「ADB 调试」仍处于开启 3) 盒子与电脑在同一局域网")
    return False


# ---------------- 助手 ----------------
def ask_assistant(question: str) -> str:
    payload = {
        "model": "workbuddy",
        "messages": [{"role": "user", "content": question}],
    }
    try:
        req = urllib.request.Request(
            GATEWAY,
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer local-only",
                "X-Source": "box",
            },
            method="POST",
        )
        with _opener.open(req, timeout=90) as r:
            data = json.loads(r.read())
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"助手暂时联系不上（{str(e)[:40]}）"


# ---------------- TTS + 播放 ----------------
def _patch_ssl():
    import aiohttp

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    _orig = aiohttp.TCPConnector.__init__

    def _patched(self, *args, **kwargs):
        kwargs["ssl"] = ctx
        return _orig(self, *args, **kwargs)

    aiohttp.TCPConnector.__init__ = _patched


def synth_mp3(text: str, out_path: str) -> bool:
    """用 edge TTS 合成语音文件。"""
    try:
        _patch_ssl()
        from tetos import get_speaker

        cfg = json.loads((BRIDGE_DIR / "config.json").read_text(encoding="utf-8"))
        tts_cfg = cfg.get("tts_options") or {}
        voice = tts_cfg.get("voice") or "zh-CN-XiaoxiaoNeural"
        rate = tts_cfg.get("rate") or "+10%"

        speaker = get_speaker("edge")(voice=voice, rate=rate)
        asyncio.run(speaker.synthesize(text, out_path, lang="zh-CN"))
        return Path(out_path).exists() and Path(out_path).stat().st_size > 1000
    except Exception as e:
        log(f"语音合成失败: {str(e)[:100]}")
        return False


# ---------------- 统一音色播报（edge TTS + 音箱拉取）----------------
_TTS_DIR = Path(tempfile.mkdtemp(prefix="jt-tts-"))
_HTTP_PORT = 8100
_http_ready = False


def local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def ensure_http_server():
    """起一个本地 HTTP 服务，把合成好的 mp3 暴露给音箱拉取。"""
    global _http_ready
    if _http_ready:
        return
    try:
        handler = functools.partial(SimpleHTTPRequestHandler, directory=str(_TTS_DIR))
        srv = ThreadingHTTPServer(("0.0.0.0", _HTTP_PORT), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        _http_ready = True
        log(f"音频服务已启动: http://{local_ip()}:{_HTTP_PORT}/")
    except Exception as e:
        log(f"音频服务启动失败: {str(e)[:100]}")


def stop_xiaoai() -> bool:
    """打断盒子上小爱正在进行的播报。

    实测：返回键只关 UI 不停止 TTS；强制停止进程能立即静音，
    且小爱是系统应用，系统会在 2-3 秒内自动重启它，副作用可控。
    """
    try:
        # 1. 官方广播（部分版本有效，无害）
        adb("am broadcast -a com.xiaomi.mitv.voice.STOPSPEAKING", timeout=10)

        # 2. 确认小爱在前台才强停，避免打断正在看的内容
        code, out = adb("dumpsys window | grep mCurrentFocus", timeout=15)
        focused = (out or "").lower()
        if "voicecontrol" not in focused:
            log("小爱未在前台，跳过打断")
            return False

        adb("am force-stop com.xiaomi.voicecontrol", timeout=15)
        log("已强制停止小爱（打断播报，系统会自动重启它）")
        return True
    except Exception as e:
        log(f"打断失败: {str(e)[:80]}")
        return False


def speak_with_tts(text: str) -> bool:
    """用「天声小爱」配置的音色播报（edge TTS + 音箱拉取 URL）。"""
    if not _mi_ready():
        log("小米账号未登录，跳过音箱播报")
        return False
    ensure_http_server()
    if not _http_ready:
        return speak_on_speaker(text)
    # 没解析到音箱 deviceID（如账号下无音箱）时，退回小爱原声，避免推给空设备报错
    if not SPEAKER_DEVICE:
        return speak_on_speaker(text)

    fname = f"reply_{int(time.time() * 1000)}.mp3"
    fpath = _TTS_DIR / fname
    if not synth_mp3(text, str(fpath)):
        return speak_on_speaker(text)          # 合成失败则退回小爱原声

    url = f"http://{local_ip()}:{_HTTP_PORT}/{fname}"
    try:
        _patch_ssl()
        import aiohttp
        from miservice import MiAccount, MiNAService

        cfg = json.loads((BRIDGE_DIR / "config.json").read_text(encoding="utf-8"))
        token_path = str(Path.home() / ".mi.token")

        async def _run():
            async with aiohttp.ClientSession() as session:
                account = MiAccount(
                    session, cfg.get("account", ""), cfg.get("password", ""), token_path
                )
                mina = MiNAService(account)
                await mina.play_by_url(SPEAKER_DEVICE, url, _type=1)

        asyncio.run(_run())
        log(f"音箱播报（配置音色）: {url}")
        # 清理旧文件（保留最近 5 个）
        files = sorted(_TTS_DIR.glob("reply_*.mp3"))
        for old in files[:-5]:
            old.unlink(missing_ok=True)
        return True
    except Exception as e:
        log(f"配置音色播报失败，退回小爱原声: {str(e)[:90]}")
        return speak_on_speaker(text)


def speak_on_speaker(text: str) -> bool:
    """让小爱音箱播报文本（mina 原生 TTS，作为兜底）。"""
    if not _mi_ready():
        log("小米账号未登录，跳过音箱播报")
        return False
    if not SPEAKER_DEVICE:
        log("没有可用的音箱设备，无法播报（请先在米家绑定音箱）")
        return False
    try:
        _patch_ssl()
        import aiohttp
        from miservice import MiAccount, MiNAService

        cfg = json.loads((BRIDGE_DIR / "config.json").read_text(encoding="utf-8"))
        token_path = str(Path.home() / ".mi.token")

        async def _run():
            async with aiohttp.ClientSession() as session:
                account = MiAccount(
                    session,
                    cfg.get("account", ""),
                    cfg.get("password", ""),
                    token_path,
                )
                mina = MiNAService(account)
                await mina.text_to_speech(SPEAKER_DEVICE, text)

        asyncio.run(_run())
        log(f"音箱播报已推送（{len(text)} 字）")
        return True
    except Exception as e:
        log(f"音箱播报失败: {str(e)[:120]}")
        return False


def play_on_box(local_mp3: str) -> bool:
    """推送音频到盒子并播放。"""
    if not adb_push(local_mp3, REMOTE_AUDIO):
        return False
    code, out = adb(
        f'am start -n com.mitv.videoplayer/.VideoPlayerActivity -d "file://{REMOTE_AUDIO}" -t "audio/mpeg"',
        timeout=30,
    )
    ok = "Starting:" in out
    log(f"盒子播报{'已触发' if ok else '触发失败'}: {out.strip()[:80]}")
    return ok


# ---------------- 主循环 ----------------
def stream_logcat(keyword: str, play: bool):
    """轮询 logcat 尾部，解析语音识别结果。

    keyword 支持逗号分隔多个同音变体（如 "天声小爱,天生小爱,天爱"），
    因为小米的语音识别经常把唤醒词听成同音词。
    """
    # 优先用命令行参数；默认读 config.json，与音箱入口共用同一套规则
    cfg = {}
    try:
        cfg = json.loads((BRIDGE_DIR / "config.json").read_text(encoding="utf-8"))
    except Exception:
        pass

    if keyword and keyword != "auto":
        keywords = [k.strip() for k in keyword.split(",")]
    else:
        keywords = list(cfg.get("keyword") or ["天声小爱"])

    # 免唤醒模式：关键字列表里含空串 "" 表示不过滤（遥控器语音键本身已是明确意图）
    no_keyword = "" in keywords
    keywords = [k for k in keywords if k] or ["天声小爱"]
    log(f"唤醒词: {'/'.join(keywords)}" + ("（免唤醒：任意语音都转给助手）" if no_keyword else ""))
    log("开始监听盒子语音 …（Ctrl+C 停止）")

    # 防待机：MiBOX4 在显示器关闭后会自行进入待机，ADB 随之断开。
    # 三道保险（对无电池设备的系统级开关不一定都生效，能设则设）：
    #   1) stay_on_while_plugged_in —— 插电时保持唤醒
    #   2) deviceidle disable       —— 关闭 Doze 深度休眠
    #   3) 主循环每 30 秒发 KEYCODE_WAKEUP —— 兜底，把已待机的盒子叫醒
    adb("settings put global stay_on_while_plugged_in 7", timeout=8, retry=False)
    adb("dumpsys deviceidle disable", timeout=8, retry=False)
    log("防休眠保活已开启（每 30 秒唤醒一次，显示器关闭时也能保持连接）")

    # 启动时先读一次，把历史记录登记为已处理（避免立即触发旧对话）
    seen: set[str] = set()
    code, out = adb("logcat -d -t 4000 -v time | grep RecognizeResult", timeout=30)
    for line in (out or "").splitlines():
        m = RECOG_RE.search(ANSI_CLEAN.sub("", line))
        if m and m.group(2) == "true" and m.group(1).strip():
            ts = line.split(" ")[1] if len(line.split(" ")) > 1 else ""
            seen.add(f"{ts}|{m.group(1).strip()}")
    log(f"已忽略 {len(seen)} 条历史识别结果")

    try:
        last_wake = 0.0
        while True:
            code, out = adb(
                "logcat -d -t 4000 -v time | grep RecognizeResult", timeout=30
            )
            for line in (out or "").splitlines():
                if "RecognizeResult" not in line or "true]" not in line:
                    continue
                line = ANSI_CLEAN.sub("", line)
                m = RECOG_RE.search(line)
                if not m:
                    continue
                text, is_final = m.group(1).strip(), m.group(2) == "true"
                if not is_final or not text:
                    continue

                # 用「日志时间戳 + 文本」做唯一键，彻底避免同一句重复触发
                parts = line.split(" ")
                ts = parts[1] if len(parts) > 1 else ""
                key = f"{ts}|{text}"
                if key in seen:
                    continue
                seen.add(key)
                if len(seen) > 500:
                    seen = set(list(seen)[-300:])

                log(f"识别到: {text}")
                hit = next((k for k in keywords if k in text), "")
                if not no_keyword and not hit:
                    log("  未命中唤醒词，跳过")
                    continue

                # 裁掉"问一下X/问X"这类前缀，保留实际问题（免唤醒模式下不裁剪）
                question = text
                if not no_keyword:
                    for kw in (f"问一下{hit}", f"问{hit}"):
                        if question.startswith(kw):
                            trimmed = question[len(kw):].strip(" ，,。")
                            if trimmed:
                                question = trimmed
                            break

                # 先打断小爱，别让它把话说完
                stop_xiaoai()

                log(f"  提问助手: {question}")
                t0 = time.time()
                answer = ask_assistant(question)
                log(f"  助手回答（{time.time()-t0:.1f}s）: {answer[:60]}")

                if play and answer:
                    speak_with_tts(answer)

            # 防待机保活：每 30 秒发一次唤醒键。
            # 盒子醒着时该事件无副作用；若已进入待机则能把它叫醒，避免 ADB 掉线。
            if time.time() - last_wake >= 30:
                last_wake = time.time()
                adb("input keyevent KEYCODE_WAKEUP", timeout=8, retry=False)

            time.sleep(1.2)
    except KeyboardInterrupt:
        log("已停止")


def main():
    global BOX_SERIAL, SPEAKER_DEVICE

    from paths import watch_parent

    watch_parent()
    ap = argparse.ArgumentParser()
    ap.add_argument("--keyword", default="auto",
                    help="唤醒词，逗号分隔；auto=使用配置文件里的设置（与音箱一致）")
    ap.add_argument("--no-play", action="store_true", help="只测识别，不播报")
    args = ap.parse_args()

    # 解析音箱（播报需要 deviceID）——失败不阻塞盒子入口，播报会退回小爱原声
    try:
        spk = devmod.resolve_speaker()
        SPEAKER_DEVICE = spk.get("device_id") or ""
        if SPEAKER_DEVICE:
            log(f"播报设备: {spk.get('name') or SPEAKER_DEVICE}")
        else:
            log("未解析到音箱 deviceID，播报将退回小爱原声")
    except Exception as e:
        log(f"解析音箱失败: {str(e)[:100]}")

    fail_count = 0
    # 永不放弃：盒子闲置会休眠、ADB 会掉线，进程不能因此退出。
    # 掉线后每 20 秒自动重连；用户用遥控器唤醒盒子后最多 20 秒自动恢复。
    while True:
        # 盒子地址：配置优先，没有就扫描局域网（换网段后自动重新发现）
        if not BOX_SERIAL:
            try:
                BOX_SERIAL = devmod.resolve_box_serial()
            except Exception as e:
                log(f"扫描盒子失败: {str(e)[:100]}")
            if not BOX_SERIAL:
                log("未找到可用的盒子，60 秒后重新扫描（也可在设置中填写盒子 IP）")
                time.sleep(60)
                continue

        if ensure_connected(retries=3):
            fail_count = 0
            try:
                stream_logcat(args.keyword, play=not args.no_play)
            except Exception as e:
                log(f"监听中断: {str(e)[:120]}")
        else:
            fail_count += 1
            # 连续失败到一定次数，认为地址可能已变（换网络/换路由器），重新扫描
            if fail_count >= 3:
                fail_count = 0
                log("连续多次连接失败，重新扫描盒子地址 …")
                try:
                    devmod.forget_box_ip()
                except Exception:
                    pass
                BOX_SERIAL = ""

        log("盒子暂时不可用，20 秒后自动重连（用遥控器唤醒盒子即可恢复）")
        time.sleep(20)


if __name__ == "__main__":
    main()
