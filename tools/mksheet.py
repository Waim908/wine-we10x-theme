#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mksheet.py —— 把 We10X 的单体图标排成 **wine 同款的「拼版 SVG」**。

wine 的拼版规格（从 dlls/*/resources/*.svg 反推，所有图标一致）
---------------------------------------------------------------
    * 画布 632x272；
    * 帧元素是根 `<svg>` 的**直接子元素**，带
          id="icon:<尺寸>-<色深>"   以及   x / y / width / height
      后四个属性是「在这张渲染图上的裁切框」，`tools/buildimage` 就靠它们切帧；
    * 10 个槽位是**固定**的（wine 全树一致）：

          256-32 @ (  8,   8)
           48-32 @ (272, 220)    48-8 @ (328, 220)    48-4 @ (384, 220)
           32-32 @ (440, 236)    32-8 @ (480, 236)    32-4 @ (520, 236)
           16-32 @ (560, 252)    16-8 @ (584, 252)    16-4 @ (608, 252)

    * wine 自己这些帧有的是矢量（`<rect>` 占位 + 别处的 `<g>` 图形），有的是内嵌
      base64 PNG。**本项目一律用嵌套 `<image>` 引 We10X 的 SVG data-URI**：
      矢量、任意尺寸都清晰，而且不会和源文件的 id/渐变名打架（直接内联 path 会）。

用法
----
    python3 mksheet.py <映射表.tsv> <输出目录> [--only 名字] [--quiet]

产出 `<输出目录>/<wine资源名>.svg`，可直接交给 buildimage.py 出 .ico。
同时打印/返回每个资源的取源结果（哪个类别、哪个尺寸目录），便于核对。
"""

import argparse
import base64
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import warnings

import xml.etree.ElementTree as ET

warnings.filterwarnings("ignore", category=DeprecationWarning)

# ------------------------------------------------------------------ 常量

SHEET_W, SHEET_H = 632, 272

# (尺寸, 色深) -> (x, y, w, h)   —— wine 的固定槽位
# 顺序刻意与 wine 的拼版**文档顺序**一致（16→32→48→256，每个尺寸内 4→8→32），
# 因为 buildimage 是按文档顺序写 ICONDIRENTRY 的，这样产物与官方同序。
SLOTS = [
    ((16, 4), (608, 252, 16, 16)),
    ((16, 8), (584, 252, 16, 16)),
    ((16, 32), (560, 252, 16, 16)),
    ((32, 4), (520, 236, 32, 32)),
    ((32, 8), (480, 236, 32, 32)),
    ((32, 32), (440, 236, 32, 32)),
    ((48, 4), (384, 220, 48, 48)),
    ((48, 8), (328, 220, 48, 48)),
    ((48, 32), (272, 220, 48, 48)),
    ((256, 32), (8, 8, 256, 256)),
]
NEEDED_SIZES = (16, 32, 48, 256)

# 每个色深档「图里最多允许多少种颜色」——照 wine 内嵌 PNG 的真实规格定的：
#   实测 wine 的 dlls/*/resources/*.svg，低色深格一律是**预先量化好的索引色 PNG**，
#   位深 4 的调色板 14~15 项、位深 8 的 111~254 项，tRNS 都只有 1 字节
#   （＝只有一个「全透明」项，边缘是硬边）。
#   32bpp 格则相反：在 32/48/256 档是**矢量绘制**（`<rect fill:none>` 占位 + 别处
#   的 `<g>`），只有 16 档 wine 用了 RGBA PNG。
#   ⇒ 也就是说同一尺寸的那三格不是「三种风格」，而是**同一张图的三种颜色数**。
PNG_COLORS = {4: 16, 8: 256}

# 类别优先级：同名的图标可能散在多个类别里，按这个顺序取
CATEGORY_ORDER = ["places", "devices", "apps", "actions", "mimes",
                  "status", "categories", "preferences", "emblems"]

XLINK = "{http://www.w3.org/1999/xlink}href"
SVG_NS = "http://www.w3.org/2000/svg"


# ------------------------------------------------------------------ We10X 索引

def build_index(src_dir):
    """扫 We10X 的 src/，返回 {名字: {类别: {尺寸目录...}}} 以及 {名字: {类别: 尺寸}}。

    尺寸目录既可能是数字（16/22/24/32）也可能是 "scalable"。
    """
    idx = {}
    for cat in sorted(os.listdir(src_dir)):
        cpath = os.path.join(src_dir, cat)
        if not os.path.isdir(cpath):
            continue
        for size in sorted(os.listdir(cpath)):
            spath = os.path.join(cpath, size)
            if not os.path.isdir(spath):
                continue
            if size.startswith("symbolic"):       # 单色版，不要
                continue
            for fn in os.listdir(spath):
                if not fn.endswith(".svg"):
                    continue
                idx.setdefault(fn[:-4], {}).setdefault(cat, set()).add(size)
    return idx


def _dir_kind(size):
    """把尺寸目录分成三类，用于排序：数字尺寸 -> int；scalable -> 很好。"""
    return int(size) if size.isdigit() else None


def resolve(idx, name, cat, target):
    """在**指定类别**里给目标尺寸挑一张源图，返回 (类别, 尺寸目录, 文件路径)。

    规则（对应 We10X 的 index.theme 设计）：
      目标 ≥32  → 先 scalable/，再 32/（status、actions 等的 32 目录本身是
                  Scalable 型，MaxSize 到 256/512），再退到最近尺寸；
      目标 <32  → 先同名固定尺寸目录，再退到最近尺寸。
    注意：**不跨类别回退** —— 类别是映射表指定的，跨类别会让风格乱掉。
    """
    avail = idx.get(name, {}).get(cat)
    if not avail:
        return None

    def pick(size_dir):
        return (cat, size_dir, None)          # 路径由调用方拼

    if target >= 32:
        if "scalable" in avail:
            return pick("scalable")
        if "32" in avail:
            return pick("32")

    nums = sorted(int(s) for s in avail if s.isdigit())
    if not nums:
        return pick("scalable") if "scalable" in avail else None

    if str(target) in avail:
        return pick(str(target))
    # 最近尺寸：先找 >= target 里最小的，再找 < target 里最大的
    bigger = [n for n in nums if n > target]
    smaller = [n for n in nums if n < target]
    if bigger:
        return pick(str(min(bigger)))
    if smaller:
        return pick(str(max(smaller)))
    if "scalable" in avail:
        return pick("scalable")
    return None


# ------------------------------------------------------------------ 拼版

def render_png(svg_path, size):
    """用 rsvg-convert 把源 SVG 渲染成 size×size 的 RGBA 图（PIL Image）。"""
    from PIL import Image
    with tempfile.TemporaryDirectory(prefix="mksheet.") as d:
        out = os.path.join(d, "r.png")
        subprocess.run(["rsvg-convert", "-w", str(size), "-h", str(size),
                        svg_path, "-o", out],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        im = Image.open(out)
        im.load()
    return im.convert("RGBA")


def quant_indexed_png(svg_path, size, colors):
    """渲染源图并量化成**索引色 PNG**（单个全透明项 + 位深按色数），返回 PNG 字节。

    这是照 wine 的低色深格做的：
      * wine 的 `icon:16-4` 是位深 4、调色板 14~15 项的索引色 PNG；
        `icon:48-8` 是位深 8、254 项；`tRNS` 都只有 **1 字节**（＝只有一个透明色）。
      * 换句话说低色深格是**预先量化好的位图**，不是矢量 —— 这正是「同一尺寸的三格
        颜色数递减」的来源，也让 icotool 不必自己瞎猜调色板。
    alpha 按 wine 的做法二值化（`<128` 全透），因为 ICO 的 AND 掩码本来就只有 1 bit。
    """
    import numpy as np
    from PIL import Image

    im = render_png(svg_path, size)
    a = np.asarray(im)                              # H,W,4
    trans = a[..., 3] < 128
    keep = ~trans

    idx = np.zeros((size, size), np.uint8)          # 索引 0 恒为「透明色」
    pal = [0, 0, 0]
    if keep.any():
        rgb = a[..., :3][keep]
        n = int(min(colors - 1, max(1, len(rgb))))
        q = Image.fromarray(rgb.reshape(-1, 1, 3), "RGB").quantize(
            colors=n, method=Image.MEDIANCUT)
        qi = np.asarray(q).reshape(-1).astype(np.int32)
        idx[keep] = (qi + 1).astype(np.uint8)       # 0 留给透明
        used = int(qi.max()) + 1
        pal += (q.getpalette() or [])[: used * 3]   # 只写真正用到的项

    p = Image.fromarray(idx, mode="P")
    p.putpalette((pal + [0] * 768)[:768])
    buf = io.BytesIO()
    p.save(buf, "PNG", bits=4 if colors <= 16 else 8, transparency=0)
    return buf.getvalue()


_ATTR_RE = re.compile(r'([\w:.-]+)\s*=\s*"([^"]*)"')


def _svg_root_span(text):
    """定位根 `<svg ...>` 开标签，返回 (起点, '>' 之后)；属性值里的 '>' 不算。"""
    m = re.search(r"<svg\b", text, re.I)
    if not m:
        return None
    i, quote = m.end(), None
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == ">":
            return (m.start(), i + 1)
        i += 1
    return None


def _plain(v, default):
    """把 '64' / '64px' / '16.933mm' 取成数字。"""
    if not v:
        return float(default)
    m = re.match(r"[-+]?[\d.]+", v.strip())
    return float(m.group(0)) if m else float(default)


def _num(v):
    """数字写进 XML：整数就不带小数点，其余最多 6 位并去掉尾随 0。"""
    f = float(v)
    if f == int(f):
        return "%d" % int(f)
    return ("%.6f" % f).rstrip("0").rstrip(".")


def _rename_ids(text, tag):
    """给内部 id 加前缀，并同步改 url(#id) / href="#id"，避免多个源内联时打架。

    另外顺手给**源里本来就重复的 id** 编上序号（Inkscape 导出的稿子偶尔会这样，
    例如 `apps/scalable/system-software-update.svg` 里两个 <path> 都叫 88817）。
    重复 id 本身无害，但 <use> 克隆后文档里会再多一份，浏览器下容易踩未定义行为。
    第一个保持原名，所以 `url(#88817)` 这类引用不会指错。
    """
    seq = {}

    def _bump(m):
        name = m.group(1)
        seq[name] = seq.get(name, 0) + 1
        n = seq[name]
        return 'id="%s_%s%s"' % (tag, name, "" if n == 1 else "_%d" % n)

    text = re.sub(r'\bid="([^"]+)"', _bump, text)
    text = re.sub(r'url\(#([^)]+)\)',
                  lambda m: 'url(#%s_%s)' % (tag, m.group(1)), text)
    text = re.sub(r'((?:xlink:)?href)="#([^"]+)"',
                  lambda m: '%s="#%s_%s"' % (m.group(1), tag, m.group(2)), text)
    return text


def _box_mask(x, y, size):
    """某个槽位自己的矩形（bool 掩码）。

    判「污染」只认**这个槽位自己**的框，而不是「所有槽位的并集」：
    一个内联体溢出去、正好落进邻居槽位里，同样是叠，不能因为那块地方
    本来就有别的槽位占着就放过它。
    """
    import numpy as np
    m = np.zeros((SHEET_H, SHEET_W), bool)
    m[y:y + size, x:x + size] = True
    return m


def _toplevel_spans(inner):
    """按标签嵌套计数，切出 inner 里「根的直接子元素」的文本区间。

    只做文本级统计，不用 ElementTree —— ET 重排属性、改写命名空间前缀，
    回写后产物就不是原来那份了。
    """
    spans, depth, start = [], 0, None
    for m in re.finditer(r'<(/?)([\w:.-]+)((?:"[^"]*"|\'[^\']*\'|[^>"\'])*?)(/?)>',
                         inner):
        closing, selfclose = m.group(1) == "/", m.group(4) == "/"
        if depth == 0 and not closing:
            start = m.start()
        if not selfclose:
            depth += -1 if closing else 1
        if depth == 0 and start is not None:
            spans.append((start, m.end()))
            start = None
    return spans


def _blind(text):
    """把文内**所有** `clip-path` 引用摘掉 —— 用来模拟「不认 clip-path 的查看器」。

    用户实测反馈过两次：只给内联体挂 `clipPath` 兜不住，他的查看器根本不认
    `clip-path`。而 We10X 有一类稿子把**重复图层**放在视口外、靠 clip 才看不见
    （`apps/scalable/help-browser.svg` 就是：作者把整幅图复制了几份，副本全挪到
    坐标外，只留一份原位 —— 副本里有大量 `fill="#fff"`，clip 一失效就在拼版
    **右侧**糊出一堆纯白图形）。所以「有没有残留图层」必须在这个模式下量。
    """
    text = re.sub(r'\s*clip-path\s*=\s*"url\(#[^)]*\)"', "", text)
    text = re.sub(r'\s*clip-path\s*:\s*url\(#[^)]*\)\s*;?', "", text)
    return text


def _probe(inner, geom, nsattr, slot, blind=False):
    """把 inner 按槽位 (x,y,size) 摆进 632x272 画布（**故意不带 clip**）渲染，
    返回 `(该槽位框内的像素数组, 框外的非透明像素数)`。

    内联体一旦失去源根 <svg> 的视口，源里「画在视口外、本来靠裁剪看不见」的
    残留图层就会画出来糊掉邻居 —— 这个函数就是量化它的尺子。

    `blind=True` 时先把**源自身**带的 `clip-path` 也一并摘掉（见 `_blind`），
    用来复现「查看器不认 clip-path」时会不会露馅。
    """
    import numpy as np
    from PIL import Image

    minx, miny, vw, vh = geom
    x, y, size = slot
    if vw <= 0 or vh <= 0 or not inner.strip():
        return None, 0
    k = size / max(vw, vh)
    tx = x + (size - vw * k) / 2.0 - minx * k
    ty = y + (size - vh * k) / 2.0 - miny * k
    body = _blind(inner) if blind else inner
    doc = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<svg xmlns="http://www.w3.org/2000/svg"'
           ' xmlns:xlink="http://www.w3.org/1999/xlink"'
           ' width="%d" height="%d" viewBox="0 0 %d %d">\n'
           '<g%s transform="translate(%.10g,%.10g) scale(%.12g)">\n%s\n</g>\n</svg>\n'
           % (SHEET_W, SHEET_H, SHEET_W, SHEET_H,
              (" " + nsattr) if nsattr else "", tx, ty, k, body))
    with tempfile.TemporaryDirectory(prefix="mksheet.probe.") as d:
        src, out = os.path.join(d, "p.svg"), os.path.join(d, "p.png")
        with open(src, "w", encoding="utf-8") as fh:
            fh.write(doc)
        subprocess.run(["rsvg-convert", src, "-o", out], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        a = np.asarray(Image.open(out).convert("RGBA"))
    inside = a[y:y + size, x:x + size].copy()
    outside = int(((a[..., 3] > 8) & ~_box_mask(x, y, size)).sum())
    return inside, outside


def _probe_multi(inner, geom, nsattr, slots, blind=False):
    """对 `slots` 里**每一个**槽位各探一次，返回 `(框内像素数组的列表, 框外污染总数)`。

    为什么不能只探一个槽位（历史教训）：同一份残留图层在不同缩放下的溢出位置不一样，
    `categories/22/applications-internet-symbolic.svg` 就是「256 档污染 0、16 档 86px」——
    只看最大槽位会判它干净、于是残留被留下，在小档位糊出槽位外。
    """
    if not slots:
        return None, 0
    inside, total = [], 0
    for s in slots:
        ins, p = _probe(inner, geom, nsattr, s, blind=blind)
        if ins is None:
            return None, 0
        inside.append(ins)
        total += p
    return inside, total


def _strip_offscreen(inner, geom, nsattr, slots):
    """删掉「靠源视口/clip 才看不见」的残留图层，让内联体**不依赖任何 clip-path** 也不越界。

    We10X 的稿子里有两类这种残留：
      ① 作者从大尺寸版本复制了一份图形、坐标落在视口外，靠最外层 <svg> 的
         overflow:hidden 看不见（`drive`/`recent` 等）；
      ② 整幅图被复制成几份、副本挪到别处，靠**副本自己的 `clip-path`** 裁没
         （`apps/scalable/help-browser.svg`：副本里有大量 `fill="#fff"`，
         clip 一失效就在拼版右侧糊出一堆纯白图形）。
    拼版是「内联成 <g>」，<g> 没有视口；而用户的查看器**又不认 `clip-path`**，
    两类残留都会露出来 ⇒ 只能物理删掉。

    判据是**渲染实测**而不是估坐标 —— path 的 d 里混着相对命令（h/v/c 只写增量），
    静态算 bbox 必然算错。

    尺子用 **clip-blind 模式**（`_blind`：把源自身的 `clip-path` 也摘掉再渲染），
    因为那才是用户查看器的真实行为；而**零误删的守门条件仍在正常模式**下验：
    被删掉的元素必须满足「删完以后、**每个用到的槽位框内像素一字不变**」且
    「正常模式框外污染仍为 0」—— 正常模式下看不见的元素，删掉当然不影响它对
    图标的贡献，这样既抓得到 ②，又不会误删源里**真的**靠 clip 才成形的可见图形。

    `slots` 必须是这个源**实际会用到的全部槽位**（不是只挑一个）。
    """
    import numpy as np

    if not slots or not inner.strip():
        return inner
    inside0, out0 = _probe_multi(inner, geom, nsattr, slots)
    if inside0 is None:
        return inner
    _, base = _probe_multi(inner, geom, nsattr, slots, blind=True)
    if base == 0:
        return inner                       # 不认 clip 的查看器下也干净 ⇒ 没有残留

    def keep(ins, out):
        return (ins is not None and out == 0 and
                all(np.array_equal(x, y) for x, y in zip(ins, inside0)))

    cur = inner
    for _ in range(8):
        spans = _toplevel_spans(cur)
        if len(spans) <= 1:
            break
        # 一批一批删：先按 clip-blind 的下限量挑出嫌疑元素，再整体验一次正常模式。
        # （逐个删会是几百次渲染 —— help 的残留图层有上百个，只能批量。）
        drop = []
        for a, b in spans:
            cand = cur[:a] + cur[b:]
            if not cand.strip():
                continue
            _, p = _probe_multi(cand, geom, nsattr, slots, blind=True)
            if p < base:
                drop.append((a, b))
        if not drop:
            break
        batch = cur
        for a, b in reversed(drop):        # 从后往前切，前面的偏移才不失效
            batch = batch[:a] + batch[b:]
        ins, out = _probe_multi(batch, geom, nsattr, slots)
        if keep(ins, out):
            cur = batch
            _, base = _probe_multi(cur, geom, nsattr, slots, blind=True)
            if base == 0:
                break
            continue
        # 批量没通过（挑进来的元素在正常模式也有贡献）⇒ 退化成一次只删一个，宁少勿错
        progressed = False
        for a, b in drop:
            cand = cur[:a] + cur[b:]
            ins, out = _probe_multi(cand, geom, nsattr, slots)
            if keep(ins, out):
                cur = cand
                progressed = True
                break
        if not progressed:
            break
    return cur


def inline_body(svg_path, tag, slots=None):
    """把源 SVG 的图形部分抽成 `<g id="tag">…</g>`，返回 (xml, 几何)。

    `slots` = [(x, y, size), …]：这个源最终要摆进的**全部**槽位。给了就在内联前
    先做一次「视口外残留图层」清理（见 `_strip_offscreen`），不给就原样内联。

    为什么不嵌套 `<image xlink:href="data:image/svg+xml">`：
      wine 的拼版从来不用嵌套 SVG —— 32bpp 格一律是 `<rect fill:none>` 占位 +
      别处的 `<g>` 直接画。嵌套 SVG 在渲染器里是「替换元素」，行为随实现而异
      （老 librsvg / 浏览器都可能先按源的固有尺寸光栅化再放大 → 放大后就糊）。
      内联之后任何查看器都只能按矢量画，256 格才稳。

    几何 = 源的 (viewBox 起点 x, 起点 y, 宽, 高)，调用方据此为每个槽位算 transform。
    """
    text = open(svg_path, encoding="utf-8").read()
    span = _svg_root_span(text)
    if not span:
        raise ValueError("不是 SVG：%s" % svg_path)
    open_tag = text[span[0]:span[1]]
    body = text[span[1]:]
    cut = body.rfind("</svg>")
    if cut < 0:
        raise ValueError("找不到 </svg>：%s" % svg_path)
    inner = body[:cut]

    # 只增体积、与图形无关的东西先剥掉（Inkscape 元数据/网格设置/注释）
    inner = re.sub(r"<metadata\b.*?</metadata>", "", inner, flags=re.S)
    inner = re.sub(r"<sodipodi:namedview\b[^>]*?/>", "", inner, flags=re.S)
    inner = re.sub(r"<sodipodi:namedview\b.*?</sodipodi:namedview>", "", inner, flags=re.S)
    inner = re.sub(r"<!--.*?-->", "", inner, flags=re.S)

    attrs = dict(_ATTR_RE.findall(open_tag))
    # 源根标签上的命名空间声明必须搬过来，否则内部的 inkscape:/sodipodi: 前缀会变成未定义
    nsdecls = re.findall(r'xmlns(?::[\w.-]+)?\s*=\s*"[^"]*"', open_tag)

    vb = attrs.get("viewBox") or attrs.get("viewbox")
    if vb:
        parts = [float(v) for v in re.split(r"[\s,]+", vb.strip()) if v]
        minx, miny, vw, vh = (parts + [0.0, 0.0, 0.0, 0.0])[:4]
    else:
        minx = miny = 0.0
        vw = _plain(attrs.get("width"), 0.0)
        vh = _plain(attrs.get("height"), 0.0)

    nsattr = " ".join(nsdecls)
    attr = (" " + nsattr) if nsattr else ""

    # 先砍掉「源里本来就落在视口外」的残留图层（渲染实测，见 _strip_offscreen）。
    # 必须在加 clipPath 之前做：留着它们虽然能被 clipPath 兜住，但一旦查看器
    # 不支持 clip-path，它们就会画到槽位外糊掉邻居。
    if slots:
        inner = _strip_offscreen(inner, (minx, miny, vw, vh), nsattr, slots)

    # 源根 <svg> 的语义是「一个 width×height 的视口里画 viewBox 范围的内容」，
    # 超出视口的部分**在源里就是看不见的**（最外层 svg 默认 overflow:hidden）。
    # We10X 有若干源带「溢出图层」——例如 status/32/start-here.svg 里就有一个
    # `matrix(...,37.48,9.43)` 的 <g>，坐标落在 32x32 视口外，源里靠裁剪看不见。
    # 内联成 <g> 之后**没有视口、也就没有裁剪**，那一层会直接画到槽位外面，
    # 把相邻槽位糊掉（而且内容范围还会随所在容器的尺寸变化）。
    # ⇒ 必须显式补一个与源视口等价的 clipPath。纯矢量实现，不引入嵌套 <svg>。
    if vw > 0 and vh > 0:
        # clipPath 必须与 <g> **平级**、不能塞在 <g> 里面：<use> 会把 <g> 整棵克隆，
        # 塞在里面就会在文档里出现两份同 id 的 clipPath（重复 id ⇒ 未定义行为）。
        head = ('  <clipPath id="%s_clip">'
                '<rect x="%s" y="%s" width="%s" height="%s"/></clipPath>\n'
                '  <g%s id="%s" clip-path="url(#%s_clip)">'
                % (tag, _num(minx), _num(miny), _num(vw), _num(vh), attr, tag, tag))
    else:
        head = '  <g%s id="%s">' % (attr, tag)
    xml = head + "\n" + _rename_ids(inner, tag).strip("\n") + "\n  </g>"
    return xml, (minx, miny, vw, vh)


def use_group(tag, geom, x, y, size):
    """把 `<defs>` 里定义好的图形按槽位缩放/平移到 (x, y, size×size)。"""
    minx, miny, vw, vh = geom
    if not vw or not vh:                              # 源既无 viewBox 也无宽高
        minx = miny = 0.0
        vw = vh = float(size)
    k = size / max(vw, vh)                            # preserveAspectRatio=meet + 居中
    tx = x + (size - vw * k) / 2.0 - minx * k
    ty = y + (size - vh * k) / 2.0 - miny * k
    return ('  <g transform="translate(%.10g,%.10g) scale(%.12g)">'
            '<use xlink:href="#%s"/></g>' % (tx, ty, k, tag))


def build_sheet(resolved, src_dir, title):
    """resolved: {目标尺寸: (类别, 尺寸目录)} —— 返回拼版 SVG 的文本。

    结构**照 wine**：先按固定顺序列出 10 个槽位的「帧元素」（根 <svg> 的直接子元素），
    再把 32bpp 槽位要用的矢量图形内联在文件末尾。
    """
    name = resolved["name"]
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!-- 由 wine-win10x-theme/tools/mksheet.py 生成：%s' % title,
        '     结构与 wine 的 dlls/*/resources/*.svg 同规格：',
        '       低色深格 4/8bpp = 预先量化好的索引色 PNG（内嵌 base64，单个透明色）；',
        '       32bpp 格        = <rect fill:none> 占位，矢量图形在 <defs> 里内联、'
        '由末尾的 <use> 画出来。 -->',
        '<svg xmlns="%s" xmlns:xlink="http://www.w3.org/1999/xlink"' % SVG_NS,
        '     width="%d" height="%d" viewBox="0 0 %d %d">' % (SHEET_W, SHEET_H, SHEET_W, SHEET_H),
        '  <title>%s</title>' % title,
    ]
    vectors = []
    for (size, depth), (x, y, w, h) in SLOTS:
        cat, size_dir = resolved[size]
        path = os.path.join(src_dir, cat, size_dir, "%s.svg" % name)
        if depth >= 32:
            lines.append('  <rect id="icon:%d-%d" x="%d" y="%d" width="%d" height="%d"'
                         ' style="fill:none"/>' % (size, depth, x, y, w, h))
            vectors.append((x, y, w, path))
        else:
            png = quant_indexed_png(path, size, PNG_COLORS[depth])
            uri = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
            lines.append('  <image id="icon:%d-%d" x="%d" y="%d" width="%d" height="%d"'
                         ' xlink:href="%s"/>' % (size, depth, x, y, w, h, uri))

    # 32/48/256 三档常常取到**同一张** scalable 源。按源路径去重，只内联一份放进
    # <defs>，各槽位用 <use> 引用 —— 否则几张大图（个别源自带 512px 内嵌位图）会
    # 在拼版里重复四遍，单文件能涨到几百 KB。
    cache, defs, uses = {}, [], []
    # 一个源可能被 16/32/48/256 多个槽位共用；清理「视口外残留图层」时要**逐个槽位**
    # 都验一遍 —— 残留在不同缩放下的溢出位置不同，只看最大槽位会漏（internet 的源就是
    # 256 档污染 0、16 档 86px）。见 `_probe_multi`。
    probe_slots = {}
    for x, y, w, path in vectors:
        lst = probe_slots.setdefault(path, [])
        if (x, y, w) not in lst:
            lst.append((x, y, w))
    for x, y, w, path in vectors:
        if path not in cache:
            tag = "v%d" % len(cache)
            xml, geom = inline_body(path, tag, slots=probe_slots[path])
            cache[path] = (tag, geom)
            defs.append(xml)
        tag, geom = cache[path]
        uses.append(use_group(tag, geom, x, y, w))
    if defs:
        lines.append("  <defs>")
        lines.extend(defs)
        lines.append("  </defs>")
        lines.extend(uses)
    lines.append('</svg>')
    lines.append('')
    return "\n".join(lines)


# ------------------------------------------------------------------ 白图标体检

def white_check(path):
    """渲染一张源图，报告「不透明像素里近乎纯白」的比例。

    We10X 有若干 status/* 小图是**给深色面板用的白色图形**（例如 system-shutdown
    的 16px），放到浅色任务栏/对话框上就等于隐形。比例 >0.85 就值得怀疑。
    """
    try:
        import subprocess
        import tempfile

        from PIL import Image
    except Exception:
        return None
    tmpdir = tempfile.mkdtemp(prefix="whitecheck.")
    out = os.path.join(tmpdir, "x.png")
    try:
        subprocess.run(["rsvg-convert", "-w", "48", "-h", "48", path, "-o", out],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        im = Image.open(out).convert("RGBA")
    except Exception:
        return None
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
    px = list(im.getdata())
    opaque = [p for p in px if p[3] > 128]
    if not opaque:
        return None
    white = sum(1 for p in opaque if p[0] > 235 and p[1] > 235 and p[2] > 235)
    return white / len(opaque)


# ------------------------------------------------------------------ 主流程

def read_mapping(path):
    """读映射表。第 6 列可选：`16=24,22=24` 形式，表示「这个尺寸改从该尺寸目录取源」。

    存在的理由：We10X 有些 status/* 小图是**给深色面板用的白色图形**，同名的大尺寸
    却是彩色的（例如 security-high 的 16 是白盾、24/32 是绿盾）。用这列把白色档顶掉。
    """
    rows = []
    for ln, raw in enumerate(open(path, encoding="utf-8-sig"), 1):
        line = raw.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = [p.strip() for p in line.split("\t")]
        parts = [p for p in parts if p != ""]
        if len(parts) < 5:
            parts += [""] * (5 - len(parts))
        override = {}
        if len(parts) >= 6 and parts[5]:
            for item in parts[5].split(","):
                if "=" in item:
                    k, v = item.split("=", 1)
                    override[int(k.strip())] = v.strip()
        rows.append({"wine": parts[0], "semantic": parts[1], "we10x": parts[2],
                     "cat": parts[3], "note": parts[4], "override": override, "line": ln})
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="把 We10X 图标排成 wine 同款拼版 SVG")
    ap.add_argument("mapping", help="映射表 tsv")
    ap.add_argument("outdir", help="输出目录")
    ap.add_argument("--src", default="/home/waim/vscode-workspace/We10X-icon-theme/src",
                    help="We10X 的 src 目录")
    ap.add_argument("--only", action="append", default=None, help="只处理某个 wine 资源（可重复）")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    idx = build_index(args.src)
    rows = read_mapping(args.mapping)
    os.makedirs(args.outdir, exist_ok=True)

    report, problems, skips = [], [], []
    for row in rows:
        name = row["wine"]
        if args.only and name not in args.only:
            continue
        if not row["we10x"] or row["we10x"] == "-":
            skips.append((name, "映射表留空（保持 wine 原版）"))
            continue
        if row["we10x"] not in idx:
            problems.append((name, "We10X 里没有 `%s`" % row["we10x"]))
            continue
        if row["cat"] not in idx[row["we10x"]]:
            problems.append((name, "`%s` 不在类别 `%s` 下（实际在：%s）"
                             % (row["we10x"], row["cat"],
                                "/".join(sorted(idx[row["we10x"]])))))
            continue

        resolved = {"name": row["we10x"]}
        picks = {}
        bad = False
        for size in NEEDED_SIZES:
            forced = row["override"].get(size)
            if forced:
                if forced not in idx[row["we10x"]][row["cat"]]:
                    forced = None            # 覆盖值不存在就退回自动规则
            if forced:
                picks[size] = (row["cat"], forced)
                continue
            pick = resolve(idx, row["we10x"], row["cat"], size)
            if not pick:
                problems.append((name, "尺寸 %d 取不到源" % size))
                bad = True
                break
            picks[size] = (pick[0], pick[1])
        if bad:
            continue
        resolved.update(picks)

        svg = build_sheet(resolved, args.src, "%s  @  %s/%s" % (name, row["cat"], row["we10x"]))
        with open(os.path.join(args.outdir, "%s.svg" % name), "w", encoding="utf-8") as fh:
            fh.write(svg)

        # 白图标体检：拿 16 档的源图看
        cat16, dir16 = picks[16]
        wc = white_check(os.path.join(args.src, cat16, dir16, "%s.svg" % row["we10x"]))
        report.append({"wine": name, "we10x": row["we10x"], "cat": row["cat"],
                       "pick16": "%s/%s" % picks[16], "pick32": "%s/%s" % picks[32],
                       "pick48": "%s/%s" % picks[48], "pick256": "%s/%s" % picks[256],
                       "white16": None if wc is None else round(wc, 3)})
        if wc is not None and wc > 0.85:
            problems.append((name, "⚠ 16 档源图 %.0f%% 是近纯白（可能给深色面板用，浅底会隐形）"
                             % (wc * 100)))

    if not args.quiet:
        print("%-14s %-24s %-12s %-18s %-18s" % ("wine", "We10X", "类别", "16档", "32档"))
        for r in report:
            print("%-14s %-24s %-12s %-18s %-18s" % (
                r["wine"], r["we10x"], r["cat"], r["pick16"], r["pick32"]))
        print()
        if skips:
            print("跳过 %d 个：" % len(skips))
            for n, why in skips:
                print("  - %-14s %s" % (n, why))
        if problems:
            print("问题 %d 条：" % len(problems))
            for n, why in problems:
                print("  ! %-14s %s" % (n, why))
        print("生成 %d 张拼版 -> %s" % (len(report), args.outdir))
    return 0 if not problems else 0


if __name__ == "__main__":
    sys.exit(main())
