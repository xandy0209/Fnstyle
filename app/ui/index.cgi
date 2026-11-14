#!/bin/bash
# ---------------------------------------------------------------------------
# FN定制化配置 —— 后端 CGI
#
# 纯 bash 实现（fnOS 宿主不保证有 python3 / jq）。
# 路由（全部相对 /cgi/ThirdParty/fnstyle/index.cgi/）：
#   GET  /api/config              读取本应用的个性化配置（JSON）
#   POST /api/config              写入配置（body = {key: value, ...}）
#   POST /api/apply               把当前配置应用到 fnOS 系统（不写 FnDesk）
#   GET  /api/fndesk-config       读取 FnDesk 当前配置（只读，用于对比/导入）
#   GET  /api/theme-libs          读取主题库源列表
#   POST /api/theme-libs          保存主题库源列表
#   GET  /api/themes              读取已缓存的主题数据
#   POST /api/themes              合并写入主题数据
#   GET  /api/proxy?url=...       跨域代理（拉远程 theme.json / bg_list）
#   GET  /api/proxy-json?url=...  带 _meta 元信息的跨域代理（新增主题库时用）
#   POST /api/upload              上传图片/视频（multipart）
#   GET  /api/file?name=...       读取已上传文件
#   DELETE /api/file?name=...     删除已上传文件
# ---------------------------------------------------------------------------
set -uo pipefail

# ----------------------------- 路径配置 -----------------------------
# APP_ROOT 是 fnOS 安装后创建的标准软链：
#   /var/apps/<pkg>/target -> /volN/@appcenter/<pkg>
# 安装时 app/ 这一层会被剥掉，所以实际内容是 <target>/ui/...
APP_NAME="fnstyle"
APP_ROOT="/var/apps/${APP_NAME}/target"
UI_DIR="${APP_ROOT}/ui"
STATIC_DIR="${UI_DIR}/static"

# ---- 数据共享目录（跨设备可移植，勿硬编码 /vol1）----
# ⚠️ 不同设备的系统盘可能是 vol2 / vol3，甚至存在多卷，
#    硬编码 /vol1 会让本应用在别的机器上装完就读不到数据。
# fnOS 安装时会为 config/resource 里声明的 data-share 建立软链：
#     /var/apps/<app>/shares/<name> -> /volN/@appshare/<app>
# 这个软链在任何设备上都成立，是首选的稳定入口。
# 解析优先级：shares 软链 → TRIM_DATA_SHARE_PATHS → /vol1（老设备兜底）
resolve_share_root() {
    local p
    for p in "/var/apps/${APP_NAME}/shares/${APP_NAME}" \
             "/var/apps/${APP_NAME}/share/${APP_NAME}"; do
        if [ -d "$p" ]; then printf '%s' "$p"; return 0; fi
    done
    if [ -n "${TRIM_DATA_SHARE_PATHS:-}" ]; then
        p="${TRIM_DATA_SHARE_PATHS%%:*}"
        if [ -n "$p" ] && [ -d "$p" ]; then printf '%s' "$p"; return 0; fi
    fi
    printf '%s' "/vol1/@appshare/${APP_NAME}"
}
SHARE_ROOT="$(resolve_share_root)"
DATA_DIR="${SHARE_ROOT}/deskdata"
UPLOAD_DIR="${DATA_DIR}/upload"
LIB_DIR="${DATA_DIR}/lib"
CFG_FILE="${DATA_DIR}/fnstyle.json"
THEME_FILE="${DATA_DIR}/theme.json"
THEME_LIB_FILE="${DATA_DIR}/theme_list.json"
BG_LIB_FILE="${DATA_DIR}/bg_list.json"

# FnDesk 侧的真实路径（用于「从 FnDesk 导入」；对方未安装时应优雅降级）
if [ -d "/var/apps/fndesk/shares/fndesk" ]; then
    FND_SHARE="/var/apps/fndesk/shares/fndesk/deskdata"
else
    FND_SHARE="/vol1/@appshare/fndesk/deskdata"
fi
FND_CFG="${FND_SHARE}/fnstyle.json"
FND_THEME="${FND_SHARE}/theme.json"
FND_THEME_LIB="${FND_SHARE}/theme_list.json"
# ---------------------------------------------------------------------------
# 系统生效侧（fnOS 桌面/登录页真正读取的位置）
#
# ⚠️ 关键机制（2026-11-10 实测）：
#   /usr/trim/www/            ← nginx 对外提供，URL = /deskdata/fnstyle/xxx
#   /usr/trim/share/.restore/  ← fnOS 的「系统还原点」目录
#       ├── www.zip            ← 真正的还原源（sysrestore_service 每次从这里解压）
#       └── tow/               ← www.zip 的解压工作副本
#   /usr/trim/bin/sysrestore_service
#                             ← 守护进程，每秒把 www.zip 的内容还原回 /usr/trim/www/
#
#   所以：只 cp 图片到 /usr/trim/www/deskdata/ 会在 1 秒内被还原回旧图！
#   必须三处同时更新：www.zip（源）→ tow/（副本）→ www/（生效）
# ---------------------------------------------------------------------------
SYS_WWW="/usr/trim/www"
SYS_DESKDATA="${SYS_WWW}/deskdata"
SYS_FNSTYLE="${SYS_DESKDATA}/fnstyle"
RESTORE_DIR="/usr/trim/share/.restore"
RESTORE_ZIP="${RESTORE_DIR}/www.zip"
RESTORE_TOW="${RESTORE_DIR}/tow"


# ----------------------------- 工具函数 -----------------------------
mkdir -p "$DATA_DIR" "$UPLOAD_DIR" "$LIB_DIR" 2>/dev/null

json_escape() {
    # 转义 JSON 字符串里的特殊字符
    local s=$1
    s=${s//\\/\\\\}
    s=${s//\"/\\\"}
    s=${s//$'\n'/\\n}
    s=${s//$'\r'/\\r}
    s=${s//$'\t'/\\t}
    printf '%s' "$s"
}

# 从 JSON 中取某个顶层字符串键的值（够用即可，不追求完整 JSON 解析）
# 用法: json_get_str <file> <key>
json_get_str() {
    local file=$1 key=$2
    [ -f "$file" ] || return 1
    sed -n "s/.*\"${key}\"[[:space:]]*:[[:space:]]*\"\\([^\"]*\\)\".*/\\1/p" "$file" | head -1
}

# 从 JSON 中取某个顶层非字符串键的值（数字/布尔）
json_get_raw() {
    local file=$1 key=$2
    [ -f "$file" ] || return 1
    sed -n "s/.*\"${key}\"[[:space:]]*:[[:space:]]*\\([^,}[:space:]]*\\).*/\\1/p" "$file" | head -1
}

# 把一个扁平 JSON 对象解析成 "key<TAB>value" 行（value 保留原始 JSON 字面量）
# 与 json_parse_kv 同源，只是数据来自文件而非 stdin。
json_flatten() {
    local file=${1:-/dev/stdin}
    json_parse_kv < "$file"
}

# 批量把 key<TAB>value 合并进一个扁平 JSON 对象文件（单次重建，不会互相覆盖）
# 用法: json_merge_file <target.json> <kv.tsv>
json_merge_file() {
    local target=$1 kvfile=$2
    local tmp="${target}.merge.$$"
    [ -f "$target" ] || echo '{}' > "$target"
    # 注意：这里不要屏蔽 stderr，出错时需要知道原因
    if ! awk -f "${UI_DIR}/json_tool.awk" -v mode=merge "$target" "$kvfile" > "$tmp"; then
        return 1
    fi
    if [ ! -s "$tmp" ]; then
        # awk 没产出内容（可能是脚本路径错误或输入异常）
        rm -f "$tmp"
        return 1
    fi
    # ⚠️ mv 会用临时文件的权限取代目标文件权限。
    # awk 生成的临时文件受 umask 影响通常是 600，
    # 直接 mv 会把原本 644 的配置文件变成 600 —— 之后写进 www.zip
    # 就会让 nginx（www-data）读不到 → 403。
    chmod 644 "$tmp" 2>/dev/null
    mv -f "$tmp" "$target"
    return 0
}

# 解析一个 JSON 请求体（从 stdin），输出 "key<TAB>value" 行。
# value 保留原始 JSON 字面量（字符串带引号，数字不带）。
# 能正确处理：字符串内的逗号、转义引号、\uXXXX。
json_parse_kv() {
    awk -f "${UI_DIR}/json_tool.awk" -v mode=parse
}

# 把 "key": value 写进一个扁平 JSON 对象文件（单键版本，内部走批量合并）
#
# 用法: json_set <file> <key> <raw_value>
json_set() {
    local file=$1 key=$2 value=$3
    local kv="${file}.kv.$$"
    printf '%s\t%s\n' "$key" "$value" > "$kv"
    json_merge_file "$file" "$kv"
    local rc=$?
    rm -f "$kv"
    return $rc
}

# ---- 开关类字段的 null 归一化目标值 ----
#
# 格式：<key><TAB><value>
#
# ⚠️⚠️ 这个值必须等于**该字段在 runtime 里的缺省行为**，
#    不是 UI 上的 offValue！两者对不同字段并不相同：
#
#      字段            runtime 缺省              UI offValue
#      Fndesk_taskbar  num(x,0)  → 0 = 关闭       0 = 关闭   ✓ 相同
#      nasNameDisplay  num(x,1)  → 1 = 显示       2 = 隐藏   ✗ 不同！
#
#    nasNameDisplay 的 offValue 是 2（隐藏），
#    但 runtime 缺省是 1（显示）——
#    若按 offValue 归一化，null 会被写成 2，
#    等于把用户「显示名字」的行为反转成「隐藏名字」，是回归。
#
#    所以这里的语义统一为：**null = 该字段在 runtime 下的缺省行为**，
#    保证「界面显示什么」与「实际生效什么」永远一致。
#    （前端 app.js 的字段定义里 nullValue 必须与此表保持同步）
#
SWITCH_NULL_DEFAULTS="
Fndesk_taskbar	0
nasNameDisplay	1
autoRestoreOnUninstall	1
"

# 把 kv 文件里值为字面量 null 的字段归一化
# 参数: <kv.tsv 文件>
# 返回: 0=有字段被归一化；1=无需归一化（文件原样）
#
# ⚠️⚠️ 必须**逐行原样保留**，只替换命中 null 的那一行的值。
#    早前一版实现把「命中 null 的行」单独写进 .fixed 再整file替换，
#    结果同批次里其他字段（如 appTitle、loginOpacity）全被丢弃 ——
#    一次保存就把用户的整份配置洗白。绝对不要那样写。
normalize_null_values() {
    local kvf="$1"
    [ -f "$kvf" ] || return 1
    local out="${kvf}.fixed" key val off changed=0
    : > "$out"
    while IFS=$'\t' read -r key val; do
        [ -z "$key" ] && continue
        if [ "$val" = "null" ]; then
            off=$(printf '%s\n' "$SWITCH_NULL_DEFAULTS" | awk -F'\t' -v k="$key" '$1==k{print $2; exit}')
            if [ -n "$off" ]; then
                printf '%s\t%s\n' "$key" "$off" >> "$out"
                changed=1
                continue
            fi
        fi
        # 非 null，或该 key 没有登记默认值 → 原样保留
        printf '%s\t%s\n' "$key" "$val" >> "$out"
    done < "$kvf"
    mv -f "$out" "$kvf"
    return $((1 - changed))
}

# ---------------------------------------------------------------------------
# 归一化**存量配置文件**里的 null 值
#
# 参数: <json 文件路径>
#
# ⚠️ 为什么需要这一步（2026-11-13 实测，用户反馈「所有配置都没生效」）：
#   normalize_null_values 只作用于 **POST /api/config 的本次提交批次**。
#   但用户的脏数据是**早就写进 CFG_FILE 的存量**（比如早期版本的
#   saveField 把 NaN 序列化成 null 写进去的）。
#   而「应用到系统」复制的是 CFG_FILE 这个存量文件 ——
#   ⇒ 用户点「应用到系统」时，null 原样被复制到系统，
#     runtime 判 `num(null, 0) !== 1` ⇒ 开关不生效。
#   ⇒ 必须在 apply 之前对存量文件做一次归一化。
#
# ⚠️⚠️⚠️ 必须用**纯文本原地替换**，绝对不能「打平 → 重新序列化」！
#
#   实测踩坑（2026-11-13 v6 首版）：
#     最初实现是 json_flatten(整个文件) → 逐行处理 → json_merge_file 写回。
#     而 json_tool.awk 的 parse_obj 是**逐行记录**处理多行 JSON 的，
#     遇到 `"someList": [\n "/vol2/1000/Music"\n ]` 这种**跨行数组**，
#     数组值会被截断成 `[`，重新序列化后变成
#         "someList": [,
#         "    \"/vol2/1000/Music\"": "",
#         "  ]": ""
#     ⇒ **配置文件被写坏成非法 JSON**（用户配置直接报废）。
#
#   正解：只对「值为 null 的那一个键」做定点文本替换，
#         其余字节一个都不碰 —— 用 sed 的精确匹配即可。
# ---------------------------------------------------------------------------
normalize_cfg_file_nulls() {
    local cfgf="$1"
    [ -f "$cfgf" ] || return 1
    local key val off changed=0
    local bak="${cfgf}.normbak.$$"

    for key in $(printf '%s\n' "$SWITCH_NULL_DEFAULTS" | awk -F'\t' '{print $1}'); do
        off=$(printf '%s\n' "$SWITCH_NULL_DEFAULTS" | awk -F'\t' -v k="$key" '$1==k{print $2; exit}')
        [ -n "$off" ] || continue
        # 只在「该键的值确实是 null」时才替换
        #   匹配 "key": null （允许任意空白，不允许跨行）
        #   ⚠️ 末尾必须有 ($) 分支 —— 否则「null 在行尾」（单字段配置、
        #      文件最后一行就是它）这种形态匹配不上，会漏掉最常见的场景。
        if grep -qE "\"${key}\"[[:space:]]*:[[:space:]]*null([,}[:space:]]|$)" "$cfgf" 2>/dev/null; then
            cp -f "$cfgf" "$bak" 2>/dev/null
            # 用 awk 做逐行精确替换（避免 sed 的转义地狱，且能保持原行格式）
            awk -v k="$key" -v v="$off" '
                BEGIN { done = 0 }
                {
                    if (!done) {
                        # 只替换 "key": null 这一处；key 前的引号必须紧邻
                        pat = "\"" k "\"[[:space:]]*:[[:space:]]*null"
                        if ($0 ~ pat) {
                            gsub(pat, "\"" k "\": " v)
                            done = 1
                        }
                    }
                    print
                }
            ' "$cfgf" > "${cfgf}.norm.$$" 2>/dev/null
            if [ -s "${cfgf}.norm.$$" ]; then
                chmod 644 "${cfgf}.norm.$$" 2>/dev/null
                mv -f "${cfgf}.norm.$$" "$cfgf"
                changed=1
                log_debug "配置归一化：$key null → $off"
            else
                rm -f "${cfgf}.norm.$$" 2>/dev/null
            fi
            [ -f "$bak" ] && rm -f "$bak"
        fi
    done

    rm -f "${cfgf}.norm.$$" 2>/dev/null
    return $((1 - changed))
}

http_json() {
    local code=$1 body=$2
    # ⚠️ Content-Length 必须是「字节数」而不是「字符数」。
    # bash 的 ${#body} 是字符数，中文 UTF-8 每字 3 字节，
    # 用字符数当长度会让浏览器提前截断响应 → JSON 解析失败。
    local blen
    blen=$(printf '%s' "$body" | wc -c | tr -d ' ')
    printf 'Status: %s\r\n' "$code"
    printf 'Content-Type: application/json; charset=utf-8\r\n'
    printf 'Cache-Control: no-store\r\n'
    printf 'Access-Control-Allow-Origin: *\r\n'
    printf 'Content-Length: %s\r\n\r\n' "$blen"
    printf '%s' "$body"
}

# 输出一个 JSON 文件的内容（Content-Length 用字节数）
http_json_file() {
    local code=$1 file=$2
    local blen
    blen=$(wc -c < "$file" 2>/dev/null | tr -d ' ')
    [ -z "$blen" ] && blen=0
    printf 'Status: %s\r\n' "$code"
    printf 'Content-Type: application/json; charset=utf-8\r\n'
    printf 'Cache-Control: no-store\r\n'
    printf 'Access-Control-Allow-Origin: *\r\n'
    printf 'Content-Length: %s\r\n\r\n' "$blen"
    cat "$file"
}

# 输出一个 JSON 文件，但**剥掉 UTF-8 BOM**
#
# 为什么需要（2026-11-13 实测）：
#   远程壁纸库源里，有的带 BOM、有的不带 —— 例如
#     bg_test.json / bg_jy0812.json  → 无 BOM
#     bg_jy0813.json                  → 带 BOM（EF BB BF）
#   前端 JSON.parse() 遇到 BOM 会直接抛
#     "Unexpected UTF-8 BOM (decode using utf-8-sig)"
#   ⇒ 带 BOM 的库源一个都加载不出来，且后端日志看不出任何异常
#     （http_json_file 是 cat 原样输出，不做校验）。
#
# 剥离方式：用 sed 删掉行首的 BOM 字节序列（EF BB BF），
#   只影响**第一行**开头，正好是 BOM 所在位置，不会误伤正文。
http_json_file_nobom() {
    local code=$1 file=$2
    local tmp="${file}.nobom.$$"
    if sed '1s/^\xEF\xBB\xBF//' "$file" > "$tmp" 2>/dev/null; then
        http_json_file "$code" "$tmp"
    else
        http_json_file "$code" "$file"
    fi
    rm -f "$tmp" 2>/dev/null
}

# ---------------------------------------------------------------------------
# 外链媒体资源 → 本地化
# ---------------------------------------------------------------------------
#
# 为什么必须有这一步（2026-11-13 实测定位到的真根因）：
#
#   主题库里的图片全是**外链 URL**，例如
#     https://fndesk.imcq.top/images/theme/高达00_t1.png
#   前端把它写进配置后，apply 流程会做：
#     ① resolve_apply_list  → 取 basename  = "高达00_t1.png"
#     ② resolve_resource_src → 在本地目录里找这个文件
#     ③ 找不到 → **该资源被跳过**（return 2）
#   ⇒ 系统侧 /usr/trim/www/deskdata/fnstyle/ 里的老文件原封不动，
#     而原生 JS 里写死的引用路径也没变 ⇒ **用户看到「应用主题没生效」**。
#
#   同时原生 JS 里的路径是 `/deskdata/fnstyle/loginLogo.png` 这种固定名，
#   与外链资源的文件名完全对不上 —— 就算把外链原样写进 fnstyle.json，
#   桌面 SPA 也只认它自己那几个固定路径。
#
#   ⇒ 正解：**先把外链下载到本地、按原生写死的固定名落盘，
#     再把配置字段改成对应的本地路径**。
#     这样 apply 流程的既有逻辑（原样同步文件 + 同步原生 JS 路径）就自然生效。
#
# ⚠️⚠️ 落地文件名的铁律（2026-11-13 实测）：
#
#   落地名必须与**原生 JS 里写死的路径完全一致**：
#     /deskdata/fnstyle/loginLogo.png
#     /deskdata/fnstyle/loginBackground.png
#     /deskdata/fnstyle/deviceLogo.png
#     /deskdata/fnstyle/desktopWallpaper.jpg
#     /deskdata/fnstyle/webFavicon.png
#
#   为什么不能沿用外链自己的文件名（如「高达00_t1.png」）：
#     实测（2026-11-13）—— path 同步机制会把原生 JS 里的写死路径
#     改写成配置里的实际文件名，于是 JS 变成
#       /deskdata/fnstyle/钢之炼金术师_t1.png?v=...
#     而 apply 的资源同步按 basename 去本地找「钢之炼金术师_t1.png」，
#     本地根本没有这个文件（外链从没被下载过）⇒ **资源被跳过**。
#     ⇒ JS 指向 404 地址 ⇒ 浏览器什么都不显示 ⇒ 「应用主题没生效」。
#
#   ⇒ 所以：**下载到固定名**，配置里写 `/deskdata/fnstyle/loginLogo.png`，
#     原生 JS 的写死路径就与实际文件严丝合缝，不需要任何路径同步。
#
# ⚠️ 视频壁纸例外：
#   主题库里 t2（登录页背景）/ t4（桌面壁纸）有 mp4 动态壁纸。
#   mp4 内容存成 .png 会导致浏览器按图片解码 → 播放失败。
#   ⇒ 视频另存为 `<固定名>.mp4`（如 desktopWallpaper.mp4），
#     并把配置与原生 JS 的引用一起切到该路径。
#     （原生 JS 的路径同步逻辑按「字段语义标记」识别，
#       desktopWallpaper 这个标记在 desktopWallpaper.mp4 里同样命中 ⇒ 自动同步）
#
# 参数: <url> <目标文件名（不含扩展名）>
# 返回: 0=下载成功并已落盘（输出实际文件名到 stdout）；1=失败
# ---------------------------------------------------------------------------

# 媒体字段 → 原生写死的固定文件名（不含扩展名）
media_local_stem() {
    case "$1" in
        loginBackground)  printf 'loginBackground' ;;
        loginLogo)        printf 'loginLogo' ;;
        deviceLogo)       printf 'deviceLogo' ;;
        webFavicon)       printf 'webFavicon' ;;
        desktopWallpaper) printf 'desktopWallpaper' ;;
        *)                printf '' ;;
    esac
}

