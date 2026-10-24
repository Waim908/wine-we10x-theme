#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
buildall.py —— 一条命令跑通「映射表 → 拼版 SVG → 多帧 ICO」。

对映射表里每个 wine 资源：
  1. 用 mksheet.py 的规则挑 We10X 源图，排成 wine 同款的 632x272 拼版 SVG；
  2. 直接写进 `dlls/<模块>/resources/<名字>.svg`（**覆盖 wine 原版拼版**）；
  3. 用 buildimage.py 的 `--label-depth` 模式切帧打包成 `<名字>.ico`。

为什么 `.ico` 要用 `--label-depth`：拼版上的标注（`icon:48-4` 等）是我们要的
10 帧规格（4/8/32bpp 各尺寸 + 256/32bpp），而「按图内容定色深」的默认模式会把
彩色图的每一帧都判成 32bpp，帧集就和 wine 不一致了。

用法
----
    python3 buildall.py [--mapping tools/mapping.tsv] [--wine-tree <wine 源码树>]
                        [--out <项目根>] [--only 名字] [--dry-run]
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import buildimage                      # noqa: E402
import icocmp                          # noqa: E402
import mksheet                         # noqa: E402

DEFAULT_WINE_TREE = "/home/waim/vscode-workspace/wine-tkg-10.10-custom"
DEFAULT_SRC = "/home/waim/vscode-workspace/We10X-icon-theme/src"


def find_module(wine_tree, name, out_root=None):
    """这个资源属于 shell32 还是 user32？按谁有 <名字>.ico 判。

    先看 wine 树（标准用法），退一步再看项目自己的 `dlls/` —— 这样在没有 wine
    源码树的机器上（例如只想用 pixeledit 手画几个图标）也能构建。
    """
    for mod in ("shell32", "user32"):
        for root in (wine_tree, out_root):
            if not root:
                continue
            if os.path.exists(os.path.join(root, "dlls", mod, "resources", "%s.ico" % name)):
                return mod
    return None


def expected_frames(wine_tree, mod, name):
    """读 wine 原版 .ico 的帧集（尺寸+色深），作为产物的验收标准。"""
    path = os.path.join(wine_tree, "dlls", mod, "resources", "%s.ico" % name)
    if not os.path.exists(path):
        return None
    _, _, frames = icocmp.parse(path)
    return [(f["size"], f["bpp"]) for f in frames]


def main(argv=None):
    ap = argparse.ArgumentParser(description="映射表 → 拼版 SVG → 多帧 ICO")
    ap.add_argument("--mapping", default=os.path.join(HERE, "mapping.tsv"))
    ap.add_argument("--src", default=DEFAULT_SRC, help="We10X 的 src 目录")
    ap.add_argument("--wine-tree", default=DEFAULT_WINE_TREE,
                    help="wine 源码树（用来判模块、并对照原版帧集）")
    ap.add_argument("--out", default=os.path.dirname(HERE),
                    help="输出项目根（其下应有 dlls/<模块>/resources/）")
    ap.add_argument("--only", action="append", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    idx = mksheet.build_index(args.src)
    rows = mksheet.read_mapping(args.mapping)

    ok, skipped, failed = [], [], []
    for row in rows:
        name = row["wine"]
        if args.only and name not in args.only:
            continue
        if not row["we10x"] or row["we10x"] == "-":
            skipped.append((name, "映射表留空"))
            continue

        mod = find_module(args.wine_tree, name, args.out)
        if not mod:
            failed.append((name, "在 wine 树里找不到这个资源"))
            continue
        outdir = os.path.join(args.out, "dlls", mod, "resources")
        os.makedirs(outdir, exist_ok=True)

        # ---- 1) 取源（复用 mksheet 的规则）
        picks = {}
        bad = None
        for size in mksheet.NEEDED_SIZES:
            forced = row["override"].get(size)
            if forced and forced in idx.get(row["we10x"], {}).get(row["cat"], set()):
                picks[size] = (row["cat"], forced)
                continue
            pick = mksheet.resolve(idx, row["we10x"], row["cat"], size)
            if not pick:
                bad = "尺寸 %d 取不到源" % size
                break
            picks[size] = (pick[0], pick[1])
        if bad:
            failed.append((name, bad))
            continue

        resolved = {"name": row["we10x"]}
        resolved.update(picks)
        svg_path = os.path.join(outdir, "%s.svg" % name)
        ico_path = os.path.join(outdir, "%s.ico" % name)

        if args.dry_run:
            ok.append((name, mod, "%s/%s" % picks[16], "%s/%s" % picks[32], "dry-run"))
            continue

        # ---- 2) 拼版 SVG
        svg = mksheet.build_sheet(resolved, args.src,
                                  "%s  @  %s/%s" % (name, row["cat"], row["we10x"]),
                                  wine_name=name)
        with open(svg_path, "w", encoding="utf-8") as fh:
            fh.write(svg)

        # ---- 3) 切帧 → .ico（标注驱动色深）
        try:
            frames = buildimage.find_frames(svg_path, "ico")
            buildimage.write_icon(frames, ico_path, "ico", label_depth=True)
        except SystemExit as exc:                       # buildimage.die() 会 sys.exit
            failed.append((name, "buildimage 报错（code=%s）" % exc.code))
            continue

        # ---- 4) 验收：帧集与 wine 原版一致？
        _, _, got = icocmp.parse(ico_path)
        got_set = sorted({(f["size"], f["bpp"]) for f in got})
        want = expected_frames(args.wine_tree, mod, name)
        want_set = sorted(set(want)) if want else None
        if want_set is None:
            verdict = "?"
        elif got_set == want_set:
            verdict = "帧集与原版一致"
        else:
            only_got = [s for s in got_set if s not in want_set]
            only_want = [s for s in want_set if s not in got_set]
            bits = []
            if only_want:
                bits.append("原版多 %s" % ",".join("%d/%d" % s for s in only_want))
            if only_got:
                bits.append("本产物多 %s" % ",".join("%d/%d" % s for s in only_got))
            verdict = "10 帧标准（%s）" % "；".join(bits)
        hand = [s for s in mksheet.NEEDED_SIZES if mksheet.pixel_override(name, s)]
        ok.append((name, mod, "%s/%s" % picks[16], "%s/%s" % picks[32],
                   "%d帧 %s%s" % (len(got), verdict,
                                  ("，手绘 %s" % "/".join(str(s) for s in hand)) if hand else "")))

    if not args.quiet:
        print("%-14s %-9s %-16s %-18s %s" % ("wine", "模块", "16档源", "32档源", "结果"))
        print("-" * 96)
        for r in ok:
            print("%-14s %-9s %-16s %-18s %s" % r)
        for n, why in skipped:
            print("%-14s %-9s %-16s %-18s 跳过：%s" % (n, "-", "-", "-", why))
        for n, why in failed:
            print("%-14s %-9s %-16s %-18s ✗ %s" % (n, "-", "-", "-", why))
        print("-" * 96)
        print("成功 %d，跳过 %d，失败 %d" % (len(ok), len(skipped), len(failed)))
        print("输出根：%s" % args.out)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
