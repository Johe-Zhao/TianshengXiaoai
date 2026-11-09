# -*- coding: utf-8 -*-
"""
天声小爱 — 图标生成
===================
按 AI 稿（Recolor_..._08-12-55.png）实测的配色与几何参数，用代码重绘，
再生成 16~256 全尺寸 PNG 与多尺寸 ICO。超采样 4 倍保证边缘锐利。

设计要素：
  · 米家绿垂直渐变圆底
  · 白色圆角气泡（含底部三角尾巴）
  · 深绿圆角「T」
  · 两条加粗白色声波弧

注意：ICO 采用「BMP(DIB)」而非 PNG 压缩格式——PNG-in-ICO 在部分 Windows
版本的资源管理器/图标缓存里会读取失败、显示空白，BMP 格式兼容性最好。
"""

import math
import os
import struct

from PIL import Image, ImageDraw

OUT_DIR = os.path.join(os.path.dirname(__file__), "icons")
APP_DIR = os.path.join(os.path.dirname(__file__), "tiansheng-app")
S = 1024          # 设计基准尺寸
SS = 4            # 超采样倍数
W = S * SS

# ── 配色（取自 AI 稿实测）──
GRAD = [
    (0.00, (28, 199, 129)),   # 顶部亮绿 #1CC781
    (0.50, (17, 128, 88)),    # 中部 #118058
    (1.00, (1, 61, 50)),      # 底部深绿 #013D32
]
T_COLOR = (0, 69, 52, 255)    # #004534
WHITE = (255, 255, 255, 255)


def _grad_color(t: float):
    t = max(0.0, min(1.0, t))
    for i in range(len(GRAD) - 1):
        t0, c0 = GRAD[i]
        t1, c1 = GRAD[i + 1]
        if t0 <= t <= t1:
            k = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            return tuple(int(c0[j] + (c1[j] - c0[j]) * k) for j in range(3))
    return GRAD[-1][1]


def _gradient_image(size: int) -> Image.Image:
    """生成 size x size 的垂直渐变（先做 1 x 1024 再拉伸，快速且平滑）。"""
    strip = Image.new("RGB", (1, 1024))
    px = strip.load()
    for y in range(1024):
        px[0, y] = _grad_color(y / 1023)
    return strip.resize((size, size), Image.BILINEAR)


def thick_arc(draw, cx, cy, rx, ry, a0, a1, width, color, steps=520):
    """沿椭圆弧画粗线（端点自然圆头）。"""
    r = width / 2.0
    for i in range(steps + 1):
        a = math.radians(a0 + (a1 - a0) * i / steps)
        x = cx + rx * math.cos(a)
        y = cy + ry * math.sin(a)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=color)


def build_master() -> Image.Image:
    """绘制 4096x4096 超采样母版。"""
    sc = lambda v: v * SS  # noqa: E731

    # 1) 垂直渐变底 + 裁圆
    bg = _gradient_image(W)
    mask = Image.new("L", (W, W), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, W - 1, W - 1], fill=255)
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    img.paste(bg, (0, 0), mask)
    d = ImageDraw.Draw(img)

    # 2) 白色气泡：圆角矩形 + 三角尾巴
    d.rounded_rectangle([sc(227), sc(207), sc(797), sc(690)], radius=sc(112), fill=WHITE)
    d.polygon([(sc(422), sc(670)), (sc(602), sc(670)), (sc(512), sc(760))], fill=WHITE)

    # 3) 深绿圆角 T（药丸形笔画）
    d.rounded_rectangle([sc(359), sc(294), sc(662), sc(375)], radius=sc(40), fill=T_COLOR)
    d.rounded_rectangle([sc(465), sc(330), sc(560), sc(613)], radius=sc(47), fill=T_COLOR)

    # 4) 两条加粗白色声波弧
    thick_arc(d, sc(512), sc(790), sc(200), sc(72), 15, 165, sc(44), WHITE)
    thick_arc(d, sc(512), sc(855), sc(250), sc(95), 15, 165, sc(44), WHITE)
    return img