# 从 URL 里推断扩展名（只认常见图片/视频格式）
media_ext_from_url() {
    local u="$1"
    local ext="${u##*/}"
    ext="${ext%%\?*}"
    case "$ext" in
        *.png)  printf 'png'  ;;
        *.jpg|*.jpeg) printf 'jpg' ;;
        *.gif)  printf 'gif'  ;;
        *.webp) printf 'webp' ;;
        *.bmp)  printf 'bmp' ;;
        *.svg)  printf 'svg' ;;
        *.mp4)  printf 'mp4'  ;;
        *.webm) printf 'webm' ;;
        *.mov)  printf 'mov'  ;;
        *)      printf ''    ;;
    esac
}

# 判断内容是不是「真实的媒体文件」而不是 HTML 错误页
#
# ⚠️ 用**魔数（magic number）**判定，而不是「首字节不是 0a/7b」——
#    早期实现用 case "$head" in 0a*|7b*) 排除，
#    但 0a（换行）和 7b（{）都是合法二进制文件可能出现的首字节 ⇒ 误杀。
#
# 现在按文件类型分别核对魔数：
#   PNG   89504e47
#   JPEG  ffd8ff
#   GIF   47494638
#   WEBP  RIFF....WEBP
#   BMP   424d
#   MP4   ....ftyp
#   WEBM/MOV ....ftyp (EBML 头 1a45dfa3)
media_is_valid() {
    local f="$1" ext="$2"
    local head
    head=$(head -c 16 "$f" 2>/dev/null | od -An -tx1 2>/dev/null | tr -d ' \n')
    [ -z "$head" ] && return 1
    case "$ext" in
        png)  case "$head" in 89504e47*) return 0 ;; esac ;;
        jpg)  case "$head" in ffd8ff*)   return 0 ;; esac ;;
        gif)  case "$head" in 47494638*|4749463[9a]*) return 0 ;; esac ;;
        bmp)  case "$head" in 424d*)     return 0 ;; esac ;;
        svg)  head=$(head -c 200 "$f" 2>/dev/null | tr -d ' \r\n' | head -c 300); case "$head" in *'<svg'*|*'<?xml'*) return 0 ;; esac ;;
        webp) case "$head" in 52494646*57454250*) return 0 ;; esac ;;
        mp4|webm|mov)
            # ISO BMFF：第 5~8 字节是 ftyp（4 字节 = 8 个 hex 字符）
            # ⚠️ 「????」只匹配**一个**字符，4 字节要写 8 个 ?；
            #    写错会导致所有 mp4 都被判为非法 ⇒ 视频壁纸永远下载不下来。
            case "$head" in ????????66747970*) return 0 ;; esac
            # WebM/Matroska：EBML 头 1a45dfa3
            case "$head" in 1a45dfa3*) return 0 ;; esac
            ;;
    esac
    return 1
}

# 下载外链媒体到本地
#
# 参数: <url> <目标文件名（不含扩展名）>
# 返回: 0=成功（stdout 输出实际落地文件名）；1=失败
download_media_to_local() {
    local url="$1" stem="$2"
    local dest_dir="${DATA_DIR}/fnstyle"

    [ -z "$url" ] && return 1
    [ -z "$stem" ] && return 1

    mkdir -p "$dest_dir" 2>/dev/null || return 1

    # 从 URL 推断扩展名；推断不出来就当图片 png
    local ext
    ext=$(media_ext_from_url "$url")
    [ -z "$ext" ] && ext="png"

    # 视频与图片的固定扩展名不同：
    #   登录背景 / 桌面壁纸 → 视频用 mp4，图片用 png/jpg
    #   其余（LOGO / favicon）→ 恒为 png
    case "$stem" in
        loginBackground|desktopWallpaper)
            case "$ext" in
                mp4|webm|mov) : ;;
                *) ext="png" ;;
            esac
            ;;
        *)
            # LOGO / favicon 只接受静态图片
            case "$ext" in
                mp4|webm|mov) return 1 ;;
                jpg|jpeg) ext="jpg" ;;
                png|gif|webp|bmp|svg) : ;;
                *) ext="png" ;;
            esac
            ;;
    esac

    local dest="${dest_dir}/${stem}.${ext}"
    local tmp="${dest}.dl.$$"
    local src_tag="${dest}.src"

    # ⚠️⚠️⚠️ 幂等判定必须用「来源 URL」，不能用「文件是否存在」（2026-11-13 实测踩坑）：
    #
    #   早期实现是「文件已存在且非空 → 直接复用，不下载」：
    #     第一次应用主题「高达00」→ 下载 → 落地 loginLogo.png
    #     第二次应用主题「钢之炼金术师」→ **文件名一样（loginLogo.png）**
    #       → 命中「已存在」⇒ 跳过下载 ⇒ **旧图还在** ⇒ 换主题看起来没反应
    #
    #   ⇒ 正解：落地文件旁边存一个 sidecar（<文件>.src）记录来源 URL，
    #     URL 相同才复用；URL 不同 ⇒ 重新下载覆盖。
    #     换主题（URL 变）→ 重下 ✓；同一主题重复点应用（URL 不变）→ 复用 ✓
    if [ -s "$dest" ] && [ -f "$src_tag" ]; then
        local old_src
        old_src=$(cat "$src_tag" 2>/dev/null)
        if [ "$old_src" = "$url" ]; then
            printf '%s' "$(basename "$dest")"
            return 0
        fi
        # URL 变了 ⇒ 换个临时名下载，避免覆盖失败时旧文件也没了
        tmp="${dest}.dl.$$"
    fi

    # ⚠️ URL 里的中文要 percent-encode：
    #    主题库文件名含中文（如「高达00_t1.png」），
    #    实测 curl 7.88 直接传原始 UTF-8 也能通（服务端宽容），
    #    但部分服务器会 400 ⇒ 统一先编码更稳。
    local enc
    enc=$(url_encode "$url")

    curl -sL --max-time 120 \
         -H 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36' \
         -H 'Accept: image/avif,image/webp,image/apng,image/*,*/*;q=0.8' \
         -o "$tmp" "$enc" 2>/dev/null </dev/null

    if [ ! -s "$tmp" ]; then
        rm -f "$tmp" 2>/dev/null
        return 1
    fi
    # 校验确实是图片/视频，不是 HTML 错误页 / JSON 报错
    if ! media_is_valid "$tmp" "$ext"; then
        rm -f "$tmp" 2>/dev/null
        return 1
    fi

    mv -f "$tmp" "$dest" 2>/dev/null || { rm -f "$tmp" 2>/dev/null; return 1; }
    chmod 644 "$dest" 2>/dev/null
    # 记录来源 URL（供下次幂等判定用，见上面的说明）
    printf '%s' "$url" > "$src_tag" 2>/dev/null
    chmod 644 "$src_tag" 2>/dev/null
    printf '%s' "$(basename "$dest")"
    return 0
}

