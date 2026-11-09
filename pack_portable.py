# -*- coding: utf-8 -*-
"""
打包便携版为 zip
================
除正常压缩外，会在**写入 zip 时**对 bridge/config.json 做脱敏，
剔除账号密码、登录态 cookie 和运行时自动写回的设备标识。

背景：程序运行（设备自动发现、登录）时会把 mi_did / speaker_device_id /
speaker_name / box_ip 写回 config.json。若直接压缩目录，分发包就会带上
作者家的音箱 ID 与内网 IP。这里在打包环节统一清掉（**不改动磁盘上的原文件**，
本机运行不受影响，新用户启动后会自动重新发现）。

用法：python pack_portable.py
"""
import json
import os
import zipfile
from pathlib import Path

SRC = Path(r"D:\WorkBuddy\2026-11-07-14-47-04\天声小爱-portable")
DST = Path(r"D:\WorkBuddy\2026-11-07-14-47-04\天声小爱-便携版.zip")

# 只在分发包里剔除，磁盘原文件不动
REDACT_KEYS = (
    "account", "password", "cookie", "mi_user_id", "mi_login_at",
    "mi_did", "speaker_device_id", "speaker_name", "box_ip",
)
# 这些字段清空但不删除：保持配置结构完整，新用户首次启动会自动写回真实值
RESET_KEYS = ("hardware",)
REDACT_FILE = "bridge/config.json"
FIXED = (2000, 1, 1, 0, 0, 0)   # 固定时间戳，规避 Windows Compress-Archive 异常

n = 0
with zipfile.ZipFile(DST, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
    for root, dirs, files in os.walk(SRC):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            full = Path(root) / f
            rel = full.relative_to(SRC).as_posix()
            with full.open("rb") as fh:
                data = fh.read()

            if rel == REDACT_FILE:
                try:
                    cfg = json.loads(data.decode("utf-8"))
                    for k in REDACT_KEYS:
                        cfg.pop(k, None)
                    for k in RESET_KEYS:
                        cfg[k] = ""
                    data = (json.dumps(cfg, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
                    print(f"[脱敏] {rel}: 已剔除运行时/敏感字段，保留 {len(cfg)} 个配置")
                except Exception as e:
                    print(f"[脱敏] {rel} 处理失败（原样打包）: {e}")

            zi = zipfile.ZipInfo(rel, date_time=FIXED)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            z.writestr(zi, data)
            n += 1

print(f"打包完成，共 {n} 个文件 -> {DST}")
print(f"大小: {DST.stat().st_size/1024/1024:.1f} MB")
