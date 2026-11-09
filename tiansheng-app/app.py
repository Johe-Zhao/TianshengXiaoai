# -*- coding: utf-8 -*-
"""
天声小爱 — 语音助手桌面端
============================
双击 start.bat 启动，浏览器自动打开控制台。

职责：
  - 提供 Web 界面（状态、开关、对话、设置）
  - 管理三个后台服务（CodeBuddy / 网关 / xiaogpt）
  - 进程退出时一并关闭所有子服务
"""

import atexit
import json
import os
import signal
import sys

# pythonw（无窗口模式）下 sys.stdout/stderr 为 None，print 会崩；兜底到日志文件
if sys.stdout is None or sys.stderr is None:
    from pathlib import Path as _P
    _logf = open(_P(__file__).parent / "app.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or _logf
    sys.stderr = sys.stderr or _logf
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

sys.path.insert(0, str(Path(__file__).parent))
from core.paths import app_root, web_dir  # noqa: E402
from core.supervisor import BRIDGE_DIR, load_config, save_config, supervisor  # noqa: E402
from core.tray import Tray  # noqa: E402
from core import agent_login, mijia_login  # noqa: E402

APP_PORT = 8020

# 微软 TTS 走本机代理时证书校验会失败，统一放行（仅本机工具）
def _patch_ssl():
    import ssl
    try:
        import aiohttp
    except ImportError:
        return
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    _orig = aiohttp.TCPConnector.__init__

    def _patched(self, *args, **kwargs):
        kwargs["ssl"] = ctx
        return _orig(self, *args, **kwargs)

    aiohttp.TCPConnector.__init__ = _patched


_patch_ssl()
mijia_login.install_guard()   # 凭据守护：401 不再删除 ~/.mi.token（"账号被退出"根因）
WEB_DIR = web_dir()
DIALOGUE_LOG = BRIDGE_DIR / "dialogue_log.jsonl"

app = Flask(__name__, static_folder=None)

VOICES = [
    {"id": "mi", "name": "小爱原声", "desc": "音箱自带，最稳", "group": "基础"},
    {"id": "zh-CN-XiaoxiaoNeural", "name": "晓晓", "desc": "女声 · 温柔自然（推荐）", "group": "普通话"},
    {"id": "zh-CN-XiaoyiNeural", "name": "晓伊", "desc": "女声 · 活泼年轻", "group": "普通话"},
    {"id": "zh-CN-YunxiNeural", "name": "云希", "desc": "男声 · 年轻阳光", "group": "普通话"},
    {"id": "zh-CN-YunjianNeural", "name": "云健", "desc": "男声 · 浑厚有力", "group": "普通话"},
    {"id": "zh-CN-YunyangNeural", "name": "云扬", "desc": "男声 · 新闻播报", "group": "普通话"},
    {"id": "zh-CN-YunxiaNeural", "name": "云夏", "desc": "少年 · 童声清亮", "group": "普通话"},
    {"id": "zh-CN-liaoning-XiaobeiNeural", "name": "晓北", "desc": "东北话 · 女声", "group": "方言特色"},
    {"id": "zh-CN-shaanxi-XiaoniNeural", "name": "晓妮", "desc": "陕西话 · 女声", "group": "方言特色"},
    {"id": "zh-HK-HiuMaanNeural", "name": "晓曼", "desc": "粤语 · 女声", "group": "方言特色"},
    {"id": "zh-TW-HsiaoChenNeural", "name": "晓臻", "desc": "台湾腔 · 女声", "group": "方言特色"},
]

_shutdown_done = False


def shutdown():
    """退出清理：杀掉全部子服务（含 ConPTY 内的 node）。"""
    global _shutdown_done
    if _shutdown_done:
        return
    _shutdown_done = True
    print("\n[天声小爱] 正在停止所有服务 ...")
    supervisor.stop_all()
    print("[天声小爱] 已退出")


atexit.register(shutdown)


def _on_signal(signum, frame):
    shutdown()
    sys.exit(0)


signal.signal(signal.SIGINT, _on_signal)
signal.signal(signal.SIGTERM, _on_signal)


# ---------------- 页面 ----------------
@app.route("/")
def index():
    # 禁用缓存：否则 WebView2 会一直用旧版前端，改了 CSS/JS 用户看不到
    resp = send_from_directory(WEB_DIR, "index.html")
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp


@app.route("/<path:filename>")
def static_files(filename):
    resp = send_from_directory(WEB_DIR, filename)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp


# ---------------- 状态与开关 ----------------
@app.route("/api/status")
def api_status():
    st = supervisor.status()
    cfg = load_config()
    st["config"] = {
        "keyword": ", ".join([w for w in (cfg.get("keyword") or []) if w]),
        "no_keyword": "" in (cfg.get("keyword") or []),
        "tts": cfg.get("tts", "mi"),
        "voice": (cfg.get("tts_options") or {}).get("voice", ""),
        "rate": (cfg.get("tts_options") or {}).get("rate", "+0%"),
        "prompt": cfg.get("prompt", ""),
        "mute_xiaoai": cfg.get("mute_xiaoai", True),
        "stream": cfg.get("stream", True),
        "transition": cfg.get("transition", "正在问天声小爱，请稍等"),
        "hardware": cfg.get("hardware", ""),
        "box_ip": cfg.get("box_ip", ""),
        "account": (cfg.get("account") or "")[:3] + "****" + (cfg.get("account") or "")[-4:],
    }

    # 小米侧真实状态（token 是否有效 / 音箱是否在线），供前端精确显示
    probe = _probe_snapshot()
    st["speaker"] = {
        "online": probe["speaker_online"],
        "name": probe["speaker_name"],
        "token_valid": probe["token_valid"],
        "state": probe["state"],
        "error": probe["error"],
        "checked_at": probe["at"],
    }
    return jsonify(st)


@app.route("/api/power", methods=["POST"])
def api_power():
    body = request.get_json(silent=True) or {}
    if body.get("on"):
        result = supervisor.start_all()
        return jsonify({"ok": all(result.values()), "detail": result})
    supervisor.stop_all()
    return jsonify({"ok": True})


@app.route("/api/restart", methods=["POST"])
def api_restart():
    supervisor.stop_all()
    time.sleep(1)
    result = supervisor.start_all()
    return jsonify({"ok": all(result.values()), "detail": result})


# ---------------- 配置 ----------------
CTL_PORT = 55101  # CodeBuddy 无窗口守护进程的控制接口

AGENTS = [
    {
        "id": "workbuddy",
        "name": "WorkBuddy",
        "desc": "腾讯 AI 编程助手，能读写文件、跑命令（需登录）",
        "available": True,
    },
]


def _ctl_get(path: str) -> dict | None:
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{CTL_PORT}{path}")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=4) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _ctl_post(path: str, body: dict | None = None) -> dict | None:
    try:
        data = json.dumps(body or {}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{CTL_PORT}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _mi_logged_in() -> bool:
    """已登录判定：有 micoapi token 即可用（不依赖是否走过本程序的登录向导）。"""
    try:
        token = json.loads(mijia_login.TOKEN_PATH.read_text(encoding="utf-8"))
        return "micoapi" in token
    except Exception:
        return False


# ---------------- 小米侧真实健康探测（token 是否仍有效 + 音箱是否在线） ----------------
# 界面上的「已登录 / 已连接」必须经得起推敲：
#   · 只看 token 文件存在 → 过期了还显示已登录（假）
#   · 只看 xiaogpt 进程存活 → 进程活着但设备离线也显示已连接（假）
# 这里用 micoapi 的 device_list 实测一次，同时拿到 token 有效性与设备 presence。
_PROBE: dict = {
    "at": 0.0,
    "token_valid": None,      # None=尚未探测/无法判定 / True / False
    "speaker_online": None,   # 同上
    "speaker_name": "",
    "state": "",              # ok / auth（凭据失效） / net（网络异常） / nologin
    "error": "",
}
_PROBE_LOCK = threading.Lock()

# 判定"这是凭据失效"的关键词。注意不能把网络错误也算进去 ——
# 否则一次断网就会把界面刷成"登录已失效"，用户白跑一趟去授权。
_AUTH_HINTS = (
    "401", "auth", "login failed", "unauthorized",
    "passport", "-10015", "invalid token", "serviceToken",
)


def _is_auth_error(msg: str) -> bool:
    low = (msg or "").lower()
    return any(k.lower() in low for k in _AUTH_HINTS)


def _probe_mi_once() -> dict:
    """探测一次：token 是否有效 + 小爱音箱是否在线（阻塞，约 1~3 秒）。"""
    import asyncio

    snap: dict = {}
    try:
        from aiohttp import ClientSession
        from miservice import MiAccount, MiNAService

        cfg = load_config()
        token = str(mijia_login.TOKEN_PATH)

        async def _fetch():
            async with ClientSession() as session:
                account = MiAccount(
                    session, cfg.get("account", ""), cfg.get("password", ""), token
                )
                return await MiNAService(account).device_list()

        devices = asyncio.run(_fetch()) or []
        snap["token_valid"] = True
        snap["state"] = "ok"
        snap["error"] = ""

        # 匹配顺序：已解析的 mi_did → 配置型号 → 任意带 deviceID 的设备。
        # 不假设「LX」前缀（小米音箱还有 L05B/X08A/OH2P 等大量非 LX 型号）。
        hw = (cfg.get("hardware") or "").strip()
        did = str(cfg.get("mi_did") or "").strip()
        cand = None
        if did:
            cand = next(
                (d for d in devices if str(d.get("miotDID") or "") == did), None
            )
        if cand is None and hw:
            cand = next((d for d in devices if d.get("hardware") == hw), None)
        if cand is None:
            cand = next((d for d in devices if d.get("deviceID")), None)
        if cand:
            snap["speaker_online"] = cand.get("presence") == "online"
            snap["speaker_name"] = cand.get("name", "") or ""
        else:
            snap["speaker_online"] = False
            snap["speaker_name"] = ""
            snap["error"] = "账号下未找到小爱音箱"
    except Exception as e:
        msg = str(e)[:160]
        if _is_auth_error(msg):
            # 真正的凭据问题：明确告知需要重新授权
            snap["token_valid"] = False
            snap["state"] = "auth"
            snap["error"] = msg[:120]
        else:
            # 网络/代理/小米服务抖动：不能谎报"登录已失效"，保持未知
            snap["token_valid"] = None
            snap["state"] = "net"
            snap["error"] = f"网络异常：{msg[:100]}"
        snap["speaker_online"] = None
        snap["speaker_name"] = ""

    snap["at"] = time.time()
    with _PROBE_LOCK:
        _PROBE.update(snap)
    return snap


def _probe_loop() -> None:
    """后台线程：每 30 秒探测一次（未登录时跳过，避免无谓请求）。"""
    while True:
        try:
            if _mi_logged_in():
                _probe_mi_once()
            else:
                # 未登录时把上一次的探测结果清掉，避免界面残留旧状态
                with _PROBE_LOCK:
                    if _PROBE["state"] not in ("", "nologin"):
                        _PROBE.update(
                            token_valid=None, speaker_online=None,
                            speaker_name="", state="nologin", error="", at=time.time(),
                        )
        except Exception as e:
            print(f"[探测] 异常: {e}")
        time.sleep(30)


def _probe_snapshot() -> dict:
    with _PROBE_LOCK:
        return dict(_PROBE)



@app.get("/api/mijia/status")
def api_mijia_status():
    cfg = load_config()
    probe = _probe_snapshot()
    return jsonify({
        "logged_in": _mi_logged_in(),
        # token_valid: None=尚未探测/无法判定 / True=有效 / False=已失效
        "token_valid": probe["token_valid"],
        "probe_state": probe["state"],    # ok / auth / net / nologin
        "probe_error": probe["error"],
        "probe_at": probe["at"],
        "user_id": cfg.get("mi_user_id", ""),
        "account": cfg.get("account", ""),
        "login_at": cfg.get("mi_login_at", ""),
        "state": mijia_login.STATE,
    })


def _on_login_done(ok: bool, msg: str):
    """登录完成后：立即重新探测一次凭据，并在服务运行时重启监听。"""
    if not ok:
        return
    try:
        threading.Thread(target=_probe_mi_once, daemon=True).start()
    except Exception:
        pass
    # 登录成功后立刻刷新凭据备份（.mi.token.bak），防止再次丢失
    try:
        _mijia()._ensure_token()
    except Exception:
        pass
    if supervisor.xiaogpt.alive():
        try:
            supervisor.restart_listener()
            print("[登录] 凭据已更新，监听服务已自动重启")
        except Exception as e:
            print(f"[登录] 重启监听失败: {e}")


@app.post("/api/mijia/login")
def api_mijia_login():
    ok = mijia_login.start_login(on_done=_on_login_done)
    if not ok:
        return jsonify({"ok": False, "msg": mijia_login.STATE.get("message") or "登录窗口已在运行"})
    return jsonify({"ok": True, "msg": "登录窗口已打开，请在窗口中完成登录"})


@app.post("/api/mijia/logout")
def api_mijia_logout():
    """退出小米登录：清凭据与备份、重置探测状态、清设备缓存。

    必须连 `.mi.token.bak` 一起删，否则 mi_guard.restore() 会在下次启动时
    把备份恢复回来，看起来就像「退不出去」。
    """
    removed = []
    for name in (".mi.token", ".mi.token.bak"):
        p = Path.home() / name
        try:
            if p.exists():
                p.unlink()
                removed.append(name)
        except Exception as e:
            return jsonify({"ok": False, "msg": f"清除 {name} 失败：{e}"})

    cfg = load_config()
    for k in ("account", "password", "cookie", "mi_user_id", "mi_login_at"):
        cfg.pop(k, None)
    save_config(cfg)

    with _PROBE_LOCK:
        _PROBE.update(token_valid=None, speaker_online=None, speaker_name="",
                      state="nologin", error="", at=time.time())
    mijia_login.STATE.update(status="idle", message="", userId="")

    try:
        _mijia().clear_cache()
    except Exception:
        pass

    return jsonify({"ok": True, "removed": removed, "msg": "已退出小米登录"})


@app.get("/api/agent/status")
def api_agent_status():
    cfg = load_config()
    st = _ctl_get("/status") or {}
    return jsonify({
        "agent": cfg.get("agent", "workbuddy"),
        "agents": AGENTS,
        "backend_ready": bool(st.get("ready")),
        # logged_in 来自官方 /api/v1/auth/account/status 实测，不再靠屏幕关键词猜
        "logged_in": bool(st.get("logged_in")) and bool(st.get("ready")),
        "auth_source": st.get("auth_source", ""),
        "login_state": agent_login.STATE["status"],
        "pid": st.get("pid", 0),
        "uptime": st.get("uptime", 0),
    })


@app.get("/api/agent/login/status")
def api_agent_login_status():
    return jsonify(agent_login.STATE)


@app.post("/api/agent/logout")
def api_agent_logout():
    """退出 AI 后端登录（调官方 account/logout），并取消正在等待的授权窗口。"""
    r = _ctl_post("/logout") or {}
    if not r.get("ok"):
        return jsonify({"ok": False, "msg": r.get("msg", "退出失败，请确认 AI 后端已启动")})
    agent_login.cancel_login()
    return jsonify({"ok": True, "msg": "已退出登录"})


@app.post("/api/agent/login")
def api_agent_login():
    """触发登录：官方 API 取授权链接 → 弹出 WebView 窗口，成功后自动关闭。"""
    if not (_ctl_get("/status") or {}).get("ready"):
        return jsonify({"ok": False, "msg": "CodeBuddy 后端未启动，请先点右上角「启动」"})

    r = _ctl_post("/login") or {}
    if not r.get("ok"):
        return jsonify({"ok": False, "msg": r.get("msg", "发送登录指令失败")})

    # 正常路径：官方 API 直接返回 authUrl，不再依赖从 TUI 屏幕里抠链接
    auth_url = r.get("auth_url") or ""
    if not auth_url:
        # 兜底：TUI /login 路径，从屏幕输出里找授权链接
        import re as _re

        time.sleep(2)
        scr = (_ctl_get("/screen") or {}).get("screen", "")
        urls = _re.findall(r"https?://[^\s\"'<>)]+", scr)
        for u in reversed(urls):
            if any(k in u for k in ("login", "auth", "oauth", "codebuddy", "workbuddy", "tencent")):
                auth_url = u
                break
        if not auth_url and urls:
            auth_url = urls[-1]

    if not auth_url:
        return jsonify({
            "ok": False,
            "msg": "未取到授权链接，请稍后重试，或在日志面板查看「AI 后端」输出",
        })

    def _check():
        st = _ctl_get("/status") or {}
        return bool(st.get("ready")) and bool(st.get("logged_in"))

    # 已登录就没必要弹窗
    if _check():
        return jsonify({"ok": True, "already": True, "auth_url": auth_url,
                        "popup": False, "msg": "账号已登录，无需重复授权"})

    opened = agent_login.start_login(auth_url, _check)
    if not opened:
        # 已有窗口在等待 / 窗口创建失败：**不再自动开系统浏览器**，只给手动链接。
        # 否则点一次「登录」会同时冒出 WebView 弹窗和网页两个登录界面。
        pending = agent_login.STATE["status"] == "pending"
        return jsonify({
            "ok": True,
            "auth_url": auth_url,
            "popup": False,
            "msg": "授权窗口已在等待中" if pending else "弹窗创建失败，请点下方链接手动授权",
        })

    return jsonify({"ok": True, "auth_url": auth_url, "popup": True,
                    "msg": "授权窗口已打开，登录成功后自动关闭"})


@app.get("/api/agent/screen")
def api_agent_screen():
    return jsonify(_ctl_get("/screen") or {"screen": ""})


@app.get("/api/voices")
def api_voices():
    return jsonify(VOICES)


@app.get("/api/tts/preview")
def api_tts_preview():
    """用指定音色合成一段试听音频，直接回 mp3 给浏览器播放。"""
    voice = request.args.get("voice", "zh-CN-XiaoxiaoNeural")
    rate = request.args.get("rate", "+10%")
    text = request.args.get("text") or "你好，我是天声小爱，你的专属 AI 助手。有任何需要，直接吩咐就行。"
    text = text[:200]

    try:
        import asyncio
        import tempfile

        from tetos import get_speaker

        speaker = get_speaker("edge")(voice=voice, rate=rate)
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp.close()
        asyncio.run(speaker.synthesize(text, tmp.name, lang="zh-CN"))
        data = Path(tmp.name).read_bytes()
        Path(tmp.name).unlink(missing_ok=True)
        return Response(data, mimetype="audio/mpeg")
    except Exception as e:
        return jsonify({"error": f"试听生成失败: {str(e)[:200]}"}), 500


@app.route("/api/config", methods=["POST"])
def api_config():
    body = request.get_json(silent=True) or {}
    updates = {}

    if "keyword" in body and str(body["keyword"]).strip():
        words = [w.strip() for w in str(body["keyword"]).replace("，", ",").split(",") if w.strip()]
        if words:
            updates["keyword"] = words
    if "tts" in body:
        voice = body["tts"]
        if voice == "mi":
            updates["tts"] = "mi"
            updates["tts_options"] = {}
        else:
            updates["tts"] = "edge"
            updates["tts_options"] = {
                "voice": voice,
                "rate": body.get("rate", "+10%"),
            }
    if "prompt" in body:
        updates["prompt"] = body["prompt"]
    if "mute_xiaoai" in body:
        updates["mute_xiaoai"] = bool(body["mute_xiaoai"])
    if "stream" in body:
        updates["stream"] = bool(body["stream"])
    if "transition" in body:
        updates["transition"] = str(body["transition"]).strip()

    # 免唤醒：把空串 "" 放进 keyword 列表 —— xiaogpt 用 contains 匹配，
    # 空串命中任意文本，等于"全部转给助手"；box_bridge 也据此跳过唤醒词判断。
    if "no_keyword" in body:
        no_kw = bool(body["no_keyword"])
        words = updates.get("keyword") or (load_config().get("keyword") or [])
        words = [w for w in words if w]
        updates["keyword"] = ([""] + words) if no_kw else words

    if "box_ip" in body:
        # 盒子地址：允许清空（清空后会重新扫描局域网）
        ip = str(body.get("box_ip") or "").strip()
        updates["box_ip"] = ip

    if updates:
        save_config(updates)
        did_restart = False
        pending = False

        # 音色/触发词/人设改动 → 重启音箱监听服务才生效（严格单实例）
        need_listener = any(
            k in updates
            for k in ("keyword", "tts", "prompt", "mute_xiaoai", "stream", "transition")
        )
        if need_listener:
            if supervisor.xiaogpt.alive():
                supervisor.restart_listener()
                did_restart = True
            else:
                pending = True

        # 盒子地址 / 免唤醒改动 → 重启盒子桥接
        if "box_ip" in updates or "keyword" in updates:
            if supervisor.boxbridge.alive():
                try:
                    supervisor.boxbridge.stop()
                    time.sleep(0.8)
                    supervisor.boxbridge.start()
                    did_restart = True
                except Exception as e:
                    print(f"[设置] 重启盒子桥接失败: {e}")
            else:
                pending = True

        # restarted=即刻生效 / pending=已保存但要等启动后才生效
        return jsonify({
            "ok": True,
            "restarted": did_restart,
            "pending": pending,
        })

    return jsonify({"ok": False, "msg": "没有可更新的字段"})


# ---------------- 米家设备 ----------------
def _mijia():
    """延迟导入 bridge/mijia（避免启动阶段依赖失败影响主程序）。"""
    import sys as _sys

    p = str(BRIDGE_DIR)
    if p not in _sys.path:
        _sys.path.insert(0, p)
    import mijia  # noqa: E402

    return mijia


@app.get("/api/devices")
def api_devices():
    """设备列表 + 当前状态（8 秒缓存，?force=1 强制刷新）。"""
    try:
        snap = _mijia().snapshot(force=request.args.get("force") == "1")
        return jsonify(snap)
    except Exception as e:
        return jsonify({"devices": [], "updated_at": 0, "error": str(e)[:160]})


@app.post("/api/devices/set")
def api_devices_set():
    """控制设备：{did, siid, piid, value}。"""
    body = request.get_json(silent=True) or {}
    did = body.get("did")
    siid, piid = body.get("siid"), body.get("piid")
    if not did or siid is None or piid is None:
        return jsonify({"ok": False, "msg": "参数不足"})
    try:
        return jsonify(_mijia().set_prop(did, siid, piid, body.get("value")))
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)[:140]})