# URL percent-encode（只编码非 ASCII 且不在安全字符集里的部分）
# 用 python3（fnOS 自带），失败则原样返回
url_encode() {
    local u="$1"
    if command -v python3 >/dev/null 2>&1; then
        python3 -c '
import sys, urllib.parse
print(urllib.parse.quote(sys.argv[1], safe=":/?#[]@!$&()*+,;=%"))' "$u" 2>/dev/null </dev/null && return 0
    fi
    printf '%s' "$u"
}

# 把 KV 文件里的外链媒体 URL 换成本地路径
#
# 参数: <KV 文件>  （每行 key<TAB>value，value 是 JSON 字符串字面量）
# 返回: 0=有本地化发生；1=没有
#
# ⚠️⚠️⚠️ 为什么不能用 `while IFS=$'\t' read -r key val`（2026-11-13 实测踩坑）：
#   bash 的 read 会把 IFS 里的 TAB 当作**空白字符**处理：
#     · 行首/行尾的 IFS 空白被折叠
#     · 连续 IFS 空白被压缩成一个
#   而 KV 的 value 是个 JSON 字符串字面量（`"https://..."`），
#   实测 read 出来的结果是：
#     key=[loginLogo	"h]   val=[tp://127.0.0.1:18099/src.png"]
#   ⇒ key 里混进了 TAB 和半个引号 ⇒ 后续所有 case 判断全部落空
#   ⇒ **一个资源都没被本地化，且没有任何报错**（静默失效，最难查）。
#
#   ⇒ 正解：用 `cut` 按 TAB 切开第 1 个字段作为 key、其余整体作为 value。
#     cut 只做字节切分，不做任何空白折叠，行为完全可预测。
localize_external_media() {
    local kv="$1"
    local line key val url stem fname local_path changed=0

    [ -f "$kv" ] || return 1

    while IFS= read -r line || [ -n "$line" ]; do
        [ -z "$line" ] && continue
        # ---- 用 cut 切分：第 1 段是 key，其余整体是 value ----
        key=$(printf '%s' "$line" | cut -f1)
        val=$(printf '%s' "$line" | cut -f2-)
        [ -z "$key" ] && continue
        [ "$key" = "$line" ] && continue      # 没有 TAB ⇒ 不是 KV 行
        stem=$(media_local_stem "$key")
        [ -z "$stem" ] && continue
        # 只处理值是 JSON 字符串字面量的行
        case "$val" in
            '"'*'"') ;;
            *) continue ;;
        esac
        # 取出裸值（去掉首尾引号）
        url="${val#\"}"
        url="${url%\"}"
        # 只处理 http/https 外链
        case "$url" in
            http://*|https://*) ;;
            *) continue ;;
        esac
        fname=$(download_media_to_local "$url" "$stem")
        if [ $? -eq 0 ] && [ -n "$fname" ]; then
            local_path="/deskdata/fnstyle/${fname}"
            # 用 awk 精确重写该行（key 里的正则特殊字符不用转义，比 sed 安全）
            if kv_replace_line "$kv" "$key" "\"$local_path\"" 2>/dev/null; then
                log_debug "外链资源已本地化：$key ← $url → $local_path"
                changed=1
            fi
        else
            log_debug "外链资源下载失败（保留原 URL）：$key ← $url"
        fi
    done < "$kv"

    [ "$changed" = "1" ] && return 0
    return 1
}

# 精确重写 KV 文件里某一行（key<TAB>value → key<TAB>新value）
#
# ⚠️ 为什么不用 sed：
#    sed 的 s|^\(${key}\)\t.*$| 里，key 若含正则特殊字符（. + * ? [ ] 等）
#    会被当作正则元字符解析；主题名/字段名里出现这些字符就会匹配错行。
#    awk 用「字符串精确比较」，没有这个问题。
#
# 参数: <KV 文件> <key> <新值（含引号的 JSON 字面量）>
kv_replace_line() {
    local kv="$1" key="$2" newval="$3"
    local tmp="${kv}.kvr.$$"
    awk -F'\t' -v k="$key" -v nv="$newval" '
        BEGIN { OFS="\t" }
        { if ($1 == k) { print k, nv; done=1 } else { print $1, $2 } }
        END { if (!done) exit 3 }
    ' "$kv" > "$tmp" 2>/dev/null
    local rc=$?
    if [ $rc -ne 0 ] || [ ! -s "$tmp" ]; then
        rm -f "$tmp" 2>/dev/null
        return 1
    fi
    # 条数必须一致（防止误删/误增行）
    local a b
    a=$(wc -l < "$kv" | tr -d ' ')
    b=$(wc -l < "$tmp" | tr -d ' ')
    if [ "$a" != "$b" ]; then
        rm -f "$tmp" 2>/dev/null
        return 1
    fi
    mv -f "$tmp" "$kv" 2>/dev/null || { rm -f "$tmp" 2>/dev/null; return 1; }
    return 0
}

ok() {
    local msg extra
    msg=$(json_escape "$1")
    extra="${2:-}"
    http_json "200 OK" "{\"success\":true,\"message\":\"${msg}\"${extra}}"
}

fail() {
    local msg
    msg=$(json_escape "$1")
    http_json "200 OK" "{\"success\":false,\"message\":\"${msg}\"}"
}

# ---------------------------------------------------------------------------
# apply_to_system —— 把配置真正应用到 fnOS 系统
#
# 背景：FnDesk 的「配置应用」动作（POST /api/update-data）会把资源落到
# /usr/trim/www/ 与还原点，但依赖 FnDesk 登录态。
# 我们独立实现这一段，避免依赖 FnDesk。
#
# 处理三处（顺序重要）：
#   1. RESTORE_ZIP  www.zip  ← 真正的还原源，必须先更新
#   2. RESTORE_TOW  tow/     ← www.zip 的工作副本
#   3. SYS_FNSTYLE  www/     ← 对外生效目录
#
# 资源清单（兜底）：fnstyle.json + loginBackground.png / loginLogo.png /
#           deviceLogo.png / desktopWallpaper.jpg / webFavicon.png
#
# ⚠️ 这只是「配置读不出来时」的兜底。
#    正常路径是 resolve_apply_list() —— 从配置里解析「实际引用的文件名」，
#    否则前端新上传的图片（文件名带 fnstyle_ 前缀）永远不在清单里，
#    点「应用到系统」时根本不会被同步到系统侧。
# ---------------------------------------------------------------------------
APPLY_RESOURCES="fnstyle.json loginBackground.png loginLogo.png deviceLogo.png desktopWallpaper.jpg webFavicon.png"

# 配置里哪些 key 指向图片资源（值形如 /deskdata/fnstyle/xxx.png）
RESOURCE_FIELDS="loginBackground loginLogo deviceLogo desktopWallpaper webFavicon"

# ---------------------------------------------------------------------------
# 资源源解析 —— 决定「哪个文件会被应用到系统」
#
# 背景（2026-11-11 实测修复）：
#   原实现把资源源硬编码为 FnDesk 目录（${FND_SHARE}/fnstyle/），
#   导致个性化中心自己上传/导入的图片永远不会被应用——
#   点「应用到系统」时，落到系统的仍是 FnDesk 那套文件。
#
# 现在按优先级依次查找，找到第一个就用它：
#   1) 本应用的 fnstyle/          ← 用户在界面上编辑/预览的主副本（最高优先）
#   2) 本应用的 upload/fnstyle/   ← 从 FnDesk 导入 / 上传的副本
#   3) 本应用的 lib/fnstyle/       ← 主题库缓存
#   4) FnDesk 的 fnstyle/          ← 回落：FnDesk 原有的资源
#
# ⚠️ 顺序为什么是这样（2026-11-11 实测）：
#   导入时资源会同时落在 fnstyle/ 和 upload/fnstyle/，两者内容相同，
#   所以 MD5 校验一直看不出问题。但一旦用户在界面上改了图（差异标记法验证），
#   排在前面的 upload/ 旧副本就会把新改的盖掉 —— 表现为「改了没反应」。
#   用户编辑的落点是 fnstyle/，所以它必须排第一。
#
# fnstyle.json 特殊：优先用本应用自己的配置（这才是用户在个性化中心里改的），
# 找不到才回落 FnDesk 的。
# ---------------------------------------------------------------------------
resolve_resource_src() {
    local name="$1"
    if [ "$name" = "fnstyle.json" ]; then
        if [ -f "${CFG_FILE}" ]; then printf '%s' "${CFG_FILE}"; return 0; fi
        if [ -f "${FND_CFG}" ]; then printf '%s' "${FND_CFG}"; return 0; fi
        return 1
    fi
    local p
    for p in "${DATA_DIR}/fnstyle/${name}" \
             "${UPLOAD_DIR}/fnstyle/${name}" \
             "${UPLOAD_DIR}/${name}" \
             "${LIB_DIR}/fnstyle/${name}" \
             "${FND_SHARE}/fnstyle/${name}"; do
        if [ -f "$p" ]; then printf '%s' "$p"; return 0; fi
    done
    return 1
}

# ---------------------------------------------------------------------------
# 从配置里解析「实际要应用的资源文件名」
#
# 背景（2026-11-11 实测）：
#   原实现用硬编码清单 APPLY_RESOURCES（loginLogo.png 等固定名），
#   但前端上传的图会存成 fnstyle_loginLogo_xxx.png 并把配置里的
#   loginLogo 字段改成这个新文件名 —— 新文件不在固定清单里，
#   于是点「应用到系统」时它永远不会被同步到系统侧，
#   表现为「上传了新图，系统还是旧图」。
#
# 现在改为：从 fnstyle.json 里读出各资源字段的**实际文件名**，
# 拼成最终清单；读不到的字段再退回固定清单兜底。
# ---------------------------------------------------------------------------
resolve_apply_list() {
    local list="fnstyle.json"
    local f name
    for f in $RESOURCE_FIELDS; do
        name=""
        # 配置值形如 "/deskdata/fnstyle/loginLogo.png" → 取 basename
        if [ -f "${CFG_FILE}" ]; then
            name=$(json_get_str "${CFG_FILE}" "$f" 2>/dev/null)
            if [ -n "$name" ]; then
                name=$(basename "$name")
            fi
        fi
        # 配置里没有 / 字段为空 → 退回固定名
        if [ -z "$name" ]; then
            case "$f" in
                loginBackground)     name="loginBackground.png" ;;
                loginLogo)           name="loginLogo.png" ;;
                deviceLogo)          name="deviceLogo.png" ;;
                desktopWallpaper)    name="desktopWallpaper.jpg" ;;
                webFavicon)          name="webFavicon.png" ;;
            esac
        fi
        [ -z "$name" ] && continue
        # 去重
        case " $list " in
            *" $name "*) ;;
            *) list="$list $name" ;;
        esac
    done
    printf '%s' "$list"
}

# 判断当前 CGI 是否以 root 身份运行（fnOS 的 config/privilege 声明 run-as: root）
is_root() {
  [ "$(id -u 2>/dev/null || echo 0)" = "0" ]
}

