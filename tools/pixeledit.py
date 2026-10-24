#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pixeledit.py —— 浏览器里的像素图标编辑器：手画最小号，保存后自动重建 .ico 与 preview.png

为什么要有它
------------
We10X 在 16 档上的简化画法不是每张都成立（糊成一团、缩完认不出是什么、白图形贴在
浅色底上等于隐形）。16x16 的取舍只能人眼定：哪两个像素该留、描边占几列、要不要
砍掉细节 —— 自动缩放与矢量化都做不对这件事。所以给一个最笨的工具：一格一格涂。

它改的是哪一层
--------------
只改**覆盖层** `tools/pixels/<wine资源名>@<尺寸>.png`，不动映射表、不改 We10X 源。
`mksheet.build_sheet` 见到这个文件，就把该尺寸的三格（4/8/32bpp）全部换成这张图：
    32bpp 格 -> 原样内嵌 RGBA PNG（wine 自己的 16-32 就是这么放的）
    4/8bpp 格 -> 按精确调色板索引化（4bpp 最多 15 色 + 透明，8bpp 255 色 + 透明）
删掉该文件即退回 We10X 源的自动渲染。

用法
----
    python3 tools/pixeledit.py                    # 默认 127.0.0.1:8765，自动开浏览器
    python3 tools/pixeledit.py --port 8770
    python3 tools/pixeledit.py --no-browser
    python3 tools/pixeledit.py --new              # 端口被占时另起一个空闲端口

端口被占不会报错退出：若占着的是另一个 pixeledit，直接复用并打开它；若是别的程序，
自动改用后面第一个空闲端口（最多试 20 个）并打印实际地址。

