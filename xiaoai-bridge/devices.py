# -*- coding: utf-8 -*-
"""
设备发现
========
让程序在**任意账号 + 任意局域网**下都能找到自己的音箱与盒子。

要解决的问题（原来都是写死的）：
  · 音箱播报需要 `deviceID`（UUID）——每个账号、每台设备都不同
  · 盒子 ADB 地址 `ip:5555` —— 每个家庭的网段都不同

策略：**配置优先，自动发现兜底，发现结果写回配置**。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import socket
import time
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent
CONFIG = BRIDGE_DIR / "config.json"
TOKEN = Path.home() / ".mi.token"
BOX_PORT = 5555


# ---------------- 配置读写 ----------------
def load_cfg() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_cfg(updates: dict) -> None:
    cfg = load_cfg()
    cfg.update(updates)
    try:
        CONFIG.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        print(f"[设备] 写入配置失败: {e}")


def local_ipv4() -> str:
    """本机在默认路由上的局域网 IPv4。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return ""


# ---------------- 小米音箱 ----------------
async def _fetch_devices(cfg: dict) -> list:
    from aiohttp import ClientSession
    from miservice import MiAccount, MiNAService

    # 未登录时不发起请求（否则 miservice 会用密码登录，反复失败触发风控）
    if not TOKEN.exists():
        return []

    # 凭据守护：401 时 miservice 会删掉凭据文件，这里拦住（只降级不删除）
    try:
        import mi_guard

        mi_guard.apply()
    except Exception:
        pass

    async with ClientSession() as session:
        account = MiAccount(
            session, cfg.get("account", ""), cfg.get("password", ""), str(TOKEN)
        )
        return await MiNAService(account).device_list()


def fetch_mina_devices(cfg: dict | None = None) -> list:
    cfg = cfg or load_cfg()
    return asyncio.run(_fetch_devices(cfg)) or []


def pick_speaker(devices: list, hardware: str = "") -> dict | None:
    """从设备列表里挑出小爱音箱。

    不能假设「LX」前缀 —— 小米音箱还有 L05B/L06A/X08A/X10A/OH2P/X6A 等一大票型号。
    策略：配置型号精确匹配 → 在线设备 → 任意带 deviceID 的设备。
    """
    devices = devices or []
    if hardware:
        for d in devices:
            if d.get("hardware") == hardware:
                return d
    for d in devices:
        if d.get("deviceID") and d.get("presence") == "online":
            return d
    for d in devices:
        if d.get("deviceID"):
            return d
    return None


def resolve_speaker(cfg: dict | None = None, save: bool = True) -> dict:
    """解析音箱信息，返回 {mi_did, device_id, name, hardware}。

    一次 micoapi device_list 就能拿到全部字段，取到后写回配置，下次直接读。
    `hardware` 也写回：xiaogpt 在 mi_did 缺失时用它兜底，必须是用户真实的型号。
    """
    cfg = cfg or load_cfg()
    mi_did = str(cfg.get("mi_did") or "")
    device_id = str(cfg.get("speaker_device_id") or "")
    name = str(cfg.get("speaker_name") or "")
    hardware = str(cfg.get("hardware") or "")

    if mi_did and device_id:
        return {"mi_did": mi_did, "device_id": device_id,
                "name": name, "hardware": hardware}

    try:
        dev = pick_speaker(fetch_mina_devices(cfg), hardware)
    except Exception as e:
        print(f"[设备] 查询音箱失败: {str(e)[:140]}")
        return {"mi_did": mi_did, "device_id": device_id,
                "name": name, "hardware": hardware}

    if not dev:
        print("[设备] 账号下未找到小爱音箱，请确认已在米家中添加")
        return {"mi_did": mi_did, "device_id": device_id,
                "name": name, "hardware": hardware}

    out = {
        "mi_did": str(dev.get("miotDID") or mi_did),
        "device_id": str(dev.get("deviceID") or device_id),
        "name": str(dev.get("name") or name),
        "hardware": str(dev.get("hardware") or hardware),
    }
    if save and out["mi_did"] and out["device_id"]:
        save_cfg(
            {
                "mi_did": out["mi_did"],
                "speaker_device_id": out["device_id"],
                "speaker_name": out["name"],
                "hardware": out["hardware"],
            }
        )
    print(f"[设备] 音箱已解析: {out['name']} (did={out['mi_did']})")
    return out


# ---------------- 小米盒子 ----------------
def _port_open(ip: str, port: int = BOX_PORT, timeout: float = 0.25) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return s.connect_ex((ip, port)) == 0
    except Exception:
        return False


def scan_lan(port: int = BOX_PORT, timeout: float = 0.25, workers: int = 64) -> list:
    """并发扫描本机所在网段，返回开放指定端口的 IP 列表。"""
    ip = local_ipv4()
    if not ip:
        return []
    prefix = ip.rsplit(".", 1)[0] + "."
    cand = [f"{prefix}{i}" for i in range(1, 255)]
    hits = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for host, ok in zip(cand, ex.map(lambda h: _port_open(h, port, timeout), cand)):
            if ok:
                hits.append(host)
    return hits


def resolve_box_serial(cfg: dict | None = None, save: bool = True) -> str:
    """解析盒子 ADB 地址，返回 "ip:5555"；解析不到返回空串。"""
    cfg = cfg or load_cfg()
    box_ip = str(cfg.get("box_ip") or "").strip()
    if box_ip:
        return f"{box_ip}:{BOX_PORT}"

    print("[设备] 未配置盒子地址，扫描局域网 5555 端口 …")
    t0 = time.time()
    hits = scan_lan()
    print(f"[设备] 扫描完成: {len(hits)} 个候选 {hits}（{time.time() - t0:.1f}s）")
    if not hits:
        print("[设备] 未发现开启 ADB 的盒子；请确认盒子已开 ADB 调试，或手动填写盒子 IP")
        return ""

    ip = hits[0]
    if save:
        save_cfg({"box_ip": ip})
        print(f"[设备] 已记录盒子地址: {ip}")
    return f"{ip}:{BOX_PORT}"


def forget_box_ip() -> None:
    """清除已记录的盒子地址（连接失败时用于触发重新扫描）。"""
    save_cfg({"box_ip": ""})
