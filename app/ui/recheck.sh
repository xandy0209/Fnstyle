#!/bin/bash
# ---------------------------------------------------------------------------
# fnstyle 后台复查脚本（由 index.cgi 的 /api/apply 后台拉起）
#
# 用途：
#   sysrestore_service 每秒把 /usr/trim/share/.restore/www.zip 的内容
#   解压回 /usr/trim/www/。刚写入 www/ 的新内容（含改写过的 index.html
#   标题）可能在 1 秒内被还原成旧值 —— 表现为「点了应用但没变化」。
#
#   所以在 CGI **已经返回响应之后**，由本脚本延时 3 秒再把正确内容写一遍。
#   此时 zip 已经是新的，sysrestore 还原出来的也是新内容，两者一致。
#
# 用法（由 CGI 以环境变量方式调用）：
#   FNSTYLE_LIST="fnstyle.json a.png" \
#   FNSTYLE_DATA_DIR=... FNSTYLE_ZIP=... ... \
#   bash recheck.sh
#
# ⚠️ 本脚本的所有输出都写日志文件，绝不能污染 CGI 的 stdout。
# ---------------------------------------------------------------------------

sleep 3

LIST="${FNSTYLE_LIST:-}"
DATA_DIR="${FNSTYLE_DATA_DIR:-}"
ZIP="${FNSTYLE_ZIP:-}"
TOW="${FNSTYLE_TOW:-}"
WWW="${FNSTYLE_WWW:-}"
SYS_FNSTYLE="${FNSTYLE_SYS_FNSTYLE:-}"
SYS_DESKDATA="${FNSTYLE_SYS_DESKDATA:-}"
CFG="${FNSTYLE_CFG:-}"
UPLOAD="${FNSTYLE_UPLOAD:-}"
LIB="${FNSTYLE_LIB:-}"
FND="${FNSTYLE_FND:-}"
FND_CFG="${FNSTYLE_FND_CFG:-}"

LOG="${DATA_DIR}/debug.log"
log() {
    printf '%s [fnstyle-recheck] %s\n' "$(date '+%F %T')" "$1" >> "$LOG" 2>/dev/null
}

# 资源源解析（与 CGI 内 resolve_resource_src 保持同一优先级）
resolve_src() {
    local name="$1"
    if [ "$name" = "fnstyle.json" ]; then
        if [ -f "$CFG" ]; then printf '%s' "$CFG"; return 0; fi
        if [ -f "$FND_CFG" ]; then printf '%s' "$FND_CFG"; return 0; fi
        return 1
    fi
    local p
    for p in "$DATA_DIR/fnstyle/$name" \
             "$UPLOAD/fnstyle/$name" \
             "$UPLOAD/$name" \
             "$LIB/fnstyle/$name" \
             "$FND/fnstyle/$name"; do
        if [ -f "$p" ]; then printf '%s' "$p"; return 0; fi
    done
    return 1
}

# 1) 资源：写回 www/ 与 tow/
for name in $LIST; do
    src=$(resolve_src "$name") || continue
    [ -f "$src" ] || continue
    cp -f "$src" "$SYS_FNSTYLE/$name" 2>/dev/null
    [ "$name" = "fnstyle.json" ] && cp -f "$src" "$SYS_DESKDATA/fnstyle.json" 2>/dev/null
    chmod 644 "$SYS_FNSTYLE/$name" 2>/dev/null
    [ "$name" = "fnstyle.json" ] && chmod 644 "$SYS_DESKDATA/fnstyle.json" 2>/dev/null
    if [ -d "$TOW/deskdata/fnstyle" ]; then
        cp -f "$src" "$TOW/deskdata/fnstyle/$name" 2>/dev/null
        chmod 644 "$TOW/deskdata/fnstyle/$name" 2>/dev/null
        [ "$name" = "fnstyle.json" ] && cp -f "$src" "$TOW/deskdata/fnstyle.json" 2>/dev/null
    fi
done

# 2) index.html：从 www.zip 里重新解出来
#    （zip 里已是改写后的新版本，解出来就是对的）
if [ -f "$ZIP" ]; then
    TMP=$(mktemp /tmp/.fnstyle_recheck_html.XXXXXX 2>/dev/null)
    if unzip -p "$ZIP" index.html > "$TMP" 2>/dev/null && [ -s "$TMP" ]; then
        cp -f "$TMP" "$WWW/index.html" 2>/dev/null
        chmod 644 "$WWW/index.html" 2>/dev/null
        [ -d "$TOW" ] && cp -f "$TMP" "$TOW/index.html" 2>/dev/null
        [ -f "$TOW/index.html" ] && chmod 644 "$TOW/index.html" 2>/dev/null
        log "index.html 已从 www.zip 重新同步"
    fi
    [ -n "$TMP" ] && rm -f "$TMP" 2>/dev/null
fi

log "后台复查完成"
exit 0
