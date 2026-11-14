#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FnStyle「应用到系统」端到端仿真验证（真实 CGI + 真实补丁脚本 + 模拟 fnOS 系统目录）

做法：把 app/ui 完整复制一份，只把硬编码的绝对路径重定向到本地沙箱，
逻辑代码一行不改；然后构造与真机同构的 www / .restore/{www.zip,tow} / appshare，
写入一份「全部字段都偏离默认值」的配置，以 FNSTYLE_INTERNAL=1 调真实 CGI 的
/api/apply，最后逐项核对系统侧最终状态。

唯一旁路：is_root 检查（真机由 fnOS CGI 网关以 root 执行）。
"""
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

SRC = Path('/Volumes/SSD-Data/trae/FnOsApp')
S = Path('/tmp/fnstyle_sandbox')
UI = S / 'var/apps/fnstyle/target/ui'
WWW = S / 'www'
RESTORE = S / '.restore'
TOW = RESTORE / 'tow'
ZIP = RESTORE / 'www.zip'
SHARE = S / 'appshare/fnstyle/deskdata'
BIN = S / 'bin'

RESULTS = []


def rec(group, item, ok, detail):
    RESULTS.append((group, item, 'PASS' if ok else 'FAIL', detail))


def md5(p):
    import hashlib
    return hashlib.md5(Path(p).read_bytes()).hexdigest()[:12]


def make_img(path, color):
    from PIL import Image, ImageDraw
    im = Image.new('RGB', (320, 200), color)
    d = ImageDraw.Draw(im)
    d.rectangle((20, 20, 300, 180), outline=(255, 255, 255), width=6)
    im.save(path)


def build_sandbox():
    if S.exists():
        shutil.rmtree(S)
    for d in (UI.parent, WWW / 'assets', WWW / 'deskdata/fnstyle',
              RESTORE, TOW, SHARE / 'fnstyle', SHARE / 'upload',
              SHARE / 'lib', S / 'appshare/fndesk/deskdata', BIN,
              S / 'var/apps/fnstyle/shares'):
        d.mkdir(parents=True, exist_ok=True)

    # ---- 应用 UI 副本：只重定向路径 ----
    shutil.copytree(SRC / 'app/ui', UI, dirs_exist_ok=True)
    repl = [
        # 顺序要紧：先替换字面量，最后再把变量形式的 APP_ROOT 换成绝对路径，
        # 否则新写入的绝对路径会被再次替换，出现「沙箱路径/沙箱路径」嵌套。
        ('/var/apps/fnstyle/target', str(S / 'var/apps/fnstyle/target')),
        ('/vol1/@appshare/fnstyle/deskdata', str(SHARE)),
        ('/vol1/@appshare/fndesk/deskdata', str(S / 'appshare/fndesk/deskdata')),
        ('/usr/trim/share/.restore', str(RESTORE)),
        ('/usr/trim/www', str(WWW)),
        ('APP_ROOT="/var/apps/${APP_NAME}/target"',
         'APP_ROOT="%s/var/apps/fnstyle/target"' % S),
    ]
    for p in UI.rglob('*'):
        if not p.is_file():
            continue
        if p.suffix not in ('.cgi', '.py', '.sh') and p.name not in ('config',):
            continue
        try:
            t = p.read_text(encoding='utf-8')
        except Exception:
            continue
        orig = t
        for a, b in repl:
            t = t.replace(a, b)
        # 唯一旁路：root 检查（真机由 CGI 网关以 root 执行）
        t = t.replace('if ! is_root; then',
                      'if [ "${FNSTYLE_SANDBOX:-0}" != "1" ] && ! is_root; then')
        if t != orig:
            p.write_text(t, encoding='utf-8')

    # macOS 没有 md5sum，提供同语义垫片（真机 Linux 自带）
    (BIN / 'md5sum').write_text(
        '#!/bin/sh\nfor f in "$@"; do md5 -q "$f" | tr -d "\\n"; printf "  %s\\n" "$f"; done\n')
    (BIN / 'md5sum').chmod(0o755)

    # ---- 模拟 fnOS 系统当前状态（旧值）----
    html = ('<!DOCTYPE html><html><head><meta charset="utf-8">'
            '<title>飞牛 fnOS</title>'
            '<meta property="og:title" content="飞牛 fnOS">'
            '<meta property="og:site_name" content="飞牛 fnOS">'
            '<meta property="og:description" content="飞牛私有云存储系统">'
            '<meta property="og:image" content="/deskdata/fnstyle/deviceLogo.png">'
            '<link rel="icon" href="/deskdata/fnstyle/deviceLogo.png?v=1794315021351">'
            '<script>document.title="飞牛 fnOS"</script>'
            '</head><body><div id="root"></div>'
            '<script src="/assets/index-D1auxRov.js"></script>'
            '</body></html>')
    (WWW / 'index.html').write_text(html, encoding='utf-8')
    (WWW / 'assets/index-D1auxRov.js').write_text(
        'var R3=e=>{let t="host - brand";e?document.title=`Stone‘s Wish`:document.title=t};'
        'var bg="/deskdata/fnstyle/loginBackground.png?v=1794315021351";',
        encoding='utf-8')

    # 旧图（用 pic 目录默认素材）
    for n in ('loginBackground.png', 'loginLogo.png', 'deviceLogo.png',
              'webFavicon.png', 'desktopWallpaper.jpg'):
        shutil.copyfile(SRC / 'pic' / n, WWW / 'deskdata/fnstyle' / n)
    (WWW / 'deskdata/fnstyle.json').write_text(
        json.dumps({'appTitle': '旧标题'}, ensure_ascii=False), encoding='utf-8')

    # zip 还原源 + tow 工作副本（含目录条目，这是历史踩坑点）
    with zipfile.ZipFile(ZIP, 'w') as z:
        z.writestr('deskdata/', '')
        z.writestr('deskdata/fnstyle/', '')
        for p in sorted(WWW.rglob('*')):
            rel = str(p.relative_to(WWW))
            if p.is_dir():
                z.writestr(rel + '/', '')
            else:
                z.write(p, rel)
    shutil.copytree(WWW, TOW, dirs_exist_ok=True)

    # ---- 用户侧数据：一份「所有项都改了」的配置 + 5 张新图 ----
    cfg = json.loads((SRC / 'app/seed/fnstyle.json').read_text(encoding='utf-8'))
    cfg.update({
        'appTitle': 'VerifyNas2026',
        'appTitle2': 'VerifyNas2026',
        'web_desc': '验证用站点描述-DESC-2026',
        'loginOpacity': 25,
        'nasNameDisplay': 2,          # 2=隐藏登录框名字
        'fndeskLoginVW': 15,
        'fndeskLoginVH': -8,
        'Fndesk_taskbar': 0,          # 0=关闭沉底横置
        'FndeskGlass': 18,             # 18px 模糊
        'FndeskIconSort': 0,
        'fndeskClickSound': 0,
    })
    colors = ['#c62828', '#1565c0', '#2e7d32', '#ef6c00', '#6a1b9a']
    for n, c in zip(ZIP_NAMES, colors):
        make_img(SHARE / 'fnstyle' / n, c)
    (SHARE / 'fnstyle.json').write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
    return cfg


ZIP_NAMES = ('loginBackground.png', 'loginLogo.png', 'deviceLogo.png',
             'webFavicon.png', 'desktopWallpaper.jpg')


def zip_md5(name):
    try:
        with zipfile.ZipFile(ZIP) as z:
            return __import__('hashlib').md5(z.read('deskdata/fnstyle/' + name)).hexdigest()[:12]
    except KeyError:
        return 'MISSING'


def run_apply():
    env = os.environ.copy()
    env.update({
        'PATH': str(BIN) + os.pathsep + env['PATH'],
        'FNSTYLE_SANDBOX': '1',
        'FNSTYLE_INTERNAL': '1',
        'TRIM_DATA_SHARE_PATHS': str(SHARE.parent),
        'FNSTYLE_DATA_DIR': str(SHARE),
        'FNSTYLE_CFG_FILE': str(SHARE / 'fnstyle.json'),
        'REQUEST_METHOD': 'POST',
        'REQUEST_URI': '/cgi/ThirdParty/fnstyle/index.cgi/api/apply',
        'SCRIPT_NAME': '/cgi/ThirdParty/fnstyle/index.cgi',
        'PATH_INFO': '/api/apply',
        'QUERY_STRING': '',
        'CONTENT_LENGTH': '0',
        'CONTENT_TYPE': '',
        'SERVER_PROTOCOL': 'HTTP/1.1',
        'SERVER_NAME': '127.0.0.1',
        'SERVER_PORT': '8443',
        'HTTP_HOST': '127.0.0.1:8443',
        'REMOTE_ADDR': '127.0.0.1',
        'HTTPS': 'on',
    })
    r = subprocess.run(['bash', str(UI / 'index.cgi')], env=env,
                        capture_output=True, text=True, timeout=180)
    return r, env


def verify(cfg, r):
    print('=== apply 退出码: %d ===' % r.returncode)
    print('--- stdout (前 1500 字) ---')
    print(r.stdout[:1500])
    print('--- stderr (前 1500 字) ---')
    print(r.stderr[:1500])
    for name in ('debug.log', 'style_override.log', 'title_override.log',
                 'cache_bust.log'):
        f = SHARE / name
        print('--- %s ---' % name)
        if f.exists():
            print('\n'.join(f.read_text(errors='replace').splitlines()[-20:]))
        else:
            print('(不存在)')

    # ① 五张图 + fnstyle.json：三处同改（www / tow / www.zip）
    for n in ZIP_NAMES:
        src = md5(SHARE / 'fnstyle' / n)
        w = md5(WWW / 'deskdata/fnstyle' / n)
        t = md5(TOW / 'deskdata/fnstyle' / n)
        z = zip_md5(n)
        rec('图片资源', n, src == w == t == z,
            '源=%s www=%s tow=%s zip=%s' % (src, w, t, z))
    # fnstyle.json 落在 www/deskdata/fnstyle.json
    src = md5(SHARE / 'fnstyle.json')
    w = md5(WWW / 'deskdata/fnstyle.json')
    t = md5(TOW / 'deskdata/fnstyle.json')
    with zipfile.ZipFile(ZIP) as zf:
        z = __import__('hashlib').md5(zf.read('deskdata/fnstyle.json')).hexdigest()[:12]
    rec('配置', 'fnstyle.json 三处同改', src == w == t == z,
        '源=%s www=%s tow=%s zip=%s' % (src, w, t, z))
    syscfg = json.loads((WWW / 'deskdata/fnstyle.json').read_text(encoding='utf-8'))
    for k in ('appTitle', 'web_desc', 'loginOpacity', 'nasNameDisplay',
              'fndeskLoginVW', 'fndeskLoginVH', 'Fndesk_taskbar', 'FndeskGlass'):
        rec('配置', '字段写入系统 %s' % k, syscfg.get(k) == cfg.get(k),
            '期望=%r 实际=%r' % (cfg.get(k), syscfg.get(k)))

    # ② index.html：标题 / 描述 / favicon / 运行时注入
    html = (WWW / 'index.html').read_text(encoding='utf-8')
    checks = [
        ('<title>VerifyNas2026</title>', '网站标题 <title>'),
        ('content="VerifyNas2026"', '网站标题 og:title / og:site_name'),
        ('content="验证用站点描述-DESC-2026"', '网站描述 og:description'),
        ('document.title="VerifyNas2026"', '网站标题 document.title'),
        ('content="/deskdata/fnstyle/webFavicon.png"', 'favicon og:image'),
        ('href="/deskdata/fnstyle/webFavicon.png"', 'favicon link'),
        ('./fnstyle-runtime.js', '桌面运行时脚本注入'),
        ('./fnstyle-login.js', '登录页运行时脚本注入'),
    ]
    for needle, label in checks:
        rec('index.html', label, needle in html, needle)
    rec('index.html', '注入标签带内容指纹 ?v=', 'fnstyle-login.js?v=' in html,
        'URL 变化才能穿透浏览器缓存')
    rec('index.html', 'login.js 未被重复引入',
        html.count('fnstyle-login.js') == 1,
        '出现次数=%d' % html.count('fnstyle-login.js'))

    # ③ 两个运行时脚本部署到三处
    for js in ('fnstyle-runtime.js', 'fnstyle-login.js'):
        src = md5(UI / 'static/js' / js)
        w = md5(WWW / js) if (WWW / js).exists() else 'MISSING'
        t = md5(TOW / js) if (TOW / js).exists() else 'MISSING'
        try:
            with zipfile.ZipFile(ZIP) as zf:
                z = __import__('hashlib').md5(zf.read(js)).hexdigest()[:12]
        except KeyError:
            z = 'MISSING'
        rec('运行时脚本', js + ' 三处同改', src == w == t == z,
            '源=%s www=%s tow=%s zip=%s' % (src, w, t, z))

    # ④ 每个字段的消费者审计（静态，确认有代码真正读它）
    loginjs = (UI / 'static/js/fnstyle-login.js').read_text(encoding='utf-8')
    runjs = (UI / 'static/js/fnstyle-runtime.js').read_text(encoding='utf-8')
    cgi = (UI / 'index.cgi').read_text(encoding='utf-8')
    consumers = {
        'appTitle': (loginjs, 'fnstyle-login.js（MutationObserver 守标题）'),
        'web_desc': (cgi, 'index.cgi（og:description）'),
        'loginOpacity': (loginjs, 'fnstyle-login.js'),
        'nasNameDisplay': (runjs, 'fnstyle-runtime.js'),
        'fndeskLoginVW': (loginjs, 'fnstyle-login.js'),
        'fndeskLoginVH': (loginjs, 'fnstyle-login.js'),
        'Fndesk_taskbar': (runjs, 'fnstyle-runtime.js'),
        'FndeskGlass': (loginjs, 'fnstyle-login.js'),
        'loginBackground': (loginjs, 'fnstyle-login.js'),
        'loginLogo': (loginjs, 'fnstyle-login.js'),
        'deviceLogo': (loginjs, 'fnstyle-login.js'),
        'desktopWallpaper': (runjs, 'fnstyle-runtime.js'),
        'webFavicon': (cgi, 'index.cgi（link rel=icon）'),
    }
    for k, (code, who) in consumers.items():
        rec('字段消费者', k, ('cfg.' + k) in code or ('"' + k + '"') in code,
            who)

    # ⑤ 三处 index.html 一致
    ht = md5(TOW / 'index.html')
    hw = md5(WWW / 'index.html')
    rec('index.html', 'www 与 tow 内容一致', ht == hw, 'www=%s tow=%s' % (hw, ht))

    # ⑥ apply 自报结果
    tail = '\n'.join((SHARE / 'debug.log').read_text(errors='replace').splitlines()[-40:]) \
        if (SHARE / 'debug.log').exists() else ''
    rec('apply', 'CGI 退出码为 0', r.returncode == 0, 'rc=%d' % r.returncode)
    rec('apply', '内部模式完成日志', '内部模式完成' in tail, tail[-160:])


def main():
    cfg = build_sandbox()
    r, env = run_apply()
    verify(cfg, r)
    print('\n=== 逐项验收 ===')
    width = max(len(x[1]) for x in RESULTS)
    last = None
    for g, item, st, detail in RESULTS:
        if g != last:
            print('\n[%s]' % g)
            last = g
        print('  %-4s %-*s  %s' % (st, width, item, detail))
    npass = sum(1 for x in RESULTS if x[2] == 'PASS')
    print('\n=== 合计: %d/%d 通过 ===' % (npass, len(RESULTS)))
    (S / 'verify_result.json').write_text(
        json.dumps([{'group': g, 'item': i, 'status': s, 'detail': d}
                    for g, i, s, d in RESULTS], ensure_ascii=False, indent=2),
        encoding='utf-8')


if __name__ == '__main__':
    main()
