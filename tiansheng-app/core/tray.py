# -*- coding: utf-8 -*-
"""
天声小爱 - 系统托盘
==================
无窗口常驻：托盘图标左键打开界面，右键菜单控制启动/停止/退出。
"""

from __future__ import annotations

import threading
import webbrowser

import pystray
from PIL import Image, ImageDraw

# 托盘图标缓存（避免每次刷新都读盘）
_ICON_CACHE: dict[bool, Image.Image] = {}


def _fallback_icon(running: bool = False) -> Image.Image:
    """图标文件缺失时的兜底：深绿圆角方块 + 白色「天」字。"""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    bg = (15, 110, 86, 255) if running else (140, 148, 158, 255)
    d.rounded_rectangle([2, 2, size - 2, size - 2], radius=14, fill=bg)
    d.line([(16, 22), (48, 22)], fill=(255, 255, 255, 255), width=5)
    d.line([(12, 36), (52, 36)], fill=(255, 255, 255, 255), width=5)
    d.line([(32, 22), (26, 50)], fill=(255, 255, 255, 255), width=5)
    d.line([(32, 22), (40, 50)], fill=(255, 255, 255, 255), width=5)
    return img


def make_icon(running: bool = False) -> Image.Image:
    """托盘图标：优先加载品牌图标（彩色=运行中 / 灰色=已停止）。"""
    if running in _ICON_CACHE:
        return _ICON_CACHE[running]
    try:
        from core.paths import app_root

        name = "icon.png" if running else "icon_off.png"
        p = app_root() / name
        if p.exists():
            im = Image.open(p).convert("RGBA").resize((64, 64), Image.LANCZOS)
            _ICON_CACHE[running] = im
            return im
    except Exception:
        pass
    im = _fallback_icon(running)
    _ICON_CACHE[running] = im
    return im


class Tray:
    def __init__(self, url: str, on_start, on_stop, on_quit, on_open=None):
        self.url = url
        self.on_start = on_start
        self.on_stop = on_stop
        self.on_quit = on_quit
        # () -> bool：激活已有的原生窗口。返回 False 时才回退到浏览器
        self.on_open = on_open
        self.icon: pystray.Icon | None = None
        self._thread: threading.Thread | None = None

    def _build(self) -> pystray.Icon:
        menu = pystray.Menu(
            pystray.MenuItem("打开控制台", self._open, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("启动服务", self._start),
            pystray.MenuItem("停止服务", self._stop),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self._quit),
        )
        return pystray.Icon("tiansheng", make_icon(False), "天声小爱", menu)

    def _open(self, icon=None, item=None):
        """打开控制台：优先激活已有窗口，绝不再无脑新开浏览器标签页。"""
        if self.on_open:
            try:
                if self.on_open():
                    return
            except Exception as e:
                print(f"[托盘] 激活窗口失败: {e}")
        # 窗口确实不存在（例如原生窗口启动失败、退回浏览器模式）才开浏览器
        webbrowser.open(self.url)

    def _start(self, icon=None, item=None):
        threading.Thread(target=self.on_start, daemon=True).start()

    def _stop(self, icon=None, item=None):
        threading.Thread(target=self.on_stop, daemon=True).start()

    def _quit(self, icon=None, item=None):
        """退出整个程序。清理与进程收尾交给 on_quit（app.quit_app）统筹。"""
        try:
            self.on_quit()
        except Exception as e:
            print(f"[托盘] 退出异常: {e}")
            try:
                if self.icon:
                    self.icon.stop()
            except Exception:
                pass

    def set_state(self, running: bool, tip: str | None = None):
        if not self.icon:
            return
        try:
            self.icon.icon = make_icon(running)
            self.icon.title = tip or ("天声小爱 · 运行中" if running else "天声小爱 · 已停止")
        except Exception:
            pass

    def run_detached(self):
        self.icon = self._build()
        self._thread = threading.Thread(target=self.icon.run, daemon=True)
        self._thread.start()
