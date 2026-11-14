#!/bin/bash
# ---------------------------------------------------------------------------
# apply_worker.sh —— 「应用到系统」的实际执行者（独立后台进程）
#
# ⚠★⚠★ 为什么必须是独立进程（2026-11-11 血泪教训）：
#
#   fnOS 的 nginx 内置自研模块 ngx_http_trim_recover，用 inotify 监控
#   /usr/trim/www/ 目录。一旦该目录下任何文件被修改，它会：
#       1) stop trim_nginx.service
#       2) 从 /usr/trim/share/.restore/www.zip 重建整个 www/
#       3) start trim_nginx.service
#   全程约 2 秒，期间**所有在途连接（包括 CGI 自己这条）被切断**。
#
#   浏览器侧：fetch() 抛 "Failed to fetch"（网络层错误，非 4xx/5xx）。
#   nginx access.log：查不到该请求 —— worker 被杀时日志还没落盘。
#   ★ 这才是「POST /api/apply 从未出现在 access.log」的真正原因。
#
#   所以：**凡是会写 /usr/trim/www/ 的动作，都必须发生在 HTTP 响应发出之后**，
#   且必须运行在完全脱离 CGI 进程的独立后台进程里（setsid + nohup）。
#
# 用法（由 index.cgi 启动，环境变量传参）：
#   FNSTYLE_LIST="..." FNSTYLE_DATA_DIR=... bash apply_worker.sh
#
# 本脚本所有输出写日志文件，绝不污染 stdout。
# ---------------------------------------------------------------------------
set -u

# 应用 UI 目录：用 /var/apps/<app>/target 软链，跨设备/跨卷通用。
# （不要写死 /usr/local/apps/@appcenter/... —— 那是当前这台机器的安装位置，
#   别的设备 install_type / 存储卷不同时路径会变。）
UI_DIR="${FNSTYLE_UI_DIR:-/var/apps/fnstyle/target/ui}"

LOG_DIR="${FNSTYLE_DATA_DIR:-}"
LOG="${LOG_DIR}/apply_worker.log"
log() {
    printf '%s [apply-worker] %s\n' "$(date '+%F %T')" "$1" >> "$LOG" 2>/dev/null
}
[ -z "$LOG_DIR" ] && exit 1
: >> "$LOG" 2>/dev/null

log "===== 后台应用开始 ====="

# ---------------------------------------------------------------------------
# 关键：不在 worker 里复制一份 apply 逻辑（会与 index.cgi 分叉），
# 而是让 index.cgi 以「内部模式」再跑一遍自己 —— 复用同一份代码，
# 保证 HTML 标题改写等逻辑永远一致。
#
# 内部模式：环境变量 FNSTYLE_INTERNAL=1 时，index.cgi 只执行 apply 流程，
# 不输出 HTTP 响应（响应由外层 CGI 提前发给浏览器了）。
# ---------------------------------------------------------------------------
CGI="${UI_DIR}/index.cgi"
if [ ! -f "$CGI" ]; then
    log "❌ 找不到 index.cgi：$CGI"
    exit 1
fi

FNSTYLE_INTERNAL=1 \
FNSTYLE_LIST="${FNSTYLE_LIST:-}" \
FNSTYLE_DATA_DIR="${FNSTYLE_DATA_DIR:-}" \
FNSTYLE_ZIP="${FNSTYLE_ZIP:-}" \
FNSTYLE_TOW="${FNSTYLE_TOW:-}" \
FNSTYLE_WWW="${FNSTYLE_WWW:-}" \
FNSTYLE_SYS_FNSTYLE="${FNSTYLE_SYS_FNSTYLE:-}" \
FNSTYLE_SYS_DESKDATA="${FNSTYLE_SYS_DESKDATA:-}" \
FNSTYLE_CFG="${FNSTYLE_CFG:-}" \
FNSTYLE_UPLOAD="${FNSTYLE_UPLOAD:-}" \
FNSTYLE_LIB="${FNSTYLE_LIB:-}" \
FNSTYLE_FND="${FNSTYLE_FND:-}" \
FNSTYLE_FND_CFG="${FNSTYLE_FND_CFG:-}" \
REQUEST_METHOD=POST \
REQUEST_URI="/cgi/ThirdParty/fnstyle/index.cgi/api/apply" \
SCRIPT_NAME="/cgi/ThirdParty/fnstyle/index.cgi" \
PATH_INFO="/api/apply" \
QUERY_STRING="" \
CONTENT_LENGTH=0 \
CONTENT_TYPE="" \
SERVER_PROTOCOL=HTTP/1.1 \
SERVER_NAME=127.0.0.1 \
SERVER_PORT=8443 \
HTTP_HOST=127.0.0.1:8443 \
REMOTE_ADDR=127.0.0.1 \
HTTPS=on \
bash "$CGI" >> "$LOG" 2>&1

RC=$?
log "index.cgi 内部模式退出码=$RC"

