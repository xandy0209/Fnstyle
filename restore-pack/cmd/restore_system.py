#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
restore_system.py v2 —— 把 fnOS 系统样式还原成原生状态（防御式「中和」方案）。

════════════════════════════════════════════════════════════════════════
为什么重写（2026-11-14 真机反馈：「先显示系统本身样式，又跳回软件配置的样式」）
════════════════════════════════════════════════════════════════════════
v1 的策略是「把应用写入的文件删掉」。但 fnOS 上有两个复活源：

  ① ngx_http_trim_recover：/usr/trim/www/ 里任何文件被改动，都会
     stop nginx → 从 /usr/trim/share/.restore/www.zip 重建整个 www/ → start。
     只要 zip 没被彻底改写（写失败 / 条目名不匹配 / 权限不足 / 旧的 zip
     副本又被覆盖回来），注入脚本和 fnstyle.json 就会在 1~10 秒后复活。
  ② apply_worker.sh / recheck.sh 是 setsid 后台进程（sleep 3 / sleep 4）。
     卸载时如果它们还在延时窗口内，会在还原之后把配置再写一遍。

浏览器侧的表现一模一样：原生壁纸先渲染出来 → 运行时脚本读到旧配置 →
把自定义壁纸淡入（fnstyle-runtime.js 的 1.6s 过渡）。用户看到的
「先原生、后跳回」并不是浏览器缓存，而是服务端真的在回写。

★ v1 还有一个具体漏洞：index.html 的注入标签只在 tow/ 与 www/ 里被剥离，
  **www.zip 里的 index.html 没有做通用清理** —— 没有干净原生副本时，
  native_for() 会原样返回被污染的 HTML，nginx 重建后脚本标签又回来了。

════════════════════════════════════════════════════════════════════════
v2 策略
════════════════════════════════════════════════════════════════════════
  1) 先终止残留的 apply_worker / recheck 后台进程（切断复活源 ②）；
  2) 三处（www.zip / tow / www）**不再删除，而是中和**：
       fnstyle-runtime.js / fnstyle-login.js → 无操作 stub（条目保留）
       deskdata/fnstyle.json                 → {"__fnstyleDisabled": true}
     ⇒ 即便 zip 重建把条目放回来、或 index.html 里还残留 script 标签，
       旧版运行时也读不到任何样式值，只能回退原生。
  3) index.html 在 zip 内也做通用清理（无原生副本时重置标题/剥离标签）；
  4) assets 主包 / fndesk.js 用原生副本或反向补丁还原，剥离历史 CSS 注入块；
  5) 两轮执行 + 中间等待，覆盖 nginx 渐进重建与后台复查的延时写入；
  6) 结尾逐项自检，把 VERIFY: PASS / FAIL 与明细写进日志与 restore_status.json。

原生基线来源（按可信度）：raw/ → fnstyle-guard/ → data-share/system_baseline/。
含注入痕迹的候选一律跳过 —— 用户可能「先卸旧版再装新版」，那份基线本身是脏的。

用法：
    python3 restore_system.py --data-dir <appshare/fnstyle/deskdata> [--settle 5]
    python3 restore_system.py --data-dir ... --verify-only
    python3 restore_system.py --data-dir ... --www ... --restore ...  # 沙箱测试