# ---------------- 对话记录 ----------------
def read_dialogue(limit: int = 100) -> list:
    """合并本地问答 + 云端音箱记录。"""
    items = []
    if DIALOGUE_LOG.exists():
        for line in DIALOGUE_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            items.append({
                "time": e.get("time", 0),
                "query": e.get("query", ""),
                "answer": e.get("answer", ""),
                "source": e.get("source", "speaker"),
            })
    items.sort(key=lambda x: x["time"])
    return items[-limit:]


@app.get("/api/dialogue")
def api_dialogue():
    limit = int(request.args.get("limit", 100))
    return jsonify(read_dialogue(limit))


@app.get("/api/dialogue/stream")
def api_dialogue_stream():
    """SSE：把新出现的问答实时推给前端。"""

    def gen():
        seen = len(read_dialogue(10000))
        yield "retry: 3000\n\n"
        while True:
            time.sleep(2)
            items = read_dialogue(10000)
            if len(items) > seen:
                for item in items[seen:]:
                    yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                seen = len(items)
            else:
                yield ": ping\n\n"

    return Response(gen(), mimetype="text/event-stream")


@app.get("/api/log")
def api_log():
    """日志抽屉：返回指定服务的日志尾部。"""
    name = request.args.get("name", "xiaogpt")
    f = BRIDGE_DIR / f"{name}_ts.log"
    if not f.exists():
        return jsonify({"text": "暂无日志"})
    text = f.read_text(encoding="utf-8", errors="replace")
    return jsonify({"text": text[-4000:]})


