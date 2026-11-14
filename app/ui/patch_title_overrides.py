#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
patch_title_overrides.py —— FN定制化配置：桌面标题统一改写

════════════════════════════════════════════════════════════════════════
一、为什么需要这个脚本（三方取证结论）
════════════════════════════════════════════════════════════════════════
fnOS 桌面的浏览器标签页标题，有 **三个** 写入方在互相覆盖：

  ① index.html 的 <title> 字面量
     由 apply_index_html() 改写（= 配置里的 appTitle）
     ⇒ 首屏 / 未登录（登录页）显示这个

  ② 桌面 SPA 主包 assets/index-*.js 里的 R3()
     R3=e=>{let t=_?`${nk}`:`${XO} ${nk}`;
           e?document.title=`Stone‘s Wish`:document.title=t}
     ⇒ **登录进桌面后，SPA 加载时把标题覆盖成硬编码的 FnDesk 当年值**
     原始 fnOS 版本这里是 `${e} - ${t}`（即「主机名 - 品牌」），
     是 FnDesk 魔改时替换成了写死的字符串。

  ③ fndesk.js 的 visibilitychange 监听
     e.appTitle&&e.appTitle2&&"飞牛 fnOS"!==e.appTitle&&
       document.addEventListener("visibilitychange",function(){
         document.hidden?document.title=e.appTitle2:document.title=e.appTitle})
     ⇒ FnDesk 的「双标题」语义：appTitle = 标签页可见时，
       appTitle2 = 切到别的标签页时（隐藏时）用的标题。
     这解释了 FnDesk 原版为什么有两个标题字段。

实测现象：用户在飞牛页面看到的标题是 appTitle（MyNas），
登录进桌面后变成「Stone‘s Wish」—— 与配置里的 appTitle2 完全一致，
因为 FnDesk 当年就把 appTitle2 设成了那个值，并硬编码进了 bundle。

════════════════════════════════════════════════════════════════════════
二、本脚本做什么（用户选择：统一成单一标题）
════════════════════════════════════════════════════════════════════════
  · 把 ② 里的硬编码改成**动态读取 fnstyle.json 的 appTitle**
    （用 ?v=时间戳 破缓存，与 fndesk.js 一致）
    读取失败 / 字段为空 → 回退原版「主机名 - 品牌」逻辑，**永不白屏**
  · 把 ③ 里的双标题语义改成**单一标题**
    两个分支都用 appTitle，切标签页不再跳变
  · ① 不动（由 apply_index_html 负责）

════════════════════════════════════════════════════════════════════════
三、⚠️ 实施要点（踩过的坑）
════════════════════════════════════════════════════════════════════════
  1) **绝不能改 bundle 的文件名**
     bundle 被 193 个 chunk 以 `from"./index-D1auxRov.js"` 引用，
     文件名里的 hash 是构建产物，改名 = 整个桌面白屏。
     ⇒ 只改内容。
  2) **必须动态定位 bundle 文件名**
     fnOS 升级后 hash 会变（index-D1auxRov.js → index-XXXX.js），
     硬编码文件名 ⇒ 升级后失效。
     ⇒ 通过 index.html 的 <script src> 反查。
  3) **必须三处同步**：www / tow / www.zip
     www.zip 是唯一还原源；sysrestore_service 每秒把 www.zip 解压回 www/。
     只改 www/ ⇒ 1 秒后被还原。
  4) **必须在写 www.zip 之前完成改写**
     写 www.zip 会触发 nginx 停启重建整个 www/（约 2 秒），
     重建期间 assets/ 是空的 → 此时改写会找不到文件而静默失败。
     ⇒ 排在 apply 流程最前面（与 apply_style_overrides 同位置）。
  5) www.zip 45MB，逐条 zip 调用会重写整个归档 → 慢。
     这里沿用 apply_style_overrides 的做法（逐条 zip -q 只更新一个条目）。
  6) **幂等**：重复执行不产生二次改写。

════════════════════════════════════════════════════════════════════════
四、用法
════════════════════════════════════════════════════════════════════════
    python3 patch_title_overrides.py <www_dir> [--log <日志文件>]
