# -*- coding: utf-8 -*-
"""
给已安装的 xiaogpt 3.24 应用「天声小爱增强补丁」
================================================
用法：
    python apply_patch.py

前提：已按 requirements.txt 安装 xiaogpt==3.24。
补丁内容（详见同目录 xiaogpt-tiansheng.patch）：
  · 无条件打断小爱播报（pause + ubus 双通道，兼容 LX05 不可靠的状态接口）
  · 轮询加速至 0.05~0.4s，打断响应更及时
  · 唤醒词「包含匹配」，规避小米 ASR 同音字问题
  · TIANSHENG_PREFIXES / TIANSHENG_TRANSITION 环境变量支持
  · config.py 硬件指令表扩充
"""

import re
import sys
from pathlib import Path

PATCH = Path(__file__).parent / "xiaogpt-tiansheng.patch"
REQUIRED = "3.24"


def locate_pkg() -> Path:
    try:
        import xiaogpt  # noqa: F401
    except ImportError:
        sys.exit("[ERROR] 未找到 xiaogpt，请先: pip install xiaogpt==3.24")
    import xiaogpt
    return Path(xiaogpt.__file__).parent


def parse_patch(text: str):
    """解析 unified diff → [(relative_path, [hunk, ...]), ...]"""
    files, cur = [], None
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("--- "):
            old = line[4:].split("\t")[0]
            new = lines[i + 1][4:].split("\t")[0]
            rel = new if not new.startswith("/dev/") else old
            rel = re.sub(r"^[ab]/", "", rel)
            cur = (rel, [])
            files.append(cur)
            i += 2
            continue
        if line.startswith("@@") and cur is not None:
            m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            a_start = int(m.group(1))
            hunk = []
            i += 1
            while i < len(lines) and not lines[i].startswith("@@") and not lines[i].startswith("--- "):
                hunk.append(lines[i])
                i += 1
            cur[1].append((a_start, hunk))
            continue
        i += 1
    return files


def apply_hunks(src_lines: list[str], hunks) -> list[str]:
    """按 hunk 从后往前应用，避免行号偏移。"""
    out = src_lines[:]
    for a_start, hunk in sorted(hunks, key=lambda h: -h[0]):
        # hunk 中的行：' ' 上下文 / '-' 删除 / '+' 新增
        ctx_del = [l[1:] for l in hunk if l[0] in (" ", "-")]
        add = [l[1:] for l in hunk if l[0] in (" ", "+")]
        start = a_start - 1  # 0-based
        # 校验上下文匹配
        for k, expect in enumerate(ctx_del):
            if out[start + k].rstrip("\n") != expect.rstrip("\n"):
                sys.exit(f"[ERROR] 第 {a_start + k + 1} 行上下文不匹配，"
                         f"请确认 xiaogpt 版本为 {REQUIRED} 且未修改过。\n"
                         f"  期望: {expect!r}\n  实际: {out[start + k]!r}")
        out[start:start + len(ctx_del)] = add
    return out


def main():
    pkg = locate_pkg()
    version = getattr(__import__("xiaogpt"), "__version__", "?")
    print(f"[OK] 找到 xiaogpt {version} @ {pkg}")
    if version != REQUIRED:
        print(f"[WARN] 本补丁针对 xiaogpt {REQUIRED}，当前 {version}，可能应用失败")

    for rel, hunks in parse_patch(PATCH.read_text(encoding="utf-8")):
        target = pkg.parent / rel
        print(f"[..] 应用 {rel}（{len(hunks)} 个 hunk）")
        src = target.read_text(encoding="utf-8").splitlines()
        result = apply_hunks(src, hunks)
        target.write_text("\n".join(result) + "\n", encoding="utf-8")
        print(f"[OK] {rel} 完成")

    print("\n[DONE] 补丁应用完成。重新启动语音服务即可生效。")


if __name__ == "__main__":
    main()