# ---------------- 系统集成：防火墙 / 窗口图标 ----------------
WINDOW_TITLE = "天声小爱 — 语音助手"


def _ensure_firewall_rule(port: int = 8100) -> None:
    """放行音频服务端口（音箱要从局域网拉取合成的 MP3）。

    Windows 防火墙默认拦截入站连接；不放行时的表现很迷惑：
    **语音识别正常、但音箱一声不响**。
    需要管理员权限，失败只提示、不阻塞启动。
    """
    import subprocess as _sp

    rule = f"TianshengTTS{port}"
    try:
        r = _sp.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", f"name={rule}"],
            capture_output=True, text=True, timeout=8,
            encoding="utf-8", errors="replace",
            creationflags=0x08000000,
        )
        if r.returncode == 0 and rule in (r.stdout or ""):
            return

        r2 = _sp.run(
            ["netsh", "advfirewall", "firewall", "add", "rule",
             f"name={rule}", "dir=in", "action=allow", "protocol=TCP",
             f"localport={port}", "profile=any"],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
            creationflags=0x08000000,
        )
        if r2.returncode == 0:
            print(f"[防火墙] 已放行 TCP {port}（音箱可拉取音频）")
        else:
            msg = (r2.stdout or r2.stderr or "").strip()[:100]
            print(f"[防火墙] 放行失败（需管理员权限）：{msg}")
    except Exception as e:
        print(f"[防火墙] 检查异常: {e}")


