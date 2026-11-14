#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apply_style_overrides.py —— FN定制化配置：登录框样式部署（v4 架构）

════════════════════════════════════════════════════════════════════════
一、v4 相对 v3 的架构级修正（2026-11-13 血泪教训）
════════════════════════════════════════════════════════════════════════
【v3 的做法（已废弃）】
  原地改写 /usr/trim/www/assets/login-form-*.css，在文件末尾追加：
      .gradient-border{position:relative;margin-left:{VW}vw;...}
  问题：**文件名不变，内容变了**。

【fnOS 的缓存契约（关键）】
  /usr/trim/nginx/conf/conf.d/html_nocache.conf：
      location ~ ^/assets/* {
          add_header cache-control "max-age=1209600, immutable";
      }
  ⇒ 14 天 + immutable ⇒ 浏览器**连条件请求都不发**。

【v3 踩的坑（实测）】
  违反 immutable 契约 ⇒ 浏览器 14 天内一直用旧版：
    ① 换图/调位置后点「应用到系统」→ 页面毫无变化
    ② 卸载 FnStyle 后服务端已恢复纯净版 → 浏览器仍是旧版
       ⇒ 表现为「登录页 LOGO 破图 + 卡片跑到 60vw + 背景变深蓝灰」

【为什么不能改 nginx 来解决】
  `nginx -t` 会从**加密的** /usr/trim/share/.restore/ng.conf.zip
  重建整个 /usr/trim/nginx/conf/ 目录。
  ⇒ 实测：写进 conf.d/*.conf 与 nginx.conf 的改动
    在下一次 nginx -t 时**全部消失** ⇒ 这条路完全堵死。

【v4 的做法】
  ① **完全不碰 /assets/ 下的任何文件**
  ② 把注入脚本部署到 /assets/ **之外**：/fnstyle-login.js
     —— 该路径不带 immutable 头（实测 /static/* → no-cache，
        / → no-store，只有 /assets/* 是 immutable）
  ③ 由 index.html 以 <script src="./fnstyle-login.js"></script> 引入
  ④ 脚本在运行时用 DOM API 设置 LOGO / 背景 / 卡片样式
  ⇒ 全部走「独立 URL + 运行时 DOM」，**不违反任何缓存契约**
  ⇒ 改配置后刷新即生效，且**卸载后刷新即恢复原生**（无残留）

════════════════════════════════════════════════════════════════════════
二、当前 fnOS 登录框的真实结构（Chrome 实测 + CSS 反查）
════════════════════════════════════════════════════════════════════════
登录页 JS（login-C-JnA4tt.js）：
    jsxs(a, {className: `gradient-border w-[85%] max-w-[350px] …`,
             children: [logo, 表单…]})
而 a = login-form-9slnkg98.js 导出的 v 组件：
    jsx(`div`, {className: l(`box-border flex transform-gpu flex-col
          items-center justify-center rounded-[32px] bg-white-10 text-white
          backdrop-blur-[20px]`, e), children: t})

⇒ clsx 把两个 className 合并到**同一个 div**：
    <div class="gradient-border … box-border … bg-white-10 … backdrop-blur-[20px]">
  Chrome 实测：getComputedStyle(card).marginLeft = 840px  (= 1400 × 60vw) ✅
  原生 LOGO：<img src="data:image/svg+xml,…" alt="logo" class="w-[60px] sm:w-auto">

三、本脚本做什么
════════════════════════════════════════════════════════════════════════
把 app/ui/static/js/fnstyle-login.js 复制到：
     /usr/trim/www/fnstyle-login.js
并确保 index.html 里引入 <script src="./fnstyle-login.js"></script>。

⚠️ 本脚本**不改任何 assets 文件**，只做「部署 + index.html 引入」。

四、必须同步三处（否则 1 秒内被还原）
════════════════════════════════════════════════════════════════════════
fnOS nginx 内置 ngx_http_trim_recover 模块：inotify 监控 /usr/trim/www/，
一有改动就 stop nginx → 从 www.zip 重建整个 www/ → start nginx（约 2 秒）。
⇒ 必须同时写：
     www.zip（还原源） + tow/（工作副本） + www/（生效）

用法: apply_style_overrides.py <www目录> <配置json> [--log <文件>] [--dry-run]
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime

MARK = 'fnstyle-login-style'
RESTORE_DIR = '/usr/trim/share/.restore'


def log(msg, logfile=None):
    line = "%s [style-override] %s" % (datetime.now().strftime('%F %T'), msg)
    print(line)
    if logfile:
        try:
            with open(logfile, 'a', encoding='utf-8') as f:
                f.write(line + "\n")
        except Exception:
            pass


def load_cfg(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        log("❌ 配置读取失败: %s" % e)
        return {}


def num(d, key, default=None):
    v = d.get(key, None)
    if v is None or isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        return v
    try:
        return int(str(v).strip())
    except Exception:
        try:
            return float(str(v).strip())
        except Exception:
            return default


def read(path):
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        return f.read()


def write(path, text):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    try:
        os.chmod(path, 0o644)
    except Exception:
        pass


# ----------------------------------------------------------------------
# 数值语义（与 FnDesk 面板 fnstyle.html 完全一致）
# ----------------------------------------------------------------------
#   loginOpacity    : 0..30    0 = 全透明（最清晰），30 = 最不透明（最模糊）
#   FndeskGlass     : -1..50   -1 = 飞牛原设(blur 8px)，0 = 关闭，N = blur(Npx)
#   fndeskLoginVW   : -80..80  水平偏移（视口宽度百分比）
#   fndeskLoginVH   : -80..80  垂直偏移（视口高度百分比）
def map_values(cfg):
    glass = num(cfg, 'FndeskGlass', -1)
    opacity = num(cfg, 'loginOpacity', 10)
    vw = num(cfg, 'fndeskLoginVW', 0)
    vh = num(cfg, 'fndeskLoginVH', 0)

    if glass == -1:
        blur = 8           # 飞牛原设（实测 index-*.css 的默认值就是 blur(8px)）
    elif glass == 0:
        blur = 0           # 完全关闭毛玻璃
    else:
        blur = max(0, min(100, int(glass)))

    # 透明度 → 背景 alpha
    #   映射口径：opacity=10 → alpha=0.10（= fnOS 登录框原生效果）
    #             opacity=0  → alpha=0    （全透明）
    #             opacity=30 → alpha=0.30 （最不透明）
    opacity = max(0, min(30, int(opacity)))
    alpha = round(opacity / 100.0, 4)

    vw = max(-80, min(80, int(vw)))
    vh = max(-80, min(80, int(vh)))

    return dict(blur=blur, alpha=alpha, vw=vw, vh=vh,
                glass=glass, opacity=opacity)


def build_rule(v):
    """保留旧接口（不再用于改写 assets，仅供 dry-run 展示与日志）。

    v4 起不再把这段规则写进任何 assets 文件 ——
    它由 fnstyle-login.js 在运行时注入到 <style> 元素里。
    """
    return (
        "/*" + MARK + "-start*/"
        ".gradient-border{"
        "position:relative;"
        "margin-left:" + str(v['vw']) + "vw;"
        "margin-top:" + str(v['vh']) + "vh;"
        "background-color:rgba(255,255,255," + ("%.4f" % v['alpha']) + ")!important;"
        "backdrop-filter:blur(" + str(v['blur']) + "px)!important;"
        "-webkit-backdrop-filter:blur(" + str(v['blur']) + "px)!important;"
        "}"
        "/*" + MARK + "-end*/"
    )