返回码：还原流程恒 0（卸载回调不能因还原失败而中断卸载）；--verify-only 失败返回 1。
"""
import argparse
import json
import os
import re
import shutil
import signal
import stat
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

INJECTED_SCRIPTS = ('fnstyle-login.js', 'fnstyle-runtime.js')
APP_ENTRY_NAMES = set(INJECTED_SCRIPTS) | {'deskdata/fnstyle.json'}
INJECT_MARKS = (
    b'deskdata/fnstyle.json?v=',
    b'fnstyle-login.js',
    b'fnstyle-runtime.js',
)
CSS_MARK = b'fnstyle-login-style'
NATIVE_TITLE = '飞牛 fnOS'

# 中和后的配置：旧版运行时只认具体字段，读不到就是原生；
# 新版运行时看到 __fnstyleDisabled 会主动清掉已注入的 DOM。
NEUTRAL_CFG = b'{"__fnstyleDisabled": true}\n'
STUB_MARK = b'__FNSTYLE_RESTORED_NOOP__'
STUB_JS = b'''/*! __FNSTYLE_RESTORED_NOOP__
 * FnStyle has been restored to the fnOS native theme.
 * Kept as a no-op instead of deleting the entry: fnOS rebuilds www/ from
 * www.zip, so an empty placeholder is the only way to guarantee the real
 * runtime can never come back through a stale script tag or a zip rebuild.
 */
(function () {
  'use strict';
  try {
    ['fnstyle_wallpaper', 'fnstyle-login-bg'].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.remove();
    });
    var styles = document.querySelectorAll('style[id^="fnstyle_"], style[id^="fnstyle-"]');
    Array.prototype.forEach.call(styles, function (el) { el.remove(); });
    window.__fnstyleDisabled = true;
  } catch (e) {}
})();
'''

STYLE_KEYS = (
    'appTitle', 'appTitle2', 'desktopWallpaper', 'loginBackground',
    'loginLogo', 'deviceLogo', 'webFavicon', 'FndeskGlass',
    'Fndesk_taskbar', 'FndeskIconSort', 'fndeskClickSound',
    'fndeskLoginVW', 'fndeskLoginVH', 'loginOpacity', 'nasNameDisplay',
)

NEW_R3 = (
    b'R3=e=>{let t=_?`${nk}`:`${XO} ${nk}`;'
    b'if(!e){document.title=t;return}'
    b'fetch("deskdata/fnstyle.json?v="+(new Date).getTime())'
    b'.then(r=>r.ok?r.json():null)'
    b'.then(c=>{document.title=(c&&c.appTitle)||(`${e} - ${t}`)})'
    b'.catch(()=>{document.title=`${e} - ${t}`})}'
)
OLD_R3 = (
    b'R3=e=>{let t=_?`${nk}`:`${XO} ${nk}`;'
    b'e?document.title=`${e} - ${t}`:document.title=t}'
)
NEW_FD = (
    'e.appTitle&&"飞牛 fnOS"!==e.appTitle&&'
    'document.addEventListener("visibilitychange",function(){'
    'document.title=e.appTitle})'
).encode('utf-8')
OLD_FD = (
    'e.appTitle&&e.appTitle2&&"飞牛 fnOS"!==e.appTitle&&'
    'document.addEventListener("visibilitychange",function(){'
    'document.hidden?document.title=e.appTitle2:document.title=e.appTitle})'
).encode('utf-8')


def norm_entry(name):
    """zip 条目名归一化：历史版本可能写成 ./xxx 或 /xxx。"""
    n = name.lstrip('/')
    while n.startswith('./'):
        n = n[2:]
    return n


def is_app_entry(name):
    n = norm_entry(name)
    return (n in APP_ENTRY_NAMES or
            n.startswith('deskdata/fnstyle/') or
            n in INJECTED_SCRIPTS)


def is_patched(data):
    return any(m in data for m in INJECT_MARKS)


def is_neutral(data):
    """配置是否已经「读不出任何样式」。"""
    try:
        obj = json.loads(data.decode('utf-8', 'replace'))
    except Exception:
        return False
    if not isinstance(obj, dict):
        return False
    for k in STYLE_KEYS:
        v = obj.get(k)
        if v not in (None, '', 0, False):
            return False
    return True


def is_stub(data):
    return STUB_MARK in data


def sanitize_html(data):
    """没有干净原生首页时，通用地剥掉注入痕迹并把标题改回系统原生值。"""
    t = data.decode('utf-8', 'replace')
    # 注入的 <script src="./fnstyle-*.js[?v=...]"></script>（任意写法）
    t = re.sub(r'<script[^>]*fnstyle-[^>]*>\s*</script>', '', t, flags=re.I)
    # 内联注入块（历史版本可能直接写 <script>…</script>）
    t = re.sub(r'<script[^>]*>\s*/\*\s*fnstyle[^<]*?</script>', '', t,
               flags=re.I | re.S)
    t = re.sub(r'<title>[^<]*</title>',
               '<title>%s</title>' % NATIVE_TITLE, t, count=1, flags=re.I)
    t = re.sub(r'(<meta[^>]*property="og:(?:title|site_name)"[^>]*content=")[^"]*(")',
               lambda m: m.group(1) + NATIVE_TITLE + m.group(2), t, flags=re.I)
    t = re.sub(r'(<link[^>]*rel="icon"[^>]*href=")[^"]*(")',
               lambda m: m.group(1) + '/favicon.ico' + m.group(2), t, flags=re.I)
    return t.encode('utf-8')


def strip_css(data):
    pat = re.compile(
        br'/\*\s*fnstyle-login-style-start\s*\*/.*?/\*\s*fnstyle-login-style-end\s*\*/',
        re.S)
    return pat.sub(b'', data)


def reverse_patch(rel, data):
    notes = []
    if rel == 'fndesk.js' and NEW_FD in data:
        data = data.replace(NEW_FD, OLD_FD)
        notes.append('fndesk.js 双标题逻辑已反向还原')
    if rel.startswith('assets/') and rel.endswith('.js'):
        if NEW_R3 in data:
            data = data.replace(NEW_R3, OLD_R3)
            notes.append('R3() 已反向还原为原生标题逻辑')
        elif b'deskdata/fnstyle.json?v=' in data:
            pat = re.compile(
                br'R3=e=>\{[^{}]*?deskdata/fnstyle\.json\?v=[^{}]*?\}', re.S)
            data, n = pat.subn(OLD_R3, data)
            if n:
                notes.append('R3() 变体已按正则反向还原 %d 处' % n)
            else:
                notes.append('⚠ R3() 变体未能反向还原，标题可能仍残留')
        if b"document.title=`Stone" in data:
            notes.append('⚠ 发现硬编码标题残留，需 raw/ 原生副本才能彻底还原')
    if rel.startswith('assets/') and rel.endswith('.css') and CSS_MARK in data:
        data = strip_css(data)
        notes.append('assets CSS 注入块已剥离')
    return data, notes


class Restore:
    def __init__(self, data_dir, www, restore, guard=None, settle=5,
                 skip_www=False):
        self.data = Path(data_dir)
        self.www = Path(www)
        self.restore = Path(restore)
        self.tow = self.restore / 'tow'
        self.raw = self.restore / 'raw'
        self.zip_path = self.restore / 'www.zip'
        self.guard = Path(guard) if guard else self.restore / 'fnstyle-guard'
        self.baseline = self.data / 'system_baseline'
        self.settle = max(0.0, settle)
        # 同步阶段专用：绝不写 /usr/trim/www/ —— 那是 nginx inotify 监控目录，
        # 一写就 stop/start trim_nginx，会切断应用中心「卸载」这条在途请求。
        self.skip_www = skip_www
        self.logs = [self.guard / 'uninstall_restore.log',
                     self.data / 'uninstall_restore.log']
        self.stats = {'zip_replaced': 0, 'zip_removed': 0,
                      'tree_replaced': 0, 'tree_removed': 0,
                      'reverse': 0, 'killed': 0, 'missing_native': []}
        for d in (self.data, self.guard):
            try:
                d.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass

    def log(self, msg):
        line = '%s %s' % (datetime.now().strftime('%F %T'), msg)
        print(line)
        for p in self.logs:
            try:
                with open(str(p), 'a', encoding='utf-8') as f:
                    f.write(line + '\n')
            except Exception:
                pass

    # ---------- 原生内容 ----------
    def native_bytes(self, rel):
        for root in (self.raw, self.guard, self.baseline):
            p = root / rel
            if not p.is_file():
                continue
            try:
                data = p.read_bytes()
            except Exception as e:
                self.log('  ! 读取原生副本失败 %s: %s' % (p, e))
                continue
            if is_patched(data) or CSS_MARK in data:
                self.log('  跳过被污染的原生候选：%s' % p)
                continue
            return data
        return None

    def native_for(self, rel, current):
        native = self.native_bytes(rel)
        if native is not None:
            return (native, None) if native == current else (native, '原生副本覆盖')
        data, notes = reverse_patch(rel, current)
        for n in notes:
            self.log('    ' + n)
        if notes:
            self.stats['reverse'] += 1
            return data, '反向补丁（无原生副本）'
        if rel in ('index.html', 'fndesk.js') or rel.startswith('assets/'):
            self.stats['missing_native'].append(rel)
        return current, None

    def restore_content(self, rel, data):
        """返回 (最终内容, 说明)。所有 zip 条目与目录文件统一走这里。"""
        if rel == 'index.html':
            native = self.native_bytes(rel)
            if native is not None:
                return (native, None) if native == data else (native, '原生首页')
            out = sanitize_html(data)
            return (out, '通用清理（无原生首页）') if out != data else (data, None)
        if rel == 'fndesk.js' or rel.startswith('assets/'):
            return self.native_for(rel, data)
        return data, None

    # ---------- 掐断复活源 ----------
    def kill_lingering_workers(self):
        """卸载时可能还有 apply_worker.sh(sleep 4) / recheck.sh(sleep 3) 活着。"""
        proc = Path('/proc')
        if not proc.is_dir():
            return
        me = os.getpid()
        for d in proc.iterdir():
            try:
                if not d.name.isdigit() or int(d.name) == me:
                    continue
                cmd = (d / 'cmdline').read_bytes().replace(b'\x00', b' ').decode(
                    'utf-8', 'replace')
                if 'apply_worker.sh' not in cmd and 'recheck.sh' not in cmd:
                    continue
                # 只杀这两个脚本，绝不匹配「cmdline 里带 fnstyle 路径」的其它进程
                os.kill(int(d.name), signal.SIGKILL)
                self.stats['killed'] += 1
                self.log('  已终止残留后台写入进程 pid=%s (%s)' % (d.name, cmd[:100]))
            except Exception:
                pass

    # ---------- zip（唯一还原源）----------
    def clean_zip(self):
        if not self.zip_path.is_file():
            self.log('找不到 www.zip，跳过 zip 还原')
            return False
        tmp = self.restore / 'www.zip.restoring'
        removed = replaced = 0
        try:
            before = os.stat(str(self.zip_path))
            with zipfile.ZipFile(str(self.zip_path), 'r') as zin, \
                    zipfile.ZipFile(str(tmp), 'w') as zout:
                for info in zin.infolist():
                    name = info.filename
                    n = norm_entry(name)
                    if info.is_dir():
                        zout.writestr(info, b'')
                        continue
                    # 应用图片：删文件条目，保留目录条目（fnOS 重建依赖显式目录项）
                    if n.startswith('deskdata/fnstyle/'):
                        removed += 1
                        continue
                    data = zin.read(name)
                    if n in INJECTED_SCRIPTS:
                        new = STUB_JS
                    elif n == 'deskdata/fnstyle.json':
                        new = NEUTRAL_CFG
                    elif n == 'index.html' or n == 'fndesk.js' or (
                            n.startswith('assets/') and
                            (n.endswith('.js') or n.endswith('.css'))):
                        new, _ = self.restore_content(n, data)
                    else:
                        new = data
                    if new != data:
                        replaced += 1
                    zout.writestr(info, new)
            # 保留原归档的属主/权限，避免新文件 mode 600 让 nginx 读不到
            try:
                os.chmod(str(tmp), stat.S_IMODE(before.st_mode))
                os.chown(str(tmp), before.st_uid, before.st_gid)
            except Exception:
                pass
            os.replace(str(tmp), str(self.zip_path))
            self.stats['zip_removed'] += removed
            self.stats['zip_replaced'] += replaced
            self.log('www.zip 已流式中和：剔除 %d 个图片条目、改写 %d 个条目内容，'
                     '目录条目原样保留' % (removed, replaced))
            return True
        except Exception as e:
            self.log('  ! www.zip 中和失败：%s' % e)
            try:
                if tmp.is_file():
                    tmp.unlink()
            except Exception:
                pass
            return False

    # ---------- tow/ 与 www/ ----------
    def neutralize_tree(self, base):
        if not base.is_dir():
            self.log('跳过不存在的目录：%s' % base)
            return
        self.log('处理目录：%s' % base)

        # 1) 注入脚本 → 无操作 stub；配置 → 空配置（不删，防重建复活）
        neutral = {
            'fnstyle-login.js': STUB_JS,
            'fnstyle-runtime.js': STUB_JS,
            'deskdata/fnstyle.json': NEUTRAL_CFG,
        }
        for rel, content in neutral.items():
            p = base / rel
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                if not p.is_file() or p.read_bytes() != content:
                    p.write_bytes(content)
                    os.chmod(str(p), 0o644)
                    self.stats['tree_replaced'] += 1
            except Exception as e:
                self.log('  ! 中和 %s 失败：%s' % (rel, e))

        # 2) 自定义图片目录：不再被任何配置引用，直接清掉
        imgdir = base / 'deskdata/fnstyle'
        if imgdir.is_dir():
            try:
                shutil.rmtree(str(imgdir))
                self.stats['tree_removed'] += 1
            except Exception as e:
                self.log('  ! 清理图片目录失败：%s' % e)

        # 3) 首页 / 桌面主包 / fndesk.js / 历史 CSS 注入
        targets = []
        for rel in ('index.html', 'fndesk.js'):
            if (base / rel).is_file():
                targets.append(base / rel)
        assets = base / 'assets'
        if assets.is_dir():
            targets += sorted(p for p in assets.iterdir()
                               if p.is_file() and p.suffix in ('.js', '.css'))
        for p in targets:
            rel = str(p.relative_to(base)).replace(os.sep, '/')
            try:
                cur = p.read_bytes()
                new, how = self.restore_content(rel, cur)
                if new != cur:
                    p.write_bytes(new)
                    os.chmod(str(p), 0o644)
                    self.stats['tree_replaced'] += 1
                    if how:
                        self.log('  已还原 %s（%s）' % (rel, how))
            except Exception as e:
                self.log('  ! 还原 %s 失败：%s' % (rel, e))

        # 4) 兜底：无论如何再剥一次 index.html 的注入标签
        idx = base / 'index.html'
        if idx.is_file():
            try:
                cur = idx.read_bytes()
                new = sanitize_html(cur) if is_patched(cur) else cur
                if new != cur:
                    idx.write_bytes(new)
                    os.chmod(str(idx), 0o644)
                    self.log('  已剥离 %s 中残留的注入 script 标签' % idx)
            except Exception as e:
                self.log('  ! 清理 index.html 失败：%s' % e)

    def neutralize_raw(self):
        """raw/ 是 fnOS 官方出厂副本；若被旧版注入污染，只清应用专属条目，
        绝不动它的 index.html / assets（那是还原用的原生基线）。"""
        if not self.raw.is_dir():
            return
        try:
            for rel in INJECTED_SCRIPTS:
                p = self.raw / rel
                if p.is_file() and not is_stub(p.read_bytes()):
                    p.write_bytes(STUB_JS)
                    os.chmod(str(p), 0o644)
                    self.log('  raw/%s 已中和为无操作 stub' % rel)
            cfg = self.raw / 'deskdata/fnstyle.json'
            if cfg.is_file() and not is_neutral(cfg.read_bytes()):
                cfg.write_bytes(NEUTRAL_CFG)
                os.chmod(str(cfg), 0o644)
                self.log('  raw/deskdata/fnstyle.json 已中和为空配置')
            imgdir = self.raw / 'deskdata/fnstyle'
            if imgdir.is_dir():
                shutil.rmtree(str(imgdir))
                self.log('  raw/deskdata/fnstyle/ 自定义图片已清除')
        except Exception as e:
            self.log('  ! 中和 raw/ 失败：%s' % e)

    def trigger_rebuild(self):
        """真正写一次 www/index.html（utime 只触发 IN_ATTRIB，不触发重建）。"""
        idx = self.www / 'index.html'
        try:
            if idx.is_file():
                cur = idx.read_bytes()
                idx.write_bytes(cur)
                os.chmod(str(idx), 0o644)
                self.log('已写 www/index.html，触发 nginx 从中和后的 www.zip 重建')
        except Exception as e:
            self.log('  ! 触发重建失败：%s' % e)

    # ---------- 自检 ----------
    def verify(self):
        problems = []
        if self.zip_path.is_file():
            try:
                with zipfile.ZipFile(str(self.zip_path)) as z:
                    for n in z.namelist():
                        nn = norm_entry(n)
                        if nn.endswith('/'):
                            continue
                        if nn.startswith('deskdata/fnstyle/'):
                            problems.append('zip 仍有应用图片条目 %s' % nn)
                            continue
                        d = z.read(n)
                        if nn in INJECTED_SCRIPTS and not is_stub(d):
                            problems.append('zip/%s 仍是真实运行时' % nn)
                        elif nn == 'deskdata/fnstyle.json' and not is_neutral(d):
                            problems.append('zip/deskdata/fnstyle.json 仍含样式配置')
                        elif nn == 'index.html' and is_patched(d):
                            problems.append('zip/index.html 仍有注入脚本标签')
                        elif (nn.startswith('assets/') or nn == 'fndesk.js') and \
                                b'deskdata/fnstyle.json?v=' in d:
                            problems.append('zip/%s 仍含动态标题补丁' % nn)
                        elif nn.endswith('.css') and CSS_MARK in d:
                            problems.append('zip/%s 仍含样式注入块' % nn)
            except Exception as e:
                problems.append('zip 无法读取：%s' % e)
        else:
            problems.append('找不到 www.zip')

        if self.raw.is_dir():
            for rel in INJECTED_SCRIPTS:
                p = self.raw / rel
                if p.is_file() and not is_stub(p.read_bytes()):
                    problems.append('raw/%s 仍是真实运行时' % rel)
            rcfg = self.raw / 'deskdata/fnstyle.json'
            if rcfg.is_file() and not is_neutral(rcfg.read_bytes()):
                problems.append('raw/deskdata/fnstyle.json 仍含样式配置')
            rimg = self.raw / 'deskdata/fnstyle'
            if rimg.is_dir() and any(x.is_file() for x in rimg.iterdir()):
                problems.append('raw/deskdata/fnstyle/ 仍有自定义图片')

        for base in (self.tow, self.www):
            tag = base.name
            for rel in INJECTED_SCRIPTS:
                p = base / rel
                if p.is_file() and not is_stub(p.read_bytes()):
                    problems.append('%s/%s 仍是真实运行时' % (tag, rel))
            cfg = base / 'deskdata/fnstyle.json'
            if cfg.is_file() and not is_neutral(cfg.read_bytes()):
                problems.append('%s/deskdata/fnstyle.json 仍含样式配置' % tag)
            idx = base / 'index.html'
            if idx.is_file() and is_patched(idx.read_bytes()):
                problems.append('%s/index.html 仍有注入脚本标签' % tag)
            assets = base / 'assets'
            if assets.is_dir():
                for p in assets.glob('*'):
                    if not p.is_file():
                        continue
                    d = p.read_bytes()
                    if b'deskdata/fnstyle.json?v=' in d:
                        problems.append('%s/assets/%s 仍含动态标题补丁' % (tag, p.name))
                    if CSS_MARK in d:
                        problems.append('%s/assets/%s 仍含样式注入块' % (tag, p.name))
        return problems

    def has_injected_state(self):
        """是否还有「真实」注入（中和后的 stub / 空配置不算）。"""
        if self.zip_path.is_file():
            try:
                with zipfile.ZipFile(str(self.zip_path)) as z:
                    for n in z.namelist():
                        nn = norm_entry(n)
                        if nn.endswith('/'):
                            continue
                        if nn.startswith('deskdata/fnstyle/'):
                            return True
                        d = z.read(n)
                        if nn in INJECTED_SCRIPTS and not is_stub(d):
                            return True
                        if nn == 'deskdata/fnstyle.json' and not is_neutral(d):
                            return True
                        if is_patched(d) or CSS_MARK in d:
                            return True
            except Exception:
                pass
        for base in (self.tow, self.www):
            idx = base / 'index.html'
            if idx.is_file() and is_patched(idx.read_bytes()):
                return True
            for rel in INJECTED_SCRIPTS:
                p = base / rel
                if p.is_file() and not is_stub(p.read_bytes()):
                    return True
            cfg = base / 'deskdata/fnstyle.json'
            if cfg.is_file() and not is_neutral(cfg.read_bytes()):
                return True
        return False

    def write_status(self, problems):
        status = {
            'ok': not problems,
            'time': datetime.now().strftime('%F %T'),
            'problems': problems,
            'stats': {k: v for k, v in self.stats.items() if k != 'missing_native'},
            'missing_native': sorted(set(self.stats['missing_native']))[:20],
        }
        for p in (self.guard / 'restore_status.json',
                  self.data / 'restore_status.json'):
            try:
                p.write_text(json.dumps(status, ensure_ascii=False, indent=2),
                             encoding='utf-8')
            except Exception:
                pass
        return status

    def run(self, only_if_injected=False, verify_only=False):
        if verify_only:
            problems = self.verify()
            self.log('VERIFY: %s' % ('PASS' if not problems else 'FAIL'))
            for p in problems:
                self.log('  - ' + p)
            self.write_status(problems)
            return not problems

        self.log('===== 系统原生样式还原开始（v2 中和方案）=====')
        if only_if_injected and not self.has_injected_state():
            self.log('未检测到应用残留，无需还原')
            self.log('===== 结束 =====')
            return True

        self.log('原生副本搜索路径：raw=%s guard=%s baseline=%s' %
                 (self.raw, self.guard, self.baseline))
        self.kill_lingering_workers()
        self.log('已清理残留后台写入进程 %d 个' % self.stats['killed'])

        # 两轮：第二轮专门覆盖 nginx 渐进重建 / 后台复查的延时写回。
        # ⚠️ skip_www（卸载回调同步阶段）只跑一轮，且绝不写 self.www ——
        #    fnOS 的 ngx_http_trim_recover 用 inotify 监控 /usr/trim/www/，
        #    任何写入都会 stop nginx → 从 www.zip 重建 → start（约 2~10 秒），
        #    期间应用中心的「卸载」请求被切断，前端就是 "Failed to fetch"。
        rounds = (1,) if self.skip_www else (1, 2)
        for round_no in rounds:
            self.log('----- 第 %d 轮 -----' % round_no)
            self.clean_zip()
            self.neutralize_tree(self.tow)
            self.neutralize_raw()
            if not self.skip_www:
                self.neutralize_tree(self.www)
                self.trigger_rebuild()
            if not self.skip_www and round_no == 1 and self.settle > 0:
                self.log('等待 %.1f 秒，覆盖 nginx 渐进重建与后台复查的延时写入'
                         % self.settle)
                time.sleep(self.settle)

        if self.skip_www:
            self.log('同步阶段完成：www.zip / tow / raw 已中和，未触碰 www/')
            self.log('（不触发 nginx 重建 ⇒ 应用中心的卸载请求能正常返回）')
            self.log('完整还原由独立后台进程延迟执行，详见 restore_deferred.sh')
            self.log('===== 系统原生样式还原结束 =====')
            return True

        problems = self.verify()
        self.log('VERIFY: %s（%d 项异常）' %
                 ('PASS' if not problems else 'FAIL', len(problems)))
        for p in problems:
            self.log('  - ' + p)
        self.write_status(problems)
        missing = self.stats['missing_native']
        if missing:
            self.log('⚠ 以下文件没有干净的原生副本，已用通用清理/反向补丁处理：%s'
                     % ', '.join(sorted(set(missing))[:12]))
        self.log('统计：zip 剔除 %d / 改写 %d，目录改写 %d / 删除 %d，'
                 '反向补丁 %d 次，终止后台进程 %d 个' %
                 (self.stats['zip_removed'], self.stats['zip_replaced'],
                  self.stats['tree_replaced'], self.stats['tree_removed'],
                  self.stats['reverse'], self.stats['killed']))
        self.log('===== 系统原生样式还原结束 =====')
        self.log('提示：浏览器需强制刷新（Ctrl/Cmd + Shift + R）；'
                 '旧标签页里的运行时脚本缓存了配置，不刷新会继续套用旧样式。')
        return True


def main():
    ap = argparse.ArgumentParser(description='还原 fnOS 系统原生样式（v2 中和方案）')
    ap.add_argument('--data-dir', required=True, help='应用 data-share/deskdata 路径')
    ap.add_argument('--www', default='/usr/trim/www', help='系统 www 目录')
    ap.add_argument('--restore', default='/usr/trim/share/.restore',
                    help='系统还原点目录（含 www.zip / tow / raw）')
    ap.add_argument('--guard', default=None, help='系统级持久基线目录')
    ap.add_argument('--settle', type=float, default=5.0,
                    help='两轮还原之间的等待秒数（覆盖 nginx 重建与后台复查）')
    ap.add_argument('--skip-www', action='store_true',
                    help='只中和还原源 www.zip / tow / raw，绝不写 www/ —— '
                         '卸载回调同步阶段专用（写 www/ 会切断卸载请求）')
    ap.add_argument('--only-if-injected', action='store_true',
                    help='仅在检测到应用残留时执行还原（安装时清理遗留用）')
    ap.add_argument('--verify-only', action='store_true',
                    help='只自检当前系统状态，不做任何写入')
    a = ap.parse_args()
    try:
        r = Restore(a.data_dir, a.www, a.restore, a.guard, a.settle,
                    a.skip_www)
        ok = r.run(only_if_injected=a.only_if_injected,
                   verify_only=a.verify_only)
        return 0 if ok or not a.verify_only else 1
    except Exception as e:
        print('[restore_system] 未捕获异常：%s' % e, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
