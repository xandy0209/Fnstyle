#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
patch_cache_bust.py —— FN定制化配置：资源 URL 缓存处理（v4 架构）

═════════════════════════════════════════════════════════════════════
一、v4 相对 v3 的架构级修正（2026-11-13 血泪教训）
═════════════════════════════════════════════════════════════════════
【v3 的做法（已废弃）】
  扫描 /usr/trim/www/assets/*.js，把其中指向
      /deskdata/fnstyle/<文件名>?v=<旧时间戳>
  的引用统一替换为当前时间戳。

  即：**原地改写 assets 下的 JS**。

【fnOS 的缓存契约（关键）】
  /usr/trim/nginx/conf/conf.d/html_nocache.conf：
      location ~ ^/assets/* {
          add_header cache-control "max-age=1209600, immutable";
      }
  ⇒ 14 天 + immutable ⇒ 浏览器**连条件请求都不发**。

【v3 踩的坑（2026-11-13 实测，已决定性复现）】
  违反 immutable 契约 ⇒ 浏览器 14 天内一直用旧版：
    ① 用户换图/调位置 → 点「应用到系统」→ 页面毫无变化
    ② 用户卸载 FnStyle → 服务端已恢复纯净版 → 浏览器仍是旧版
       ⇒ 表现为「登录页 LOGO 破图 + 卡片跑到 60vw + 背景变深蓝灰」

  实测复现数据（无头 Chrome）：
      注入 11/10 版 assets → LOGO naturalWidth 80→0、卡片 left 500→932、失败资源 0→2
      强制刷新后           → 全部恢复

【为什么不能改 nginx 来解决】
  `nginx -t` 会从**加密的** /usr/trim/share/.restore/ng.conf.zip
  重建整个 /usr/trim/nginx/conf/ 目录。
  ⇒ 实测：写进 conf.d/*.conf 与 nginx.conf 的改动
    在下一次 nginx -t 时**全部消失** ⇒ 这条路完全堵死。

【v4 的做法】
  ① **完全不碰 /assets/ 下的任何文件**
  ② 资源 URL 的缓存破除交给 **fnstyle-login.js**（部署在 /assets/ 之外）：
     它读配置时带 `?v=<时间戳>`，设置图片 src 时也带 `?v=<时间戳>`
     ⇒ 换图必生效，且不需要改任何 bundle
  ③ 因此本脚本在 v4 里**退化为「校验 + 报告」**：
     它只检查 /assets/ 下是否还残留 fnstyle 引用，
     若有 ⇒ 说明是历史污染，报告出来（并可选择回退）。

═════════════════════════════════════════════════════════════════════
二、为什么「原生纯净版」根本不需要这个脚本
═════════════════════════════════════════════════════════════════════
实测 /usr/trim/share/.restore/raw/assets/ （fnOS 官方纯净基线）：
  · 含 '/deskdata/fnstyle' 的文件数 = **0**
  · 含 'fnstyle.json' 的文件数     = **0**
⇒ 原生 fnOS **根本不消费 fnstyle.json**。
  那套消费逻辑是 FnDesk 当年注入进 bundle 的。
⇒ v4 用运行时注入替代注入 bundle，**天然不存在需要破缓存的旧引用**。

═════════════════════════════════════════════════════════════════════
三、用法
═════════════════════════════════════════════════════════════════════
    python3 patch_cache_bust.py <www_dir> [--log <日志文件>] [--audit-only]
"""

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

# /deskdata/fnstyle/xxx.png?v=1794315021351  → 捕获路径与旧时间戳
#   路径里允许字母数字与 . _ - / （不含 ? &）
RE_FNSTYLE_URL = re.compile(
    r'(/deskdata/fnstyle/[A-Za-z0-9._\-/]+)\?v=(\d+)'
)

# 原生 JS 里写死的资源字段（按 fnOS 原生消费关系）
#
# ⚠️⚠️ 为什么需要「路径同步」（2026-11-13 实测）：
#   原生 JS 里的引用路径是**写死**的：
#     /deskdata/fnstyle/loginLogo.png
#     /deskdata/fnstyle/loginBackground.png
#     /deskdata/fnstyle/deviceLogo.png
#   而 FnStyle 前端上传新图时会把文件命名成
#     /deskdata/fnstyle/fnstyle_loginLogo.png
#   并把配置字段改成这个新名字。
#   ⇒ 两边对不上 ⇒ 浏览器请求的路径在生效目录里不存在 ⇒ 404 / 显示旧图。
#
#   ⇒ apply 时必须把原生 JS 里的路径**同步成配置里的实际文件名**。
#
# 字段 → 文件名语义标记（用于识别「这个路径属于哪个字段」）：
#
# ⚠️⚠️⚠️ 匹配规则必须覆盖「非语义名」（2026-11-13 实测踩坑）：
#   早期只按「文件名里含字段标记」匹配，于是出现两种失效：
#
#   ① 中文名不匹配 —— 主题库的文件名是「钢之炼金术师_t1.png」，
#      不含 loginLogo 标记 ⇒ 正则匹配不上 ⇒
#      同步失败 ⇒ JS 仍指向旧路径（或者被上一轮的匹配结果污染成
#      /deskdata/fnstyle/钢之炼金术师_t1.png，而该文件压根没下载 ⇒ 404）。
#
#   ② 同名不同字段 —— 「钢之炼金术师_t2.jpg」含 t2 但不含 loginBackground，
#      靠 t1/t2 这类短标记极易误伤别的字段。
#
#   ⇒ 正解：**用「上一轮同步留下的痕迹」+「字段语义标记」双通道识别**。
#      由于本脚本是幂等重写（每次 apply 都从原始 zip 重建后再改写），
#      实际上最稳的做法是：
#      - 对每个字段，维护一组「已知别名」（原生写死名 + 前端上传命名模式）
#      - 用正则匹配「/deskdata/fnstyle/<任意名含该字段标记>」
#      - **并额外处理「中文名/主题名」这类无法按标记识别的残留**：
#        凡路径在 fnstyle 目录下、且当前配置给出了该字段的目标路径，
#        就按「该路径在 JS 里的引用位置」判定归属 —— 见 build_path_patterns。
#
#   实际采用的稳健方案：**每个字段匹配「含标记的文件名」或
#   「与该字段已知的默认文件名同族的文件」**，
#   并且**用配置值里的目标文件名反查**（若 JS 里已经是目标名则不需改）。
#
# 各字段的「原生写死默认文件名」（含扩展名）
#
# ⚠️ 这是路径同步的**锚点**：
#   原生 JS 里写的就是这些路径，apply 时把 JS 同步成「配置里的实际路径」。
#   若配置里恰好就是默认名 ⇒ 恒等 ⇒ 无需改写。
NATIVE_DEFAULT_NAMES = {
    "loginLogo": "loginLogo.png",
    "loginBackground": "loginBackground.png",
    "deviceLogo": "deviceLogo.png",
    "desktopWallpaper": "desktopWallpaper.jpg",
    "webFavicon": "webFavicon.png",
}

# 各字段的「文件名识别标记」列表
#
# ⚠️⚠️⚠️ 必须同时识别「语义标记」与「主题库编号标记」——
#   2026-11-13 实测踩坑（这是「主题库应用不生效」的第二层根因）：
#
#   1.0.1 的路径同步只认「语义标记」（loginLogo 等），
#   而主题库的文件名是「钢之炼金术师_t1.png」这种**编号命名**，
#   不含 loginLogo ⇒ 匹配不上。
#
#   后果链条（实测日志 `钢之炼金术师_t1.png (源不存在)` 可证）：
#     配置里是外链 URL
#       → 路径同步把原生 JS 的 /deskdata/fnstyle/loginLogo.png
#         改写成 /deskdata/fnstyle/钢之炼金术师_t1.png
#       → apply 的资源同步按 basename 去本地找「钢之炼金术师_t1.png」
#         （外链从没被下载过）⇒ 找不到 ⇒ **资源被跳过**
#       → JS 指向一个 404 地址 ⇒ 浏览器什么都不显示
#       ⇒ **「应用主题没生效」，但 apply 日志除了那行「源不存在」全是绿的**
#
#   主题库的 t0~t5 编号是 FnDesk 定义的稳定约定（见 app.js 的 THEME_ASSETS）：
#     t1 = 登录页 LOGO      → loginLogo
#     t2 = 登录页背景      → loginBackground
#     t3 = 设备 LOGO       → deviceLogo
#     t4 = 桌面壁纸        → desktopWallpaper
#     t5 = 站点图标        → webFavicon
#
#   ⇒ 把编号标记也纳入识别范围，已被污染的 JS 才能被自愈回正确路径。
NATIVE_PATH_FIELDS = {
    "loginLogo":        ["loginLogo", "_t1"],
    "loginBackground":  ["loginBackground", "_t2"],
    "deviceLogo":       ["deviceLogo", "_t3"],
    "desktopWallpaper": ["desktopWallpaper", "_t4"],
    "webFavicon":      ["webFavicon", "_t5"],
}

# 找出文本里所有指向 fnstyle 资源的路径（用于日志核对）
RE_ANY_FNSTYLE_PATH = re.compile(r'/deskdata/fnstyle/[A-Za-z0-9._\-/]+')


def build_path_targets(cfg):
    """根据配置生成「字段 → 该字段实际生效的完整路径」。

    返回 {field: "/deskdata/fnstyle/xxx.png"}；配置里没值的字段不出现。
    """
    targets = {}
    for field in NATIVE_PATH_FIELDS:
        val = (cfg.get(field) or "").strip()
        if not val:
            continue
        base = val.rsplit("/", 1)[-1]
        if not base:
            continue
        targets[field] = "/deskdata/fnstyle/" + base
    return targets


def build_path_patterns(targets):
    """为每个字段生成「匹配现有引用路径」的正则 + 目标路径。

    ⚠️⚠️⚠️ 关键：同步必须是**双向**的（2026-11-13 实测踩坑）：
      早先的实现按「固定 key」匹配原文：
        /deskdata/fnstyle/loginLogo.png → 配置里的实际路径
      这意味着**只能前进不能后退**：
        第一次换图 → JS 变成 fnstyle_loginLogo.png
        用户改回默认 → 固定 key 匹配不上 → JS 永远停在 fnstyle_loginLogo.png
        而该文件可能已被删除 ⇒ 浏览器 404 ⇒ 登录页 LOGO 空白。
      正解：**按字段语义识别**（文件名里含该字段标记，大小写不敏感），
      无论 JS 里当前是哪个文件名，都统一替换成配置里的实际路径。
      ⇒ 换图能生效，改回默认也能生效。

    ⚠️⚠️⚠️ 标记必须覆盖「主题库编号」与「中文字符」（2026-11-13 实测）：
      ① 主题库文件名是「钢之炼金术师_t1.png」这种编号命名，
         不含 loginLogo 标记 ⇒ 只认语义标记会漏匹配。
      ② 文件名含中文（高达00_t1.png / 钢之炼金术师_t2.jpg），
         字符集必须覆盖中文，否则路径被截断成「/deskdata/fnstyle/」，
         改写后 JS 会指向目录而不是文件 ⇒ 404。

    匹配规则：/deskdata/fnstyle/<文件名>，且文件名里含该字段的任一标记。
    """
    out = []
    for field, markers in NATIVE_PATH_FIELDS.items():
        target = targets.get(field)
        if not target:
            continue
        # 标记按长度降序，避免「_t1」被「_t1x」之类的更长标记抢先
        ms = sorted(markers, key=len, reverse=True)
        alt = "|".join(re.escape(m) for m in ms)
        # 文件名部分：允许中文、字母数字、. _ - ~ 与百分号
        # ⚠️ 不能包含 / 与 ? & —— 避免跨路径/跨参数误匹配
        pat = re.compile(
            r'/deskdata/fnstyle/[^/?#&\s"\'`]*?(?:' + alt + r')[^/?#&\s"\'`]*',
            re.IGNORECASE)
        out.append((pat, target))
    return out


def apply_path_mapping(text, patterns):
    """把 text 里所有指向 fnstyle 资源的旧路径，统一替换成配置里的实际路径。

    只替换路径部分，**保留 ?v= 时间戳**（时间戳由 refresh 逻辑单独处理）。

    ⚠️ 只统计「文本真的变了」的替换次数：
      替换成完全相同的字符串不算改动 ——
      否则每次 apply 都会误判「路径变了」⇒ 强制刷新时间戳
      ⇒ 浏览器每次都重拉整个 assets 目录（几十 MB）。
      （2026-11-13 实测踩坑）

    返回 (新文本, 实际改动次数)。
    """
    if not patterns:
        return text, 0
    out = text
    for pat, target in patterns:
        def _r(m, _t=target):
            return _t
        out = pat.sub(_r, out)
    if out == text:
        return text, 0
    # 统计真正不同的替换点
    changed = 0
    for pat, target in patterns:
        for m in pat.finditer(text):
            if m.group(0) != target:
                changed += 1
                break
    return out, changed


def log(msg, logfile=None):
    line = "%s [cache-bust] %s" % (
        time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    sys.stdout.flush()
    if logfile:
        try:
            with io.open(logfile, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


def zip_update(restore_zip, rel_path, source_file, logfile=None):
    """把 source_file 的内容写进 zip 的 rel_path 条目。"""
    if not os.path.isfile(restore_zip):
        log("⚠ www.zip 不存在: %s" % restore_zip, logfile)
        return False
    stage = tempfile.mkdtemp(prefix=".fnstyle_bustzip_")
    try:
        dst = os.path.join(stage, rel_path)
        d = os.path.dirname(dst)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        shutil.copy2(source_file, dst)
        os.chmod(dst, 0o644)
        r = subprocess.run(["zip", "-q", restore_zip, rel_path],
                           cwd=stage,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if r.returncode != 0:
            log("⚠ zip 写入失败 %s: %s" % (rel_path, r.stderr[:200]), logfile)
            return False
        log("✓ www.zip 已更新: %s" % rel_path, logfile)
        return True
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def find_bundle_files(www, wait_sec=25):
    """返回 assets 下所有 .js 文件（资源 URL 引用只可能在 JS 里）。

    ⚠️⚠️⚠️ 关键（2026-11-13 实测踩坑，极隐蔽）：
       apply 流程里前一步写 www.zip 会触发 fnOS 的 ngx_http_trim_recover ——
       它会 stop nginx 并**从 www.zip 重建整个 www/**。
       重建是**渐进解压**的：某一瞬间扫 assets/ 会只拿到一部分文件
       （实测：只扫到 73 个，而完整是 246 个），
       那 4 个真正含 fnstyle 资源引用的文件此时还没解压出来 ⇒
       扫完「没有需要改写的文件」，看起来像成功，实际什么都没做。

       ⇒ 必须等「文件数稳定」：连续两次扫描数量相同才算完成。
       实测重建耗时约 2~5 秒，这里最多等 25 秒。
    """
    assets = os.path.join(www, "assets")
    deadline = time.time() + wait_sec
    last_count = -1
    stable = 0
    while True:
        out = []
        if os.path.isdir(assets):
            for name in sorted(os.listdir(assets)):
                if name.endswith(".js"):
                    out.append(os.path.join(assets, name))
        count = len(out)
        if count > 0 and count == last_count:
            # 连续两次数量一致 → 认为解压完成
            stable += 1
            if stable >= 2:
                return out
        else:
            stable = 0
        last_count = count
        if time.time() >= deadline:
            return out
        time.sleep(1.0)


def compute_fingerprint(www):
    """计算 fnstyle 资源文件的「内容 + 修改时间」指纹。

    指纹变了 ⇒ 资源内容变了 ⇒ 需要刷新 URL 时间戳让浏览器重拉。
    用 mtime 而不是纯 md5：md5 计算 3MB 的 PNG 较慢，
    而 mtime + size 组合足以判断「有没有被换过」。
    """
    import hashlib
    parts = []
    dirs = [
        os.path.join(www, "deskdata", "fnstyle"),
        os.path.join(www, "deskdata"),
    ]
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            p = os.path.join(d, name)
            if not os.path.isfile(p):
                continue
            try:
                st = os.stat(p)
                parts.append("%s:%d:%d" % (name, st.st_size, int(st.st_mtime)))
            except OSError:
                continue
    if not parts:
        return ""
    return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()


def read_state(path):
    try:
        with io.open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def write_state(path, fingerprint):
    try:
        with io.open(path, "w", encoding="utf-8") as f:
            f.write(fingerprint or "")
        os.chmod(path, 0o644)
    except Exception as e:
        log("⚠ 状态文件写入失败 %s: %s" % (path, e))


def rewrite_file(path, now_ms, logfile=None, force=False, path_patterns=None):
    """计算单个 JS 文件改写后的**新内容**（不落盘）。

    返回 (new_content, changed) ；new_content 为 None 表示无需改写。

    做两件事：
      ① 路径同步：把 JS 里的旧资源路径统一成配置里的实际文件名
        （按字段语义识别，双向可逆 —— 见 build_path_patterns 注释）
      ② 时间戳刷新：?v= 统一为当前时间戳（破浏览器缓存）

    ⚠️⚠️ 绝不能就地写 /usr/trim/www/ 里的文件：
       一写就会触发 nginx 从 www.zip 重建 www/，
       刚改好的文件会被还原 + 消失，后续处理直接 FileNotFoundError。
       （2026-11-13 实测踩坑）⇒ 这里只读文件、在内存里改写，
       由调用方统一写入 www.zip → tow/ → www/。
    """
    try:
        with io.open(path, "r", encoding="utf-8", errors="surrogateescape") as f:
            src = f.read()
    except Exception as e:
        log("⚠ 读取失败 %s: %s" % (path, e), logfile)
        return None, False

    # ---- ① 路径同步 ----
    out, path_changed = apply_path_mapping(src, path_patterns or [])

    # ---- ② 时间戳刷新 ----
    matches = RE_FNSTYLE_URL.findall(out)
    ts_changed = False
    if matches:
        old_ts = set(ts for _, ts in matches)
        # 幂等：所有时间戳已是同一个值 ⇒ 说明本轮已统一过，无需再改。
        #   （注意：不能用「等于 now_ms」判断，因为 now_ms 每次都不同。）
        #
        # ⚠️⚠️ 路径变了 ⇒ 时间戳**必须**一起刷新（2026-11-13 实测）：
        #   浏览器缓存是按完整 URL（含 query）走的。
        #   若路径从 loginLogo.png 换成 fnstyle_loginLogo.png，
        #   但时间戳保持旧值不变，浏览器仍可能命中旧 URL 的缓存条目，
        #   或反过来把新 URL 的响应缓存住导致后续换图不生效。
        #   ⇒ 只要路径同步发生过，就强制刷新时间戳。
        need_refresh = force or path_changed or len(old_ts) > 1
        if need_refresh:
            def repl(m):
                return "%s?v=%d" % (m.group(1), now_ms)
            new_out = RE_FNSTYLE_URL.sub(repl, out)
            if new_out != out:
                out = new_out
                ts_changed = True
                log("✓ %s: 刷新 %d 处资源时间戳 → %d"
                    % (os.path.basename(path), len(matches), now_ms), logfile)
        else:
            log("· %s: 时间戳已统一（%s），无需改写"
                % (os.path.basename(path), list(old_ts)[0]), logfile)

    if path_changed:
        log("✓ %s: 同步 %d 处资源路径" % (os.path.basename(path), path_changed), logfile)

    if out == src:
        return None, False
    return out, True


def audit_assets(www):
    """审计 /assets/ 下是否还残留 fnstyle 引用（v4：只报告，不改写）。

    返回污染文件列表。
    """
    assets = os.path.join(www, "assets")
    hits = []
    if not os.path.isdir(assets):
        return hits
    for name in sorted(os.listdir(assets)):
        if not name.endswith((".js", ".css")):
            continue
        p = os.path.join(assets, name)
        try:
            with io.open(p, "r", encoding="utf-8", errors="surrogateescape") as f:
                text = f.read()
        except Exception:
            continue
        if "/deskdata/fnstyle" in text or "fnstyle.json" in text:
            hits.append(name)
    return hits


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    www = sys.argv[1]
    logfile = None
    if "--log" in sys.argv:
        try:
            logfile = sys.argv[sys.argv.index("--log") + 1]
        except Exception:
            logfile = None

    if not os.path.isdir(www):
        log("❌ www 目录不存在: %s" % www, logfile)
        return 1

    assets = os.path.join(www, "assets")
    # ⚠️ 可能正在被 nginx 从 www.zip 重建（重建期间 assets/ 整个目录不存在）。
    #    实测重建耗时可达 5~10 秒，这里轮询等待，绝不因为「暂时不存在」就放弃。
    waited = 0
    while not os.path.isdir(assets) and waited < 30:
        log("assets 目录暂不存在，等待 nginx 重建…（已等待 %ds）" % waited, logfile)
        time.sleep(2)
        waited += 2
    if not os.path.isdir(assets):
        log("❌ assets 目录不存在（等待 %ds 后仍无）: %s" % (waited, assets), logfile)
        return 1

    # ---- v4：只审计，不改写 ----
    hits = audit_assets(www)
    if hits:
        log("⚠ /assets/ 下仍有 %d 个文件含 fnstyle 引用（历史污染）:" % len(hits), logfile)
        for n in hits:
            log("    %s" % n, logfile)
        log("  v4 架构下这些引用不应存在（原生 fnOS 不消费 fnstyle.json）。", logfile)
        log("  ⇒ 建议从 /usr/trim/share/.restore/raw/ 纯净基线回退这些文件。", logfile)
    else:
        log("✓ /assets/ 下无 fnstyle 引用（纯净）", logfile)

    # ---- 记录资源指纹（供后续诊断用，不再用于改写）----
    fingerprint = compute_fingerprint(www)
    state_path = os.environ.get("FNSTYLE_STATE_FILE") or \
        os.path.join(os.environ.get("FNSTYLE_DATA_DIR", "/tmp"),
                     "fnstyle_cache_state.json")
    write_state(state_path, fingerprint)

    log("v4：资源缓存破除已交由 fnstyle-login.js 运行时处理，本脚本不再改写 assets。", logfile)
    return 0


if __name__ == "__main__":
    sys.exit(main())