# ---------------------------------------------------------------------------
# 收尾校验：nginx 重建 www/ 是从 www.zip 恢复的。
# 若 zip 写失败或内容不全，www/ 里的改动会在 nginx 重启后丢失。
# 等 nginx 完成停启（实测约 2 秒），再校验一次，不一致就重写。
# ---------------------------------------------------------------------------
sleep 4
LIST="${FNSTYLE_LIST:-}"
DATA_DIR="${FNSTYLE_DATA_DIR:-}"
UPLOAD="${FNSTYLE_UPLOAD:-}"
LIB="${FNSTYLE_LIB:-}"
FND="${FNSTYLE_FND:-}"
CFG="${FNSTYLE_CFG:-}"
FND_CFG="${FNSTYLE_FND_CFG:-}"
SYS_FNSTYLE="${FNSTYLE_SYS_FNSTYLE:-}"
SYS_DESKDATA="${FNSTYLE_SYS_DESKDATA:-}"

resolve_src() {
    local name="$1" p
    if [ "$name" = "fnstyle.json" ]; then
        if [ -f "$CFG" ]; then printf '%s' "$CFG"; return 0; fi
        if [ -f "$FND_CFG" ]; then printf '%s' "$FND_CFG"; return 0; fi
        return 1
    fi
    for p in "$DATA_DIR/fnstyle/$name" \
             "$UPLOAD/fnstyle/$name" \
             "$UPLOAD/$name" \
             "$LIB/fnstyle/$name" \
             "$FND/fnstyle/$name"; do
        if [ -f "$p" ]; then printf '%s' "$p"; return 0; fi
    done
    return 1
}

fixed=0
for name in $LIST; do
    src=$(resolve_src "$name") || continue
    [ -f "$src" ] || continue
    tgt="$SYS_FNSTYLE/${name}"
    [ "$name" = "fnstyle.json" ] && tgt="$SYS_DESKDATA/fnstyle.json"
    [ -f "$tgt" ] || { log "⚠ 重建后文件缺失，重写：$name"; cp -f "$src" "$tgt" 2>/dev/null; chmod 644 "$tgt" 2>/dev/null; fixed=$((fixed+1)); continue; }
    if ! cmp -s "$src" "$tgt"; then
        log "⚠ 重建后内容不一致，重写：$name"
        cp -f "$src" "$tgt" 2>/dev/null
        chmod 644 "$tgt" 2>/dev/null
        fixed=$((fixed+1))
    fi
done

if [ "$fixed" -eq 0 ]; then
    log "✅ 校验通过：nginx 重建后内容一致"
else
    log "已修正 $fixed 个文件"
fi

# ---------------------------------------------------------------------------
# 登录框 CSS 的专项复查（2026-11-11 新增）
#
# 登录框样式（毛玻璃/透明度/位置）不是靠复制资源文件实现的，
# 而是由 apply_style_overrides.py 改写 assets/login-form-*.css 的内容。
# nginx 重建 www/ 是从 www.zip 恢复的 —— 如果那时 zip 写入失败或条目不全，
# 改写出来的样式会在 1 秒内被还原成旧值，表现为「应用了但没变化」。
#
# 这里在 nginx 停启完成后再跑一次改写脚本（幂等），确保最终状态正确。
# ---------------------------------------------------------------------------
STYLE_SCRIPT="${UI_DIR}/apply_style_overrides.py"
WWW_DIR="${FNSTYLE_WWW:-/usr/trim/www}"
if [ -f "$STYLE_SCRIPT" ] && [ -f "$CFG" ]; then
    log "复查登录框样式改写…"
    python3 "$STYLE_SCRIPT" "$WWW_DIR" "$CFG" \
        --log "${FNSTYLE_DATA_DIR}/style_override.log" >> "$LOG" 2>&1
    log "登录框样式复查退出码=$?"
else
    log "（跳过登录框样式复查：脚本或配置不存在）"
fi

# ---------------------------------------------------------------------------
# 桌面标题的专项复查（2026-11-11 新增）
#
# 桌面标签页标题有两个写入方在写 www/ 下的文件：
#   · assets/index-*.js（桌面 SPA 主包）里的 R3()
#   · fndesk.js 的 visibilitychange 监听
# nginx 重建 www/ 是从 www.zip 恢复的 —— 如果那时 zip 写入失败或条目不全，
# 改写出来的标题逻辑会在 1 秒内被还原成 FnDesk 的硬编码值，
# 表现为「登录进桌面后标题又变回 Stone‘s Wish」。
#
# 这里在 nginx 停启完成后再跑一次标题改写脚本（幂等），确保最终状态正确。
# ---------------------------------------------------------------------------
TITLE_SCRIPT="${UI_DIR}/patch_title_overrides.py"
if [ -f "$TITLE_SCRIPT" ]; then
    log "复查桌面标题统一…"
    python3 "$TITLE_SCRIPT" "$WWW_DIR" \
        --log "${FNSTYLE_DATA_DIR}/title_override.log" >> "$LOG" 2>&1
    log "桌面标题复查退出码=$?"
else
    log "（跳过桌面标题复查：脚本不存在）"
fi

log "===== 后台应用结束 ====="
exit 0