"""

import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

RESTORE_DIR = "/usr/trim/share/.restore"


def log_factory(logfile):
    def log(msg):
        line = "[%s] %s" % (os.getpid(), msg)
        try:
            print(line)
        except Exception:
            pass
        if logfile:
            try:
                with io.open(logfile, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                pass
    return log


# ---------------------------------------------------------------------------
# 一、定位 bundle 文件名（动态，绝不硬编码）
# ---------------------------------------------------------------------------
def find_bundle(www, wait_sec=30):
    """从 index.html 的 <script src> 里反查桌面 SPA 主包。

    返回 (assets_dir, bundle_filename) 或 (None, None)。
    识别依据：该 js 文件里含 `document.title=` 且体积 > 1MB（桌面主包特征）。

    ⚠️⚠️ 必须带等待重试（2026-11-13 实测踩坑）：
       apply 流程里，前一步（部署运行时脚本）写 www.zip 会触发
       fnOS 的 ngx_http_trim_recover —— 它会 stop nginx 并
       **从 www.zip 重建整个 www/**，重建期间 assets/ 目录是空的。
       此时若不等待，就会报「未能定位桌面 SPA 主包」而静默失败。
       实测重建耗时约 2 秒，这里最多等 15 秒。
    """
    idx = os.path.join(www, "index.html")
    deadline = time.time() + wait_sec
    while True:
        result = _find_bundle_once(www, idx)
        if result[0] is not None:
            return result
        if time.time() >= deadline:
            return None, None
        time.sleep(0.5)


def _find_bundle_once(www, idx):
    if not os.path.isfile(idx):
        return None, None
    try:
        with io.open(idx, "r", encoding="utf-8") as f:
            html = f.read()
    except Exception:
        return None, None

    assets = os.path.join(www, "assets")
    # 优先：从 <script src="/assets/xxx.js"> 收集候选
    cands = re.findall(r'/assets/([A-Za-z0-9_\-.]+\.js)', html)
    # 退而求其次：扫 assets 目录里所有 js
    if os.path.isdir(assets):
        for fn in sorted(os.listdir(assets)):
            if fn.endswith(".js") and fn not in cands:
                cands.append(fn)

    best = None
    for fn in cands:
        p = os.path.join(assets, fn)
        if not os.path.isfile(p):
            continue
        try:
            size = os.path.getsize(p)
        except OSError:
            continue
        if size < 1000000:          # 桌面主包 > 1MB
            continue
        try:
            with io.open(p, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception:
            continue
        # 桌面主包特征：含 R3=e=> 且含 document.title
        # ⚠ 不能只读文件头 —— R3 定义在 bundle 中部（实测在 1/3 处）
        if "R3=e=>" in text and "document.title" in text:
            best = fn
            break
    if best:
        return assets, best
    return None, None


# ---------------------------------------------------------------------------
# 二、bundle 内容改写
# ---------------------------------------------------------------------------
OLD_HARD = re.compile(
    r"R3=e=>\{let t=_\?`\$\{nk\}`:`\$\{XO\} \$\{nk\}`;"
    r"e\?document\.title=`Stone‘s Wish`:document\.title=t\}"
)
OLD_RAW = re.compile(
    r"R3=e=>\{let t=_\?`\$\{nk\}`:`\$\{XO\} \$\{nk\}`;"
    r"e\?document\.title=`\$\{e\} - \$\{t\}`:document\.title=t\}"
)
NEW_R3 = (
    "R3=e=>{let t=_?`${nk}`:`${XO} ${nk}`;"
    "if(!e){document.title=t;return}"
    "fetch(\"deskdata/fnstyle.json?v=\"+(new Date).getTime())"
    ".then(r=>r.ok?r.json():null)"
    ".then(c=>{document.title=(c&&c.appTitle)||(`${e} - ${t}`)})"
    ".catch(()=>{document.title=`${e} - ${t}`})}"
)


def patch_bundle_text(text):
    """返回 (新文本, 状态)。状态 ∈ {patched, already, nomatch}"""
    if OLD_HARD.search(text):
        m = OLD_HARD.search(text)
        return text[:m.start()] + NEW_R3 + text[m.end():], "patched"
    if OLD_RAW.search(text):
        m = OLD_RAW.search(text)
        return text[:m.start()] + NEW_R3 + text[m.end():], "patched"
    if "deskdata/fnstyle.json?v=" in text and "R3=e=>" in text:
        return text, "already"
    return text, "nomatch"


def patch_bundle_file(path, log):
    try:
        with io.open(path, "r", encoding="utf-8") as f:
            src = f.read()
    except Exception as e:
        log("⚠ 读取失败 %s: %s" % (path, e))
        return False
    out, status = patch_bundle_text(src)
    if status == "nomatch":
        log("⚠ 未匹配到标题逻辑: %s" % os.path.basename(path))
        return False
    if status == "already" or out == src:
        log("· 已改写(幂等): %s" % os.path.basename(path))
        return True
    tmp = path + ".titlepatch.tmp"
    try:
        with io.open(tmp, "w", encoding="utf-8") as f:
            f.write(out)
        os.chmod(tmp, 0o644)
        os.rename(tmp, path)
    except Exception as e:
        log("⚠ 写入失败 %s: %s" % (path, e))
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False
    log("✓ bundle 标题改写: %s (%d -> %d 字节)" % (
        os.path.basename(path), len(src), len(out)))
    return True


# ---------------------------------------------------------------------------
# 三、fndesk.js 的双标题逻辑改写
# ---------------------------------------------------------------------------
OLD_FD = (
    'e.appTitle&&e.appTitle2&&"飞牛 fnOS"!==e.appTitle&&'
    'document.addEventListener("visibilitychange",function(){'
    'document.hidden?document.title=e.appTitle2:document.title=e.appTitle})'
)
NEW_FD = (
    'e.appTitle&&"飞牛 fnOS"!==e.appTitle&&'
    'document.addEventListener("visibilitychange",function(){'
    'document.title=e.appTitle})'
)


def patch_fndesk_text(text):
    if NEW_FD in text:
        return text, "already"
    if OLD_FD in text:
        return text.replace(OLD_FD, NEW_FD), "patched"
    return text, "nomatch"


def patch_fndesk_file(path, log):
    try:
        with io.open(path, "r", encoding="utf-8") as f:
            src = f.read()
    except Exception as e:
        log("⚠ 读取失败 %s: %s" % (path, e))
        return False
    out, status = patch_fndesk_text(src)
    if status == "nomatch":
        log("⚠ fndesk.js 未匹配到双标题逻辑")
        return False
    if status == "already" or out == src:
        log("· fndesk.js 已改写(幂等)")
        return True
    tmp = path + ".titlepatch.tmp"
    try:
        with io.open(tmp, "w", encoding="utf-8") as f:
            f.write(out)
        os.chmod(tmp, 0o644)
        os.rename(tmp, path)
    except Exception as e:
        log("⚠ 写入失败 %s: %s" % (path, e))
        return False
    log("✓ fndesk.js 双标题已统一: %d -> %d 字节" % (len(src), len(out)))
    return True


# ---------------------------------------------------------------------------
# 四、三处同步（www / tow / www.zip）
# ---------------------------------------------------------------------------
def zip_update(restore_zip, rel_path, source_file, log):
    """把 source_file 的内容写进 zip 的 rel_path 条目。"""
    if not os.path.isfile(restore_zip):
        log("⚠ www.zip 不存在: %s" % restore_zip)
        return False
    stage = tempfile.mkdtemp(prefix=".fnstyle_titlezip_")
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
            log("⚠ zip 写入失败 %s: %s" % (rel_path, r.stderr[:200]))
            return False
        log("✓ www.zip 已更新: %s" % rel_path)
        return True
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def audit_bundle(www, log):
    """审计 /assets/ 下是否还残留 FnDesk 的标题硬编码（v4：只报告，不改写）。

    返回 (bundle名 or None, 命中列表)
    """
    assets = os.path.join(www, "assets")
    if not os.path.isdir(assets):
        return None, []
    bundle = None
    hits = []
    for name in sorted(os.listdir(assets)):
        if not name.endswith(".js"):
            continue
        p = os.path.join(assets, name)
        try:
            with io.open(p, "r", encoding="utf-8", errors="surrogateescape") as f:
                text = f.read()
        except Exception:
            continue
        # 原生 R3() 形态： R3=e=>{...document.title=...
        # FnDesk 魔改后是写死的字符串（如 "Stone‘s Wish"）
        if "document.title=" in text and ("Stone" in text or "fnstyle.json" in text):
            hits.append(name)
    return bundle, hits


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    www = sys.argv[1]
    logfile = None
    if "--log" in sys.argv:
        i = sys.argv.index("--log")
        if i + 1 < len(sys.argv):
            logfile = sys.argv[i + 1]
    log = log_factory(logfile)

    assets = os.path.join(www, "assets")
    waited = 0
    while not os.path.isdir(assets) and waited < 30:
        log("assets 目录暂不存在，等待 nginx 重建…（已等待 %ds）" % waited)
        time.sleep(2)
        waited += 2
    if not os.path.isdir(assets):
        log("✗ assets 目录不存在（等待 %ds 后仍无）" % waited)
        return 2

    # ---- v4：只审计，不改写 bundle ----
    bundle, hits = audit_bundle(www, log)
    if hits:
        log("⚠ /assets/ 下 %d 个文件仍含 FnDesk 时代的标题硬编码:" % len(hits))
        for n in hits:
            log("    %s" % n)
        log("  v4 架构下标题由 fnstyle-login.js 运行时接管，")
        log("  ⇒ 建议从 /usr/trim/share/.restore/raw/ 纯净基线回退这些文件。")
    else:
        log("✓ /assets/ 下无标题硬编码残留（纯净）")

    log("v4：标题统一已交由 fnstyle-login.js 运行时处理，本脚本不再改写 bundle。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