# ---------------------------------------------------------------------------
# apply_index_html —— 把「网站标题 / 描述」写进 fnOS 桌面页 index.html
#
# 背景（2026-11-10 实测，用户反馈「配置了网站标题但实际没变化」）：
#   fnOS 的 index.html 里，标题是**写死的字面量**，不是从 fnstyle.json 读的：
#
#     <title>飞牛 fnOS</title>                                  ← 静态标题
#     <meta property="og:title" content="Stone‘s Wish">        ← 字面量
#     <meta property="og:site_name" content="Stone‘s Wish">    ← 字面量
#     document.title="Stone‘s Wish"                            ← 字面量（JS 改写）
#
#   那段 JS 是 FnDesk 魔改时写进去的：
#     window.addEventListener('load',()=>{const e=['5ddd.com','fnos.net'],
#       h=location.hostname,m=!e.some(d=>h.includes(d));
#       m&&setTimeout(()=>{document.title="Stone‘s Wish"},500)})
#
#   也就是说：**只改 fnstyle.json 的 appTitle 对浏览器标签页标题没有任何影响**，
#   除非同时改写 HTML 里的这几处字面量。
#
#   ⚠️ 其中 <title> 是「首屏」标题（浏览器标签页立即显示的就是它），
#      og:title / document.title 只影响分享卡片和 JS 延迟改写。
#      2026-11-11 补上了 <title> 的替换，避免首屏仍显示「飞牛 fnOS」。
#
# 本函数：从配置取 appTitle / appTitle2 / web_desc，
#         改写 index.html 中的对应字面量，然后三处同改（zip / tow / www）。
#
# ⚠★ 调用时机：本函数会写 /usr/trim/www/index.html → 触发 fnOS nginx 停启重建。
#    所以**绝不能**在 CGI 主进程输出 HTTP 响应之前调用它
#    （否则 nginx 重启会把当前连接切断 → 浏览器 "Failed to fetch"）。
#    现在的做法是：主进程只回响应，真正写入由 apply_worker.sh 在独立后台进程里做。
# ---------------------------------------------------------------------------
# 把字符串安全地嵌进 JS 字符串字面量（转义引号、反斜杠、换行）
js_escape() {
    local s=$1
    s=${s//\\/\\\\}
    s=${s//\'/\\\'}
    s=${s//\"/\\\"}
    s=${s//$'\n'/\\n}
    s=${s//$'\r'/\\r}
    printf '%s' "$s"
}

# 把字符串安全地嵌进 HTML 属性值（转义 & " < >）
#
# ⚠️ bash 的 ${var//pat/repl} 里，repl 中的 & 会被解释为「匹配的文本」，
#    所以 ${s//&/\&amp;} 实测会变成 \&amp;（多一个反斜杠）。
#    正确写法是用带引号的字符串做替换值（bash 不会把 & 当锚点）：
#      ${s//&/"&amp;"}
html_escape() {
    local s=$1
    s=${s//&/"&amp;"}
    s=${s//\"/"&quot;"}
    s=${s//</"&lt;"}
    s=${s//>/"&gt;"}
    printf '%s' "$s"
}

# 把字符串安全地嵌进 sed 的**替换串**
#
# ⚠️ sed 的替换串里 & 有特殊含义（表示「整个匹配」），
#    所以转义后的实体（&quot; &amp; &lt; &gt;）必须再把 & 转义成 \&，
#    否则替换结果会被截断（实测："My \"Home\"" 只留下 "My "）。
#    同时替换串里的 / 也要转义（这里用 # 作分隔符，主要防 & 和 #）。
# 把字符串安全地嵌进 sed 的**替换串**
#
# ⚠️ sed 替换串里有两个特殊字符：
#    &  → 表示「整个匹配」，必须转义成 \&
#    \  → 表示反向引用（\1 \n 等），必须转义成 \\
#    另外分隔符 # 也要转义
#
# ⚠️ 但「是否要转义 \」取决于值来自哪里：
#    - HTML 属性值：经过 html_escape 后含 &quot; &amp; 等实体，
#      这些实体里没有 \，所以 \\ 的转义是多余的但无害（实体不含 \）。
#    - JS 字面量：js_escape 已经产出 \" 这样的 JS 转义，
#      这时如果再把 \ 转义成 \\，sed 输出会变成 \\\" —— JS 语义就错了。
#      实测 \ 在 sed 替换串里后跟普通字符（如 \"）时是安全的，
#      只有 \1 \n 这类反向引用才需要转义。
#
# 所以这里只转义 & 和 #，不动 \。JS 与 HTML 两条路径都能正确输出。
sed_repl_escape() {
    local s=$1
    # ⚠️ sed 替换串里有两个特殊字符：
    #    &  → 表示「整个匹配」，必须转义成 \&
    #    \  → 会被吃掉（\" 变成 "），必须转义成 \\，让 sed 输出 \
    #    另外分隔符 # 也要转义
    #
    # ⚠️ 顺序很重要：
    #    1) 先把 \ 转义成 \\（否则 js_escape 产出的 JS 转义 \" 会丢反斜杠）
    #    2) 再把 & 转义成 \&（这一步会引入新的 \，不能再回头转义）
    #    3) 最后转义分隔符 #
    s=${s//\\/\\\\}
    s=${s//&/"\\&"}
    s=${s//#/\\#}
    printf '%s' "$s"
}

# 读取配置里的字符串值（正确处理转义引号）
#
# ⚠️ 不用 json_get_str：它的 sed 正则 `\"\\([^\"]*\\)\"` 遇到值里有转义引号
#    （\"）时会提前截断（实测 "My \"Home\"" 只取到 "My \"）。
#    json_tool.awk 的 parse 模式是逐字符扫描，能正确处理。
#
# 用法: json_get_str_safe <file> <key>     → 输出去掉外层引号的原始值
json_get_str_safe() {
    local file=$1 key=$2
    [ -f "$file" ] || return 1
    awk -f "${UI_DIR}/json_tool.awk" -v mode=parse "$file" 2>/dev/null \
    | awk -F'\t' -v k="$key" '
        $1 == k {
            v = $2
            # 去掉 JSON 字面量的外层引号，并解开转义
            if (v ~ /^".*"$/) {
                v = substr(v, 2, length(v) - 2)
                gsub(/\\"/, "\"", v)
                gsub(/\\\\/, "\\", v)
                gsub(/\\n/, "\n", v)
                gsub(/\\r/, "\r", v)
                gsub(/\\t/, "\t", v)
            }
            print v
            exit
        }'
}

# 改写一份 index.html 副本里的标题/描述字面量
# 参数: <html 文件路径>
# 依赖: 配置里的 appTitle / appTitle2 / web_desc
rewrite_index_html() {
    local html="$1"
    [ -f "$html" ] || return 1

    local title title2 desc favicon
    title=$(json_get_str_safe "${CFG_FILE}" "appTitle" 2>/dev/null)
    title2=$(json_get_str_safe "${CFG_FILE}" "appTitle2" 2>/dev/null)
    desc=$(json_get_str_safe "${CFG_FILE}" "web_desc" 2>/dev/null)
    favicon=$(json_get_str_safe "${CFG_FILE}" "webFavicon" 2>/dev/null)

    [ -z "$title" ] && return 1
    # appTitle 为空时退回 appTitle2
    [ -z "$title" ] && title="$title2"

    local jt ht hd
    jt=$(js_escape "$title")
    ht=$(sed_repl_escape "$(html_escape "$title")")
    hd=$(sed_repl_escape "$(html_escape "$desc")")
    # JS 替换串里的 & 同样要转义
    jt=$(sed_repl_escape "$jt")

    # ---- favicon / og:image 指向的应该是「网页 favicon」，不是「设备 LOGO」----
    #
    # 背景（2026-11-11 取证）：
    #   fnOS 的 index.html 里写死的是
    #     <link rel="icon" href="/deskdata/fnstyle/deviceLogo.png?v=...">
    #     <meta property="og:image" content="/deskdata/fnstyle/deviceLogo.png">
    #   而配置里有两个独立字段：
    #     webFavicon  → 网页 favicon（浏览器标签页图标）
    #     deviceLogo  → 设备 LOGO（登录页/桌面用）
    #   FnDesk 自己也是靠正则替换这两个标签来改 favicon 的。
    #   ⇒ 不改的话，用户在「网页 favicon」里换了图，浏览器标签页仍是旧的设备 LOGO。
    local fav_attr=""
    if [ -n "$favicon" ]; then
        # favicon 是路径（/deskdata/fnstyle/xxx.png），不含 HTML 实体，
        # 直接做 sed 替换串转义即可（& \ # 三个特殊字符）。
        fav_attr=$(sed_repl_escape "$favicon")
    fi

    local tmp="${html}.rw.$$"
    # 用 sed 逐处替换。HTML 是单行长文本，sed 的 s/// 处理单行足够。
    #
    # ⚠️ <title> 标签也必须一起改：
    #   浏览器标签页首屏显示的是 <title>，而不是 og:title。
    #   fnOS 桌面页写死的是 <title>飞牛 fnOS</title>，
    #   只有 og:title / document.title 被改 → 首屏仍显示「飞牛 fnOS」，
    #   要等 500ms 后 JS 改写才变成用户配置的标题。
    #   所以这里把 <title> 也纳入替换范围。
    sed \
      -e "s#<title>[^<]*</title>#<title>${ht}</title>#g" \
      -e "s#<meta property=\"og:title\" content=\"[^\"]*\">#<meta property=\"og:title\" content=\"${ht}\">#g" \
      -e "s#<meta property=\"og:site_name\" content=\"[^\"]*\">#<meta property=\"og:site_name\" content=\"${ht}\">#g" \
      -e "s#<meta property=\"og:description\" content=\"[^\"]*\">#<meta property=\"og:description\" content=\"${hd}\">#g" \
      -e "s#document\.title=\"[^\"]*\"#document.title=\"${jt}\"#g" \
      "$html" > "$tmp" 2>/dev/null

    # favicon 单独一轮（需要配置值，可能为空）
    if [ -n "$fav_attr" ]; then
        sed \
          -e "s#<link rel=\"icon\" href=\"[^\"]*\">#<link rel=\"icon\" href=\"${fav_attr}\">#g" \
          -e "s#<meta property=\"og:image\" content=\"[^\"]*\">#<meta property=\"og:image\" content=\"${fav_attr}\">#g" \
          "$tmp" > "$tmp.fav" 2>/dev/null
        if [ -s "$tmp.fav" ]; then
            mv -f "$tmp.fav" "$tmp"
        else
            rm -f "$tmp.fav" 2>/dev/null
        fi
    fi

    [ -s "$tmp" ] || { rm -f "$tmp"; return 1; }

    # ---- 注入 FnStyle 运行时脚本 ----
    #
    # 为什么需要（2026-11-13 取证）：
    #   fnOS 原生代码里**根本没有** fnstyle.json 的消费端
    #   （实测纯净基线 raw/assets/ 里 '/deskdata/fnstyle' 出现 0 次）。
    #   桌面壁纸、隐藏登录框名字、图标排序、点击音效、任务栏置底、
    #   隐藏系统图标这些项在原生里没有任何消费端 ——
    #   它们原本由 FnDesk 的 fndesk.js 实现，而该脚本已被彻底清理。
    #   ⇒ 必须自己注入等价实现。
    #
    # 两个脚本的分工（v4 架构）：
    #   fnstyle-runtime.js → 桌面侧（壁纸/音效/任务栏/图标）
    #   fnstyle-login.js    → 登录页侧（LOGO/背景/卡片样式/标题/设备LOGO）
    #
    # ⚠️ 关键：两个脚本都放在 /assets/ **之外**（www 根目录）。
    #   因为 fnOS 对 /assets/* 下发 "max-age=1209600, immutable"，
    #   而这些脚本需要能随版本更新被浏览器重新拉取。
    #
    # 注入位置：<title> 之前 —— 先于主包加载，以便尽早监听 DOM 变化。
    #
    # ⚠️ v13b：script 标签带内容指纹 ?v=<md5>。
    #   原因（2026-11-13 用户实测「升级后没生效」）：runtime 响应头只有 etag、
    #   没有 cache-control，浏览器按启发式策略缓存；裸 URL 的文件内容变了
    #   页面也感知不到。带上 ?v=<md5> 后内容一变 URL 就变 ⇒ 任何一次页面
    #   加载（无需强刷）都必然拉到最新文件。
    #   幂等：先删掉旧标签（带或不带 ?v= 都算旧），再插入带新指纹的标签。
    for runtime_src in fnstyle-runtime.js fnstyle-login.js; do
        local src_file="${UI_DIR}/static/js/${runtime_src}"
        local rt_ver=""
        if [ -f "$src_file" ] && command -v md5sum >/dev/null 2>&1; then
            rt_ver="$(md5sum "$src_file" 2>/dev/null | awk '{print $1}')"
        fi
        local runtime_tag="<script src=\"./${runtime_src}\"></script>"
        if [ -n "$rt_ver" ]; then
            runtime_tag="<script src=\"./${runtime_src}?v=${rt_ver}\"></script>"
        fi
        # 先删除旧标签（v13b 前 = 裸 src；v13b 后 = 带 ?v=<md5>）
        sed -E -e "s#<script src=\"\./${runtime_src}\?v=[0-9a-f]+\"></script>##" \
               -e "s#<script src=\"\./${runtime_src}\"></script>##" \
               "$tmp" > "$tmp.rt2" 2>/dev/null
        if [ -s "$tmp.rt2" ]; then
            mv -f "$tmp.rt2" "$tmp"
        else
            rm -f "$tmp.rt2" 2>/dev/null
        fi
        # 再在 <title> 前插入新标签
        sed -e "s#<title>#${runtime_tag}<title>#" "$tmp" > "$tmp.rt" 2>/dev/null
        if [ -s "$tmp.rt" ]; then
            mv -f "$tmp.rt" "$tmp"
        else
            rm -f "$tmp.rt" 2>/dev/null
        fi
    done

    # mv 会用临时文件权限取代目标权限 → 先 chmod
    chmod 644 "$tmp" 2>/dev/null
    mv -f "$tmp" "$html"
    return 0
}

# 把改写后的 index.html 同步到三处（zip 源 / tow 副本 / www 生效目录）
apply_index_html() {
    local src_html="${SYS_WWW}/index.html"
    [ -f "$src_html" ] || return 2

    # 先在 www 上改写出一份新内容
    local work="/tmp/.fnstyle_html.$$"
    cp -f "$src_html" "$work" 2>/dev/null || return 1
    if ! rewrite_index_html "$work"; then
        rm -f "$work"
        return 1
    fi

    # 1) zip 源（真正的还原源，必须先更新）
    if [ -f "$RESTORE_ZIP" ] && command -v zip >/dev/null 2>&1; then
        local stage="/tmp/.fnstyle_htmlzip.$$"
        rm -rf "$stage"
        mkdir -p "$stage" 2>/dev/null
        cp -f "$work" "$stage/index.html" 2>/dev/null
        if [ -f "$stage/index.html" ]; then
            ( cd "$stage" && zip -q "$RESTORE_ZIP" "index.html" ) 2>/dev/null
        fi
        rm -rf "$stage"
    fi
    # 2) 工作副本 tow/
    [ -d "$RESTORE_TOW" ] && cp -f "$work" "${RESTORE_TOW}/index.html" 2>/dev/null
    # 3) 生效目录 www/
    cp -f "$work" "$src_html" 2>/dev/null
    chmod 644 "$src_html" 2>/dev/null
    [ -f "${RESTORE_TOW}/index.html" ] && chmod 644 "${RESTORE_TOW}/index.html" 2>/dev/null

    rm -f "$work"

    # 4) 延时复查已移到后台（spawn_restore_recheck）
    #
    # ⚠️ 竞态：sysrestore_service 每秒把 www.zip 的内容解压回 /usr/trim/www/。
    #    如果 zip 更新失败（或 zip 里有旧条目），刚写入的 www/index.html
    #    会被立刻还原成旧标题 —— 表现为「点了应用但没变化」。
    #
    #    之前是在这里 sleep 2 后前台复查，但这会让 /api/apply 总耗时超过 4 秒，
    #    是「Failed to fetch」的第二重风险（网关/浏览器等待超时）。
    #    现在改为：apply_to_system 返回响应后，由后台脚本延时 3 秒再复查一次。
    #    —— 响应立刻返回，HTML 在 3 秒内也会稳定下来。
    return 0
}

# ---------------------------------------------------------------------------
# 登录框样式（毛玻璃 / 透明度 / 位置）—— v4 运行时注入
#
# ⚠️⚠️ v4 架构变更（2026-11-13 血泪教训）：
#
#   【v3 的做法（已废弃）】
#     直接改写 assets/login-form-*.css，在末尾追加 .gradient-border 覆盖规则。
#     问题：**文件名不变，内容变了**。
#
#   【fnOS 的缓存契约 —— 关键】
#     /usr/trim/nginx/conf/conf.d/html_nocache.conf：
#         location ~ ^/assets/* {
#             add_header cache-control "max-age=1209600, immutable";
#         }
#     ⇒ 14 天 + immutable ⇒ 浏览器**连条件请求都不发**。
#
#   【v3 踩的坑（已决定性复现）】
#     违反 immutable 契约 ⇒ 浏览器 14 天内一直用旧版：
#       ① 换图/调位置后点「应用到系统」→ 页面毫无变化
#       ② 卸载 FnStyle 后服务端已恢复纯净版 → 浏览器仍是旧版
#          ⇒ 表现为「登录页 LOGO 破图 + 卡片跑到 60vw + 背景变深蓝灰」
#     实测：注入 11/10 版 assets → LOGO naturalWidth 80→0、卡片 left 500→932
#
#   【为什么不能改 nginx】
#     `nginx -t` 会从**加密的** /usr/trim/share/.restore/ng.conf.zip
#     重建整个 /usr/trim/nginx/conf/ 目录
#     ⇒ 实测：写进 conf.d/*.conf 与 nginx.conf 的改动下次 nginx -t 就消失
#     ⇒ 这条路完全堵死。
#
#   【v4 的做法】
#     ① **完全不碰 /assets/ 下的任何文件**
#     ② 把 fnstyle-login.js 部署到 www 根目录（**不在 /assets/ 下**）
#        —— 该路径不带 immutable 头，可被浏览器重新拉取
#     ③ 由 index.html 以 <script src="./fnstyle-login.js"> 引入
#     ④ 脚本运行时用 DOM API 设置 LOGO / 背景 / 卡片样式 / 标题
#     ⇒ 不违反任何缓存契约；改配置刷新即生效；卸载后刷新即恢复原生
#
#   详细设计见同目录 apply_style_overrides.py 顶部注释。
# ---------------------------------------------------------------------------
apply_style_overrides() {
    local script="${UI_DIR}/apply_style_overrides.py"
    [ -f "$script" ] || { log_debug "样式改写脚本不存在: $script"; return 2; }
    command -v python3 >/dev/null 2>&1 || { log_debug "python3 不可用"; return 2; }

    # 配置源：本应用的 fnstyle.json（用户在界面上改的）
    local cfg="${CFG_FILE}"
    [ -f "$cfg" ] || cfg="${FND_CFG}"
    [ -f "$cfg" ] || { log_debug "找不到 fnstyle.json，跳过样式改写"; return 2; }

    local out
    out=$(python3 "$script" "${SYS_WWW}" "$cfg" --log "${DATA_DIR}/style_override.log" 2>&1)
    local rc=$?
    if [ $rc -ne 0 ]; then
        log_debug "样式改写失败(rc=$rc): $(printf '%s' "$out" | tr '\n' ' ')"
        return 1
    fi
    # 逐行写进 debug.log，便于事后核对
    printf '%s' "$out" | while IFS= read -r line; do
        [ -n "$line" ] && log_debug "$line"
    done
    return 0
}

# ---------------------------------------------------------------------------
# apply_title_overrides —— 审计 assets 里的标题硬编码残留（v4）
#
# 背景（2026-11-11 三方取证，用户反馈「在飞牛页面显示的标题和
#       不在飞牛页面显示的标题不一样」）：
#
#   fnOS 桌面的标签页标题有 **三个** 写入方在互相覆盖：
#
#   ① index.html 的 <title> 字面量
#      由 apply_index_html() 改写（= 配置里的 appTitle）
#      ⇒ 首屏 / 未登录（登录页）显示这个
#
#   ② 桌面 SPA 主包 assets/index-*.js 里的 R3()
#      e?document.title=`Stone‘s Wish`:document.title=t
#      ⇒ **登录进桌面后，SPA 加载时把标题覆盖成硬编码字符串**
#      原始 fnOS 版本这里写的是 `${主机名} - ${品牌}`，
#      是 FnDesk 魔改时替换成了当年写死的值。
#      ⇒ 这就是「同一个站点两个标题」的根因。
#
#   ③ fndesk.js 的 visibilitychange 监听（FnDesk 的双标题语义）
#      ⇒ appTitle = 标签页可见时；appTitle2 = 切走时。
#      用户切标签页时标题会跳变，且 appTitle2 在本应用 UI 里没有输入框。
#
# ⚠️⚠️ v4 架构变更（2026-11-13 血泪教训）：
#   v3 曾在这里**改写 bundle**（assets/index-*.js）来把 ② 改成动态读取。
#   但 assets/* 是 immutable 的 ⇒ 浏览器 14 天不重拉 ⇒
#   「改了没反应」+「卸载后仍显示旧值」。
#
#   v4 改为：**标题统一由 fnstyle-login.js 运行时接管**
#     · MutationObserver 监听 <title> 变化，原生 R3() 改了就改回来
#     · visibilitychange 时也维持配置值
#   ⇒ 完全不碰 assets/。
#
#   本函数现在只做**审计**：检查 assets/ 下是否还残留 FnDesk 时代的
#   标题硬编码，有则报告（提示从 raw/ 纯净基线回退）。
#
# ⚠⚠️ 绝不能改 bundle 的文件名：
#   bundle 被 193 个 chunk 以 from"./index-D1auxRov.js" 引用，
#   文件名里的 hash 是构建产物，改名 = 整个桌面白屏。
# ---------------------------------------------------------------------------
apply_title_overrides() {
    local script="${UI_DIR}/patch_title_overrides.py"
    [ -f "$script" ] || { log_debug "标题改写脚本不存在: $script"; return 2; }
    command -v python3 >/dev/null 2>&1 || { log_debug "python3 不可用"; return 2; }
    local out
    out=$(python3 "$script" "${SYS_WWW}" --log "${DATA_DIR}/title_override.log" 2>&1)
    local rc=$?
    if [ $rc -ne 0 ]; then
        log_debug "标题改写失败(rc=$rc): $(printf '%s' "$out" | tr '\n' ' ')"
        return 1
    fi
    printf '%s' "$out" | while IFS= read -r line; do
        [ -n "$line" ] && log_debug "$line"
    done
    return 0
}

# ---------------------------------------------------------------------------
# 资源缓存时间戳刷新（破浏览器缓存）
#
# ⚠️⚠️ 为什么必须有这一步（2026-11-13 实测取证）：
#
#   fnOS 原生 JS 里个性化图片的引用 URL 被 FnDesk 当年写死了固定时间戳：
#     /deskdata/fnstyle/loginBackground.png?v=1794315021351
#   实测解码 = 2026-11-10 20:50:21（FnDesk 注入那一刻），是**常量**。
#
#   ⇒ 用户在 FnStyle 里换了新图 → 服务端文件确实换了，
#     但浏览器请求的还是那个老 URL → 命中本地缓存 → 继续显示旧图。
#   ⇒ 表现就是「配置应用到系统没生效」，而 apply 日志全是绿的。
#
#   （fnstyle.json 自身没这问题：主包 R3() 用的是动态时间戳。）
#
#   本步骤把 assets/*.js 里这些 ?v= 统一刷新为当前时间戳，
#   并同步到 tow/ 与 www.zip（还原源）三处。
#   详见同目录 patch_cache_bust.py 顶部注释。
# ---------------------------------------------------------------------------
apply_cache_bust() {
    local script="${UI_DIR}/patch_cache_bust.py"
    [ -f "$script" ] || { log_debug "缓存刷新脚本不存在: $script"; return 2; }
    command -v python3 >/dev/null 2>&1 || { log_debug "python3 不可用"; return 2; }
    local out
    # ⚠️ 必须用 export：在 $( ) 内做「VAR=x cmd」的前缀赋值，
    #    该变量不会出现在命令替换子进程的环境里（非交互式 shell 的行为）。
    #    之前写成 FNSTYLE_DATA_DIR="${DATA_DIR}" python3 ... 导致
    #    状态文件路径落到了 /tmp ⇒ 指纹永远读不到 ⇒ 缓存永不刷新。
    export FNSTYLE_DATA_DIR="${DATA_DIR}"
    export FNSTYLE_CFG_FILE="${CFG_FILE}"
    out=$(python3 "$script" "${SYS_WWW}" --log "${DATA_DIR}/cache_bust.log" 2>&1)
    local rc=$?
    if [ $rc -ne 0 ]; then
        log_debug "缓存刷新失败(rc=$rc): $(printf '%s' "$out" | tr '\n' ' ')"
        return 1
    fi
    printf '%s' "$out" | while IFS= read -r line; do
        [ -n "$line" ] && log_debug "$line"
    done
    return 0
}

# ---------------------------------------------------------------------------
# 批量写入 www.zip（还原源）
#
# 背景（2026-11-11 实测）：
#   原实现里 apply_one_resource() 对**每个**资源都单独调一次
#   `zip -q "$RESTORE_ZIP" ...`。www.zip 有 45MB，每次 zip 都要重写整个归档，
#   8 个资源 = 8 次重写，既慢又让归档长时间处于「半更新」状态。
#
# 现在改为：把所有资源先 stage 到一个目录，最后**一次** zip 调用写完。
#
# 参数: <资源名列表（空格分隔）>
# 返回: 0=已写入（APPLY_ZIP_STAGED 指向 stage 目录）；1=未写入
# ---------------------------------------------------------------------------
stage_zip_resources() {
    local list="$1"
    local name src
    APPLY_ZIP_STAGED=""
    [ -f "$RESTORE_ZIP" ] || return 1
    command -v zip >/dev/null 2>&1 || return 1

    local stage="/tmp/.fnstyle_zipstage.$$"
    rm -rf "$stage" 2>/dev/null
    mkdir -p "$stage/deskdata/fnstyle" 2>/dev/null || return 1

    local staged=0
    for name in $list; do
        src=$(resolve_resource_src "$name") || continue
        [ -f "$src" ] || continue
        cp -f "$src" "$stage/deskdata/fnstyle/${name}" 2>/dev/null || continue
        chmod 644 "$stage/deskdata/fnstyle/${name}" 2>/dev/null
        staged=$((staged+1))
        # fnstyle.json 在 zip 里有两个位置，www/deskdata/fnstyle.json 是 fnOS 读取的
        if [ "$name" = "fnstyle.json" ]; then
            cp -f "$src" "$stage/deskdata/fnstyle.json" 2>/dev/null
            chmod 644 "$stage/deskdata/fnstyle.json" 2>/dev/null
        fi
    done

    if [ "$staged" -eq 0 ]; then
        rm -rf "$stage" 2>/dev/null
        return 1
    fi

    # ⚠️⚠️ 必须用 `zip -r` 加**整个 deskdata 目录**，不能只加文件条目！
    #
    # 根因（2026-11-13 实测，症状：所有个性化配置「应用到系统」后全部不生效）：
    #   fnOS 的 nginx 用 ngx_http_trim_recover 从 www.zip 重建 www/ 时，
    #   **只认 zip 里显式存在的目录条目**。
    #   而 `zip file1 file2`（不带 -r）**只写文件条目、不写目录条目**。
    #   ⇒ zip 里没有 "deskdata/" 和 "deskdata/fnstyle/" 这两个目录条目
    #   ⇒ 重建时无法识别路径边界，deskdata/fnstyle/xxx.png 被**平铺到 www 根目录**
    #   ⇒ 浏览器请求 /deskdata/fnstyle/loginLogo.png → 404
    #   ⇒ 注入脚本 fetch('deskdata/fnstyle.json') → 404 → 拿不到配置 → 全部配置不生效
    #
    #   实测佐证：
    #     www.zip 里 deskdata/ 目录条目缺失 ⇒ www/ 根目录散落着 loginLogo.png 等
    #     补上目录条目后重建 ⇒ www/deskdata/fnstyle/ 路径完全正确
    #
    # 用 -r 加目录即可自动带上目录条目；.keep 占位保证空目录也会被记录。
    ( cd "$stage" && touch "deskdata/fnstyle/.keep" 2>/dev/null )

    # 一次性写入（-q 静默，失败不输出到 stdout）
    if ( cd "$stage" && zip -q -r "$RESTORE_ZIP" "deskdata" ) 2>/dev/null; then
        :
    else
        rm -rf "$stage" 2>/dev/null
        return 1
    fi

    APPLY_ZIP_STAGED="$stage"
    return 0
}

# 把单个资源从 FnDesk 数据目录同步到系统侧三处
# 参数: <文件名>
apply_one_resource() {
    local name="$1"
    # 源路径按优先级解析：本应用 upload/fnstyle → 本应用 fnstyle → lib/fnstyle → FnDesk
    local src
    src=$(resolve_resource_src "$name") || return 2
    [ -f "$src" ] || return 2
    local n=0
    # 1) 还原源 www.zip —— 由 apply_to_system 批量一次性写入（见 stage_zip_resources）
    #    这里不再单独调 zip：每次 zip 都要重写 45MB 归档，8 个资源逐个更新
    #    既是性能浪费，也会让 zip 处于「半更新」状态更久。
    #    本函数只负责 tow/ 与 www/ 两处即时生效。
    if [ -n "${APPLY_ZIP_STAGED:-}" ] && [ -f "$APPLY_ZIP_STAGED" ]; then
        n=$((n+1))
    fi
    # 2) 还原点工作副本 tow/
    #    ⚠️ 必须先 mkdir -p：目录不存在时 cp 会静默失败（2>/dev/null 吞掉错误），
    #    症状是 tow/ 里缺文件 → 后续 nginx 重建从 tow 恢复时又丢内容。
    mkdir -p "$RESTORE_TOW/deskdata/fnstyle" 2>/dev/null
    cp -f "$src" "$RESTORE_TOW/deskdata/fnstyle/${name}" 2>/dev/null && n=$((n+1))
    [ "$name" = "fnstyle.json" ] && cp -f "$src" "$RESTORE_TOW/deskdata/fnstyle.json" 2>/dev/null
    # 3) 对外生效目录 www/
    mkdir -p "$SYS_FNSTYLE" 2>/dev/null
    cp -f "$src" "$SYS_FNSTYLE/${name}" 2>/dev/null && n=$((n+1))
    [ "$name" = "fnstyle.json" ] && cp -f "$src" "$SYS_DESKDATA/fnstyle.json" 2>/dev/null
    # 4) 强制权限 644 —— 关键！
    #    cp 会保留源文件权限；若源是 700（仅 root 可读），
    #    nginx worker（www-data）就读不到 → 403。
    chmod 644 "${SYS_FNSTYLE}/${name}" 2>/dev/null
    [ "$name" = "fnstyle.json" ] && chmod 644 "${SYS_DESKDATA}/fnstyle.json" 2>/dev/null
    chmod 644 "${RESTORE_TOW}/deskdata/fnstyle/${name}" 2>/dev/null
    [ "$name" = "fnstyle.json" ] && chmod 644 "${RESTORE_TOW}/deskdata/fnstyle.json" 2>/dev/null
    return 0
}

# ---------------------------------------------------------------------------
# 部署 FnStyle 运行时脚本到三处（www / tow / www.zip）
#
# 运行时脚本放在应用的 static/js/ 下（随 fpk 一起分发），
# apply 时复制到 www/ 根目录（index.html 里用 ./fnstyle-runtime.js 引用）。
#
# ⚠️ 三处必须同改：
#   www.zip 是唯一还原源，sysrestore_service 每秒把它解压回 www/。
#   只写 www/ ⇒ 1 秒后被还原 → 404 → 运行时失效。
#
# ⚠️ v4 架构（2026-11-13）：两个脚本都部署，且**都放在 www 根目录
#    （不在 /assets/ 下）**。
#   原因：fnOS 对 /assets/* 下发 "max-age=1209600, immutable"，
#   而这些脚本需要能被浏览器重新拉取（随应用升级更新）。
#   /assets/ 之外的路径没有 immutable 头 ⇒ 天然可重验证。
# ---------------------------------------------------------------------------
deploy_runtime() {
    local n=0
    local rel

    for rel in fnstyle-runtime.js fnstyle-login.js; do
        local src="${UI_DIR}/static/js/${rel}"
        if [ ! -f "$src" ]; then
            log_debug "运行时脚本不存在，跳过: $src"
            continue
        fi

        # 1) www.zip 还原源
        if [ -f "$RESTORE_ZIP" ] && command -v zip >/dev/null 2>&1; then
            local stage="/tmp/.fnstyle_rtzip.$$"
            rm -rf "$stage"; mkdir -p "$stage" 2>/dev/null
            cp -f "$src" "${stage}/${rel}" 2>/dev/null
            if [ -f "${stage}/${rel}" ]; then
                # ⚠️ zip 条目名取决于 cwd + 相对路径 ⇒ 必须在 stage 里执行
                ( cd "$stage" && zip -q "$RESTORE_ZIP" "$rel" ) 2>/dev/null && n=$((n+1))
            fi
            rm -rf "$stage"
        fi

        # 2) tow/ 工作副本
        if [ -d "$RESTORE_TOW" ]; then
            cp -f "$src" "${RESTORE_TOW}/${rel}" 2>/dev/null && n=$((n+1))
            [ -f "${RESTORE_TOW}/${rel}" ] && chmod 644 "${RESTORE_TOW}/${rel}" 2>/dev/null
        fi

        # 3) www/ 生效目录
        cp -f "$src" "${SYS_WWW}/${rel}" 2>/dev/null && n=$((n+1))
        [ -f "${SYS_WWW}/${rel}" ] && chmod 644 "${SYS_WWW}/${rel}" 2>/dev/null
    done

    log_debug "运行时脚本已部署（$n 处）"
    return 0
}

# 执行完整应用流程
# 参数: <是否输出明细>
#
# ⚠★⚠★ 关键约束一：stdout 只能出现「HTTP 响应头 + 响应体」。
#    CGI 网关（trim_cgi）要求 stdout 的第一个字节就是 "Status:"，
#    任何写在头之前的内容都会让网关判定「这不是合法 CGI 响应」
#    → 直接断连 → 浏览器侧表现为 fetch() 抛 "Failed to fetch"。
#
#    历史教训（2026-11-11）：这里原本用 printf 把明细行打到 stdout，
#    导致「应用到系统」永远报错，而 SSH 直跑 CGI 却「全绿」——
#    因为 SSH 直跑不经过网关解析。这是最典型的假绿。
#
#    因此：所有明细一律输出到 stderr（>&2），或写进日志文件。
#
# ⚠★⚠★ 关键约束二（真正的根因，2026-11-11 定位）：
#    本函数会写 /usr/trim/www/（对外生效目录）。而 fnOS 的 nginx 内置
#    自研模块 ngx_http_trim_recover，用 inotify 监控 /usr/trim/www/：
#    一旦该目录下任何文件被修改，就 stop trim_nginx → 从 www.zip 重建
#    整个 www/ → start trim_nginx。约 2 秒，期间**所有在途连接被切断**，
#    浏览器侧就是 fetch() 抛 "Failed to fetch"，
#    且 nginx access.log 里查不到该请求（worker 被杀时日志还没落盘）。
#
#    ⇒ 所以本函数**只能在响应发出之后**运行：
#      正常模式：/api/apply 先回响应，再由 apply_worker.sh 独立后台调用本函数；
#      内部模式：FNSTYLE_INTERNAL=1 时本函数独占执行（此时父连接已结束）。
# ------------------------------------------------------------------
apply_to_system() {
    local verbose="${1:-0}"
    local applied=0 skipped=0 failed=0
    local list=""
    # 动态清单：从配置解析实际引用的文件名（前端新上传的图也能生效）
    list=$(resolve_apply_list)
    # 兜底：配置完全读不出来时用固定清单
    [ -z "$list" ] && list="$APPLY_RESOURCES"
    for r in $APPLY_RESOURCES; do
        case " $list " in
            *" $r "*) ;;
            *) list="$list $r" ;;
        esac
    done

    # ---- 第 0 步：登录框样式改写（毛玻璃 / 透明度 / 位置）----
    #
    # 这三项在 fnOS 里不是配置驱动的，必须直接改写系统 CSS 才生效，
    # 详见 apply_style_overrides() 上方的取证说明。
    #
    # ⚠★⚠️ 必须排在最前面（2026-11-11 实测踩坑）：
    #   第 1 步 stage_zip_resources 写 www.zip 会触发 nginx 的
    #   ngx_http_trim_recover —— 它会 stop nginx 并**从 www.zip 重建整个 www/**，
    #   重建期间 /usr/trim/www/assets/ 是空的。
    #   若样式改写排在后面，此时 glob('assets/login-form-*.css') 会返回空，
    #   报「找不到 assets/login-form-*.css」而静默失败。
    #   排在最前面：改写时 www/ 完整 → 改写后的 CSS 进 zip →
    #   随后的资源写入不会碰 assets/ 条目 → nginx 重建后 CSS 仍是改写后的版本。
    STYLE_OK=0
    if apply_style_overrides; then
        STYLE_OK=1
        applied=$((applied + 1))
        [ "$verbose" = "1" ] && printf '  ✓ 登录框样式（毛玻璃/透明度/位置）\n' >&2
    else
        [ "$verbose" = "1" ] && printf '  - 登录框样式（未生效，见 debug.log）\n' >&2
    fi

    # ---- 第 0.5 步：桌面标签页标题统一 ----
    #
    # 必须在第 1 步（写 www.zip）之前：
    #   写 zip 会触发 nginx 停启、从 zip 重建整个 www/，
    #   重建期间 assets/ 是空的 → 此时改写找不到 bundle 会静默失败。
    #
    # 为什么需要这一步：
    #   桌面标签页标题有三个写入方在互相覆盖 ——
    #     ① index.html 的 <title>（由第 2 步 apply_index_html 负责）
    #     ② 桌面 SPA 主包里的 R3()（**硬编码** FnDesk 当年值，会覆盖 ①）
    #     ③ fndesk.js 的 visibilitychange（双标题语义，切标签页会跳变）
    #   只改 ① 的话，登录进桌面后标题就变回硬编码值 ⇒ 用户反馈的
    #   「在飞牛页面显示的标题和不在飞牛页面显示的标题不一样」。
    TITLE_OK=0
    if apply_title_overrides; then
        TITLE_OK=1
        applied=$((applied + 1))
        [ "$verbose" = "1" ] && printf '  ✓ 桌面标题统一（标签页/登录页一致）\n' >&2
    else
        [ "$verbose" = "1" ] && printf '  - 桌面标题统一（未生效，见 debug.log）\n' >&2
    fi

    # ---- 第 0.6 步：（资源缓存刷新已移到第 2 步之后）----
    # 缓存刷新的指纹依赖「资源文件的最终内容」，
    # 必须等所有资源都写入 www/ 之后再算，否则指纹算的是旧内容。

    # ---- 第 0.4 步：存量配置 null 归一化（必须在第 1 步之前）----
    #
    # ⚠️ 必须在 stage_zip_resources 之前：
    #   第 1 步会把 CFG_FILE（存量配置）复制进 www.zip 并落到系统。
    #   存量里的 null 若不先清掉，就会原样进系统 ⇒ 开关不生效。
    #
    #   （POST 时的归一化只管「本次提交」，
    #     用户早就存在的脏数据只能在这里补。）
    NORMALIZED=0
    if normalize_cfg_file_nulls "${CFG_FILE}"; then
        NORMALIZED=1
        [ "$verbose" = "1" ] && printf '  ✓ 配置 null 值归一化\n' >&2
    fi

    # ---- 第 1 步：批量写入 www.zip（还原源），一次 zip 调用完成所有资源 ----
    # 必须在改 tow/ 与 www/ 之前完成：zip 是唯一还原源，
    # 若 zip 里还是旧内容，sysrestore_service 会在 1 秒内把 www/ 还原回去。
    stage_zip_resources "$list" || log_debug "批量写 www.zip 失败或无资源"

    for name in $list; do
        if apply_one_resource "$name"; then
            applied=$((applied+1))
            [ "$verbose" = "1" ] && printf '  ✓ %s\n' "$name" >&2
        else
            case $? in
                2) skipped=$((skipped+1)); [ "$verbose" = "1" ] && printf '  - %s (源不存在)\n' "$name" >&2 ;;
                *) failed=$((failed+1));  [ "$verbose" = "1" ] && printf '  ✗ %s (失败)\n' "$name" >&2 ;;
            esac
        fi
    done

    # 清理 stage 目录
    [ -n "${APPLY_ZIP_STAGED:-}" ] && rm -rf "$APPLY_ZIP_STAGED" 2>/dev/null
    APPLY_ZIP_STAGED=""

    # ---- 第 2 步：同步更新 index.html 里的标题/描述字面量 ----
    # 浏览器标签页标题读的是 HTML 里的写死字面量，不是 fnstyle.json。
    # 不做这一步，用户改了「网站标题」也不会有任何可见变化。
    HTML_OK=0
    if apply_index_html; then
        HTML_OK=1
        applied=$((applied + 1))
        [ "$verbose" = "1" ] && printf '  ✓ index.html (标题/描述)\n' >&2
    fi

    # ---- 第 2.5 步：部署 FnStyle 运行时脚本 ----
    #
    # 桌面壁纸 / 隐藏登录框名字 / 图标排序 / 点击音效 / 任务栏置底 /
    # 隐藏系统图标 —— 这些项在 fnOS 原生代码里没有任何消费端，
    # 由本应用自带的 fnstyle-runtime.js 兑现（替代已被删除的 fndesk.js）。
    #
    # ⚠️ 排在资源写入之后：它会写 www.zip，触发 nginx 从 zip 重建 www/。
    #   若排在前面，重建期间 assets/ 是空的，
    #   后面的标题改写与缓存刷新就会「找不到文件」而静默失败
    #   （2026-11-13 实测踩坑）。
    if deploy_runtime; then
        applied=$((applied + 1))
        [ "$verbose" = "1" ] && printf '  ✓ FnStyle 运行时（桌面壁纸/图标/音效/任务栏）\n' >&2
    else
        [ "$verbose" = "1" ] && printf '  - FnStyle 运行时（部署失败，见 debug.log）\n' >&2
    fi

    # ---- 第 3 步：资源缓存时间戳刷新（破浏览器缓存）----
    #
    # ⚠️ 必须排在最后：指纹依赖「资源文件的最终内容」，
    #   必须等所有资源都写入 www/ 之后再算。
    #
    # 没有这一步：用户在 FnStyle 换了新图 → 服务端文件换了，
    # 但浏览器请求的 URL 里的 ?v= 是写死的旧时间戳 → 命中缓存 → 显示旧图。
    BUST_OK=0
    if apply_cache_bust; then
        BUST_OK=1
        applied=$((applied + 1))
        [ "$verbose" = "1" ] && printf '  ✓ 资源缓存刷新（换图立即生效）\n' >&2
    else
        [ "$verbose" = "1" ] && printf '  - 资源缓存刷新（未生效，见 debug.log）\n' >&2
    fi

    # ---- 第 3 步：不再在此处做后台复查 ----
    #
    # ⚠★⚠★ 历史包袱清理（2026-11-11）：
    #   原来这里会 spawn_restore_recheck（延时 3 秒复查），
    #   但真正的根因是 —— 写 /usr/trim/www/ 会触发 nginx 的
    #   ngx_http_trim_recover 模块停启 nginx，把当前 CGI 连接切断。
    #   复查逻辑已整体挪到 apply_worker.sh（独立后台进程）里：
    #   响应先发给浏览器 → worker 再写 www/ → 触发 nginx 重建 → 最后校验。
    #   这里保留原调用会与 worker 抢写，故移除。

    APPLIED_COUNT="$applied"
    SKIPPED_COUNT="$skipped"
    FAILED_COUNT="$failed"
    return 0
}

# 写一行到调试日志（不污染 stdout）
log_debug() {
    printf '%s [fnstyle] %s\n' "$(date '+%F %T')" "$1" >> "${DATA_DIR}/debug.log" 2>/dev/null
}

# ---------------------------------------------------------------------------
# 启动后台应用进程（apply_worker.sh）
#
# ⚠★⚠★ 这是「Failed to fetch」修复的核心（2026-11-11）：
#
#   fnOS 的 nginx 内置自研模块 ngx_http_trim_recover，用 inotify 监控
#   /usr/trim/www/ 目录。一旦该目录下任何文件被修改，它会：
#       stop trim_nginx.service → 从 www.zip 重建整个 www/ → start
#   全程约 2 秒，期间所有在途连接（含 CGI 自己这条）被切断 →
#   浏览器 fetch() 抛 "Failed to fetch"，且 access.log 里查不到该请求。
#
#   所以顺序必须是：
#       1) CGI 先把 HTTP 响应写给浏览器并结束
#       2) 后台 worker 再去写 www/（触发 nginx 重建，此时已无人受害）
#
#   用 setsid + nohup 让 worker 完全脱离 CGI 进程组，
#   避免 CGI 退出时被连带杀掉。
# ---------------------------------------------------------------------------
spawn_apply_worker() {
    local list="$1"
    local script="${UI_DIR}/apply_worker.sh"
    [ -f "$script" ] || { log_debug "应用脚本不存在: $script"; return 1; }

    FNSTYLE_LIST="$list" \
    FNSTYLE_DATA_DIR="$DATA_DIR" \
    FNSTYLE_ZIP="$RESTORE_ZIP" \
    FNSTYLE_TOW="$RESTORE_TOW" \
    FNSTYLE_WWW="$SYS_WWW" \
    FNSTYLE_SYS_FNSTYLE="$SYS_FNSTYLE" \
    FNSTYLE_SYS_DESKDATA="$SYS_DESKDATA" \
    FNSTYLE_CFG="$CFG_FILE" \
    FNSTYLE_UPLOAD="$UPLOAD_DIR" \
    FNSTYLE_LIB="$LIB_DIR" \
    FNSTYLE_FND="$FND_SHARE" \
    FNSTYLE_FND_CFG="$FND_CFG" \
    setsid nohup bash "$script" >/dev/null 2>&1 &

    disown 2>/dev/null
    log_debug "已启动后台应用进程（列表: $list）"
    return 0
}

read_body() {
    # 读取 POST body。
    #
    # ⚠️ 不能只用 CONTENT_LENGTH 做 head -c：fnOS 的 CGI 网关在某些情况下
    # 会把「请求体 + 上一次响应」一起喂给 stdin（HTTP keep-alive 复用连接时
    # 尤其明显），导致解析出多余字段（success/message 混进配置）。
    #
    # 策略：优先按 CONTENT_LENGTH 精确读取；若读到的内容里出现了第二个
    # JSON 对象的起始特征（}, {），说明有粘连，只保留第一段。
    local len=${CONTENT_LENGTH:-0}
    local raw
    if [ "$len" -gt 0 ] 2>/dev/null; then
        raw=$(head -c "$len" 2>/dev/null)
    else
        raw=$(cat 2>/dev/null)
    fi
    # 去掉粘连的后续 JSON 片段
    #
    # ⚠️ 历史 bug（2026-11-12 修复）：
    #   旧逻辑只按 { } 深度找「第一个完整对象」的结束位置就截断。
    #   对数组 body（如 POST /api/themes 的 [{"ID":0,...},{...}]）来说，
    #   第一个 } 就让 depth 归零 → 只保留 "[{...}"，
    #   后面的对象和结尾的 ] 全被砍掉 → 存盘后 JSON 非法 → 前端解析失败。
    #   现在改成同时跟踪 [] 深度：只有「对象深度与数组深度都归零」才截断，
    #   并且数组要等 ] 出现才算完整。
    printf '%s' "$raw" | awk '
        {
            s = $0
            depth = 0; bdepth = 0; inq = 0; end = 0
            n = length(s)
            for (i = 1; i <= n; i++) {
                c = substr(s, i, 1)
                if (inq) {
                    if (c == "\\") { i++; continue }
                    if (c == "\"") inq = 0
                    continue
                }
                if (c == "\"") { inq = 1; continue }
                if (c == "{") depth++
                if (c == "}") {
                    depth--
                    if (depth < 0) depth = 0
                }
                if (c == "[") bdepth++
                if (c == "]") {
                    bdepth--
                    if (bdepth < 0) bdepth = 0
                }
                # 顶层是一个完整 JSON 值（对象或数组）时才截断
                if (depth == 0 && bdepth == 0) {
                    # 数组必须等到 ]，对象必须等到 }
                    if (c == "}" || c == "]") { end = i; break }
                }
            }
            if (end > 0) { print substr(s, 1, end); exit }
            print s
        }
    '
}

# 从 query string 中取参数值
# 用法: query_get <query_string> <key>
query_get() {
    local q=$1 k=$2 rest seg
    rest=$q
    while [ -n "$rest" ]; do
        case "$rest" in
            *'&'*) seg="${rest%%&*}"; rest="${rest#*&}" ;;
            *)      seg="$rest"; rest="" ;;
        esac
        case "$seg" in
            "$k="*) printf '%s' "${seg#*=}"; return 0 ;;
        esac
    done
    return 1
}

# URL 解码（%XX 与 +）
url_decode() {
    local s=$1
    s=$(printf '%b' "${s//%/\\x}")
    printf '%s' "${s//+/ }"
}

# --------------------------- 路由分发 ---------------------------
REQ_METHOD="${REQUEST_METHOD:-GET}"
URI="${REQUEST_URI:-/}"
QUERY="${QUERY_STRING:-}"

# 剥掉 /cgi/ThirdParty/fnstyle/index.cgi 前缀，得到相对路径
REL="$URI"
case "$REL" in
    */index.cgi/*) REL="/${REL#*/index.cgi/}" ;;
    */index.cgi)   REL="/" ;;
    *)             REL="/${REL#/}" ;;
esac
REL="${REL%%\?*}"
[ -z "$REL" ] && REL="/"

case "$REL" in
    /api/config)
        if [ "$REQ_METHOD" = "GET" ]; then
            [ -f "$CFG_FILE" ] || { echo '{}' > "$CFG_FILE"; chmod 644 "$CFG_FILE"; }
            # 显式输出头（Content-Length 用字节数），避免网关截断
            http_json_file "200 OK" "$CFG_FILE"
            exit 0
        fi
        # POST：body 形如 {"key": value, ...}，批量写入
        BODY=$(read_body)
        # 诊断：记录原始 body 与解析结果（可用 FNSTYLE_DEBUG=1 关闭）
        if [ "${FNSTYLE_DEBUG:-0}" = "1" ]; then
            {
                echo "=== ts=$(date '+%F %T') pid=$$ ==="
                echo "CONTENT_LENGTH=[${CONTENT_LENGTH:-unset}]"
                echo "CONTENT_TYPE=[${CONTENT_TYPE:-unset}]"
                echo "BODY_BYTES=$(printf '%s' "$BODY" | wc -c | tr -d ' ')"
                echo "BODY_RAW=[$BODY]"
                echo "--- parsed ---"
                printf '%s' "$BODY" | json_parse_kv 2>&1
                echo "--- end ---"
            } >> "${DATA_DIR}/debug.log" 2>&1
        fi
        [ -z "$BODY" ] && { fail "请求体为空"; exit 0; }
        [ -f "$CFG_FILE" ] || echo '{}' > "$CFG_FILE"

        # 解析请求体为 key<TAB>value（正确处理字符串里的逗号与转义引号）
        KV="${CFG_FILE}.kv.$$"
        printf '%s' "$BODY" | json_parse_kv > "$KV"

        # 过滤掉空值（保留 "0"、"-1" 这类合法值）
        grep -v '^$' "$KV" | awk -F'\t' 'length($1)>0 && length($2)>0' > "${KV}.clean"
        CNT=$(wc -l < "${KV}.clean" | tr -d ' ')

        # ---- null 值规范化（2026-11-13 新增）----
        #
        # 症状：某个开关字段（Fndesk_taskbar）在配置里是 null，
        #       界面上却因为「null → 回退默认值」显示成「开启」——
        #       界面在骗人，实际 runtime 判 `num(null,0) !== 1` → 不生效。
        #
        # 成因：前端 saveField 里 `if (f.numeric) val = parseInt(val, 10)`，
        #       当 val 为 null/undefined 时 parseInt 得 NaN，
        #       JSON.stringify({k: NaN}) → {"k":null} ⇒ 后端原样写入 null。
        #
        # 这里在服务端兜底：null 一律按该字段的 **runtime 缺省行为** 归一化
        #（见 SWITCH_NULL_DEFAULTS 表）。
        # 注意：不能用 offValue —— nasNameDisplay 的 offValue 是 2（隐藏），
        #       而 runtime 缺省是 1（显示），用错会把用户行为反转。
        normalize_null_values "${KV}.clean" || true

        # ---- 外链媒体资源本地化（2026-11-13 新增）----
        #
        # 主题库里的图片都是外链 URL，直接写进配置会导致 apply 流程
        # 找不到本地文件 → 资源被跳过 → 系统里毫无变化。
        # 这里先把外链下载到本地（按固定名落盘），再把配置改成本地路径。
        if [ "$CNT" -gt 0 ]; then
            localize_external_media "${KV}.clean" || true
        fi

        # ---- appTitle 与 appTitle2 联动（2026-11-11）----
        #
        # FnDesk 原版是「双标题」语义：
        #   appTitle  = 标签页可见时的标题
        #   appTitle2 = 切到别的标签页时（隐藏时）的标题
        # 但本应用已按用户要求改成「统一成单一标题」——
        #   · bundle 的 R3() 改成动态读 appTitle
        #   · fndesk.js 的 visibilitychange 两个分支都用 appTitle
        # 而 appTitle2 在 UI 上没有输入框 ⇒ 用户无法控制它，
        # 留着旧值「Stone‘s Wish」只会造成配置自相矛盾。
        #
        # 所以：**用户改 appTitle 时，同步覆盖 appTitle2**，
        # 保证配置文件里两个字段始终一致，将来若恢复双标题语义也不会错乱。
        if grep -q '^appTitle	' "${KV}.clean" 2>/dev/null; then
            AT_VAL=$(awk -F'\t' '$1=="appTitle"{print $2; exit}' "${KV}.clean")
            if [ -n "$AT_VAL" ]; then
                # 值形如 "MyNas"（已带引号的 JSON 字符串字面量）
                printf 'appTitle2\t%s\n' "$AT_VAL" >> "${KV}.clean"
                CNT=$((CNT + 1))
            fi
        fi

        if [ "$CNT" -gt 0 ]; then
            if json_merge_file "$CFG_FILE" "${KV}.clean"; then
                # ---- 卸载还原开关：把用户选择同步到独立标记文件 ----
                # 卸载回调以 root 运行，可能读不到/来不及读 CFG_FILE
                #（卸载时 data-share 可能已被清理），所以额外落一份纯文本
                # 标记，内容只有 0 / 1。读取优先级见 uninstall_callback。
                AR_VAL=$(awk -F'\t' '$1=="autoRestoreOnUninstall"{gsub(/[^0-9]/,"",$2);print $2; exit}' "${KV}.clean" 2>/dev/null)
                if [ "$AR_VAL" = "0" ] || [ "$AR_VAL" = "1" ]; then
                    printf '%s\n' "$AR_VAL" > "${DATA_DIR}/auto_restore" 2>/dev/null || true
                    chmod 644 "${DATA_DIR}/auto_restore" 2>/dev/null || true
                fi
                rm -f "$KV" "${KV}.clean"
                ok "配置已保存（$CNT 项）"
            else
                rm -f "$KV" "${KV}.clean"
                fail "配置写入失败"
            fi
        else
            rm -f "$KV" "${KV}.clean"
            fail "请求体没有可解析的键值"
        fi
        exit 0
        ;;

    /api/fndesk-config)
        if [ -f "$FND_CFG" ]; then
            http_json_file "200 OK" "$FND_CFG"
        else
            http_json "200 OK" '{}'
        fi
        exit 0
        ;;

    # ------------------------------------------------------------------
    # /api/sync  已移除（2026-11-10，用户要求）
    #
    # 原功能：把本应用配置写回 FnDesk 的 fnstyle.json + theme_list.json，
    #           再调用 apply_to_system 应用到系统。
    #
    # 移除原因：个性化中心与 FnDesk 应完全解耦 —— 改本应用不该动 FnDesk 的数据，
    #           也不需要「先同步再应用」两步。直接用 /api/apply 即可生效。
    #
    # 如需恢复：把本应用的配置单向导入 FnDesk（只读 FnDesk，不写回），
    #           用 /api/fndesk-config + /api/config 组合即可，不要写 FnDesk。
    # ------------------------------------------------------------------

    /api/apply)
        # 只做「应用到系统」，不重写 FnDesk 配置
        # 供前端在用户改完 FnDesk 侧配置后单独触发
        if ! is_root; then
            fail "未获得 root 权限，无法写入系统目录"
            exit 0
        fi

        # ⚠★⚠★ 内部模式（由 apply_worker.sh 以 FNSTYLE_INTERNAL=1 调起）
        #
        #   此时父进程（浏览器那次请求）已经拿到响应并结束，
        #   所以这里可以放心地直接写 /usr/trim/www/ —— 触发 nginx 重建
        #   不会切断任何在途连接。
        #
        #   内部模式不输出 HTTP 响应（响应由外层已发完）。
        if [ "${FNSTYLE_INTERNAL:-0}" = "1" ]; then
            apply_to_system 1
            log_debug "内部模式完成：生效 $APPLIED_COUNT / 跳过 $SKIPPED_COUNT / 失败 $FAILED_COUNT"
            exit 0
        fi

        # ---- 正常模式：先把响应发给浏览器，再让后台去写 www/ ----
        #
        # 绝不能在这里直接调 apply_to_system：那会写 /usr/trim/www/，
        # 触发 nginx 的 ngx_http_trim_recover 模块停启 nginx，
        # 把当前这条 CGI 连接切断 → 浏览器 fetch() 抛 "Failed to fetch"。
        #
        # 正确顺序：响应先走 → 后台 worker 再写。
        APPLY_LIST=$(resolve_apply_list)
        [ -z "$APPLY_LIST" ] && APPLY_LIST="$APPLY_RESOURCES"
        for r in $APPLY_RESOURCES; do
            case " $APPLY_LIST " in
                *" $r "*) ;;
                *) APPLY_LIST="$APPLY_LIST $r" ;;
            esac
        done

        ok "正在应用到 fnOS 系统…"
        spawn_apply_worker "$APPLY_LIST"
        exit 0
        ;;

    /api/theme-libs)
        if [ "$REQ_METHOD" = "GET" ]; then
            [ -f "$THEME_LIB_FILE" ] || echo '[]' > "$THEME_LIB_FILE"
            http_json_file "200 OK" "$THEME_LIB_FILE"
            exit 0
        fi
        BODY=$(read_body)
        if [ -n "$BODY" ]; then
            printf '%s' "$BODY" > "$THEME_LIB_FILE.tmp.$$" && chmod 644 "$THEME_LIB_FILE.tmp.$$" && mv -f "$THEME_LIB_FILE.tmp.$$" "$THEME_LIB_FILE"
            ok "主题库源列表已保存"
        else
            fail "请求体为空"
        fi
        exit 0
        ;;

    /api/themes)
        if [ "$REQ_METHOD" = "GET" ]; then
            [ -f "$THEME_FILE" ] || echo '[]' > "$THEME_FILE"
            http_json_file "200 OK" "$THEME_FILE"
            exit 0
        fi
        BODY=$(read_body)
        if [ -n "$BODY" ]; then
            printf '%s' "$BODY" > "$THEME_FILE.tmp.$$" && chmod 644 "$THEME_FILE.tmp.$$" && mv -f "$THEME_FILE.tmp.$$" "$THEME_FILE"
            ok "主题数据已保存"
        else
            fail "请求体为空"
        fi
        exit 0
        ;;

    /api/proxy)
        # 跨域代理：拉远程 JSON（theme.json / bg_list 等）
        URL=$(query_get "$QUERY" url)
        if [ -z "$URL" ]; then
            fail "缺少 url 参数"
            exit 0
        fi
        URL=$(url_decode "$URL")
        case "$URL" in
            http://*|https://*) ;;
            *) fail "仅支持 http/https"; exit 0 ;;
        esac
        RESP_FILE="${LIB_DIR}/proxy.$$"
        curl -sL --max-time 30 -H 'User-Agent: Mozilla/5.0' -o "$RESP_FILE" "$URL" 2>/dev/null

        # ★ 主题库「默认进行加载」的离线兜底：
        #   真机首次打开时若外链超时 / 返回 HTML 错误页，旧版会让主题库
        #   直接变空且不再重试。这里先校验远程内容确实是 JSON，失败则
        #   回退到安装时播种的出厂快照（按 URL 的 md5 命名，与 seed/theme_cache
        #   及 preview.py 的 cache_key 完全一致）。
        #   首字节：5b='['、7b='{'、ef=UTF-8 BOM（后面 nobom 会剥掉）。
        #   没有 od 的环境退化为「文件非空即有效」，保持原行为。
        VALID=0
        if [ -s "$RESP_FILE" ]; then
            FIRST="$(head -c 1 "$RESP_FILE" 2>/dev/null | od -An -tx1 2>/dev/null | tr -d ' \n')"
            case "$FIRST" in
                5b|7b|ef|'') VALID=1 ;;
            esac
        fi
        if [ "$VALID" != "1" ]; then
            CLEAN_URL="$(printf '%s' "$URL" | sed 's/[?&]v=[0-9]*$//')"
            SNAP_KEY="$(printf '%s' "$CLEAN_URL" | md5sum 2>/dev/null | awk '{print $1}')"
            if [ -z "$SNAP_KEY" ] && command -v python3 >/dev/null 2>&1; then
                SNAP_KEY="$(CLEAN_URL="$CLEAN_URL" python3 -c 'import os,hashlib;print(hashlib.md5(os.environ["CLEAN_URL"].encode()).hexdigest())' 2>/dev/null)"
            fi
            SNAP_FILE="${LIB_DIR}/fnstyle_theme_cache/${SNAP_KEY}.json"
            if [ -n "$SNAP_KEY" ] && [ -s "$SNAP_FILE" ]; then
                cp -f "$SNAP_FILE" "$RESP_FILE" 2>/dev/null
                log_debug "proxy 远程不可用，回退出厂快照 ${SNAP_KEY}.json：$URL"
            fi
        fi
        if [ ! -s "$RESP_FILE" ]; then
            rm -f "$RESP_FILE"
            fail "远程拉取失败：$URL"
            exit 0
        fi
        # ⚠️ 必须剥 BOM：远程壁纸库源里有的带 UTF-8 BOM（实测 bg_jy0813.json），
        #    前端 JSON.parse 遇到 BOM 会直接抛 "Unexpected UTF-8 BOM"。
        http_json_file_nobom "200 OK" "$RESP_FILE"
        rm -f "$RESP_FILE"
        exit 0
        ;;

    /api/proxy-json)
        # 带元信息的跨域代理（对齐 FnDesk 的 api/proxy-json）
        #
        # FnDesk 前端在新增主题库时调这个端点，期待返回体里带 _meta，
        # 但 FnDesk 的实现只是原样透传，而所有远程主题库都是纯数组、
        # 不含 _meta —— 那条分支实际从未生效（死代码）。
        # 这里由 lib_meta.py 负责补齐：数组 → 到官方清单 theme_list.json
        # 按 URL 匹配出 {name,desc,from} 后合并进 _meta。
        URL=$(query_get "$QUERY" url)
        if [ -z "$URL" ]; then
            fail "缺少 url 参数"
            exit 0
        fi
        URL=$(url_decode "$URL")
        case "$URL" in
            http://*|https://*) ;;
            *) fail "仅支持 http/https"; exit 0 ;;
        esac
        RESP_FILE="${LIB_DIR}/proxyjson.$$"
        if [ -x /usr/bin/python3 ] && [ -f "${UI_DIR}/lib_meta.py" ]; then
            /usr/bin/python3 "${UI_DIR}/lib_meta.py" "$URL" > "$RESP_FILE" 2>/dev/null
        else
            # 没有 python3 时退化为普通代理（无 _meta）
            curl -sL --max-time 30 -H 'User-Agent: Mozilla/5.0' -o "$RESP_FILE" "$URL" 2>/dev/null
        fi
        if [ ! -s "$RESP_FILE" ]; then
            rm -f "$RESP_FILE"
            fail "远程拉取失败：$URL"
            exit 0
        fi
        # 同 /api/proxy：剥掉 UTF-8 BOM，否则前端 JSON.parse 会失败
        http_json_file_nobom "200 OK" "$RESP_FILE"
        rm -f "$RESP_FILE"
        exit 0
        ;;

    /api/file)
        NAME=$(query_get "$QUERY" name)
        NAME=$(url_decode "$NAME")
        # 防目录穿越
        case "$NAME" in
            *..*|/*) fail "非法文件名"; exit 0 ;;
        esac
        # 查找顺序：data/fnstyle/（用户编辑的主副本，最高优先）→ upload/ 根 →
        #           upload/fnstyle/（FnDesk 导入旧副本）→ lib/fnstyle/ → FnDesk 原件
        #
        # ⚠️ 为什么 data/fnstyle/ 必须排第一（2026-11-13 实测修复）：
        #   旧顺序把 upload/ 副本排在前面。用户在界面里改图后，新图落在
        #   data/fnstyle/ 并同步到系统侧 /usr/trim/www/deskdata/fnstyle/，
        #   而 upload/fnstyle/ 里的旧副本会把预览盖回去 ——
        #   表现为「配置页缩略图是旧图，登录页/桌面却是新图」
        #   （实测：loginLogo.png 配置页显示 Windows logo 旧副本 90378cbd，
        #    登录页实际是新图 66a8fa74）。
        #   与 resolve_resource_src（应用到系统）的顺序保持一致。
        FILE=""
        for cand in "${DATA_DIR}/fnstyle/${NAME}" \
                    "${UPLOAD_DIR}/${NAME}" \
                    "${UPLOAD_DIR}/fnstyle/${NAME}" \
                    "${LIB_DIR}/fnstyle/${NAME}" \
                    "${FND_SHARE}/fnstyle/${NAME}"; do
            if [ -f "$cand" ]; then FILE="$cand"; break; fi
        done
        if [ "$REQ_METHOD" = "DELETE" ]; then
            if [ -z "$FILE" ]; then fail "文件不存在：$NAME"; exit 0; fi
            rm -f "$FILE" && ok "已删除" || fail "删除失败"
            exit 0
        fi
        if [ -z "$FILE" ] || [ ! -f "$FILE" ]; then
            fail "文件不存在：$NAME"
            exit 0
        fi
        # 直接输出文件内容（供 <img src> / <video src> 使用）
        case "$FILE" in
            *.png)  CT="image/png" ;;
            *.jpg|*.jpeg) CT="image/jpeg" ;;
            *.gif)  CT="image/gif" ;;
            *.webp) CT="image/webp" ;;
            *.svg)  CT="image/svg+xml" ;;
            *.ico)  CT="image/x-icon" ;;
            *.mp4)  CT="video/mp4" ;;
            *.webm) CT="video/webm" ;;
            *.json) CT="application/json; charset=utf-8" ;;
            *)      CT="application/octet-stream" ;;
        esac
        FSIZE=$(wc -c < "$FILE" | tr -d ' ')
        printf 'Status: 200 OK\r\n'
        printf 'Content-Type: %s\r\n' "$CT"
        printf 'Content-Length: %s\r\n' "$FSIZE"
        printf 'Cache-Control: no-cache\r\n\r\n'
        cat "$FILE"
        exit 0
        ;;

    /api/upload)
        # 上传：支持两种方式
        #   A) 原始二进制 body：POST /api/upload?field=loginLogo&name=logo.png
        #      Content-Type: application/octet-stream
        #   B) multipart/form-data（兼容浏览器 FormData）
        FIELD=""; FNAME=""
        BOUNDARY=$(printf '%s' "${CONTENT_TYPE:-}" | sed -n 's/.*boundary=//p' | tr -d '"')

        if [ -n "$BOUNDARY" ]; then
            # ---------- 方式 B：multipart ----------
            TMPF=$(mktemp /tmp/fnstyle_up.XXXXXX)
            cat > "$TMPF"
            # 第 1 段是 field，第 2 段是文件
            FIELD=$(awk -v b="--$BOUNDARY" '
                index($0, b) == 1 { part++; next }
                part == 1 && index($0, "name=\"field\"") {
                    s = $0
                    sub(/^.*name="field"[ \t]*/, "", s)
                    print s
                    exit
                }
            ' "$TMPF" | tr -d '\r\n')
            FNAME=$(awk -v b="--$BOUNDARY" '
                index($0, b) == 1 { part++; next }
                part == 2 && index($0, "name=\"filename\"") {
                    s = $0
                    sub(/^.*filename="/, "", s)
                    sub(/".*$/, "", s)
                    print s
                    exit
                }
            ' "$TMPF" | tr -d '\r\n')
            # 提取第 2 段的文件内容（跳过 multipart 头，取到下一个边界之前）
            awk -v b="--$BOUNDARY" '
                index($0, b) == 1 { part++; inbody = 0; next }
                part == 2 {
                    if (!inbody) {
                        if ($0 == "") inbody = 1
                    } else {
                        printf "%s\n", $0
                    }
                }
            ' "$TMPF" > "${UPLOAD_DIR}/.raw.$$"
            rm -f "$TMPF"
            RAWFILE="${UPLOAD_DIR}/.raw.$$"
        else
            # ---------- 方式 A：原始二进制 ----------
            FIELD=$(query_get "$QUERY" field)
            FNAME=$(query_get "$QUERY" name)
            RAWFILE="${UPLOAD_DIR}/.raw.$$"
            cat > "$RAWFILE"
        fi

        [ -z "$FIELD" ] && FIELD="loginBackground"
        if [ -z "$FNAME" ]; then
            rm -f "$RAWFILE"
            fail "缺少文件名"
            exit 0
        fi

        # 统一文件名规则：与 FnDesk 的 deskdata 命名保持一致
        SAFE=$(basename "$FNAME" | tr -d '/\\' | tr -cd 'A-Za-z0-9._-')
        if [ -z "$SAFE" ] || [ "$SAFE" = "." ] || [ "$SAFE" = ".." ]; then
            EXT="${FNAME##*.}"
            case "$EXT" in
                [A-Za-z0-9]*) SAFE="upload.${EXT}" ;;
                *)          SAFE="upload.bin" ;;
            esac
        fi
        # 加上时间戳避免同名覆盖
        SAFE="fnstyle_${SAFE}"

        if [ ! -s "$RAWFILE" ]; then
            rm -f "$RAWFILE"
            fail "上传内容为空"
            exit 0
        fi

        # 图片只落到本应用的 upload 目录（独立存储，不写 FnDesk）
        mv -f "$RAWFILE" "${UPLOAD_DIR}/${SAFE}"

        # 路径统一用 /deskdata/fnstyle/xxx（fnOS 桌面静态目录可直接解析）
        URL="/deskdata/fnstyle/${SAFE}"
        PREVIEW="/cgi/ThirdParty/fnstyle/index.cgi/api/file?name=${SAFE}"
        json_set "$CFG_FILE" "$FIELD" "\"${URL}\""
        ok "上传成功：$SAFE" ",\"data\":{\"field\":\"${FIELD}\",\"name\":\"${SAFE}\",\"url\":\"${URL}\",\"preview\":\"${PREVIEW}\"}}"
        exit 0
        ;;

    /)
        # 静态首页
        if [ -f "${UI_DIR}/index.html" ]; then
            printf 'Content-Type: text/html; charset=utf-8\r\n\r\n'
            cat "${UI_DIR}/index.html"
        else
            printf 'Content-Type: text/plain; charset=utf-8\r\n\r\n'
            printf 'fnstyle: UI not found\n'
        fi
        exit 0
        ;;

    *)
        # 其余路径当静态文件（REL 相对于 UI_DIR，如 /static/css/app.css）
        SAFE=$(printf '%b' "${REL//%/\\x}" | tr -cd 'A-Za-z0-9._/-')
        case "$SAFE" in
            *..*) fail "非法路径"; exit 0 ;;
        esac
        FILE="${UI_DIR}${SAFE}"
        if [ -f "$FILE" ]; then
            case "$FILE" in
                *.html) CT="text/html; charset=utf-8" ;;
                *.css)  CT="text/css; charset=utf-8" ;;
                *.js)   CT="application/javascript; charset=utf-8" ;;
                *.json) CT="application/json; charset=utf-8" ;;
                *.png)  CT="image/png" ;;
                *.jpg|*.jpeg) CT="image/jpeg" ;;
                *.svg)  CT="image/svg+xml" ;;
                *.ico)  CT="image/x-icon" ;;
                *.webp) CT="image/webp" ;;
                *)      CT="application/octet-stream" ;;
            esac
            printf 'Content-Type: %s\r\n' "$CT"
            printf 'Cache-Control: no-cache\r\n\r\n'
            cat "$FILE"
        else
            fail "Not Found：$REL"
        fi
        exit 0
        ;;
esac
