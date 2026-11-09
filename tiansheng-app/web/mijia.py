# -*- coding: utf-8 -*-
"""
米家设备：发现、状态读取、属性控制
==================================
给控制台看板和设备控制提供数据。

**关键修复**：miservice 的 `UA_MIIO` 里硬编码了别人的 `userId`，
小米服务端会返回 `-127 client withdraw`（表现为所有米家接口不可用）。
这里在导入时用当前 token 的真实身份重建 UA，之后 device_list / miotspec 全部正常。

缓存策略（避免频繁请求小米接口）：
  · 设备列表 30 秒
  · 设备状态 8 秒
  · 设备规格（siid/piid 定义）落盘永久缓存
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

BRIDGE = Path(__file__).resolve().parent
CONFIG = BRIDGE / "config.json"
CACHE_DIR = BRIDGE / ".cache"
SPEC_FILE = CACHE_DIR / "miot_index.json"    # model -> urn
DETAIL_FILE = CACHE_DIR / "miot_detail.json"  # urn -> 规格详情
TOKEN = Path.home() / ".mi.token"

DEV_TTL = 30.0
STATE_TTL = 8.0

_lock = threading.Lock()
_dev_cache: dict = {"at": 0.0, "list": []}
_state_cache: dict = {}   # did -> {"at": float, "props": list}

# ---------------- 属性中文名 ----------------
LABELS = {
    "on": "开关", "switch-status": "开关", "power": "开关",
    "brightness": "亮度", "color-temperature": "色温", "color": "颜色",
    "mode": "模式", "saturability": "饱和度",
    "fan-level": "风速", "gear-fan-level": "档位", "stepless-fan-level": "无级风速",
    "horizontal-swing": "左右摆风", "vertical-swing": "上下摆风",
    "horizontal-swing-included-angle": "摆风角度", "swing-mode": "摆风模式",
    "target-temperature": "目标温度", "temperature": "温度",
    "relative-humidity": "湿度", "humidity": "湿度",
    "pm2.5-density": "PM2.5", "pm10-density": "PM10",
    "air-quality": "空气质量", "aqi": "空气质量",
    "filter-life-level": "滤芯余量", "filter-used-time": "滤芯已用",
    "total-dissolved-solids-for-input-water": "进水TDS",
    "total-dissolved-solids-for-output-water": "出水TDS",
    "water-level": "水位", "alarm": "报警", "device-fault": "故障",
    "status": "状态", "heat-level": "加热档",
    "child-lock": "童锁", "physical-controls-locked": "按键锁",
    "screen-brightness": "屏幕亮度", "volume": "音量", "mute": "静音",
    "eco": "ECO", "heater": "辅热", "dryer": "干燥", "anion": "负离子",
    "uv": "UV杀菌", "countdown-time": "倒计时", "playing-state": "播放状态",
    "holiday-mode": "假期模式", "anti-icing-remind": "防冻提醒",
    "self-check-results": "自检", "device-id": "设备ID",
    "fault": "故障", "target-time": "定时",
}

# 不属于"用户关心的状态"，不展示
SKIP_NAMES = {
    "device-manufacturer", "device-model", "device-serial-number", "device-name",
    "current-firmware-version", "device-id", "common-params", "count",
    "action-after-finish", "self-check-results", "anti-icing-remind",
}

UNIT = {"color-temperature": "K", "temperature": "℃", "target-temperature": "℃",
        "relative-humidity": "%", "humidity": "%", "brightness": "", "saturability": "%",
        "filter-life-level": "%", "volume": ""}

# 枚举值中文（规格里的 description 是英文）
VALUE_CN = {
    "auto": "自动", "manual": "手动", "cool": "制冷", "heat": "制热", "dry": "除湿",
    "fan": "送风", "idle": "待机", "busy": "运行中", "sleep": "睡眠", "off": "关",
    "on": "开", "level1": "1 档", "level2": "2 档", "level3": "3 档", "level4": "4 档",
    "level5": "5 档", "straight wind": "直吹", "natural wind": "自然风",
    "sleep wind": "睡眠风", "no faults": "无故障", "fault": "故障", "color": "彩光",
    "day": "日光", "night": "夜间", "recover before": "恢复", "stay": "保持",
    "turn off": "关闭", "low": "低", "middle": "中", "high": "高", "silent": "静音",
    "turbo": "强力", "warm": "暖光", "cold": "冷光", "none": "无",
    "idle.": "待机", "washing": "清洗中", "heating": "加热中", "cooling": "制冷中",
}

TYPE_CN = {
    "light": "灯光", "fan": "风扇", "airconditioner": "空调", "purifier": "净化器",
    "waterheater": "热水器", "waterpurifier": "净水器", "router": "路由",
    "speaker": "音箱", "plug": "插座", "humidifier": "加湿器", "other": "设备",
}

POWER_KEYS = ("on", "switch-status", "power")

TYPE_RULES = [
    ("light", ("light",)),
    ("fan", ("fan",)),
    ("airconditioner", ("aircondition",)),
    ("purifier", ("airp.", "airpuri", "airpurifier")),
    ("waterheater", ("waterheater",)),
    ("waterpurifier", ("waterpuri",)),
    ("router", ("router",)),
    ("speaker", ("speaker",)),
    ("plug", ("plug", "switch")),
    ("humidifier", ("humidifier",)),
]


# ---------------- 基础 ----------------
def _patch_ua() -> str:
    """按当前 token 的真实身份重建 xiaomiio 的 UA（修 -127 风控）。"""
    try:
        import miservice.miioservice as mio

        tok = json.loads(TOKEN.read_text(encoding="utf-8"))
        uid = str(tok.get("userId") or "")
        dev = str(tok.get("deviceId") or "")
        if uid and dev:
            mio.UA_MIIO = (
                f"iOS-14.4-6.0.103-iPhone12,3--{dev}-{uid}-84A3F44E137B71AE-iPhone"
            )
        return uid
    except Exception:
        return ""


_patch_ua()


def _cfg() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_json(p: Path, data) -> None:
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _type_of(model: str) -> str:
    m = (model or "").lower()
    for name, keys in TYPE_RULES:
        for k in keys:
            if k in m:
                return name
    return "other"


# ---------------- 小米接口封装 ----------------
async def _io():
    """建立 MiIOService（每次新建会话，Flask 多线程下最稳）。"""
    import aiohttp
    import miservice.miioservice as mio
    from miservice import MiAccount

    cfg = _cfg()
    session = aiohttp.ClientSession()
    acc = MiAccount(session, cfg.get("account", ""), cfg.get("password", ""), str(TOKEN))
    return session, mio.MiIOService(acc)


async def _fetch_devices():
    session, io = await _io()
    try:
        return await io.device_list(name="full")
    finally:
        await session.close()


async def _spec_index() -> dict:
    """model -> urn 索引（落盘缓存）。"""
    cached = _load_json(SPEC_FILE)
    if cached:
        return cached

    session, io = await _io()
    try:
        idx = await io.miot_spec()
    finally:
        await session.close()
    if isinstance(idx, dict) and idx:
        _save_json(SPEC_FILE, idx)
    return idx if isinstance(idx, dict) else {}


async def _spec_detail(urn: str) -> dict:
    """某型号的规格详情（落盘缓存）。"""
    cache = _load_json(DETAIL_FILE)
    if urn in cache:
        return cache[urn]

    # miot-spec.org 公开接口，不需要账号
    import aiohttp

    async with aiohttp.ClientSession() as s:
        async with s.get("https://miot-spec.org/miot-spec-v2/instance", params={"type": urn}) as r:
            detail = await r.json(content_type=None)
    if isinstance(detail, dict) and detail.get("services"):
        cache[urn] = detail
        _save_json(DETAIL_FILE, cache)
    return detail if isinstance(detail, dict) else {}


def _control_meta(detail: dict) -> list:
    """从规格里提取可展示/可操作的属性元数据。"""
    out = []
    for svc in detail.get("services", []) or []:
        siid = svc.get("iid")
        if siid == 1:                      # 设备信息，跳过
            continue
        svc_desc = (svc.get("description") or "").lower()
        for p in svc.get("properties", []) or []:
            name = (p.get("description") or "").strip()
            key = name.lower().replace(" ", "-")
            if not name or key in SKIP_NAMES:
                continue
            access = p.get("access") or []
            if "read" not in access:
                continue
            fmt = p.get("format") or ""
            rng = p.get("value-range")
            lst = p.get("value-list")
            if fmt == "bool":
                kind = "bool"
            elif lst:
                kind = "enum"
            elif rng:
                kind = "range"
            elif fmt in ("uint8", "uint16", "uint32", "int8", "int16", "int32", "float"):
                kind = "number"
            else:
                continue                    # string 之类不展示

            item = {
                "siid": siid,
                "piid": p.get("iid"),
                "key": key,
                "label": LABELS.get(key, name),
                "kind": kind,
                "rw": "write" in access,
                "service": svc_desc,
            }
            if kind == "range" and rng:
                item["min"], item["max"], item["step"] = rng[0], rng[1], (rng[2] if len(rng) > 2 else 1)
            if kind == "enum" and lst:
                item["options"] = [
                    {
                        "v": o.get("value"),
                        "t": VALUE_CN.get(
                            (o.get("description") or "").lower(), o.get("description")
                        ),
                    }
                    for o in lst
                ][:16]
            if key in UNIT:
                item["unit"] = UNIT[key]
            out.append(item)
    return out


def _summary(controls: list) -> str:
    """卡片摘要：数值 > 枚举 > 布尔；只取已中文化的属性。"""
    def known(c):
        return c["key"] in LABELS

    parts = []
    for key in ("target-temperature", "temperature", "brightness", "relative-humidity",
                "filter-life-level", "volume"):
        for c in controls:
            if c["key"] == key and c.get("value") is not None and known(c):
                parts.append(f"{c['label']}{c['value']}{c.get('unit', '')}")
                break
        if len(parts) >= 2:
            break

    for c in controls:
        if len(parts) >= 3:
            break
        if c["kind"] == "enum" and c.get("value") is not None and known(c) and c["key"] != "status":
            t = next((o["t"] for o in c.get("options", []) if o["v"] == c["value"]), None)
            if t:
                parts.append(t)

    for c in controls:
        if len(parts) >= 3:
            break
        if (c["kind"] == "bool" and c["key"] not in POWER_KEYS
                and c.get("value") is not None and known(c)
                and c["key"] not in ("alarm", "device-fault", "child-lock",
                                     "physical-controls-locked", "mute")):
            parts.append(f"{c['label']}{'开' if c['value'] else '关'}")

    return " · ".join(parts[:3])


async def _one_device(dev: dict, spec_idx: dict) -> dict:
    model = dev.get("model") or ""
    did = str(dev.get("did") or "")
    base = {
        "did": did,
        "name": dev.get("name") or model,
        "model": model,
        "online": bool(dev.get("isOnline")),
        "ip": dev.get("localip") or "",
        "rssi": dev.get("rssi") if dev.get("rssi") not in (None, 0) else None,
        "type": _type_of(model),
        "type_cn": TYPE_CN.get(_type_of(model), "设备"),
        "controls": [],
        "primary": None,
        "summary": "",
        "spec_ok": False,
    }

    urn = spec_idx.get(model)
    if not urn:
        return base
    try:
        detail = await _spec_detail(urn)
        controls = _control_meta(detail)
    except Exception:
        return base
    if not controls:
        return base

    # 批量读值（一次请求）
    session, io = await _io()
    try:
        vals = await io.miot_get_props(did, [(c["siid"], c["piid"]) for c in controls])
    except Exception:
        vals = [None] * len(controls)
    finally:
        await session.close()

    for c, v in zip(controls, vals):
        c["value"] = v

    # 同名属性去重（不同 service 可能有同标签属性）
    seen, uniq = set(), []
    for c in controls:
        if c["label"] in seen:
            continue
        seen.add(c["label"])
        uniq.append(c)
    controls = uniq

    # 主开关
    for c in controls:
        if c["key"] in POWER_KEYS and c["kind"] == "bool":
            base["primary"] = {
                "siid": c["siid"], "piid": c["piid"], "value": c["value"],
                "rw": c["rw"],
            }
            break

    base["controls"] = controls
    base["summary"] = _summary(controls)
    base["spec_ok"] = True
    return base


# ---------------- 对外接口 ----------------
def devices(force: bool = False) -> list:
    """设备列表（含在线状态，30 秒缓存）。"""
    with _lock:
        if not force and time.time() - _dev_cache["at"] < DEV_TTL and _dev_cache["list"]:
            return _dev_cache["list"]
    try:
        lst = asyncio.run(_fetch_devices()) or []
    except Exception:
        return _dev_cache["list"]
    slim = [
        {
            "did": str(d.get("did") or ""),
            "name": d.get("name") or d.get("model"),
            "model": d.get("model") or "",
            "online": bool(d.get("isOnline")),
            "ip": d.get("localip") or "",
            "rssi": d.get("rssi") if d.get("rssi") not in (None, 0) else None,
        }
        for d in lst
    ]
    with _lock:
        _dev_cache["at"] = time.time()
        _dev_cache["list"] = slim
    return slim


async def _snapshot_async() -> dict:
    devs = await _fetch_devices() or []
    idx = await _spec_index()

    sem = asyncio.Semaphore(4)

    async def guarded(d):
        async with sem:
            try:
                return await _one_device(d, idx)
            except Exception:
                return {
                    "did": str(d.get("did") or ""),
                    "name": d.get("name"),
                    "model": d.get("model") or "",
                    "online": bool(d.get("isOnline")),
                    "ip": d.get("localip") or "",
                    "rssi": None,
                    "type": _type_of(d.get("model") or ""),
                    "controls": [],
                    "primary": None,
                    "summary": "",
                    "spec_ok": False,
                }

    results = await asyncio.gather(*[guarded(d) for d in devs])
    return {"updated_at": time.time(), "devices": list(results)}


def snapshot(force: bool = False) -> dict:
    """全部设备 + 状态（8 秒缓存）。"""
    key = "__all__"
    with _lock:
        c = _state_cache.get(key)
        if not force and c and time.time() - c["at"] < STATE_TTL:
            return c["data"]
    try:
        data = asyncio.run(_snapshot_async())
    except Exception as e:
        with _lock:
            c = _state_cache.get(key)
        return c["data"] if c else {"updated_at": 0, "devices": [], "error": str(e)[:120]}
    with _lock:
        _state_cache[key] = {"at": time.time(), "data": data}
    return data


async def _set_prop_async(did: str, siid: int, piid: int, value) -> int:
    session, io = await _io()
    try:
        return await io.miot_set_prop(did, (siid, piid), value)
    finally:
        await session.close()


def set_prop(did: str, siid: int, piid: int, value) -> dict:
    """控制设备。返回 {'ok': bool, 'code': int}。"""
    try:
        code = asyncio.run(_set_prop_async(str(did), int(siid), int(piid), value))
    except Exception as e:
        return {"ok": False, "code": -1, "msg": str(e)[:140]}
    ok = code == 0
    if ok:
        # 让下一次读取拿到新值
        with _lock:
            _state_cache.pop("__all__", None)
    return {"ok": ok, "code": code}


def spec_status() -> dict:
    """规格缓存情况（给界面提示用）。"""
    idx = _load_json(SPEC_FILE)
    det = _load_json(DETAIL_FILE)
    return {"models": len(idx), "details": len(det)}
