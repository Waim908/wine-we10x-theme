#!/usr/bin/env bash
#
# install.sh —— 把本项目的图标资源覆盖到一棵 wine 源码树。
#
#   ./install.sh /path/to/wine-source-tree
#   ./install.sh --dry-run /path/to/wine-source-tree
#
# 只覆盖 **tools/mapping.tsv 里列出的那些资源**（即本项目真正改过的 .svg/.ico），
# 而且是「wine 树里已有同名文件」才覆盖 —— 不会往 wine 树里塞新文件。
# 位图 / 光标（obm_*、ocr_*、idb_*、ietoolbar*、searching.avi）不在这张表里，保持不动。
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRY=0
TREE=""

while [ $# -gt 0 ]; do
  case "$1" in
    -n|--dry-run) DRY=1 ;;
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "未知参数：$1" >&2; exit 2 ;;
    *)  TREE="$1" ;;
  esac
  shift
done

if [ -z "$TREE" ]; then
  echo "用法: $0 [--dry-run] <wine 源码树>" >&2
  exit 2
fi
if [ ! -d "$TREE/dlls" ]; then
  echo "不是 wine 源码树：$TREE（找不到 dlls/）" >&2
  exit 2
fi

# 从映射表取资源名（跳过注释行与空行，只取第 1 列）
NAMES="$(awk -F'\t' '
  /^[[:space:]]*#/ { next }
  NF == 0 { next }
  { n=$1; gsub(/^[[:space:]]+|[[:space:]]+$/, "", n); if (n != "" && n != "-") print n }
' "$HERE/tools/mapping.tsv")"

if [ -z "$NAMES" ]; then
  echo "映射表里没读到任何资源名" >&2
  exit 1
fi

copied=0
missing=0
for mod in shell32 user32; do
  src="$HERE/dlls/$mod/resources"
  dst="$TREE/dlls/$mod/resources"
  if [ ! -d "$src" ]; then continue; fi
  if [ ! -d "$dst" ]; then
    echo "跳过 $mod：目标没有 $dst"
    continue
  fi
  while read -r name; do
    for ext in svg ico; do
      f="$src/$name.$ext"
      [ -e "$f" ] || continue
      if [ ! -e "$dst/$name.$ext" ]; then
        echo "跳过（$mod 里没有同名文件）：$name.$ext"
        missing=$((missing + 1))
        continue
      fi
      if [ "$DRY" = 1 ]; then
        echo "would cp  $mod/$name.$ext"
      else
        cp -p "$f" "$dst/$name.$ext"
        echo "copied    $mod/$name.$ext"
      fi
      copied=$((copied + 1))
    done
  done <<< "$NAMES"
done

if [ "$DRY" = 1 ]; then
  echo "—— dry-run：将覆盖 $copied 个文件（跳过 $missing 个）"
else
  echo "—— 已覆盖 $copied 个文件（跳过 $missing 个）"
  echo
  echo "接下来必须重编嵌了这些资源的两个 DLL，否则设备上看到的还是旧图标："
  echo "    make -C dlls/shell32 && make -C dlls/user32"
  echo "  ⚠ 别写 \`make dlls/shell32 dlls/user32\` —— 顶层 Makefile 里没有这个目标，"
  echo "    它只是磁盘上的目录，make 会回一句 \"Nothing to be done\" 然后什么都不做。"
fi
