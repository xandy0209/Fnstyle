#!/bin/bash
# 构建「FnStyle 系统还原」一次性工具包 + 自包含的手动还原脚本
#
# 产出：
#   dist/fnstylerestore_1.0.0_all.fpk   装一次即还原（推荐，无需 SSH）
#   tools/restore_now.sh                单文件手动还原（有终端时）
#
# 用法：bash restore-pack/build.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/restore-pack"
cd "$ROOT"

echo "[1/5] 同步还原核心脚本"
cp -f cmd/restore_system.py "$SRC/cmd/restore_system.py"
cp -f cmd/restore_deferred.sh "$SRC/cmd/restore_deferred.sh"
chmod 755 "$SRC/cmd/"*

echo "[2/5] 生成自包含的 tools/restore_now.sh"
{
cat <<'HEAD'
#!/bin/bash
# ---------------------------------------------------------------------------
# FnStyle 一键还原（手动版，单文件自包含）
#
# 用法（在飞牛 NAS 的 SSH / 终端里，用 root 执行）：
#     bash restore_now.sh
#
# 适用：卸载 FnStyle 后系统仍保留应用最后配置的样式，或出现
#       「先显示系统原生样式、随后又跳回软件配置样式」的回写现象。
# 内嵌的还原逻辑与安装包里的 cmd/restore_system.py 完全一致（v2 中和方案）。
# 日志：/usr/trim/share/.restore/fnstyle-guard/restore_now.log
# ---------------------------------------------------------------------------
set -uo pipefail
if [ "$(id -u)" != "0" ]; then
    echo "请用 root 执行（sudo bash restore_now.sh）"; exit 1
fi
GUARD="/usr/trim/share/.restore/fnstyle-guard"
mkdir -p "$GUARD" || exit 1
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
cat > "$TMP/restore_system.py" <<'PYEOF'
HEAD
cat cmd/restore_system.py
cat <<'TAIL'
PYEOF
cp -f "$TMP/restore_system.py" "$GUARD/restore_system.py" 2>/dev/null || true
chmod 755 "$GUARD/restore_system.py" 2>/dev/null || true
echo "===== $(date '+%F %T') 手动还原开始（v2 中和方案）=====" | tee -a "$GUARD/restore_now.log"
# 先掐断可能还在 sleep 3/4 的后台复查进程
if command -v pgrep >/dev/null 2>&1; then
    for pat in apply_worker.sh recheck.sh; do
        for pid in $(pgrep -f "$pat" 2>/dev/null); do
            kill -9 "$pid" 2>/dev/null || true
        done
    done
fi
python3 "$TMP/restore_system.py" \
    --data-dir "$GUARD/run-data" \
    --www /usr/trim/www \
    --restore /usr/trim/share/.restore \
    --settle 5 2>&1 | tee -a "$GUARD/restore_now.log"
echo "----- 自检 -----" | tee -a "$GUARD/restore_now.log"
python3 "$TMP/restore_system.py" \
    --data-dir "$GUARD/run-data" \
    --www /usr/trim/www \
    --restore /usr/trim/share/.restore \
    --verify-only 2>&1 | tee -a "$GUARD/restore_now.log"
echo "===== $(date '+%F %T') 手动还原结束 =====" | tee -a "$GUARD/restore_now.log"
echo "完成后请【强制刷新】浏览器（Cmd/Ctrl + Shift + R）；"
echo "旧标签页里的脚本缓存了配置，不刷新会继续套用旧样式。"
echo "自检结果：${GUARD}/restore_status.json"
TAIL
} > tools/restore_now.sh
chmod 755 tools/restore_now.sh

echo "[3/5] 打包 app/ → app.tgz"
TMP_APP="$(mktemp -d)"
TMP_OUT="$(mktemp -d)"
trap 'rm -rf "$TMP_APP" "$TMP_OUT"' EXIT
cp -R "$SRC/app/." "$TMP_APP/"
cp -R "$SRC/config" "$TMP_APP/config"
find "$TMP_APP" -exec touch -t 202001010000 {} + 2>/dev/null || true
APP_FILES="$(cd "$TMP_APP" && find . -mindepth 1 \
    \( -name '__pycache__' -o -name '.DS_Store' \) -prune -o \
    -type f ! -name '*.pyc' -print | sed 's|^\./||' | LC_ALL=C sort)"
tar --owner=0 --group=0 --numeric-owner \
    -cf - -C "$TMP_APP" $APP_FILES | gzip -n -9 > "$SRC/app.tgz"
APP_TGZ_MD5="$(md5 -q "$SRC/app.tgz")"

echo "[4/5] 写入 manifest checksum = $APP_TGZ_MD5"
python3 - "$SRC/manifest" "$APP_TGZ_MD5" <<'PY'
import sys, re
from pathlib import Path
p, md5 = Path(sys.argv[1]), sys.argv[2]
t = p.read_text(encoding='utf-8')
t = re.sub(r'(?m)^checksum\s*=.*$', 'checksum              = ' + md5, t)
p.write_text(t, encoding='utf-8')
PY

for entry in manifest app.tgz cmd config wizard ICON.PNG ICON_256.PNG; do
    cp -R "$SRC/$entry" "$TMP_OUT/"
done
find "$TMP_OUT" -exec touch -t 202001010000 {} + 2>/dev/null || true
OUT_FILES="$(cd "$TMP_OUT" && find . -mindepth 1 \
    \( -name '__pycache__' -o -name '.DS_Store' \) -prune -o \
    -type f ! -name '*.pyc' -print | sed 's|^\./||' | LC_ALL=C sort)"
mkdir -p dist
tar --owner=0 --group=0 --numeric-owner \
    -cf - -C "$TMP_OUT" $OUT_FILES | gzip -n -9 > dist/fnstylerestore_1.0.0_all.fpk

echo "[5/5] 完成"
echo "  dist/fnstylerestore_1.0.0_all.fpk  $(wc -c < dist/fnstylerestore_1.0.0_all.fpk | tr -d ' ') 字节"
echo "  tools/restore_now.sh              $(wc -c < tools/restore_now.sh | tr -d ' ') 字节"
rm -f "$SRC/app.tgz"