# ── v4：注入脚本的文件名与来源 ──────────────────────────────────────
# 放在 /assets/ **之外** —— 只有 /assets/* 带 immutable 头
LOGIN_JS_NAME = "fnstyle-login.js"
LOGIN_JS_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "static", "js", LOGIN_JS_NAME)
LOGIN_JS_TAG = '<script src="./%s"></script>' % LOGIN_JS_NAME


def find_login_js_src():
    """定位注入脚本源文件。

    优先用应用目录里的 static/js/fnstyle-login.js；
    找不到时回退到同目录的 fnstyle-runtime.js 旁边。
    """
    if os.path.isfile(LOGIN_JS_SRC):
        return LOGIN_JS_SRC
    alt = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "static", "js", "fnstyle-login.js")
    if os.path.isfile(alt):
        return alt
    return None


def ensure_index_html_tag(html_path):
    """确保 index.html 里有 <script src="./fnstyle-login.js"></script>。

    返回 (是否改动, 说明)
    ⚠️ 不动 /assets/，只动 index.html —— index.html 是 no-store，不受 immutable 影响。
    """
    try:
        with open(html_path, 'r', encoding='utf-8', errors='replace') as f:
            html = f.read()
    except Exception as e:
        return False, "读取失败: %s" % e

    if LOGIN_JS_TAG in html:
        return False, "已存在（幂等）"

    # 插在主包 script 之前 —— 保证注入脚本先加载、能监听 DOM 变化
    m = re.search(r'<script[^>]*src="/assets/index-[^"]*\.js"[^>]*></script>', html)
    if not m:
        return False, "找不到主包 script 标签，未插入"

    new_html = html[:m.start()] + LOGIN_JS_TAG + html[m.start():]
    try:
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(new_html)
        try:
            os.chmod(html_path, 0o644)
        except Exception:
            pass
    except Exception as e:
        return False, "写入失败: %s" % e
    return True, "已插入 %s" % LOGIN_JS_TAG