def _set_app_user_model_id(app_id: str = "TianshengXiaoai.Desktop") -> None:
    """让任务栏把本程序识别为独立应用（不与 pythonw.exe 合并分组）。"""
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception as e:
        print(f"[图标] AppUserModelID 设置失败: {e}")


def _find_hwnd_by_title(title: str):
    """按窗口标题查找窗口句柄。"""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        found: list = []
        PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _cb(hwnd, _lparam):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                if buf.value == title:
                    found.append(hwnd)
            return True

        user32.EnumWindows(PROC(_cb), 0)
        return found[0] if found else None
    except Exception:
        return None


# 主窗口引用（托盘激活时用 pywebview 原生 show()，比纯 Win32 更可靠）
_MAIN_WINDOW = None
# 是否正在「真正退出」：区分用户点 X（隐藏到托盘）和托盘菜单退出（结束进程）
_EXITING = False

# ---------------- 主窗口控制（供托盘调用） ----------------
WM_CLOSE = 0x0010
SW_RESTORE = 9
SW_HIDE = 0
SM_SHUTTINGDOWN = 0x2000


def _system_shutting_down() -> bool:
    """系统关机/注销时不许拦截关闭，否则会拖住关机流程。"""
    try:
        import ctypes

        return bool(ctypes.windll.user32.GetSystemMetrics(SM_SHUTTINGDOWN))
    except Exception:
        return False


