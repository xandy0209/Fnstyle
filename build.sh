#!/bin/bash
# FnStyle fpk 构建脚本
#
# 产出 fnstyle_<version>_all.fpk
#   fpk = tar.gz，内部结构：
#     app.tgz      ← app/ 目录整体打包（ui + seed）
#     cmd/         ← 安装/启动/卸载脚本
#     config/      ← privilege + resource
#     manifest     ← 应用元数据（版本号在此）
#     wizard/      ← 安装向导
#     ICON*.PNG
#
# 用法：
#   ./build.sh              # 构建
#   ./build.sh --clean      # 构建前清理产物
#
# 打包后建议校验：
#   trim-cli app install-fpk fnstyle_1.0.0_all.fpk --dry-run --yes

set -euo pipefail

cd "$(dirname "$0")"

APP_NAME="fnstyle"
PLATFORM="all"

if [ "${1:-}" = "--clean" ]; then
    echo "[clean] 清理旧的 fpk 与 app.tgz"
    rm -f ./*.fpk ./app.tgz
fi

# ---- 1. 读取版本号（manifest 是唯一权威来源）----
if [ ! -f manifest ]; then
    echo "[fail] 找不到 manifest" >&2
    exit 1
fi
VERSION="$(awk -F= '/^version[[:space:]]*=/ {gsub(/[[:space:]]/, "", $2); print $2}' manifest)"
if [ -z "$VERSION" ]; then
    echo "[fail] 无法从 manifest 解析 version" >&2
    exit 1
fi

APPNAME_IN_MANIFEST="$(awk -F= '/^appname[[:space:]]*=/ {gsub(/[[:space:]]/, "", $2); print $2}' manifest)"
if [ "$APPNAME_IN_MANIFEST" != "$APP_NAME" ]; then
    echo "[fail] manifest 的 appname=$APPNAME_IN_MANIFEST，与脚本预期 $APP_NAME 不一致" >&2
    exit 1
fi

OUT="${APP_NAME}_${VERSION}_${PLATFORM}.fpk"

echo "[1/6] 版本号: $VERSION"

# ---- 2. 检查必需文件 ----
REQUIRED="manifest app/ui/fnstyle_server.py app/ui/index.cgi app/ui/index.html app/seed/default_themes.json app/seed/theme_cache cmd/main cmd/install_callback cmd/uninstall_callback cmd/restore_system.py cmd/restore_deferred.sh"
for f in $REQUIRED; do
    if [ ! -e "$f" ]; then
        echo "[fail] 缺少必需文件: $f" >&2
        exit 1
    fi
done
echo "[2/6] 必需文件检查通过"

# ---- 3. 打包 app/ → app.tgz ----
echo "[3/6] 打包 app/ → app.tgz"
rm -f app.tgz
# fnOS 的 app.tgz 内不含 app/ 前缀，且需要内含一份 config/
# （privilege / resource），与官方发行包结构保持一致
TMP_APP="$(mktemp -d)"
TMP_OUT=""
trap 'rm -rf "$TMP_APP" ${TMP_OUT:+"$TMP_OUT"}' EXIT
cp -R app/. "$TMP_APP/"
if [ -d config ]; then
    rm -rf "$TMP_APP/config"
    cp -R config "$TMP_APP/config"
fi

# ⚠️⚠️ 必须让构建**可重复**（deterministic）！
#
#   问题（2026-11-13 实测）：`tar czf` 会把
#     ① gzip 头里的**当前时间戳**
#     ② 每个文件的**原始 mtime**
#   一起写进包 ⇒ 同一份源码连续构建两次，产出的 fpk
#   大小/MD5/SHA256 **全都不同**（实测 1224967 / 1224980 字节两个值）。
#   ⇒ README 里写的校验值永远对不上，用户下载后一校验就发现不一致，
#     反而以为是安装包被篡改。
#
#   修法：
#     · gzip 用 -n（不写时间戳、不写原文件名）—— 这一步消除了最大的不确定性
#     · 所有文件的 mtime 用 touch 统一固定（tar 会记录 mtime）
#     · 文件顺序固定（GNU tar 用 --sort=name；
#       macOS 的 BSD tar 不支持该选项 ⇒ 退化为「先 find 排序再逐个传入」）
#     · owner/group 固定为 0:0（避免不同机器构建出不同包）
#
# ⚠️⚠️ macOS 自带的是 BSD tar：
#    不支持 --sort=name，也不支持 --mtime='...'（长选项形式）。
#    ⇒ mtime 统一靠 touch 落地，tar 选项只用 BSD 也支持的那些。
#    （GNU tar 上同样可正常工作：touch 过的 mtime 会被原样记录）

# 统一的构建时间戳（固定值 ⇒ 产物可重复）
BUILD_EPOCH="202001010000"

# 把所有文件的 mtime 统一成固定值
normalize_mtimes() {
    local dir="$1"
    find "$dir" -exec touch -t "$BUILD_EPOCH" {} + 2>/dev/null || true
}

portable_find_sorted() {
    # 在 $1 目录下递归列出**相对文件路径**（只列文件），按 LC_ALL=C 排序
    #
    # ⚠️ 只列文件，不列目录：
    #     tar 遇到目录会**递归打包**其内容；若目录与文件都列进参数，
    #     同一份内容会被存两遍（实测 app.tgz 从 1.5MB 膨胀到 4.7MB）。
    #     tar 在打包文件时会自动补出所需的目录条目。
    #
    # ⚠️ 排除规则必须写成「prune 后不 print」：
    #     find . -mindepth 1 \( -name X -o -name Y \) -prune -o -print
    #   -prune 命中时 **不执行 -print**（-o 的短路语义），
    #   被排除的目录及其全部子内容都不会出现在列表里。
    #   之前写成 `-name A -prune -o -name B -prune -o -print` 时，
    #   __pycache__ 目录本身虽被 prune，但**它内部的 .pyc 文件
    #   仍会被列出**（prune 只阻止目录向下递归）—— 实测 app.tgz 混进了 __pycache__。
    ( cd "$1" && find . -mindepth 1 \
        \( -name '__pycache__' -o -name '.DS_Store' \) -prune -o \
        -type f ! -name '*.pyc' -print | sed 's|^\./||' | LC_ALL=C sort )
}

normalize_mtimes "$TMP_APP"
APP_FILES="$(portable_find_sorted "$TMP_APP")"
tar --owner=0 --group=0 --numeric-owner \
    -cf - -C "$TMP_APP" $APP_FILES | gzip -n -9 > app.tgz

APP_TGZ_MD5="$(md5 -q app.tgz)"

# ---- 4. 写入 manifest 的 checksum（= app.tgz 的 MD5）----
echo "[4/5] 写入 manifest checksum"
if grep -qE '^[[:space:]]*checksum[[:space:]]*=' manifest; then
    # 替换已有行
    sed -i.bak -E "s|^[[:space:]]*checksum[[:space:]]*=.*$|checksum              = ${APP_TGZ_MD5}|" manifest
    rm -f manifest.bak
else
    # 追加到末尾
    printf 'checksum              = %s\n' "$APP_TGZ_MD5" >> manifest
fi
echo "      checksum = $APP_TGZ_MD5"

# ---- 5. 组装 fpk ----
echo "[5/6] 组装 $OUT"
rm -f "$OUT"
# 顺序无关，但 manifest 放前面便于人工 inspect
# ⚠️ 同样要固定时间戳/属主/顺序，保证构建可重复（见第 3 步的说明）
#    外层包用独立的暂存目录组装 —— 避免对整个仓库（含 .git）做 touch
TMP_OUT="$(mktemp -d)"
for entry in manifest app.tgz cmd config wizard ICON.PNG ICON_256.PNG; do
    cp -R "$entry" "$TMP_OUT/"
done
normalize_mtimes "$TMP_OUT"
OUT_FILES="$(portable_find_sorted "$TMP_OUT")"
tar --owner=0 --group=0 --numeric-owner \
    -cf - -C "$TMP_OUT" $OUT_FILES | gzip -n -9 > "$OUT"

# ---- 6. 校验产出 ----
echo "[6/6] 校验产出"
if [ ! -s "$OUT" ]; then
    echo "[fail] 产出的 fpk 为空" >&2
    exit 1
fi
SIZE="$(wc -c < "$OUT" | tr -d ' ')"
MD5="$(md5 -q "$OUT")"
SHA256="$(shasum -a 256 "$OUT" | cut -d' ' -f1)"

echo ""
echo "构建完成"
echo "  文件    : $OUT"
echo "  大小    : $SIZE 字节"
echo "  MD5     : $MD5"
echo "  SHA256  : $SHA256"
echo ""
echo "建议在 NAS 上先做 dry-run 校验："
echo "  trim-cli app install-fpk $OUT --dry-run --yes"
echo ""
echo "注意：fpk 不支持覆盖安装，已安装需先卸载。"
