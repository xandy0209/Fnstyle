#!/bin/bash
# ---------------------------------------------------------------------------
# restore_deferred.sh —— 卸载时的「延迟后台还原执行器」
#
# ⚠★⚠★ 为什么必须是「延迟 + 完全脱离会话」的独立进程：
#   fnOS 的 nginx 内置 ngx_http_trim_recover 用 inotify 监控 /usr/trim/www/，
#   一旦该目录下任何文件被修改就 stop nginx → 从 www.zip 重建整个 www/
#   → start nginx（约 2~10 秒），期间**所有在途连接被切断**。
#   应用中心的「卸载 / 安装」请求就是一条在途连接，被切断后前端
#   fetch() 抛 "Failed to fetch" → 弹窗「无法卸载 fnstyle」。
#
#   实测（2026-11-14）：v2 还原在 uninstall_callback 里同步跑，卸载必失败。
#   正确顺序（与 index.cgi::apply_to_system 同一套铁律）：
#     ① 回调同步阶段只中和 www.zip / tow / raw（不碰 www/，不触发重建）
#     ② 回调立刻 exit 0 ⇒ 应用中心卸载请求正常返回
#     ③ 本脚本由 setsid + nohup 拉起，先 sleep N 秒避开请求收尾，
#        再写 www/、触发 nginx 重建、两轮复查并自检
#        —— 那时已无在途连接，nginx 停启无害。
#
# 环境变量（生产用默认值；本地沙箱验证可覆盖）：
#   FNSTYLE_GUARD_DIR / FNSTYLE_WWW / FNSTYLE_RESTORE_ROOT / FNSTYLE_DATA_DIR
#   FNSTYLE_RESTORE_DELAY（默认 12 秒）/ FNSTYLE_RESTORE_SETTLE（默认 5 秒）
#
# 日志：${GUARD}/restore_now.log；完成后清除 ${GUARD}/restore_pending
# ---------------------------------------------------------------------------
set -uo pipefail

GUARD="${FNSTYLE_GUARD_DIR:-/usr/trim/share/.restore/fnstyle-guard}"
WWW="${FNSTYLE_WWW:-/usr/trim/www}"
RESTORE="${FNSTYLE_RESTORE_ROOT:-/usr/trim/share/.restore}"
DATA="${FNSTYLE_DATA_DIR:-${GUARD}/run-data}"
DELAY="${FNSTYLE_RESTORE_DELAY:-12}"
SETTLE="${FNSTYLE_RESTORE_SETTLE:-5}"
SCRIPT="${GUARD}/restore_system.py"
LOG="${GUARD}/restore_now.log"
PENDING="${GUARD}/restore_pending"

mkdir -p "$GUARD" "$DATA" 2>/dev/null || true
dlog() {
    echo "[restore_deferred $(date '+%F %T')] $*" >> "$LOG" 2>/dev/null || true
}

dlog "===== 后台还原排程开始（delay=${DELAY}s settle=${SETTLE}s）====="
case "$DELAY" in
    ''|*[!0-9.]*) DELAY=12 ;;
esac
if [ "$DELAY" != "0" ]; then
    dlog "等待 ${DELAY}s，避开应用中心在途 HTTP 请求与卸载收尾"
    sleep "$DELAY"
fi
dlog "开始完整还原：此刻写 www/ 触发 nginx 重建已不会切断任何卸载请求"

if command -v python3 >/dev/null 2>&1 && [ -f "$SCRIPT" ]; then
    python3 "$SCRIPT" \
        --data-dir "$DATA" --www "$WWW" --restore "$RESTORE" \
        --guard "$GUARD" --settle "$SETTLE" >> "$LOG" 2>&1
    dlog "还原脚本退出码 $?"
    if ! python3 "$SCRIPT" \
        --data-dir "$DATA" --www "$WWW" --restore "$RESTORE" \
        --guard "$GUARD" --verify-only >> "$LOG" 2>&1; then
        dlog "⚠ 自检未通过，执行最小中和兜底"
        for d in "$WWW" "${RESTORE}/tow"; do
            [ -d "$d" ] || continue
            mkdir -p "$d/deskdata" 2>/dev/null || true
            printf '{"__fnstyleDisabled": true}\n' > "$d/deskdata/fnstyle.json" 2>/dev/null || true
            for s in fnstyle-login.js fnstyle-runtime.js; do
                printf '/*! __FNSTYLE_RESTORED_NOOP__ */\n' > "$d/$s" 2>/dev/null || true
            done
            rm -rf "$d/deskdata/fnstyle" 2>/dev/null || true
        done
        if command -v zip >/dev/null 2>&1 && [ -f "${RESTORE}/www.zip" ]; then
            zip -dq "${RESTORE}/www.zip" \
                "fnstyle-login.js" "fnstyle-runtime.js" \
                "deskdata/fnstyle.json" "deskdata/fnstyle/*" \
                >> "$LOG" 2>&1 || true
        fi
    fi
elif command -v python3 >/dev/null 2>&1; then
    # guard 脚本被意外删除：用内嵌的同一套「中和」逻辑兜底
    dlog "guard 脚本缺失，使用内嵌 PYCODE 兜底还原"
    cat > "${GUARD}/restore_inline.py" <<'PYCODE'