只用标准库 + Pillow（本项目的既有依赖），页面是 tools/pixeledit.html，没有前端构建步骤。
"""

import argparse
import base64
import contextlib
import errno
import io
import json
import os
import sys
import threading
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import buildall          # noqa: E402
import icocmp            # noqa: E402
import mksheet           # noqa: E402
import preview           # noqa: E402

from PIL import Image    # noqa: E402

PAGE = os.path.join(HERE, "pixeledit.html")
LOCK = threading.Lock()
FRAME_CACHE = {}

# 编辑 16 时拿 32 当参考，编辑 32 拿 48，依此类推（256 没有更大的，退到 48）
REF_SIZE = {16: 32, 32: 48, 48: 256, 256: 48}


# ------------------------------------------------------------------ 读现有产物

def icon_index():
    """可编辑的图标 = 映射表里真的有 We10X 源、项目 dlls 里也有 .ico 的那些。"""
    items = []
    for row in mksheet.read_mapping(os.path.join(HERE, "mapping.tsv")):
        name = row["wine"]
        if not row["we10x"] or row["we10x"] == "-":
            continue
        mod = buildall.find_module(buildall.DEFAULT_WINE_TREE, name, ROOT)
        if not mod:
            continue
        items.append({"name": name, "module": mod,
                      "source": "%s/%s" % (row["cat"], row["we10x"])})
    return sorted(items, key=lambda x: (x["module"], x["name"]))


def frames_of(mod, name):
    """拆 .ico 拿每一帧的 PIL 图：{尺寸: {色深: image}}，进程内缓存。"""
    key = (mod, name)
    if key in FRAME_CACHE:
        return FRAME_CACHE[key]
    path = os.path.join(ROOT, "dlls", mod, "resources", "%s.ico" % name)
    _, _, frames = icocmp.parse(path)
    by = {}
    for f in frames:
        by.setdefault(f["size"], {})[f["bpp"]] = f["image"]
    FRAME_CACHE[key] = by
    return by


def frame_at(by, size, prefer=32):
    """取某尺寸的帧，优先 32bpp（它是矢量渲染出来的，细节最全）。"""
    got = by.get(size)
    if not got:
        return None
    return got.get(prefer) or got[max(got)]


def png_data_url(im):
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def rgba_bytes(im, size):
    """统一成 size×size 的 RGBA 原始字节，alpha 二值化（像素画只认硬边）。"""
    im = im.convert("RGBA")
    if im.size != (size, size):
        im = im.resize((size, size), Image.NEAREST)
    data = bytearray(im.tobytes())
    for i in range(3, len(data), 4):
        data[i] = 255 if data[i] >= 128 else 0
    return bytes(data)


def color_count(data):
    return len({data[i:i + 3] for i in range(0, len(data), 4) if data[i + 3]})


def shots(mod, name, size):
    """回给前端的两个对照图：当前尺寸的**产物帧**，和更大一档的参考。"""
    by = frames_of(mod, name)
    ref_size = REF_SIZE.get(size, 32)
    cur, ref = frame_at(by, size), frame_at(by, ref_size)
    return {"product": png_data_url(cur) if cur else None,
            "reference": png_data_url(ref) if ref else None,
            "ref_size": ref_size}


# ------------------------------------------------------------------ 重建

def rebuild(name):
    """只重建这一个图标（拼版 + .ico），然后刷一遍全项目 preview.png。"""
    buf = io.StringIO()
    with LOCK, contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        rc = buildall.main(["--only", name])
        msg = buf.getvalue().strip().splitlines()
        try:
            preview.main([])
        except Exception as exc:                       # noqa: BLE001
            msg.append("preview.png 重建失败: %s" % exc)
    FRAME_CACHE.clear()
    return rc, "\n".join(msg[-3:])


# ------------------------------------------------------------------ HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "pixeledit/1.0"

    def log_message(self, fmt, *args):                 # 静音默认访问日志
        pass

    # -- 工具 ---------------------------------------------------------
    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _fail(self, msg, code=400):
        self._json({"ok": False, "error": str(msg)}, code)

    def _query(self):
        return {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}

    def _item(self, name):
        for it in icon_index():
            if it["name"] == name:
                return it
        raise KeyError("没有这个图标：%s" % name)

    # -- GET ----------------------------------------------------------
    def do_GET(self):
        path = urlsplit(self.path).path
        try:
            if path in ("/", "/index.html"):
                with open(PAGE, "rb") as fh:
                    self._send(200, fh.read(), "text/html; charset=utf-8")
            elif path == "/api/icons":
                self._json({"ok": True, "icons": self._icons()})
            elif path == "/api/pixels":
                self._json({"ok": True, **self._pixels(self._query())})
            elif path == "/preview.png":
                with open(os.path.join(ROOT, "preview.png"), "rb") as fh:
                    self._send(200, fh.read(), "image/png")
            else:
                self._send(404, b"not found", "text/plain; charset=utf-8")
        except Exception as exc:                       # noqa: BLE001
            self._fail(exc, 500)

    def _icons(self):
        items = []
        for it in icon_index():
            row = dict(it)
            try:
                row["thumb"] = png_data_url(frame_at(frames_of(it["module"], it["name"]), 16))
            except Exception:                          # noqa: BLE001
                row["thumb"] = None
            row["overrides"] = [s for s in mksheet.NEEDED_SIZES
                                if os.path.exists(mksheet.pixel_path(it["name"], s))]
            items.append(row)
        return items

    def _pixels(self, q):
        name = q.get("name", "")
        size = int(q.get("size", "16"))
        if size not in mksheet.NEEDED_SIZES:
            raise ValueError("不支持的尺寸：%d" % size)
        it = self._item(name)

        hand = mksheet.pixel_override(name, size)
        base = hand
        if base is None:
            base = frame_at(frames_of(it["module"], name), size)
        if base is None:
            raise ValueError("%s 没有 %d 档的帧" % (name, size))

        data = rgba_bytes(base, size)
        return {"name": name, "size": size, "hand": hand is not None,
                "pixels": base64.b64encode(data).decode("ascii"),
                "colors": color_count(data),
                "source": it["source"], "module": it["module"],
                **shots(it["module"], name, size)}

    # -- POST ---------------------------------------------------------
    def do_POST(self):
        path = urlsplit(self.path).path
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            if path == "/api/save":
                self._json(self._save(body))
            elif path == "/api/reset":
                self._json(self._reset(body))
            else:
                self._send(404, b"not found", "text/plain; charset=utf-8")
        except Exception as exc:                       # noqa: BLE001
            self._fail(exc, 500)

    def _save(self, body):
        name = body["name"]
        size = int(body["size"])
        it = self._item(name)
        data = base64.b64decode(body["pixels"])
        if len(data) != size * size * 4:
            raise ValueError("像素字节数不对：%d，应为 %d" % (len(data), size * size * 4))

        buf = bytearray(data)
        for i in range(3, len(buf), 4):
            buf[i] = 255 if buf[i] >= 128 else 0
        im = Image.frombytes("RGBA", (size, size), bytes(buf))

        os.makedirs(mksheet.PIXEL_DIR, exist_ok=True)
        im.save(mksheet.pixel_path(name, size))

        rc, log = rebuild(name)
        return {"ok": rc == 0, "log": log, "colors": color_count(bytes(buf)),
                **shots(it["module"], name, size)}

    def _reset(self, body):
        name = body["name"]
        size = int(body.get("size") or 0)
        it = self._item(name)
        targets = [size] if size else list(mksheet.NEEDED_SIZES)
        removed = []
        for s in targets:
            p = mksheet.pixel_path(name, s)
            if os.path.exists(p):
                os.remove(p)
                removed.append(s)
        rc, log = rebuild(name)
        return {"ok": rc == 0, "removed": removed, "log": log,
                **shots(it["module"], name, size or 16)}


def probe_instance(host, port):
    """这个端口上是不是已经有一个 pixeledit？是就返回它的图标数，否则 None。

    走 /api/icons 而不是只看端口是否可连：端口被别的程序占着时也要能区分开。
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open("http://%s:%d/api/icons" % (host, port), timeout=2) as r:
            raw = r.read(4 << 20)
    except Exception:                                   # noqa: BLE001
        return None
    try:
        d = json.loads(raw.decode("utf-8"))
    except Exception:                                   # noqa: BLE001
        return None
    if isinstance(d, dict) and d.get("ok") and isinstance(d.get("icons"), list):
        return len(d["icons"])
    return None