def _main_hwnd():
    return _find_hwnd_by_title(WINDOW_TITLE)


def activate_main_window() -> bool:
    """激活/恢复控制台窗口（含已最小化到托盘的情况），**不再另开浏览器**。"""
    import ctypes

    hwnd = _main_hwnd()
    if _MAIN_WINDOW is not None:
        try:
            _MAIN_WINDOW.show()
        except Exception:
            pass
    if not hwnd:
        return _MAIN_WINDOW is not None
    try:
        u = ctypes.windll.user32
        u.ShowWindow(hwnd, SW_RESTORE)      # 隐藏/最小化时先还原
        u.SetForegroundWindow(hwnd)         # 置前
        return True
    except Exception as e:
        print(f"[窗口] 激活失败: {e}")
        return False


def quit_app() -> None:
    """托盘「退出」：放行窗口关闭 → on_closed 清理 → 整个进程结束。"""
    global _EXITING
    _EXITING = True          # 关键：让 closing 事件不再拦截（区别于用户点 X）
    hwnd = _main_hwnd()
    if hwnd:
        try:
            import ctypes

            ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            # 正常链路：on_closed → shutdown + 托盘停止 → webview.start 返回 → 进程结束。
            # 万一窗口关不掉，3 秒后强制收尾，避免托盘退出后程序还在后台。
            t = threading.Timer(3.0, _force_exit)
            t.daemon = True
            t.start()
            return
        except Exception as e:
            print(f"[退出] 关闭窗口失败: {e}")
    _force_exit()