import os, sys, shutil, stat, zipfile
www, restore = sys.argv[1:3]
raw = os.path.join(restore, 'raw')
zip_path = os.path.join(restore, 'www.zip')
NEUTRAL = b'{"__fnstyleDisabled": true}\n'
STUB = (b'/*! __FNSTYLE_RESTORED_NOOP__ */\n'
        b'(function(){try{["fnstyle_wallpaper","fnstyle-login-bg"].forEach('
        b'function(i){var e=document.getElementById(i);if(e)e.remove()});'
        b'Array.prototype.forEach.call(document.querySelectorAll('
        b'\'style[id^="fnstyle"]\'),function(e){e.remove()})}catch(e){}})();\n')
SCRIPTS = ('fnstyle-login.js', 'fnstyle-runtime.js')

def norm(n):
    n = n.lstrip('/')
    while n.startswith('./'):
        n = n[2:]
    return n

def neutral_bytes(n, d):
    k = norm(n)
    if k in SCRIPTS:
        return STUB
    if k == 'deskdata/fnstyle.json':
        return NEUTRAL
    rawp = os.path.join(raw, k)
    if k in ('index.html', 'fndesk.js') or k.startswith('assets/'):
        if os.path.isfile(rawp):
            rd = open(rawp, 'rb').read()
            if b'fnstyle-' not in rd and b'deskdata/fnstyle.json?v=' not in rd:
                return rd
    if k == 'index.html' and b'fnstyle-' in d:
        import re
        return re.sub(rb'<script[^>]*fnstyle-[^>]*>\s*</script>', b'', d)
    return d

if os.path.isfile(zip_path):
    tmp = zip_path + '.restoring'
    try:
        st = os.stat(zip_path)
        with zipfile.ZipFile(zip_path) as zin, zipfile.ZipFile(tmp, 'w') as zout:
            for info in zin.infolist():
                n = norm(info.filename)
                if info.is_dir():
                    zout.writestr(info, b'')
                    continue
                if n.startswith('deskdata/fnstyle/'):
                    continue
                zout.writestr(info, neutral_bytes(n, zin.read(info.filename)))
        os.chmod(tmp, stat.S_IMODE(st.st_mode))
        try: os.chown(tmp, st.st_uid, st.st_gid)
        except OSError: pass
        os.replace(tmp, zip_path)
        print('inline: www.zip 已中和')
    except Exception as e:
        print('inline: zip 中和失败', e)
        try: os.unlink(tmp)
        except OSError: pass

for base in (os.path.join(restore, 'tow'), www):
    if not os.path.isdir(base):
        continue
    for rel in SCRIPTS:
        try: open(os.path.join(base, rel), 'wb').write(STUB)
        except OSError: pass
    dd = os.path.join(base, 'deskdata')
    try:
        os.makedirs(dd, exist_ok=True)
        open(os.path.join(dd, 'fnstyle.json'), 'wb').write(NEUTRAL)
    except OSError: pass
    shutil.rmtree(os.path.join(dd, 'fnstyle'), ignore_errors=True)
    for rel in ('index.html', 'fndesk.js'):
        p = os.path.join(base, rel)
        if os.path.isfile(p):
            try: open(p, 'wb').write(neutral_bytes(rel, open(p, 'rb').read()))
            except OSError: pass
    ad = os.path.join(base, 'assets')
    if os.path.isdir(ad):
        for n in os.listdir(ad):
            p = os.path.join(ad, n)
            if os.path.isfile(p) and (n.endswith('.js') or n.endswith('.css')):
                try: open(p, 'wb').write(neutral_bytes('assets/' + n, open(p, 'rb').read()))
                except OSError: pass
    print('inline: 已中和', base)
try:
    p = os.path.join(www, 'index.html')
    open(p, 'wb').write(open(p, 'rb').read())
except OSError: pass
print('inline: 完成')
PYCODE
    python3 "${GUARD}/restore_inline.py" "$WWW" "$RESTORE" >> "$LOG" 2>&1
    dlog "内嵌兜底退出码 $?"
else
    # 连 python3 都没有：写空配置 + 无操作脚本（绝不删，防 zip 重建复活）
    dlog "缺少 python3，执行最小中和"
    for d in "$WWW" "${RESTORE}/tow"; do
        [ -d "$d" ] || continue
        mkdir -p "$d/deskdata" 2>/dev/null || true
        printf '{"__fnstyleDisabled": true}\n' > "$d/deskdata/fnstyle.json" 2>/dev/null || true
        for s in fnstyle-login.js fnstyle-runtime.js; do
            printf '/*! __FNSTYLE_RESTORED_NOOP__ */\n' > "$d/$s" 2>/dev/null || true
        done
        rm -rf "$d/deskdata/fnstyle" 2>/dev/null || true
        if [ -f "${RESTORE}/raw/index.html" ]; then
            cp -f "${RESTORE}/raw/index.html" "$d/index.html" 2>/dev/null || true
        elif [ -f "$d/index.html" ]; then
            sed -E 's#<script[^>]*fnstyle-[^>]*></script>##g' \
                "$d/index.html" > "$d/index.html.tmp" 2>/dev/null && \
                mv -f "$d/index.html.tmp" "$d/index.html" 2>/dev/null || \
                rm -f "$d/index.html.tmp" 2>/dev/null
        fi
    done
    if command -v zip >/dev/null 2>&1 && [ -f "${RESTORE}/www.zip" ]; then
        zip -dq "${RESTORE}/www.zip" \
            "fnstyle-login.js" "fnstyle-runtime.js" \
            "deskdata/fnstyle.json" "deskdata/fnstyle/*" 2>/dev/null || true
    fi
fi

rm -f "$PENDING" 2>/dev/null || true
dlog "===== 后台还原结束（浏览器需 Cmd/Ctrl + Shift + R 强刷）====="
exit 0
