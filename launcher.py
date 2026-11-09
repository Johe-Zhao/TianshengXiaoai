# -*- coding: utf-8 -*-
"""
天声小爱 — 启动器（Launcher）
==============================
双击后：以无窗口方式启动便携 Python 运行 app/app.py（GUI 本体），
自身立即退出。真正的界面由 app.py 通过 pywebview 原生窗口呈现。

目录结构约定（launcher 所在目录为根）：
  天声小爱.exe
    ├── app/            （app.py + core/ + web/）
    ├── bridge/         （后端脚本 + config.json）
    └── runtime/
         ├── python/    （便携 Python）
         └── node/      （便携 Node + codebuddy-code）
"""

import ctypes
import os
import subprocess
import sys
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000


def _msg(text: str, title: str = "天声小爱") -> None:
    try:
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)
    except Exception:
        pass


def main() -> int:
    root = Path(sys.executable).resolve().parent
    pythonw = root / "runtime" / "python" / "pythonw.exe"
    app_py = root / "app" / "app.py"

    missing = []
    if not pythonw.exists():
        missing.append(str(pythonw))
    if not app_py.exists():
        missing.append(str(app_py))
    if missing:
        _msg("运行时文件缺失，请确认包完整：\n\n" + "\n".join(missing))
        return 1

    env = dict(os.environ)
    env["TIANSHENG_BUNDLED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"

    try:
        subprocess.Popen(
            [str(pythonw), str(app_py)],
            cwd=str(root / "app"),
            env=env,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception as e:
        _msg(f"启动失败：{e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
