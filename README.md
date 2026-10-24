# wine-We10x-theme

用 [We10X-icon-theme](https://github.com/yeyushengfan258/We10X-icon-theme) 的图标替换 **wine 的
shell32 / user32 图标资源**，把 `/desktop=shell` 里那套 Tango 风格换成 Windows 10 X 风格。

本目录里 `dlls/` 下就是**照 wine 源码树原样摆放**的资源目录 —— 直接覆盖到 wine 源码树对应位置即可。

* `dlls/shell32/resources/` —— 43 个图标（`*.ico` + 配套 `*.svg` 拼版）
* `dlls/user32/resources/` —— 7 个 `oic_*.ico`（对话框图标、盾牌、wine 标志）
* 其余文件（`obm_*.bmp`、`ocr_*.cur`、`idb_*.bmp`、`searching.avi`、`ietoolbar*` 等）
  **保持 wine 原版未动** —— 位图/光标不属于本次范围。

## 为什么长这样：wine 的图标是「一张拼版切出来的」

wine 的每个图标资源是**一个 `.svg` 拼版** + 一个 `.ico`，两者同名：

* 拼版画布固定 **632x272**，根 `<svg>` 的**直接子元素**带 `id="icon:<尺寸>-<色深>"`，
  并且必须带 `x` / `y` / `width` / `height` —— 那是「在这张渲染图上的裁切框」；
* 10 个槽位**全树固定**（本项目的生成器就按这张表排）：

  | 帧 | 位置 | 帧 | 位置 |
  |---|---|---|---|
  | `icon:256-32` | (8, 8) | `icon:32-4` | (520, 236) |
  | `icon:48-32`  | (272, 220) | `icon:32-8` | (480, 236) |
  | `icon:48-8`   | (328, 220) | `icon:32-32` | (440, 236) |
  | `icon:48-4`   | (384, 220) | `icon:16-4` | (608, 252) |
  |               |            | `icon:16-8` | (584, 252) |
  |               |            | `icon:16-32` | (560, 252) |

* 官方工具 `tools/buildimage`（Perl）负责「渲染拼版 → 按裁切框切帧 → 交给 icotool 打包」。
  它只在 **maintainer mode** 才被 `make` 调用，且要求 `rsvg-convert` + ImageMagick `convert`
  + `icotool` 三者齐全；普通构建**直接用提交好的 `.ico`**，不重新生成。
  ⇒ 所以本项目的产物是 `.ico` 本身，不需要 icoutils。

### ⚠ 同一尺寸的三格不是「三种风格」，而是「三种颜色数」

这是最容易做错的一点。看 `dlls/shell32/resources/folder.svg` 这种原版文件，同一尺寸下
并排的三个格子里，**四个尺寸档里三格是同一张图的三种颜色深度**：

| 格（文档顺序里的位置） | wine 原版的真实编码 | 本项目怎么生成 |
|---|---|---|
| 第 3 个 / `-32`（32bpp） | **矢量绘制**：一个 `<rect style="fill:none">` 占位当裁切框，真正的图形画在别处的 `<g>` 里 | `<rect fill:none>` 占位 + 图形内联进 `<defs>`，末尾用 `<use>` 画 |
| 第 2 个 / `-8`（8bpp） | **位深 8 的索引色 PNG**（实测调色板 111~254 项，`tRNS` 只有 1 字节） | 渲染成 256 色以内、位深 8 的索引色 PNG，内嵌 base64 |
| 第 1 个 / `-4`（4bpp） | **位深 4 的索引色 PNG**（调色板 14~15 项，`tRNS` 1 字节） | 渲染成 16 色以内、位深 4 的索引色 PNG，内嵌 base64 |

也就是说「第 3 格清楚、第 2 格颜色少一点、第 1 格颜色最少」，是**颜色数量**造成的，
不是画了三张不同的图。`tRNS` 只有 1 字节 ⇒ 低色深格是**硬边**（只有一个全透明色，
没有半透明渐变），这正是小尺寸下那种「像素感」的来源。

**所以：**

* 32bpp 格**必须**是矢量（占位 `rect` + 别处绘制）。**不要用
  `<image xlink:href="data:image/svg+xml;base64,…">` 嵌套一张 SVG** —— 嵌套 SVG 在渲染器里是
  「替换元素」，行为随实现而异（老 librsvg / 某些浏览器会先按源的固有尺寸光栅化再放大），
  256 格就可能糊掉。本项目改成把源图**内联**（`<defs>` + `<use>`），任何查看器都只能按矢量画。
* 低色深格**必须**是已经量化好的**索引色 PNG**，不是矢量。做成矢量的话 icotool 得自己猜
  调色板，风格和「颜色递减」的规律也就没了。
* 同一张 scalable 源会被 32/48/256 三档共用，本项目按源路径去重、只内联一份放进 `<defs>`，
  各槽位用 `<use>` 引用（否则个别带大尺寸内嵌位图的源会让单文件涨到几百 KB）。


## 目录

```
tools/
  buildimage.py   wine 官方 tools/buildimage 的 Python 复刻（本项目自己的 SVG→ICO 实现）
  icocmp.py       拆 .ico/.cur 逐帧打印结构；给两个文件就逐帧比对
  selftest.py     拿 wine 自带的 .svg 跑 buildimage.py，与官方 .ico 逐帧对照（复刻自检）
  mksheet.py      把 We10X 单体图标排成 wine 同款拼版 SVG
                  （低色深格 = 量化好的索引色 PNG；32bpp 格 = <rect> 占位 + <defs>/<use> 内联矢量；
                   内联体按源视口挂 clipPath，挡住源的「溢出图层」）
  buildall.py     一条命令：映射表 → 拼版 SVG → 多帧 ICO（写进 dlls/...）
  preview.py      把产出的 .ico 摊成对照图（可选 4/8/32bpp），用于肉眼验收
  mapping.tsv     wine ↔ We10X 映射表（唯一需要人工维护的「数据」）
  pixeledit.py    浏览器里的像素编辑器（本地 HTTP 服务，保存即重建 .ico 与 preview.png）
  pixeledit.html  编辑器的页面本体（无依赖、无构建步骤）
  pixels/         手绘覆盖层：<wine资源名>@<尺寸>.png，见「手绘最小号图标」
install.sh        把本目录的资源覆盖到一棵 wine 源码树
```

## 用法

```sh
# 1. 重新生成全部资源（会覆盖 dlls/**/resources/*.{svg,ico}）
python3 tools/buildall.py                 # 默认读 tools/mapping.tsv
python3 tools/buildall.py --dry-run       # 只看取源计划，不写文件
python3 tools/buildall.py --only folder   # 只做某一个资源

# 2. 肉眼验收（输出 preview.png，每行一个图标 × 256/48/32/16 四档）
python3 tools/preview.py
python3 tools/preview.py --bpp 4          # 看 4bpp 量化帧

# 3. 装进 wine 源码树
./install.sh /path/to/wine-source-tree
./install.sh --dry-run /path/to/wine-source-tree

# 4. 复刻自检（抽查 buildimage.py 是否忠实复现 wine 官方产物）
python3 tools/selftest.py <wine树>/dlls/shell32/resources <wine树>/dlls/user32/resources
```

## 手绘最小号图标（16 档）

16 档是自动缩出来的，有些图缩完就认不出是什么了（纸张糊成一团、白色图形贴在浅色底上
等于隐形）。16×16 上「哪两个像素该留、描边占几列」只能人眼看、人手画 —— 缩放和矢量化
都做不对这件事。所以另有一个最笨的工具：

```sh
python3 tools/pixeledit.py
# 起来后打开 http://127.0.0.1:8765/（默认只听本机，这个服务能写项目文件）
```

端口被占不会报错退出：占着的是另一个 pixeledit 就直接复用它并打印地址；是别的程序就自动
改用后面第一个空闲端口（最多试 20 个）。想无视已运行的实例、验证改动后的新代码，加 `--new`。
`Address already in use` 说明上一次的实例还在跑 —— 要么直接用它，要么先 `pkill -f
"pixeledit.py"`。

左侧选图标，铅笔/橡皮/填充/取色，右键直接擦，`Ctrl+Z` 撤销。改动落在
`tools/pixels/<资源名>@<尺寸>.png`，按「保存并重建」时自动做完整条链路：重建该图标的
拼版 SVG → 切出 10 帧 `.ico` → 重刷 `preview.png`。按「还原」删掉覆盖，回到 We10X
源图的自动渲染。

右侧「产物」区是画的时候唯一要盯的地方：左边是本尺寸从 `.ico` 里切出来的帧（wine 真正会
拿到的样子），右边是更大一档的参考（对形状用）。两张都按**整数倍**放大 —— `×1 / ×2 / ×4 /
×8`，档位记在 localStorage 里，切图标不变。窄栏塞不下时退到「放得下的最大整数倍」并在
标题里注明（非整数倍会让像素块大小不匀）。点任意一张或按「放大」全屏看，取景框内也是整数倍，
`Esc` 关闭；256 档在栏里只能「按栏宽」显示，要看细节就点开全屏 —— 全屏同样取能放下的
最大整数倍，并标出实际倍数。

取色工具（`I`）除了在画布上吸色，也能**从参考图上吸**：切到取色后点参考图即可，光标会从
放大镜变成十字，底部提示会报出坐标与色值（例如 `取自参考图 (9,4) #363636`）。点到透明格会
提示「没有颜色可取」而不是把上次的颜色当成吸到的。取色状态下点参考图不再触发全屏，要放大请
按「放大」按钮；全屏里点图同样能吸色（大图更好点），点空白处或 `Esc` 才关闭。

吸到的颜色会进「颜色」栏下方那条**取色记录**（最多 24 个，最近的在最前）：它和上面那排
「本图颜色」不是一回事 —— 本图颜色只列当前位图上用到的色，参考图上的目标色通常不在里面，
所以只看那一排会以为取色没生效。取色记录跨图标、跨刷新都留着（存在 localStorage），
点一下即可选中，右键移除单个，「清空」清掉全部；手输进 `#hex` 的颜色也会入记录。
目前它不写进 `tools/pixels/`、也不参与构建，纯属画图时的取色板。

落盘规则（和 wine 的帧规格对齐）：

* 覆盖是**按尺寸**的：同一尺寸的 4/8/32bpp 三格本来就是同一张图的三种颜色数，
  所以换就三格一起换，不允许只改其中一格。
* 32bpp 格原样内嵌 RGBA PNG（wine 自己的 `icon:16-32` 就是这么放的）；4/8bpp 格按
  **精确调色板**索引化（15 色 + 透明 / 255 色 + 透明），只有颜色数超上限才退回中位切分
  —— 手绘的颜色不能被子采样挪位。
* alpha 二值化（`<128` 全透）。ICO 的 AND 掩码只有 1 bit，半透明本来就是假的。

`tools/pixels/` 里的 PNG 是**产物的一部分**，要跟着提交；删掉目录等于放弃全部手绘版本。
`tools/pixels/_bak/` 例外：那是被换下来的旧手绘图（例如曾经和光盘画成同一张的
`optical_drive@16`），不参与构建、不进提交，需要时可以挪回上一级还原。

## 为什么 `dlls/shell32/resources/` 有 95 个文件，而上游 wine 是 91 个

多出来的 4 个是 `ietoolbar.bmp` / `ietoolbar.svg` / `ietoolbar_small.bmp` / `ietoolbar_small.svg`。
**它们不是本项目产生的**，来自 **wine-staging 的 `shell32-Toolbar_Bitmaps` 补丁集**
（bug #40236「Add missing resources for IE6 buttons」，Dmitry Timoshkov）：

* 它给 `dlls/shell32/` 加一套 IE6 风格工具栏位图，`shell32.rc` 里以 **204~207 号 `BITMAP`** 引用，
  `Makefile.in` 的 `SOURCES` 里加两个 `.svg`。上游 wine 的 `shell32.rc` **全部用命名 ID、没有一个数字 ID**，
  上游 10.10 的 `resources/` 里也没有 `ietoolbar*`（从 wine-0.0.2 到 11.14 都没有）。
* 上游只把 `ietoolbar` 放在 **`dlls/ieframe/`**（`IDB_IETOOLBAR`，192×32）——和 shell32 里这份
  （384×24）**不是同一张图**，不要混为一谈。

本项目的 `dlls/shell32/resources/` 是从一棵**已经打好该补丁**的 wine-tkg 树播种来的（所以也一并带了
`idb_sort_decr/incr.bmp`、`idb_tb_large/small.bmp`、`searching.avi` 这些非图标资源）。文件计数：

```
上游 wine 10.10   91 = 43 个图标 ×(svg+ico) + idb_sort_decr/incr + idb_tb_large/small + searching.avi  (5)
打了补丁的树      95 = 91 + ietoolbar{,_small}.{bmp,svg}                                              (4)
```

**不用管它**：`mksheet.py` 从不生成这 4 个（不在 `tools/mapping.tsv` 里），`install.sh` 也只覆盖表里
那 49 个、且只在目标树已有同名文件时才覆盖 ⇒ 装进别的树不会多塞文件，装进**没打该补丁**的 vanilla
树也不会留下悬空引用（`shell32.rc` 不引用它）。想给它们也换主题，才需要自己往映射表里加。

## 改了资源之后要不要重编

**要。** 资源嵌在 DLL 里，改了 `dlls/{shell32,user32}/resources/**` 必须重编这两个 DLL，
否则设备上看到的还是旧图标：

```sh
make -C dlls/shell32                     # ⚠ 不能写 `make dlls/shell32`
make -C dlls/user32
# 或直接 `make` 整树增量构建
```

⚠ **`make dlls/shell32 dlls/user32` 是个陷阱，什么都不做**：wine 顶层 `Makefile` 是 `configure`
生成的（`wine_fn_output_makefile`，顶层根本没有 `Makefile.in`），它的 `all:` 直接列出**带全路径的产物**，
**没有「按子目录名」的便利目标**。而 `dlls/shell32` 恰好是磁盘上存在的目录，GNU make 对「存在的目录 +
无规则」的目标只会打印 `make: Nothing to be done for 'dlls/shell32'.` 并返回 0 ⇒ 看起来跑了，其实没跑。

设备上确认：`/desktop=shell` 下看开始菜单按钮、桌面「我的电脑」、回收站、驱动器图标、
以及各对话框左上角图标是否变了。

## 取源规则（为什么 16 档和 32 档可能不是同一张图）

按 We10X 自己的 `src/index.theme` 约定：

* 目标尺寸 **≥32** → 优先该类别下的 `scalable/`；没有就用 `32/`
  （actions/status/categories/preferences 的 `32` 目录本身就是 Scalable 型，MaxSize 到 256/512）；
  再没有就退到最近的固定尺寸。
* 目标尺寸 **<32** → 优先同名固定尺寸目录（`16/`），再退到最近尺寸。
* **不跨类别回退** —— 类别由映射表第 4 列写死，因为同名图标在不同类别下的画风完全不同
  （例如 `start-here`：`places/` 下是苹果标，`status/` 下才是 Windows 旗标）。

所以 32/48/256 三档通常来自同一张 scalable 图（风格自洽），16 档用该题材自己的小尺寸图
（更清晰，wine 官方也是分尺寸画不同图的）。

`symbolic/`（单色稿）也进索引，但**不参与自动挑选**：只有这个名字既没有数字尺寸目录、
也没有 `scalable/` 时才兜底用它。用途是点那些「全主题只有单色版」的题材 ——
例如 `drive-optical-symbolic` 就只有 `devices/symbolic/` 一档。普通彩色图标不会被它抢走。

### 单引号属性的 SVG 会被静默缩放错（`drive-optical-symbolic` 的坑）

有些稿子不是 Inkscape 出的，根标签写成单引号：

```xml
<svg height='16.15' width='16' xmlns='http://www.w3.org/2000/svg'>
```

`mksheet.py` 原来只按 `="…"` 提属性 ⇒ `width`/`height` 一个都读不到，几何退化成 0，
`use_group` 就按「原尺寸」把图形摆进槽位 —— **256 格里只剩一个小点**，48/32/16 看起来
「差不多但偏小、偏下」，而且不报任何错。现在 `_attr_dict()` 单双引号都认，
根标签的 `xmlns:` 声明也一起认（否则内联后 `inkscape:` 前缀会变成未定义）。
**判据**：新增源之后，先看 256 档是不是明显比 48 档「大一个量级」；不对就 `grep` 一下源
根标签的引号。

### 源里「靠视口/clip 藏起来」的残留图层（内联时必须**删掉**，否则会糊掉相邻槽位）

We10X 的稿子里有两类这种残留，共同点是**在源里本来就看不见**：

* **① 视口外图层**：源 SVG 的根 `<svg>` 语义是「一个 `width`×`height` 的视口，里面按 `viewBox`
  的用户坐标画」，**超出视口的部分在源里就是看不见的**（最外层 `<svg>` 默认 `overflow:hidden`）。
  例如 `status/32/start-here.svg`（无 `viewBox`，`width/height=32`）里就有一个
  `matrix(0.7322,0,0,0.7477,37.48,9.43)` 的 `<g>`，坐标落在 32×32 视口之外。
* **② 自带 `clip-path` 裁掉的副本**：作者把整幅图复制成几份、副本挪到别处、靠**副本自己的
  `clip-path`** 裁没。`apps/scalable/help-browser.svg` 就是这样 —— 副本里有大量
  `fill="#fff"`，在拼版**右侧**糊出「白色云朵 / 白色吃豆人 / 白圈」。

两类在源里都看不见，**内联成 `<g>` 之后既没有视口、`clip-path` 也不一定有人认**，
那一层就直接画到槽位外面糊掉邻居；而且内容范围还会随「所在容器的尺寸」变化（同一个内联体
放进 32px 画布是 24×22，放进 64px 画布就变成 55×24）。

⇒ 处理分两层：

1. **主手段：删掉残留图层**（`mksheet.py::_strip_offscreen`）。只靠 `clipPath` 兜是不够的 ——
   查看器一旦不支持 `clip-path`，那些图层照旧画出来（下面「为什么删层优先」有实测）。所以
   直接把「在源里本来就看不见、对图标毫无贡献」的顶层元素从内联体里删掉。判据是**渲染实测**
   而不是估坐标：path 的 `d` 里混着相对命令（`h/v/c` 只写增量），静态算 bbox 必错。

   * **量污染的尺子是 clip-blind 模式**（`_probe(..., blind=True)`，会把**源自身**的 `clip-path`
     也摘掉再渲染）—— 那才是用户查看器的真实行为；只看「带 clip 时干不干净」会漏掉 ②
     （`help` 带 clip 时污染 0、摘掉全部 clip 后 2503 个纯白像素）。
   * **守门条件仍在正常模式**：被删掉的元素必须「删完以后**它用到的每个槽位框内像素一个不变**」
     且「正常模式框外污染仍为 0」。正常模式下看不见的元素，删掉当然不影响它对图标的贡献 ——
     这样既抓得到 ②，又不会误删源里**真靠 clip 才成形**的可见图形。
   * **批量删**：② 的残留可能有上百个顶层元素（`help` 就是），逐个删要好几百次渲染，所以先把
     「删掉后 clip-blind 污染下降」的元素一次挑出来整批试，整批过不了再退化成一次一个。

   ⚠ **探测必须逐槽位做，不能只挑一个**（`_probe_multi`）。同一份残留在不同缩放下的溢出位置
   不一样：`categories/22/applications-internet-symbolic.svg` 里那条"画在视口外的死 path"
   在 **256 档污染 0、16 档 86px、32 档 267、48 档 538**。早先的实现只拿**最大的**那个槽位去
   探（当时的注释写"缩放越大越容易看出来"，是错的），于是判它干净、残留原样留下，小档位照样
   糊出槽位外。（那张源后来因为别的原因被换掉了 —— 见「已知坑」里的位图坑；但它作为
   "逐槽位探测"的样本仍然成立。）
2. **双保险：与源视口等价的 `clipPath`**（纯矢量，不引入嵌套 `<svg>`），保证即使有漏网的
   溢出也画不出槽位。**注意 `clipPath` 必须与 `<g>` 平级、不能塞在 `<g>` 里面**：`<use>`
   会把 `<g>` 整棵克隆，塞在里面会让文档里出现两份同 id 的 `clipPath`（重复 id ⇒ 浏览器下
   行为未定义）。

**为什么删层优先、不能只留 clip**：用户实测反馈过两次 ——「上一轮只加了 clip，图标仍然叠在一起」
（① 类残留），以及「只挂 clip 的那版，`help.svg` 右侧照样冒出白色吃豆人」（② 类残留）。
两次都说明**运行时的查看器不认 `clip-path`**，所以量污染必须用 clip-blind 模式
（把所有 `clip-path` 都摘掉再渲染），只有那个模式下判定为干净才算真修好。

判据（改完必跑）：把整张拼版渲染成 632×272，**① 带 clip 时、② 摘掉自家 clip 后、
③ 摘掉全部 clip-path（含源自身的）后，10 个槽位矩形之外的像素都必须为 0**。修复前
`menu` 的 32bpp 矢量格是 32×28 顶格 + 污染到 48-4 槽位；早期只加 clip 的那版，`recent` 摘掉
clip 后有 53 个污染像素、`drive` 28 个；`categories/22/applications-internet-symbolic.svg` 那条
死 path 在 1 个槽位上有 2673 个越界像素；`help` 在 ③ 口径下有 2503 个纯白像素。现在全项目
50 个拼版文件三种情形都 = 0
（只有 `oic_hand` / `oic_note` / `oic_ques` 在 ③ 口径下还剩 384 个 **alpha≤4** 的极淡残影，
阈值 α>8 下为 0，肉眼不可见）。

### 已知坑（都已在映射表里处理掉）

* **白图标**：We10X 有若干 `status/*` 小图是**给深色面板用的白色图形**（如 `security-high` 的
  16 是白盾、`system-shutdown` 的 16 是白色电源符）。放到浅色任务栏/对话框上等于隐形。
  `mksheet.py` 会自动体检并告警（不透明像素里 >85% 近纯白），映射表用第 6 列把该档顶掉。
* **题材选错：`oic_sample`（IDI_APPLICATION）是「窗口」，不是「文件」**：wine 原版画的是白底 +
  蓝色标题栏的窗口。早先取 `mimes/application-x-executable`（白纸 + 齿轮）——那是**文件类型**
  图标，和 `document`（蓝纸 + 白线条）都是纸张形，在列表里看着像同一个图标。现改取
  `apps/scalable/preferences-system-windows`（纯矢量窗口，无内嵌位图，16~256 都能缩放）。
  判据：非文件题材的图标不该同时出现**折角 + 纸边**这两个特征。
* **逐尺寸画风不一致**：例如 `dialog-question` 16 是紫色、32 是蓝色；`status/24/security-high`
  是浅绿、`status/32` 才是饱和绿。同样用第 6 列统一到 32 档。
* **`16/` 目录里放的根本是另一张图**（比"画风不一致"更严重，16 档会显示成一个**别的图标**）：
  `actions/16/document-open-recent.svg`（10 KB 彩色文件夹）与 `actions/32`（664 B 单色文档+时钟）
  是两个完全不同的题材 —— 于是 `recent.svg` 的 16 档显示成一个「Windows 资源管理器」样子的
  彩色文件夹。⚠ 这是 We10X 的稿子问题，不是取源规则的错。修法是第 6 列把该档顶到
  **与大档同源**（`16=32` / `16=scalable` / `16=24` / `16=22`）。
  本项目已按此处理 20 个：`audio/delete/disk_drive/drive/eject/folder_open/image/log_off/
  mycomputer/mydocs/netdrive/optical_disc/play/printer/ramdisk/recent/video/oic_ques/
  oic_sample/oic_shield`。
  **判据**：渲染实测「16 档 vs 48 档」的形状相似度 —— 但**纯形状 IoU 只会给出误导性高分**
  （两个都是实心块的"文件夹 vs 文档"也能得 0.91），所以最终判定要用**源 path 的 `d` 结构**
  （子路径序列与命令字母是否对应）+ 把槽位渲成 ASCII 图直接看题材。
* **同名多档的"另一张图"也可能是同一字形的重绘，别误判**：`copy/move/window/rename/sleep/
  restricted` 的 16 与 24/32 出自不同文件、坐标量纲与留白都不同，形状 IoU 会掉到 0.5~0.7，
  但 `d` 的结构一一对应（如 `edit-copy` 16 与 24 都是"两张叠纸 + 折角"），属**同题材小尺寸
  简化稿**，保持现状（Wine 官方也是分尺寸画不同稿）。
* **同名档里"矢量"其实是位图**（`internet` 的坑）：`categories/22/applications-internet-symbolic.svg`
  看着是 SVG，内部却嵌了一张 **100×100 位图**（外加一条被平移到视口外的死 path），
  于是 256 档等于把位图放大 2.56 倍 —— **必糊，换取源尺寸救不了**。
  同名的纯矢量版只在 `categories/symbolic/`（云朵线框，形状对不上）。
  ⇒ 本项目改用 `devices/{16,24}/network-wireless.svg`（纯矢量 WiFi 扇形，保持原有外观且 256 档锐利）。
  **教训**：拿到一个源先看它 `path` / `image` 各几个 —— `image>0` 就意味着大档会糊。
* **`links/` 会把品牌 logo 焊到通用名上**：We10X 的 `links/apps/scalable/` 里
  `applications-internet.svg -> web-browser.svg`、`internet-web-browser.svg -> web-browser.svg`，
  而 `web-browser.svg` 是 **Microsoft Edge 的 logo**（蓝绿漩涡）。照「internet / applications-internet」
  这类名字找源就会撞上它。**取源前先 `readlink` 一下别名指向谁**，并确认目标不是厂商 logo。
* **`blank.svg` / `blank.ico` 不动**：wine 上游自带的「空白图标」源与产物，不是本项目生成的
  （与上游 10.10 逐字节相同）。`blank.svg` 是 632×272 拼版，但 10 个槽位全是
  `<rect style="fill:none">` —— 只有位置标记、不画图案，所以打开看就是一片空白；
  `tools/buildimage` 按这些 `<rect>` 的 x/y/w/h 从渲染结果裁块、打包成 10 帧全透明的
  `blank.ico`。`shell32.rc` 把这一张 ico 复用了 **6 处**：`IDI_SHELL_BLANK1..4`（ID 50~53，
  Windows 就有的保留图标号，只为对齐编号）和 `IDI_SHELL_DOWNLOAD(_EMBLEM)`（231/232，rc 里标
  `FIXME: Following resource is not yet added` —— wine 还没画下载文件夹图标，先用它占位）。
  代码里没有 `.c` 引用它们，属未实现的占位资源。映射表留空 = 保持原版；换成主题图案反而会让
  这些位置冒出错误图标。
* **`menu` 与 `oic_winlogo` 必须分开取源**：这两个以前都取了 `status/start-here`，结果
  「菜单」图标和「开始按钮徽标」长得一模一样。现在 `menu` → `actions/hamburger-menu`
  （汉堡三横线，符号语义更准），`oic_winlogo` → `status/start-here-symbolic`（Windows 旗标）。
  ⚠ `hamburger-menu` 在 We10X 里**只有 `actions/16` 一档**，32/48/256 都由这一张矢量放大
  （纯几何图形，放大不糊）；`start-here-symbolic` 只有 16/22/24 档，大档取 24。
* **「光驱」和「光盘」在 We10X 里是同一张图**：`devices/scalable/drive-optical.svg` 与
  `media-optical.svg` **逐字节相同**（md5 一致），`links/devices/{16,22,24}/drive-optical.svg`
  还全是指向 `media-optical` 的软链。按名字选源，`optical_drive` 和 `optical_disc` 必然撞脸
  （早期的 16 档手绘也是照同一张参考描的，所以两个尺寸档一起糊）。现改为 `optical_drive` 取
  `devices/symbolic/drive-optical-symbolic`（**驱动器面板 + 托盘里露半个盘**，全主题唯一一张
  真正的光驱画法），`optical_disc` 仍取 `media-optical`（圆盘），`drive` 仍是 `drive-harddisk`
  （横条盒）—— 面板 / 圆盘 / 横条盒三种形状互相可分。
  注意这张是单色稿（`fill='#363636'` 深灰线框，浅色背景可见），风格与旁边彩色图标不同；
  16~256 四档都走它，纯矢量放大不糊。曾试过 `drive-removable-media`（立式黑盒）：语义是
  「可移动介质驱动器」、且 16 档是另一套线框稿（和 32+ 的实心盒对不上），不如这张准。
  **判据**：两个资源落在同一类别时，先 `md5sum` 比一遍源文件，再 `readlink` 看 16/22/24 档
  是不是软链；名字不同不代表图不同。
* **`start-here-symbolic` 名字带 symbolic 但是彩色的**：源的 CSS 只声明
  `.ColorScheme-Text{color:#ffffff}`，而图形实际用 `fill="url(#…)"` 引渐变，所以 `color`
  不生效，渲染出来是**蓝色渐变旗标**（白底/深底都可见）。若哪天换成了真的 `currentColor`
  单色版本，就要留意它会变成白色（给深色面板用的）。
* **「SVG 外壳 + 内嵌位图」的源**（体检项，本项目实测不是缺陷）：We10X 里有一批源的
  `<svg>` 内部其实包着一张 base64 位图 —— `preferences/*` 几乎全是内嵌 31×31、
  `places/user-trash-full` 内嵌 256×256、`status/dialog-*` 内嵌 31×31。这种源放到 256 格
  等于「位图放大」。本项目实际用到的几个（`users`/`oic_shield`/`oic_hand`/`oic_note`/
  `oic_ques`/`image`/`trash_full`）逐个目视过 256 帧，仍然干净（那些位图本身只是纯色块/
  渐变，放大后看不出），所以没换源。**以后新增映射时先探一下**：
  `grep -c 'data:image' <We10X src>/<类别>/<尺寸>/<名字>.svg` —— 非 0 就意味着这档是位图，
  配合 `identify` 看位图尺寸是否远小于目标尺寸。


## 帧集说明

产物统一为 **10 帧**：`16/32/48` 各出 4bpp + 8bpp + 32bpp，`256` 出 32bpp（PNG 内嵌）。
这与 wine 最常见的拼版模式一致。wine 官方有些手绘稿用 24bpp 代替 8bpp、或没有 4bpp 帧，
`buildall.py` 会在末尾把这类差异逐条列出来（属既定选择，不是错误）。

`.ico` 用 `buildimage.py --label-depth` 生成 —— 色深以拼版上的标注为准。
（`buildimage.py` 默认模式是「按图内容定色深」，那是为了复现 wine 官方产物用的；
若用它生成彩色图，每一帧都会被判成 32bpp，帧集就不对了。）

## 验收口径（改完自己核一遍）

| 项目 | 判据 | 现状 |
|---|---|---|
| 拼版契约 | 根 `<svg>` 的直接子元素里正好 10 个 `id="icon:尺寸-色深"`；`-4/-8` 是 `<image>`（内嵌索引 PNG），`-32` 是 `<rect>` | 49/49 通过 |
| 低色深格规格 | `-4` 是位深 4 索引 PNG、`-8` 是位深 8 索引 PNG，都带 `tRNS` | 全部符合 |
| 颜色递减 | 同一尺寸三格渲染后的唯一色数：32bpp ≥ 8bpp ≥ 4bpp | 49/49 通过 |
| 不越界 | 整张拼版渲染 632×272，10 个槽位矩形之外的像素数：**① 带 clip、② 摘掉自家 clip、③ 摘掉全部 clip-path（含源自身的，= 模拟不认 clip 的查看器）三种情形** | **全 50 个 = 0（三种情形都是；③ 口径下 3 个 oic_* 还剩 alpha≤4 的极淡残影）** |
| 16 档取源 | 16 档与大档（48/256）是否同一题材；`16/` 目录是错图的已用第 6 列顶到大档 | 20 个已顶；其余为同题材简化稿（用 `d` 结构核过） |
| id 完整性 | 文档内无重复 `id`、`url(#…)` 引用全部有定义 | 49/49 通过（`_rename_ids` 会给源自带的重复 id 编号） |
| 取源保真 | 每个 32bpp 槽位与「该档的源 SVG 直接 `rsvg-convert -w <size>`」逐像素比对 | 196 个槽位：165 个**逐像素一致**，其余最大通道差 ≤3（1~5 个像素的亚像素相位差，肉眼不可见）；最大通道差 >8 的 **0 个** |
| 单文件体积 | —— | 6.7~179 KB，均值 **18.6 KB**（wine 原版 `folder.svg` 是 43.7 KB） |

体积最大的几个（`trash_full` 179KB、`help` 152KB、`trash_file` 81KB）是因为 We10X 那几个源 SVG
**内部自带 512×512 量级的 base64 位图**（源本身就是「SVG 外壳 + 位图」），不是生成器的问题。

### 与 wine 原版的一点既定差异

wine 官方的 4bpp 格带**抖动图案**（有序抖动/误差扩散），本项目用 PIL 的中值切分量化、
出的是**纯色块**。We10X 本身是扁平纯色风格，抖动反而会在 16/32px 下显脏，所以没加。
若要完全照抄 wine 的抖动，改 `mksheet.py` 的 `quant_indexed_png()` 即可。