def bind_server(host, port, reuse):
    """绑定端口。占用了就：认出自己人 -> 复用；别人占着 -> 往后找一个空的。

    返回 (srv, 实际端口)；若判定为「已有一个实例在跑」返回 (None, port)。
    """
    try:
        return ThreadingHTTPServer((host, port), Handler), port
    except OSError as exc:
        if exc.errno not in (errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", -1)):
            raise

    n = probe_instance(host, port)
    if n is not None and not reuse:
        return None, port

    first = port
    for cand in range(first + 1, first + 21):
        try:
            return ThreadingHTTPServer((host, cand), Handler), cand
        except OSError as exc:
            if exc.errno not in (errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", -1)):
                raise
    raise SystemExit("端口 %d-%d 都被占用，换个 --port 吧。" % (first, first + 20))


def main(argv=None):
    ap = argparse.ArgumentParser(description="像素图标编辑器（手绘覆盖层）")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1",
                    help="默认只听本机；要给别的机器用再改，注意这个服务能写项目文件")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--new", action="store_true",
                    help="忽略已在运行的实例，换一个空闲端口另起一个")
    args = ap.parse_args(argv)

    old = probe_instance(args.host, args.port) if args.port else None
    srv, port = bind_server(args.host, args.port, args.new)
    if srv is None:
        url = "http://%s:%d/" % (args.host, args.port)
        print("端口 %d 上已经有一个 pixeledit 在跑（%d 个图标），直接用它：" % (args.port, old or 0))
        print("  %s" % url)
        print("要另起一个（比如验证改动后的新代码）就加 --new。")
        if not args.no_browser:
            webbrowser.open(url)
        return 0
    if port != args.port:
        print("端口 %d 被别的程序占着，改用 %d。" % (args.port, port))
    url = "http://%s:%d/" % (args.host, port)
    print("pixeledit -> %s" % url)
    print("覆盖图目录：%s" % mksheet.PIXEL_DIR)
    print("Ctrl+C 结束。")
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