def _force_exit() -> None:
    shutdown()
    try:
        (app_root() / ".instance.lock").unlink(missing_ok=True)
    except Exception:
        pass
    os._exit(0)


def _apply_window_icon(title: str = WINDOW_TITLE) -> None:
    """给窗口换上品牌图标（标题栏用 16px、任务栏用 32px）。"""
    import time as _t

    ico = app_root() / "app.ico"
    if not ico.exists():
        print("[图标] 未找到 app.ico，跳过窗口图标设置")
        return

    for _ in range(24):                    # 最多等约 12 秒，等窗口真正出现
        _t.sleep(0.5)
        hwnd = _find_hwnd_by_title(title)
        if not hwnd:
            continue
        try:
            import ctypes

            user32 = ctypes.windll.user32
            IMAGE_ICON = 1
            LR_LOADFROMFILE = 0x00000010
            WM_SETICON = 0x0080
            ICON_SMALL, ICON_BIG = 0, 1

            hbig = user32.LoadImageW(None, str(ico), IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
            hsmall = user32.LoadImageW(None, str(ico), IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
            if hbig:
                user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hbig)
            if hsmall:
                user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hsmall)
            print(f"[图标] 窗口图标已设置 (hwnd={hwnd})")
            return
        except Exception as e:
            print(f"[图标] 设置窗口图标失败: {e}")
            return
    print("[图标] 未找到窗口，图标未设置")


