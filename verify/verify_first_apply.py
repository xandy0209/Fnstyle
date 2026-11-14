#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
「全新安装 → 第一次点『应用到系统』」专项仿真

与 verify_apply_chain.py 的差别：那个脚本模拟的是「系统里已经有一套旧图、
用户改了配置再应用」；本脚本模拟的是**刚装完、系统侧一张图都没有**的状态：

  - www/          没有 deskdata/fnstyle/ 目录
  - www.zip       没有 deskdata/ 与 deskdata/fnstyle/ 目录条目
  - tow/          同 www/
  - appshare      只有 install_callback 刚播种的 seed 默认资源（5 张图 + fnstyle.json）

目的：复现「第一次应用时部分图片失败/跳过」这类反馈，确认首次应用能否
一次性把全部资源落到 www / tow / www.zip 三处。
"""
import hashlib
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

SRC = Path('/Volumes/SSD-Data/trae/FnOsApp')
S = Path('/tmp/fnstyle_firstapply')
UI = S / 'var/apps/fnstyle/target/ui'
WWW = S / 'www'
RESTORE = S / '.restore'
TOW = RESTORE / 'tow'
ZIP = RESTORE / 'www.zip'
SHARE = S / 'appshare/fnstyle/deskdata'
BIN = S / 'bin'

SEED_IMAGES = ('loginBackground.png', 'loginLogo.png', 'deviceLogo.png',
              'webFavicon.png', 'desktopWallpaper.jpg')


def md5(p):
    return hashlib.md5(Path(p).read_bytes()).hexdigest()[:12]


def build():
    if S.exists():
        shutil.rmtree(S)
    # ⚠️ 故意不创建 WWW/deskdata/fnstyle —— 这就是「首次安装」的关键差异
    for d in (UI.parent, WWW / 'assets', RESTORE, TOW, SHARE / 'fnstyle',
              SHARE / 'upload', SHARE / 'lib',
              S / 'appshare/fndesk/deskdata', BIN,
              S / 'var/apps/fnstyle/shares'):
        d.mkdir(parents=True, exist_ok=True)

    shutil.copytree(SRC / 'app/ui', UI, dirs_exist_ok=True)
    repl = [
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
        if p.suffix not in ('.cgi', '.py', '.sh') and p.name != 'config':
            continue
        try:
            t = p.read_text(encoding='utf-8')
        except Exception:
            continue
        orig = t
        for a, b in repl:
            t = t.replace(a, b)
        t = t.replace('if ! is_root; then',
                      'if [ "${FNSTYLE_SANDBOX:-0}" != "1" ] && ! is_root; then')
        if t != orig:
            p.write_text(t, encoding='utf-8')

    (BIN / 'md5sum').write_text(
        '#!/bin/sh\nfor f in "$@"; do md5 -q "$f" | tr -d "\\n"; printf "  %s\\n" "$f"; done\n')
    (BIN / 'md5sum').chmod(0o755)

    # ---- fnOS 原生系统 www/：只有框架文件，没有任何个性化图片 ----
    (WWW / 'index.html').write_text(
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<title>飞牛 fnOS</title>'
        '<meta property="og:title" content="飞牛 fnOS">'
        '<meta property="og:site_name" content="飞牛 fnOS">'
        '<meta property="og:description" content="飞牛私有云存储系统">'
        '<link rel="icon" href="/favicon.ico">'
        '</head><body><div id="root"></div>'
        '<script src="/assets/index-D1auxRov.js"></script>'
        '</body></html>', encoding='utf-8')
    (WWW / 'assets/index-D1auxRov.js').write_text(
        'var R3=e=>{let t="host - brand";e?document.title=`Stone‘s Wish`:document.title=t};',
        encoding='utf-8')

    # ---- 还原源 www.zip：同样没有 deskdata 目录条目 ----
    with zipfile.ZipFile(ZIP, 'w') as z:
        z.write(WWW / 'index.html', 'index.html')
        z.write(WWW / 'assets/index-D1auxRov.js', 'assets/index-D1auxRov.js')
    shutil.copytree(WWW, TOW, dirs_exist_ok=True)

    # ---- install_callback 刚播种完的应用数据：原封不动的 seed ----
    shutil.copyfile(SRC / 'app/seed/fnstyle.json', SHARE / 'fnstyle.json')
    for n in SEED_IMAGES:
        shutil.copyfile(SRC / 'app/seed/fnstyle' / n, SHARE / 'fnstyle' / n)
    return None


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
        'QUERY_STRING': '', 'CONTENT_LENGTH': '0', 'CONTENT_TYPE': '',
        'SERVER_PROTOCOL': 'HTTP/1.1', 'SERVER_NAME': '127.0.0.1',
        'SERVER_PORT': '8443', 'HTTP_HOST': '127.0.0.1:8443',
        'REMOTE_ADDR': '127.0.0.1', 'HTTPS': 'on',
    })
    return subprocess.run(['bash', str(UI / 'index.cgi')], env=env,
                           capture_output=True, text=True, timeout=180)


def main():
    build()
    r = run_apply()
    print('=== CGI 退出码: %d ===' % r.returncode)
    if r.stderr.strip():
        print('--- stderr（apply 逐项明细） ---')
        print(r.stderr.strip()[-2500:])

    log = (SHARE / 'debug.log')
    tail = '\n'.join(log.read_text(errors='replace').splitlines()[-25:]) if log.exists() else '(无 debug.log)'
    print('--- debug.log 末尾 ---')
    print(tail)

    stat = '未找到统计行'
    for line in tail.splitlines():
        if '内部模式完成' in line:
            stat = line
    print('\n=== 首次应用统计: %s ===' % stat)

    results = []
    skipped = 0
    for line in (r.stderr or '').splitlines():
        if '(源不存在)' in line or '(失败)' in line:
            skipped += 1
    results.append(('逐项明细无跳过/失败行', skipped == 0, '命中 %d 行' % skipped))

    # 5 张图 + fnstyle.json：三处同改
    for n in SEED_IMAGES:
        src = md5(SHARE / 'fnstyle' / n)
        w = md5(WWW / 'deskdata/fnstyle' / n) if (WWW / 'deskdata/fnstyle' / n).exists() else 'MISSING'
        t = md5(TOW / 'deskdata/fnstyle' / n) if (TOW / 'deskdata/fnstyle' / n).exists() else 'MISSING'
        try:
            with zipfile.ZipFile(ZIP) as z:
                zmd5 = hashlib.md5(z.read('deskdata/fnstyle/' + n)).hexdigest()[:12]
        except KeyError:
            zmd5 = 'MISSING'
        results.append(('%s 三处同改' % n, src == w == t == zmd5,
                        '源=%s www=%s tow=%s zip=%s' % (src, w, t, zmd5)))

    cfg_src = md5(SHARE / 'fnstyle.json')
    cfg_w = md5(WWW / 'deskdata/fnstyle.json') if (WWW / 'deskdata/fnstyle.json').exists() else 'MISSING'
    try:
        with zipfile.ZipFile(ZIP) as z:
            cfg_z = hashlib.md5(z.read('deskdata/fnstyle.json')).hexdigest()[:12]
    except KeyError:
        cfg_z = 'MISSING'
    results.append(('fnstyle.json 落到 www 与 zip', cfg_src == cfg_w == cfg_z,
                    '源=%s www=%s zip=%s' % (cfg_src, cfg_w, cfg_z)))

    with zipfile.ZipFile(ZIP) as z:
        names = z.namelist()
    results.append(('www.zip 含 deskdata/ 目录条目', 'deskdata/' in names,
                    'deskdata/ 存在' if 'deskdata/' in names else '缺失'))
    results.append(('www.zip 含 deskdata/fnstyle/ 目录条目', 'deskdata/fnstyle/' in names,
                    'deskdata/fnstyle/ 存在' if 'deskdata/fnstyle/' in names else '缺失'))

    print('\n=== 逐项验收 ===')
    for item, ok, detail in results:
        print('  %-4s %-38s %s' % ('PASS' if ok else 'FAIL', item, detail))
    passed = sum(1 for _, ok, _ in results if ok)
    print('\n=== 合计: %d/%d 通过 ===' % (passed, len(results)))

    (S / 'result.json').write_text(json.dumps({
        'scenario': '全新安装后第一次应用到系统',
        'apply_log': stat,
        'checks': [{'item': i, 'status': 'PASS' if ok else 'FAIL', 'detail': d}
                   for i, ok, d in results],
        'passed': passed, 'total': len(results),
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if passed == len(results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
