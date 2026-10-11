#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
selftest.py —— 拿 wine 自带的资源当标尺，自检 buildimage.py。

做法：对 `resources/` 里的每一对 `<名字>.svg` / `<名字>.ico`，用本项目复刻的
buildimage.py 重新生成一个 .ico，然后和 wine 官方那份逐帧比结构（帧数/尺寸/色深）
与逐帧比内容（DIB 字节 / PNG 解码像素）。

    python3 selftest.py <wine的resources目录> [更多目录...] [--label-depth]

两种模式：
  默认            帧色深由图自己定（icotool 的真实行为），逐帧对齐 wine 官方产物
  --label-depth   帧色深以 `icon:<尺寸>-<色深>` 的标注为准（拼版作者的意图）
                  —— wine 自己的拼版里有不少「标注与图不符」的帧，因此这个模式
                  会和官方产物不一致（通常是官方产物更低、被内嵌真彩图带偏）。
"""

import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import buildimage                      # noqa: E402
import icocmp                          # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
BUILDIMAGE = os.path.join(HERE, "buildimage.py")


def build(svg, out, label):
    cmd = [sys.executable, BUILDIMAGE]
    if label:
        cmd.append("--label-depth")
    cmd += [svg, out]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)


def verdict(a, b):
    """返回 (结论, 细节)。结论取值：identical / pixels / structure。"""
    if len(a) != len(b):
        return "structure", "帧数 %d≠%d" % (len(a), len(b))
    diffs = []
    for x, y in zip(a, b):
        if x["size"] != y["size"]:
            diffs.append("%dpx 尺寸 %s≠%s" % (x["size"], x["size"], y["size"]))
        elif x["bpp"] != y["bpp"]:
            diffs.append("%dpx 色深 %s≠%s" % (x["size"], x["bpp"], y["bpp"]))
    if diffs:
        return "structure", "；".join(diffs[:3])
    exact = 0
    pix = 0
    for x, y in zip(a, b):
        if x["kind"] == "BMP":
            if x["xor"] == y["xor"] and x["andmask"] == y["andmask"]:
                exact += 1
                continue
        if icocmp.pixel_diff(x["image"], y["image"]) == 0:
            exact += 1
        else:
            pix += 1
    if pix == 0:
        return "identical", "%d/%d 帧像素级一致" % (exact, len(a))
    return "pixels", "%d 帧像素有差（渲染器抗锯齿差异）" % pix


def main(argv):
    dirs = [a for a in argv[1:] if not a.startswith("--")]
    label = "--label-depth" in argv
    if not dirs:
        sys.exit(__doc__)

    total = {"identical": 0, "pixels": 0, "structure": 0, "skip": 0}
    with tempfile.TemporaryDirectory() as tmp:
        for d in dirs:
            print("=" * 24, d)
            for name in sorted(os.listdir(d)):
                if not name.endswith(".svg"):
                    continue
                key = name[:-4]
                svg = os.path.join(d, name)
                ref = os.path.join(d, key + ".ico")
                if not os.path.exists(ref):
                    continue                      # 没有对应 .ico 的（光标/位图）跳过
                if "icon:" not in open(svg, encoding="utf-8", errors="replace").read():
                    total["skip"] += 1
                    continue                  # 光标/位图拼版不属于本次范围
                out = os.path.join(tmp, key + ".ico")
                try:
                    build(svg, out, label)
                except subprocess.CalledProcessError as exc:
                    print("  ✗ %-22s 生成失败 %s" % (key, exc))
                    total["structure"] += 1
                    continue
                v, detail = verdict(icocmp.parse(ref)[2], icocmp.parse(out)[2])
                total[v] += 1
                mark = {"identical": "✓", "pixels": "≈", "structure": "✗"}[v]
                print("  %s %-22s %s" % (mark, key, detail))
    print("-" * 24)
    print("合计：像素级一致 %d，仅渲染差异 %d，结构不符 %d，跳过 %d"
          % (total["identical"], total["pixels"], total["structure"], total["skip"]))
    return 1 if total["structure"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