def main():
    global _MAIN_WINDOW
    url = f"http://127.0.0.1:{APP_PORT}/"

    # 任务栏图标分组标识（必须在创建窗口前设置）
    _set_app_user_model_id()
    # 放行音频端口（否则音箱拉不到播报音频）
    _ensure_firewall_rule()

    # 单实例保护（Windows 命名互斥量，启动瞬间即生效，无竞态）
    try:
        import ctypes

        ERROR_ALREADY_EXISTS = 183
        _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\TianshengXiaoai_SingleInstance")
        if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            print("天声小爱已在运行，直接激活现有界面")
            if not activate_main_window():
                webbrowser.open(url)
            return
    except Exception as e:
        print(f"[单实例] 互斥量创建失败，跳过保护: {e}")

    # 单实例锁（防止重复启动导致多个 xiaogpt 抢同一句语音）
    lock_file = app_root() / ".instance.lock"
    try:
        if lock_file.exists():
            old_pid = int(lock_file.read_text().strip())
            import subprocess as _sp
            r = _sp.run(["tasklist", "/FI", f"PID eq {old_pid}"],
                        capture_output=True, text=True,
                        encoding="utf-8", errors="replace")
            if str(old_pid) in r.stdout:
                print(f"天声小爱已在运行 (PID={old_pid})，激活现有界面")
                if not activate_main_window():
                    webbrowser.open(url)
                return
    except Exception:
        pass
    lock_file.write_text(str(__import__("os").getpid()), encoding="utf-8")
    atexit.register(lambda: lock_file.unlink(missing_ok=True))

    tray = Tray(
        url=url,
        on_start=lambda: supervisor.start_all(),
        on_stop=supervisor.stop_all,
        on_quit=quit_app,                # 退出整个程序（原来只停服务）
        on_open=activate_main_window,    # 激活已有窗口（原来另开浏览器）
    )
    tray.run_detached()

    # 定期同步托盘图标状态
    def watch():
        while True:
            time.sleep(4)
            alive = supervisor.xiaogpt.alive()
            tray.set_state(alive)

    threading.Thread(target=watch, daemon=True).start()

    # Flask 后台线程 + 原生桌面窗口（WebView2）
    def serve():
        app.run(host="127.0.0.1", port=APP_PORT, threaded=True, use_reloader=False)

    threading.Thread(target=serve, daemon=True).start()
    # 后台健康探测：token 有效性与音箱在线状态（界面状态必须真实）
    threading.Thread(target=_probe_loop, daemon=True).start()
    time.sleep(2)

    try:
        import webview

        window = webview.create_window(
            WINDOW_TITLE,
            url,
            width=1140,
            height=780,
            min_size=(900, 620),
            background_color="#f4f5f7",
            confirm_close=False,
        )
        _MAIN_WINDOW = window

        def on_closing():
            """点 X = 最小化到托盘；只有托盘「退出」或系统关机才真正关闭。

            pywebview 的 closing 事件里，handler 返回 False 会取消关闭。
            """
            # 托盘没起来时不能隐藏窗口，否则用户将没有任何退出入口
            if _EXITING or _system_shutting_down() or tray.icon is None:
                return True                  # 放行，走 on_closed 清理并退出
            try:
                import ctypes

                hwnd = _main_hwnd()
                if hwnd:
                    ctypes.windll.user32.ShowWindow(hwnd, SW_HIDE)
                else:
                    window.hide()
            except Exception as e:
                print(f"[窗口] 隐藏到托盘失败: {e}")
            try:
                if tray.icon and hasattr(tray.icon, "notify"):
                    tray.icon.notify("天声小爱已最小化到托盘，双击图标可恢复")
            except Exception:
                pass
            return False                     # 取消关闭，程序继续常驻托盘

        try:
            window.events.closing += on_closing
        except Exception as e:
            print(f"[窗口] 关闭事件注册失败: {e}")

        def on_shown():
            # 窗口出现后再套品牌图标（标题栏 + 任务栏）
            threading.Thread(target=_apply_window_icon, daemon=True).start()

        try:
            window.events.shown += on_shown
        except Exception:
            threading.Thread(target=_apply_window_icon, daemon=True).start()

        def on_closed():
            shutdown()
            if tray.icon:
                tray.icon.stop()

        window.events.closed += on_closed
        webview.start()          # 阻塞直到窗口关闭
    except Exception as e:
        # WebView 不可用时退回浏览器模式
        print(f"[窗口] 原生窗口启动失败（{e}），回退到浏览器模式")
        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    finally:
        shutdown()


if __name__ == "__main__":
    main()
