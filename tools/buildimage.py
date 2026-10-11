#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
buildimage.py —— wine 官方 `tools/buildimage` 的 Python 复刻。

为什么需要它
------------
wine 自带的 `tools/buildimage` 是 Perl 脚本，依赖 `XML::LibXML` 与 `icotool`
（icoutils 包）。精简环境 / 容器里这两样经常缺失（本项目所在机器就是这样），
于是用「Python 标准库 + Pillow + rsvg-convert」复刻同一份契约。
产出与 wine 官方链路（buildimage + icotool）结构一致。

wine 的契约（与原脚本逐条对应）
-------------------------------
1. 先用 rsvg-convert 把整张 SVG 渲染成一张 PNG。
2. 在 SVG 里找 `<svg>` 的**直接子元素**，其 `id` 形如
       icon:<尺寸>-<色深>     图标帧
       cursor:<尺寸>-<色深>   光标帧
       hotspot:<尺寸>         光标热点（`<rect>` 的 x/y）
   元素自带 `x` / `y` / `width` / `height` 四个属性 —— 就是在这张 PNG 上的裁切框。
3. 若元素还带 `xlink:href="data:image/png;base64,..."`，则不裁切，直接把这段
   base64 当这一帧（wine 用它来放手工调色的 4bpp/8bpp 小图）。
4. **帧的真实色深由图本身决定，不看 id 里的标注**：调色板 ≤16 色 → 4bpp，
   ≤256 色 → 8bpp，带 alpha/RGB → 32bpp。（原脚本里 id 的色深只用于裁剪时的
   `convert -depth N`，icotool 最终仍按图内容写 ICONDIRENTRY。）
5. 尺寸 ≥128 且色深 ≥24 的帧以**原始 PNG**形式内嵌（wine 走 `icotool --raw`）。
6. `.bmp` 输出看**根 `<svg>` 自己**的 id：`bitmap:<尺寸>-<色深>`。
7. ICO/CUR 里帧的顺序 = SVG 文档里的顺序。

用法
----
    python3 buildimage.py <输入.svg> <输出.ico|.cur|.bmp>
