#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
icocmp.py —— 拆开 .ico / .cur，打印逐帧结构；给两个文件时逐帧比对。

用途有二：
  1. 看清 wine 原资源的帧集（尺寸/色深/调色板/AND 掩码）。
  2. 自检 buildimage.py：拿 wine 自带的 `<name>.svg` 重跑一遍，和 wine 的
     `<name>.ico` 逐帧比，确认复刻链路没走偏。

    python3 icocmp.py <文件.ico>
    python3 icocmp.py <A.ico> <B.ico>
"""

import io
import struct
import sys

from PIL import Image

HEADER = "<IiiHHIIiiII"


def parse(path):
    """把一个 ICO/CUR 拆成帧列表；每帧带解码后的 PIL 图和原始字节。"""
    data = open(path, "rb").read()
    reserved, magic, count = struct.unpack("<HHH", data[:6])
    frames = []
    for i in range(count):
        w, h, ncolors, rsv, planes, bits, size, off = struct.unpack(
            "<BBBBHHII", data[6 + 16 * i:22 + 16 * i])
        blob = data[off:off + size]
        rec = {
            "size": w or 256, "w": w, "h": h, "colorcount": ncolors,
            "planes": planes, "bits": bits, "bytes": size, "off": off,
            "blob": blob,
        }
        if blob[:8] == b"\x89PNG\r\n\x1a\n":
            rec["kind"] = "PNG"
            rec["bpp"] = 32
            rec["image"] = Image.open(io.BytesIO(blob)).convert("RGBA")
        else:
            hs = struct.unpack("<I", blob[:4])[0]
            bw, bh2, pl, bpp, comp, imsz = struct.unpack("<iiHHII", blob[4:24])
            ncol = 1 << bpp if bpp <= 8 else 0
            pal = blob[hs:hs + ncol * 4]
            xstart = hs + ncol * 4
            xstride = ((bw * bpp + 31) // 32) * 4
            xor = blob[xstart:xstart + xstride * (bh2 // 2)]
            astride = ((bw + 31) // 32) * 4
            amask = blob[xstart + len(xor):xstart + len(xor) + astride * (bh2 // 2)]
            rec.update(kind="BMP", bpp=bpp, hdr=hs, imgsz=imsz,
                       palette=len(pal) // 4, xor=xor, andmask=amask)
            rec["image"] = decode_dib(bw, bh2 // 2, bpp, pal, xor, amask)
        frames.append(rec)
    return reserved, magic, frames


def decode_dib(w, h, bpp, pal, xor, amask):
    """把 DIB + AND 掩码还原成 RGBA，便于肉眼/像素比对。"""
    img = Image.new("RGBA", (w, h))
    px = img.load()
    xstride = ((w * bpp + 31) // 32) * 4
    astride = ((w + 31) // 32) * 4
    for row in range(h):
        srow = h - 1 - row
        base = srow * xstride
        abase = srow * astride
        for col in range(w):
            if bpp == 32:
                b, g, rr, a = xor[base + col * 4:base + col * 4 + 4]
            elif bpp == 24:
                b, g, rr = xor[base + col * 3:base + col * 3 + 3]
                a = 255
            else:
                if bpp == 8:
                    idx = xor[base + col]
                elif bpp == 4:
                    byte = xor[base + col // 2]
                    idx = (byte >> 4) if col % 2 == 0 else (byte & 0x0F)
                elif bpp == 1:
                    byte = xor[base + col // 8]
                    idx = (byte >> (7 - col % 8)) & 1
                else:
                    idx = 0
                o = idx * 4
                if o + 2 < len(pal):
                    b, g, rr = pal[o], pal[o + 1], pal[o + 2]
                else:
                    b = g = rr = 0
                a = 255
            if amask and (amask[abase + col // 8] & (0x80 >> (col % 8))):
                a = 0
            px[col, row] = (rr, g, b, a)
    return img


def describe(path):
    reserved, magic, frames = parse(path)
    print("=" * 22, path)
    print("  type=%s（1=ICO 2=CUR）帧数=%d" % (magic, len(frames)))
    for i, f in enumerate(frames):
        extra = ""
        if f["kind"] == "BMP":
            extra = " 调色板=%d项 头=%d imgsz=%d AND掩码=%dB" % (
                f["palette"], f["hdr"], f["imgsz"], len(f["andmask"]))
        print("  [%2d] %3dx%-3d bpp=%-2d 目录bit=%s 色数标注=%-3d %6dB%s"
              % (i, f["size"], f["size"], f["bpp"], f["bits"], f["colorcount"],
                 f["bytes"], extra))
    return frames


def compare(a, b):
    fa, fb = describe(a), describe(b)
    print("-" * 22, "逐帧比对")
    if len(fa) != len(fb):
        print("  ✗ 帧数不同：%d vs %d" % (len(fa), len(fb)))
    for i in range(max(len(fa), len(fb))):
        if i >= len(fa) or i >= len(fb):
            continue
        x, y = fa[i], fb[i]
        tags = []
        if x["size"] != y["size"]:
            tags.append("尺寸 %s≠%s" % (x["size"], y["size"]))
        if x["bpp"] != y["bpp"]:
            tags.append("bpp %s≠%s" % (x["bpp"], y["bpp"]))
        if x["kind"] != y["kind"]:
            tags.append("%s≠%s" % (x["kind"], y["kind"]))
        same = "结构一致"
        if x["kind"] == "BMP" and not tags:
            if x["xor"] == y["xor"]:
                same = "位图像素逐字节一致"
            else:
                diff = sum(1 for p, q in zip(x["xor"], y["xor"]) if p != q)
                d = pixel_diff(x["image"], y["image"])
                same = "位图 %d 字节不同 / 解码后 %d 像素不同" % (diff, d)
            if x["andmask"] == y["andmask"]:
                same += "，AND 一致"
            else:
                same += "，AND 不同"
        elif x["kind"] == "PNG":
            d = pixel_diff(x["image"], y["image"])
            same = "PNG 解码后 %d 像素不同（渲染器/编码器差异）" % d
        print("  [%2d] %3dx%-3d %-8s %s%s"
              % (i, x["size"], x["size"],
                 "%s/%s" % (x["bpp"], y["bpp"]), same,
                 ("  ⚠ " + "；".join(tags)) if tags else ""))


def pixel_diff(a, b):
    """两张 RGBA 图的差异像素数（尺寸不同按较大者算，超出部分记全差）。"""
    if a.size != b.size:
        return max(a.size[0] * a.size[1], b.size[0] * b.size[1])
    from PIL import ImageChops
    d = ImageChops.difference(a.convert("RGBA"), b.convert("RGBA"))
    return sum(1 for px in d.getdata() if px != (0, 0, 0, 0))


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) == 1:
        describe(args[0])
    elif len(args) == 2:
        compare(args[0], args[1])
    else:
        sys.exit(__doc__)
