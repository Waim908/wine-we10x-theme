#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
preview.py —— 把生成好的 .ico 摊成一张对照图，用于肉眼验收。

每个图标一行 4 个小格：256（缩到 64）、48、32、16（按原尺寸 1:1 放大展示，
小尺寸用 NEAREST 放大以免看不清边缘）。

用法
----
    python3 preview.py [--root <项目根>] [--out 预览.png] [--sizes 256,48,32,16]
                       [--only 名字]
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image, ImageDraw          # noqa: E402

import icocmp                             # noqa: E402

CELL = 72          # 每格画布边长
GAP = 4
LABEL_H = 22


def frames_of(path, bpp=32):
    """按尺寸取帧；同一尺寸有多帧（4/8/32bpp）时按 bpp 挑。"""
    _, _, frames = icocmp.parse(path)
    out = {}
    for f in frames:
        if f["bpp"] == bpp:
            out[f["size"]] = f["image"]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="把生成的 .ico 摊成对照图")
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--out", default=None)
    ap.add_argument("--sizes", default="256,48,32,16")
    ap.add_argument("--only", action="append", default=None)
    ap.add_argument("--bg", default="white", choices=["white", "grey", "dark"])
    ap.add_argument("--bpp", type=int, default=32,
                    help="看哪一档色深的帧（4/8/32；默认 32）")
    args = ap.parse_args(argv)

    sizes = [int(s) for s in args.sizes.split(",") if s.strip()]
    out = args.out or os.path.join(args.root, "preview.png")
    bgc = {"white": (255, 255, 255, 255), "grey": (200, 200, 200, 255),
           "dark": (40, 40, 40, 255)}[args.bg]

    items = []
    for mod in ("shell32", "user32"):
        d = os.path.join(args.root, "dlls", mod, "resources")
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".ico"):
                continue
            if args.only and fn[:-4] not in args.only:
                continue
            try:
                items.append((mod, fn[:-4], frames_of(os.path.join(d, fn), args.bpp)))
            except Exception as exc:                 # noqa: BLE001
                print("跳过 %s: %s" % (fn, exc))

    cols = len(sizes)
    cell_w = CELL + GAP
    row_w = 150 + cols * cell_w
    row_h = CELL + LABEL_H

    canvas = Image.new("RGBA", (row_w + GAP, GAP + len(items) * row_h + GAP), bgc)
    dr = ImageDraw.Draw(canvas)
    # 表头
    for c, s in enumerate(sizes):
        dr.text((GAP + 150 + c * cell_w + 4, 4), "%dpx" % s, fill=(90, 90, 90, 255))

    for i, (mod, name, by_size) in enumerate(items):
        y = GAP + row_h * i + 18
        dr.text((GAP + 4, y + CELL // 2 - 6), "%s/%s" % (mod[:7], name[:12]), fill=(0, 0, 0, 255))
        for c, s in enumerate(sizes):
            im = by_size.get(s)
            if im is None:
                continue
            x = GAP + 150 + c * cell_w
            if im.size != (CELL, CELL):
                im2 = im.resize((CELL, CELL), Image.NEAREST)
            else:
                im2 = im
            canvas.alpha_composite(im2, (x + 2, y))

    canvas.convert("RGB").save(out)
    print("预览 -> %s（%d 个图标 × %s）" % (out, len(items), args.sizes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