"""

import base64
import io
import os
import re
import struct
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

from PIL import Image

XLINK = "{http://www.w3.org/1999/xlink}href"
SVG_NS = "http://www.w3.org/2000/svg"

LABELS = {
    "ico": r"icon:(\d*)-(\d*)",
    "cur": r"cursor:(\d*)-(\d*)",
    "bmp": r"bitmap:(\d*)-(\d*)",
}
VALID_DEPTHS = (1, 4, 8, 24, 32)


def die(msg):
    sys.stderr.write("buildimage: %s\n" % msg)
    sys.exit(1)


# ---------------------------------------------------------------- SVG 渲染

def _num(value):
    """把 '632' / '632px' / '16.933pt' 之类的属性解析成数字，失败返回 None。"""
    if value is None:
        return None
    m = re.match(r"\s*(-?[0-9.]+)", value)
    return float(m.group(1)) if m else None


def render_svg(svg_path, out_png):
    """用 rsvg-convert 把整张 SVG 渲染成 PNG（有明确的 width/height 时按它定画布）。"""
    root = ET.parse(svg_path).getroot()
    cmd = ["rsvg-convert", svg_path, "-o", out_png]
    w, h = _num(root.get("width")), _num(root.get("height"))
    if w and h:
        cmd += ["-w", str(int(round(w))), "-h", str(int(round(h)))]
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError:
        die("找不到 rsvg-convert（Arch: pacman -S librsvg）")
    except subprocess.CalledProcessError as exc:
        die("rsvg-convert 失败: %s" % exc)


# ---------------------------------------------------------------- 找帧

def _png_plte_count(data):
    """数一下 PNG 的 PLTE 块里有几项颜色（决定它是 4bpp 还是 8bpp 图）。"""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    pos = 8
    while pos + 8 <= len(data):
        length, ctype = struct.unpack(">I4s", data[pos:pos + 8])
        if ctype == b"PLTE":
            return length // 3
        if ctype == b"IEND":
            break
        pos += 12 + length
    return None


def find_frames(svg_path, ext):
    """按 wine 的规则列出所有帧，返回 [(size, depth, image)]，顺序 = 文档顺序。"""
    root = ET.parse(svg_path).getroot()
    label = re.compile(LABELS[ext])

    if ext == "bmp":
        # .bmp：标注挂在根 <svg> 自己身上，整张图就是这一帧
        m = re.search(label, root.get("id") or "")
        if not m:
            die("no render directive found in %s" % svg_path)
        return [(int(m.group(1)), int(m.group(2)), None)]

    if ext == "cur":
        hotspots = {}
        for child in root:
            hm = re.search(r"hotspot:(\d*)", child.get("id") or "")
            if hm:
                hotspots[int(hm.group(1))] = (_num(child.get("x")) or 0,
                                              _num(child.get("y")) or 0)
    else:
        hotspots = {}

    # 只扫「有 id 的直接子元素」，且 id 里含 icon:/cursor:
    wanted = []
    for child in root:
        cid = child.get("id") or ""
        m = re.search(label, cid)
        if not m:
            continue
        wanted.append((child, int(m.group(1) or 0), int(m.group(2) or 0)))

    if not wanted:
        die("no render directive found in %s" % svg_path)

    rendered = None          # 惰性渲染：全是 base64 帧时就不必调用 rsvg
    tmpdir = tempfile.mkdtemp(prefix="buildimage.")
    tmp_png = os.path.join(tmpdir, "sheet.png")
    frames = []
    try:
        for child, size, depth in wanted:
            if depth not in VALID_DEPTHS:
                sys.stderr.write("buildimage: 警告 非法色深 %d (%s)\n" % (depth, child.get("id")))
            href = child.get(XLINK) or child.get("href") or ""
            if href.startswith("data:image/png;base64,"):
                raw = base64.b64decode(href.split(",", 1)[1])
                img = Image.open(io.BytesIO(raw))
                img.load()
                img.info["plte"] = _png_plte_count(raw)
            else:
                if rendered is None:
                    render_svg(svg_path, tmp_png)
                    rendered = Image.open(tmp_png)
                    rendered.load()
                x, y = _num(child.get("x")) or 0, _num(child.get("y")) or 0
                w, h = _num(child.get("width")) or 0, _num(child.get("height")) or 0
                if not w or not h:
                    die("%s 缺少 width/height" % child.get("id"))
                img = rendered.crop((int(round(x)), int(round(y)),
                                     int(round(x + w)), int(round(y + h))))
            if ext == "cur" and size in hotspots:
                img.info["hotspot"] = hotspots[size]
            frames.append((size, depth, img))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return frames


# ---------------------------------------------------------------- 帧编码

def _palette_entries(bpp):
    return 1 << bpp


def encode_frame(img, size, depth, label_depth=False):
    """把一帧编码成 ICO/CUR 的「图片数据」，返回 (bytes, 目录项里的 bitcount)。

    - 尺寸 >=128 且色深 >=24 → 原始 PNG（对应 icotool --raw）
    - 其余 → 带调色板的 DIB（BMP 头 + 调色板 + XOR 位图 + AND 掩码）

    色深怎么定？
    -------------
    默认**由图本身决定**（这是 wine 官方链路里 icotool 的真实行为，也是唯一能
    逐帧复现 wine 那批 .ico 的规则）：
        有半透明像素（alpha 既非 0 也非 255）→ 32bpp
        否则按「实际用到的颜色数」挑最小够用的：≤2→1，≤16→4，≤256→8，再多→24
    `label_depth=True` 则改成**以 id 里的标注为准**（`icon:48-4` 就出 4bpp）。
    注意：wine 自己的拼版里就有不少「标注与图不符」的帧（例如标 `-8` 而内嵌的是
    真彩 PNG，官方产物实际是 24bpp），所以标注模式会与官方产物不一致。
    """
    if size >= 128 and depth >= 24:
        buf = io.BytesIO()
        img.convert("RGBA").save(buf, "PNG")
        return buf.getvalue(), 32

    # 拆出 alpha（AND 掩码要用）
    if img.mode in ("RGBA", "LA", "P"):
        alpha = img.convert("RGBA").getchannel("A")
    else:
        alpha = None

    if label_depth:
        # 以 id 里的标注为准
        bpp = 4 if depth <= 4 else (8 if depth <= 8 else (24 if depth == 24 else 32))
        pal_img = None
        if bpp <= 8:
            pal_img = img.convert("RGB").convert(
                "P", palette=Image.ADAPTIVE, colors=_palette_entries(bpp))
    else:
        # 与 icotool 一致：色深由图自己决定
        #   有半透明像素（alpha 既非 0 也非 255）→ 32bpp
        #   否则按「实际用到的颜色数」挑最小够用的色深。
        # 实测口径（拿 wine 全部 50 个图标反推）：
        #   只数**不透明像素**的颜色，完全透明那一堆不算；≤1 种 → 1bpp（例如
        #   blank.svg 那张全透明拼版）；≤16 → 4bpp；≤256 → 8bpp；再多 → 24bpp。
        # 这条口径同时解释了 window.svg 的 icon:48-4（位深标 8、PLTE 17 项、实际只用
        # 13 种颜色 → 官方产物 4bpp）与 control.svg 的 icon:16-4（2 种颜色 → 4bpp）。
        raw = img.convert("RGBA").tobytes()          # 每像素 4 字节 RGBA
        if set(raw[3::4]) - {0, 255}:
            bpp = 32                                  # 有半透明 → 必须 32bpp
        else:
            ncol = len({raw[i:i + 3] for i in range(0, len(raw), 4) if raw[i + 3]})
            bpp = 1 if ncol <= 1 else (4 if ncol <= 16 else (8 if ncol <= 256 else 24))
        pal_img = None
        if bpp <= 8:
            pal_img = img.convert("RGB").convert(
                "P", palette=Image.ADAPTIVE, colors=_palette_entries(bpp))

    w, h = img.size
    out = io.BytesIO()

    if bpp == 32:
        rgba = img.convert("RGBA")
        xor = bytearray()
        for row in range(h - 1, -1, -1):            # DIB 自下而上
            for col in range(w):
                r, g, b, a = rgba.getpixel((col, row))
                xor += bytes((b, g, r, a))
        pal_bytes = b""
        ncolors = 0
    elif bpp == 24:
        rgb = img.convert("RGB")
        stride = ((w * 24 + 31) // 32) * 4
        xor = bytearray()
        for row in range(h - 1, -1, -1):
            line = bytearray(stride)
            for col in range(w):
                r, g, b = rgb.getpixel((col, row))
                line[col * 3:col * 3 + 3] = bytes((b, g, r))
            xor += line
        pal_bytes = b""
        ncolors = 0
    else:
        ncolors = _palette_entries(bpp)
        pal = (pal_img.getpalette() or [])[: ncolors * 3]
        pal += [0] * (ncolors * 3 - len(pal))       # 补齐到 2^bpp 项
        pal_bytes = b"".join(bytes((pal[i * 3 + 2], pal[i * 3 + 1], pal[i * 3], 0))
                             for i in range(ncolors))
        stride = ((w * bpp + 31) // 32) * 4
        xor = bytearray()
        idx = pal_img.load()
        for row in range(h - 1, -1, -1):
            line = bytearray(stride)
            for col in range(w):
                v = idx[col, row]
                if bpp == 8:
                    line[col] = v
                elif bpp == 4:
                    line[col // 2] |= (v & 0x0F) << (4 if col % 2 == 0 else 0)
                elif v:                                # 1bpp
                    line[col // 8] |= 0x80 >> (col % 8)
            xor += line

    # AND 掩码：位 =1 表示透明
    amask = bytearray()
    astride = ((w + 31) // 32) * 4
    for row in range(h - 1, -1, -1):
        line = bytearray(astride)
        for col in range(w):
            opaque = True if alpha is None else alpha.getpixel((col, row)) >= 128
            if not opaque:
                line[col // 8] |= 0x80 >> (col % 8)
        amask += line

    hdr = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, bpp, 0,
                      len(xor), 0, 0, ncolors, 0)
    out.write(hdr)
    out.write(pal_bytes)
    out.write(xor)
    out.write(amask)
    return out.getvalue(), bpp


def write_icon(frames, out_path, ext, label_depth=False):
    """按 ICONDIR + ICONDIRENTRY + 各帧 的结构写出 .ico / .cur。"""
    cur = ext == "cur"
    count = len(frames)
    dir_entries = bytearray()
    blobs = bytearray()
    offset = 6 + 16 * count

    for size, depth, img in frames:
        data, bitcount = encode_frame(img, size, depth, label_depth)
        colorcount = 16 if bitcount <= 4 else 0
        spot = img.info.get("hotspot", (0, 0)) if cur else (0, 0)
        # 目录项里宽/高各占 1 字节，0 表示 256
        w = h = 0 if size >= 256 else size
        if cur:
            # CUR 的目录项把 planes/bitcount 两个字段复用作热点坐标
            planes, bits = int(spot[0]), int(spot[1])
        else:
            planes, bits = 1, bitcount
        dir_entries += struct.pack("<BBBBHHII", w & 0xFF, h & 0xFF, colorcount, 0,
                                   planes, bits, len(data), offset)
        blobs += data
        offset += len(data)

    magic = 2 if cur else 1
    with open(out_path, "wb") as fh:
        fh.write(struct.pack("<HHH", 0, magic, count))
        fh.write(dir_entries)
        fh.write(blobs)
    return count


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    flags = [a for a in argv[1:] if a.startswith("--")]
    label_depth = "--label-depth" in flags    # 按 id 标注定色深（默认按图定）
    if len(args) != 2:
        die("用法: buildimage.py [--label-depth] <输入.svg> <输出.ico|.cur|.bmp>")
    svg_path, out_path = args
    ext = os.path.splitext(out_path)[1].lstrip(".").lower()
    if ext not in LABELS:
        die("只支持 .bmp / .ico / .cur 输出，收到 .%s" % ext)

    frames = find_frames(svg_path, ext)
    if ext == "bmp":
        # .bmp 输出：整张渲染图就是这一帧；色深 24 时去掉 alpha（对应 convert +matte）
        _, depth, _ = frames[0]
        tmpdir = tempfile.mkdtemp(prefix="buildimage.")
        tmp_png = os.path.join(tmpdir, "sheet.png")
        render_svg(svg_path, tmp_png)
        img = Image.open(tmp_png)
        img.convert("RGB" if depth == 24 else "RGBA").save(out_path, "BMP")
        shutil.rmtree(tmpdir, ignore_errors=True)
        return 0
    n = write_icon(frames, out_path, ext, label_depth)
    return 0 if n else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