def deploy_login_js(www, dry=False):
    """把 fnstyle-login.js 部署到 /usr/trim/www/（assets 之外）。

    返回 (是否改动, 说明, 相对路径列表)
    """
    src = find_login_js_src()
    if not src:
        return False, "❌ 找不到注入脚本源文件（static/js/%s）" % LOGIN_JS_NAME, []

    rel = LOGIN_JS_NAME                      # ← 落在 www/ 根，不在 assets/ 下
    dst = os.path.join(www, rel)

    try:
        with open(src, 'rb') as f:
            new_bytes = f.read()
    except Exception as e:
        return False, "❌ 读取源文件失败: %s" % e, []

    old_bytes = None
    if os.path.isfile(dst):
        try:
            with open(dst, 'rb') as f:
                old_bytes = f.read()
        except Exception:
            old_bytes = None

    if old_bytes == new_bytes:
        return False, "= 注入脚本已是最新，无需改动", []

    if dry:
        return True, "[dry-run] 将写入 %s（%d 字节）" % (rel, len(new_bytes)), [rel]

    try:
        with open(dst, 'wb') as f:
            f.write(new_bytes)
        os.chmod(dst, 0o644)
    except Exception as e:
        return False, "❌ 写入 %s 失败: %s" % (dst, e), []

    return True, "✓ 已部署注入脚本 %s（%d 字节）" % (rel, len(new_bytes)), [rel]


def rewrite_css(css_file, v, dry=False):
    """⚠️ v4 起**不再使用**。

    保留函数名仅为兼容旧调用；实际不写任何文件。
    """
    log("· v4：跳过 assets CSS 改写（改由 fnstyle-login.js 运行时注入）")
    return False, 0


def sync_zip_tow(www, files, logfile=None):
    """把改写后的文件同步到 www.zip（还原源）与 tow/（工作副本）。"""
    restore_zip = os.path.join(RESTORE_DIR, 'www.zip')
    restore_tow = os.path.join(RESTORE_DIR, 'tow')

    # 1) tow/ 工作副本
    n = 0
    for src in files:
        rel = os.path.relpath(src, www)
        dst = os.path.join(restore_tow, rel)
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
            os.chmod(dst, 0o644)
            n += 1
        except Exception as e:
            log("⚠ tow 同步失败 %s: %s" % (rel, e))
    if n:
        log("✓ tow/ 已同步 %d 个文件" % n)

    # 2) www.zip 还原源（逐个 zip 调用，避免整包重写 45MB）
    if not os.path.isfile(restore_zip):
        log("⚠ www.zip 不存在: %s" % restore_zip)
        return
    for src in files:
        rel = os.path.relpath(src, www)
        stage = tempfile.mkdtemp(prefix='.fnstyle_css')
        try:
            dst = os.path.join(stage, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
            # ⚠️ 关键：zip 的条目名取决于 cwd + 传入的相对路径。
            #    必须在 stage 目录里执行，且传 "fnstyle-login.js"（不带 assets/ 前缀）
            r = subprocess.run(['zip', '-q', restore_zip, rel],
                               cwd=stage, capture_output=True)
            if r.returncode != 0:
                log("⚠ zip 写入失败 %s: %s"
                    % (rel, r.stderr.decode('utf-8', 'replace')[:150]))
            else:
                log("✓ www.zip 已更新: %s" % rel)
        except Exception as e:
            log("⚠ zip 同步失败 %s: %s" % (rel, e))
        finally:
            shutil.rmtree(stage, ignore_errors=True)


def main():
    if len(sys.argv) < 3:
        log("用法: apply_style_overrides.py <www目录> <配置json> [--log <文件>] [--dry-run]")
        return 1
    www = sys.argv[1]
    cfg_path = sys.argv[2]
    logfile = None
    dry = '--dry-run' in sys.argv
    if '--log' in sys.argv:
        try:
            logfile = sys.argv[sys.argv.index('--log') + 1]
        except Exception:
            logfile = None

    cfg = load_cfg(cfg_path)
    if not cfg:
        return 1

    v = map_values(cfg)
    log("配置: Glass=%s→blur=%spx ; opacity=%s→alpha=%.4f ; VW=%s%% VH=%s%%"
        % (v['glass'], v['blur'], v['opacity'], v['alpha'], v['vw'], v['vh']))

    # ⚠️ v4：**不再改写 assets/login-form-*.css**
    #    样式改由 /fnstyle-login.js 在运行时注入（避开 immutable 缓存契约）。
    touched = []
    changed = 0

    # ---- ① 部署注入脚本到 www/ 根目录（不在 assets/ 下）----
    ok, msg, rels = deploy_login_js(www, dry=dry)
    log(("✓ " if ok else "· ") + msg if msg.startswith("=") else msg)
    if ok:
        changed += 1
        touched.extend(rels)

    # ---- ② 确保 index.html 引入了它 ----
    html_path = os.path.join(www, 'index.html')
    if os.path.isfile(html_path):
        html_changed, html_msg = ensure_index_html_tag(html_path)
        log(("✓ " if html_changed else "· ") + "index.html: " + html_msg)
        if html_changed:
            changed += 1
            touched.append('index.html')
    else:
        log("⚠ 找不到 %s，跳过引入" % html_path)

    # ---- ③ 三处同步（zip 源 / tow 副本 / www 生效）----
    if changed and not dry:
        files = [os.path.join(www, r) for r in touched]
        files = [f for f in files if os.path.isfile(f)]
        if files:
            sync_zip_tow(www, files, logfile)

    log("完成，改动项: %d%s" % (changed, '（dry-run）' if dry else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