# ---------------- ICO（BMP/DIB 格式，兼容性最好） ----------------
def _ico_bmp_blob(im: Image.Image) -> bytes:
    """把 RGBA 图转成 ICO 内部使用的 BMP(DIB) 数据块。"""
    im = im.convert("RGBA")
    w, h = im.size
    px = im.load()

    # BITMAPINFOHEADER（biHeight 需写 2*h，含 XOR 位图 + AND 掩码）
    header = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, 0, 0, 0, 0, 0)

    # XOR 位图：BGRA、自下而上
    xor = bytearray()
    for y in range(h - 1, -1, -1):
        for x in range(w):
            r, g, b, a = px[x, y]
            xor += bytes((b, g, r, a))

    # AND 掩码：1 bit/px，行按 4 字节对齐，1 = 透明
    row_bytes = ((w + 31) // 32) * 4
    mask = bytearray()
    for y in range(h - 1, -1, -1):
        row = bytearray(row_bytes)
        for x in range(w):
            if px[x, y][3] == 0:
                row[x // 8] |= 0x80 >> (x % 8)
        mask += row

    return header + bytes(xor) + bytes(mask)


def save_ico(master: Image.Image, path: str, sizes=(16, 32, 48, 64, 128, 256)):
    """生成多尺寸 ICO（全部使用 BMP 格式，避免 PNG-in-ICO 兼容性问题）。"""
    blobs = []
    for s in sorted(sizes):
        blob = _ico_bmp_blob(master.resize((s, s), Image.LANCZOS))
        blobs.append((s, blob))

    out = bytearray(struct.pack("<HHH", 0, 1, len(blobs)))
    offset = 6 + 16 * len(blobs)
    dirs = bytearray()
    for s, blob in blobs:
        wh = 0 if s >= 256 else s
        dirs += struct.pack("<BBBBHHII", wh, wh, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    out += dirs
    for _, blob in blobs:
        out += blob
    with open(path, "wb") as f:
        f.write(out)


def _desaturate(src: Image.Image) -> Image.Image:
    """停用态图标：降饱和 + 压暗，但**保留色相**并保留原 alpha。

    注意：不能做成纯灰度——Windows 11 深色任务栏会把「无彩色」的托盘图标
    当作单色图标自动反色，导致绿色图标变成黑底白字。
    """
    from PIL import ImageEnhance

    im = src.convert("RGBA")
    rgb = im.convert("RGB")
    rgb = ImageEnhance.Color(rgb).enhance(0.32)       # 保留约 1/3 饱和度
    rgb = ImageEnhance.Brightness(rgb).enhance(0.82)  # 略压暗，表示"已停止"
    out = rgb.convert("RGBA")
    out.putalpha(im.split()[3])                       # 关键：恢复原 alpha
    return out


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    master = build_master()

    # 1024 主图（与 AI 稿对照）
    master.resize((S, S), Image.LANCZOS).save(os.path.join(OUT_DIR, "icon_rebuild_1024.png"))

    # 各尺寸 PNG
    for s in (256, 128, 64, 48, 32, 16):
        master.resize((s, s), Image.LANCZOS).save(os.path.join(OUT_DIR, f"icon_{s}.png"))
    print("PNG 各尺寸完成")

    # 多尺寸 ICO（BMP 格式）
    save_ico(master, os.path.join(OUT_DIR, "tiansheng.ico"))
    print("ICO（BMP 格式）完成")

    # 程序用图标：彩色 + 灰度（均保留透明通道）
    icon_colored = master.resize((256, 256), Image.LANCZOS)
    icon_colored.save(os.path.join(APP_DIR, "icon.png"))
    _desaturate(icon_colored).save(os.path.join(APP_DIR, "icon_off.png"))
    print("icon.png / icon_off.png 完成（保留 alpha）")

    # 窗口/任务栏图标用的 ICO（LoadImageW 只认 .ico 文件）
    save_ico(master, os.path.join(APP_DIR, "app.ico"))
    print("app.ico 完成（供窗口图标使用）")

    # 网页资源：logo（界面左上）、favicon
    web_dir = os.path.join(APP_DIR, "web")
    master.resize((128, 128), Image.LANCZOS).save(os.path.join(web_dir, "logo.png"))
    master.resize((64, 64), Image.LANCZOS).save(os.path.join(web_dir, "favicon.png"))
    save_ico(master, os.path.join(web_dir, "favicon.ico"), sizes=(16, 32, 48))
    print("logo.png / favicon 完成")


if __name__ == "__main__":
    main()
