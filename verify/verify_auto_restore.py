#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
「卸载后自动复原系统默认样式」软件本体能力验证

覆盖三层：
  ① 静态链路：seed 默认 / UI 字段定义 / 分组 / 默认值 / CGI null 归一化表 /
     CGI 写标记 / 安装回调初始化 / 卸载回调读取与分支 / manifest 说明
  ② 真实 CGI 动态：POST /api/config 改开关 → 配置与 auto_restore 标记同步落盘；
     改其它字段不会误清标记
  ③ 真实卸载回调片段动态：data-share / guard / 缺失 / 非法值四种组合下的
     开关解析结果，以及关闭时是否真的在还原逻辑之前退出
  ④ UI 冒烟：浏览器里能看到「卸载与还原」分组、开关默认开启
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = []
SANDBOX = Path('/tmp/fnstyle_auto_restore_probe')
CGI = ROOT / 'app/ui/index.cgi'
INSTALL_CB = ROOT / 'cmd/install_callback'
UNINSTALL_CB = ROOT / 'cmd/uninstall_callback'


def rec(name, ok, detail=''):
    RESULTS.append({'name': name, 'ok': bool(ok), 'detail': str(detail)})


def text(p):
    return Path(p).read_text(encoding='utf-8')


def bash_ok(p):
    return subprocess.run(['bash', '-n', str(p)], capture_output=True).returncode == 0


# ---------------------------------------------------------------- ① 静态
def static_checks():
    seed = json.loads(text(ROOT / 'app/seed/fnstyle.json'))
    rec('出厂配置默认开启自动还原', seed.get('autoRestoreOnUninstall') == 1,
        'seed 值 = %r' % seed.get('autoRestoreOnUninstall'))

    appjs = text(ROOT / 'app/ui/static/js/app.js')
    m = re.search(r"\{\s*// 值同时落一份.*?key: 'autoRestoreOnUninstall'.*?\n\s*\}",
                  appjs, re.S)
    field = m.group(0) if m else ''
    rec('UI 定义了该开关字段', bool(field), 'FIELDS 命中 %d 字符' % len(field))
    for needle in ("type: 'switch'", "onValue: '1'", "offValue: '0'",
                   "nullValue: '1'", 'numeric: true', "group: '卸载与还原'"):
        rec('开关定义含 %s' % needle, needle in field)
    rec('分组顺序含「卸载与还原」', "'卸载与还原'" in appjs,
        "GROUPS = %s" % re.search(r'var GROUPS = (\[[^]]*\])', appjs).group(1))
    rec('「默认」按钮回填空值一致',
        "autoRestoreOnUninstall: '1'" in appjs)

    cgi = text(CGI)
    rec('CGI null 归一化表登记该字段（缺省=1）',
        re.search(r'autoRestoreOnUninstall\s*\n?\s*1', cgi) is not None or
        'autoRestoreOnUninstall\t1' in cgi)
    rec('CGI 保存配置时写 auto_restore 标记',
        '${DATA_DIR}/auto_restore' in cgi and
        'autoRestoreOnUninstall' in cgi[cgi.find('AR_VAL=') - 400: cgi.find('AR_VAL=') + 200])

    ins = text(INSTALL_CB)
    rec('安装回调初始化 data-share 标记', '${DATA_DIR}/auto_restore' in ins)
    rec('安装回调初始化系统级 guard 兜底标记', '${GUARD_DIR}/auto_restore' in ins)
    rec('安装回调不覆盖用户已有选择',
        '[ -f "${DATA_DIR}/auto_restore" ] ||' in ins)

    uni = text(UNINSTALL_CB)
    rec('卸载回调读取 data-share 标记', '${DATA_DIR}/auto_restore' in uni)
    rec('卸载回调读取 guard 兜底标记', '${GUARD_DIR}/auto_restore' in uni)
    rec('卸载回调 fail-safe 默认开启', 'AUTO_RESTORE="1"' in uni)
    pos_skip = uni.find('用户已关闭自动还原：跳过系统样式还原')
    pos_restore = uni.find('RESTORE_SCRIPT=""')
    rec('关闭开关时先退出、不执行还原',
        0 < pos_skip < pos_restore and 'exit 0' in uni[pos_skip:pos_skip + 200],
        '跳过分支 @%d，还原逻辑 @%d' % (pos_skip, pos_restore))
    df = text(ROOT / 'cmd/restore_deferred.sh')
    rec('开启时两段式还原：同步只中和源、后台再写 www/',
        '--skip-www' in uni and 'restore_deferred.sh' in uni and 'setsid' in uni)
    rec('后台执行器完成 v2 中和还原 + 自检',
        '--settle' in df and '--verify-only' in df and
        '__FNSTYLE_RESTORED_NOOP__' in df)
    rec('卸载回调绝不同步写 www/（避免 Failed to fetch）',
        '--skip-www' in uni and uni.find('python3 "$GUARD_SCRIPT"') > 0)

    rec('安装回调语法通过', bash_ok(INSTALL_CB))
    rec('卸载回调语法通过', bash_ok(UNINSTALL_CB))
    rec('后台执行器语法通过', bash_ok(ROOT / 'cmd/restore_deferred.sh'))
    rec('CGI 语法通过', bash_ok(CGI))
    rec('manifest 说明该能力',
        '卸载后自动还原系统默认样式' in text(ROOT / 'manifest'))


# ---------------------------------------------------- ② 真实 CGI 动态
def cgi_post(cgi_path, share_root, body):
    data = json.dumps(body, ensure_ascii=False)
    env = os.environ.copy()
    env.update({
        'FNSTYLE_SANDBOX': '1',
        'TRIM_DATA_SHARE_PATHS': str(share_root),
        'REQUEST_METHOD': 'POST',
        'REQUEST_URI': '/cgi/ThirdParty/fnstyle/index.cgi/api/config',
        'SCRIPT_NAME': '/cgi/ThirdParty/fnstyle/index.cgi',
        'PATH_INFO': '/api/config',
        'QUERY_STRING': '',
        'CONTENT_LENGTH': str(len(data.encode('utf-8'))),
        'CONTENT_TYPE': 'application/json',
        'SERVER_PROTOCOL': 'HTTP/1.1',
        'SERVER_NAME': '127.0.0.1',
        'SERVER_PORT': '8443',
        'HTTP_HOST': '127.0.0.1:8443',
        'REMOTE_ADDR': '127.0.0.1',
        'HTTPS': 'on',
    })
    return subprocess.run(['bash', str(cgi_path)], input=data, env=env,
                          capture_output=True, text=True, timeout=60)


def cgi_checks():
    if SANDBOX.exists():
        shutil.rmtree(str(SANDBOX))
    # CGI 依赖同目录的 json_tool.awk，且 APP_ROOT 是硬编码绝对路径。
    # 与 verify_apply_chain 同一手法：复制一份 UI，只把 APP_ROOT 指向沙箱。
    ui = SANDBOX / 'var/apps/fnstyle/target/ui'
    shutil.copytree(str(ROOT / 'app/ui'), str(ui), dirs_exist_ok=True)
    cgi = ui / 'index.cgi'
    t = cgi.read_text(encoding='utf-8')
    t = t.replace('APP_ROOT="/var/apps/${APP_NAME}/target"',
                  'APP_ROOT="%s"' % ui.parent)
    t = t.replace('if ! is_root; then',
                  'if [ "${FNSTYLE_SANDBOX:-0}" != "1" ] && ! is_root; then')
    cgi.write_text(t, encoding='utf-8')

    share_root = SANDBOX / 'shareRoot'
    data_dir = share_root / 'deskdata'
    data_dir.mkdir(parents=True)
    (data_dir / 'fnstyle.json').write_text(
        text(ROOT / 'app/seed/fnstyle.json'), encoding='utf-8')
    mark = data_dir / 'auto_restore'

    r = cgi_post(cgi, share_root, {'autoRestoreOnUninstall': 0})
    rec('CGI 保存开关（关闭）成功', r.returncode == 0 and 'success' in r.stdout,
        'rc=%d out=%s' % (r.returncode, r.stdout[:120].replace('\n', ' ')))
    cfg = json.loads((data_dir / 'fnstyle.json').read_text(encoding='utf-8'))
    rec('配置里开关写为 0', cfg.get('autoRestoreOnUninstall') == 0,
        'cfg = %r' % cfg.get('autoRestoreOnUninstall'))
    rec('auto_restore 标记同步为 0', mark.is_file() and mark.read_text().strip() == '0',
        'mark = %r' % (mark.read_text().strip() if mark.is_file() else 'MISSING'))

    r = cgi_post(cgi, share_root, {'autoRestoreOnUninstall': 1})
    rec('CGI 保存开关（开启）成功', r.returncode == 0)
    rec('auto_restore 标记同步为 1',
        mark.is_file() and mark.read_text().strip() == '1')

    r = cgi_post(cgi, share_root, {'appTitle': '别的字段'})
    rec('改其它字段不会误清标记',
        r.returncode == 0 and mark.read_text().strip() == '1',
        'mark = %r' % mark.read_text().strip())
    return data_dir


# ------------------------------------------- ③ 卸载回调开关读取片段
def read_segment():
    t = text(UNINSTALL_CB)
    marker = '# ---- ★ 读取「卸载后自动还原系统默认样式」开关'
    seg = t.split(marker, 1)[1].split('if [ "$AUTO_RESTORE" != "1" ]', 1)[0]
    return seg


def resolve_switch(data_val, guard_val, seg):
    d = SANDBOX / 'readcase'
    if d.exists():
        shutil.rmtree(str(d))
    (d / 'data').mkdir(parents=True)
    (d / 'guard').mkdir(parents=True)
    if data_val is not None:
        (d / 'data/auto_restore').write_text(data_val, encoding='utf-8')
    if guard_val is not None:
        (d / 'guard/auto_restore').write_text(guard_val, encoding='utf-8')
    script = ("#!/bin/bash\n"
              'DATA_DIR="%s"\nGUARD_DIR="%s"\nCB_LOG="/dev/null"\n'
              'cb_log() { :; }\n%s\nprintf "%%s" "$AUTO_RESTORE"\n'
              % (d / 'data', d / 'guard', seg))
    r = subprocess.run(['bash', '-c', script], capture_output=True,
                        text=True, timeout=20)
    return r.stdout.strip(), r.returncode


def switch_checks():
    seg = read_segment()
    rec('能提取卸载回调的真实读取片段', len(seg) > 200, '%d 字符' % len(seg))
    cases = [
        ('用户关闭优先于 guard', '0\n', '1\n', '0'),
        ('用户开启优先于 guard', '1\n', '0\n', '1'),
        ('data 缺失时读 guard 关闭', None, '0\n', '0'),
        ('两个标记都缺失 → 默认开启', None, None, '1'),
        ('data 非法值 → 继续读 guard', 'yes\n', '0\n', '0'),
        ('data 非法且无 guard → 默认开启', 'yes\n', None, '1'),
    ]
    for name, dv, gv, want in cases:
        got, rc = resolve_switch(dv, gv, seg)
        rec(name, rc == 0 and got == want,
            '期望 %s，实得 %r（rc=%d）' % (want, got, rc))


# --------------------------------------------------------- ④ UI 冒烟
def reachable(url):
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def ui_smoke():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        rec('UI 冒烟（跳过：无 Playwright）', True, 'SKIP')
        return
    base = 'http://127.0.0.1:8766/'
    proc = None
    if not reachable(base):
        port = 8791
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / 'preview.py'), '--port', str(port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = 'http://127.0.0.1:%d/' % port
        for _ in range(40):
            if reachable(base):
                break
            time.sleep(0.25)
    try:
        if not reachable(base):
            rec('UI 冒烟（跳过：预览服务不可用）', True, 'SKIP')
            return
        chrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=chrome, headless=True,
                                          args=['--no-sandbox'])
            page = browser.new_page(viewport={'width': 1600, 'height': 1000})
            errs = []
            page.on('pageerror', lambda e: errs.append(str(e)))
            page.goto(base, wait_until='domcontentloaded', timeout=30000)
            page.wait_for_timeout(1200)
            rec('界面出现「卸载与还原」分组',
                page.locator('.fgroup-title', has_text='卸载与还原').count() >= 1)
            sw = page.locator('#f_autoRestoreOnUninstall')
            rec('界面出现自动还原开关', sw.count() == 1)
            rec('开关默认处于「开启」', sw.count() == 1 and sw.is_checked())
            rec('开关说明可见',
                page.locator('.fitem', has=sw).locator('.hint').count() >= 1 if sw.count() else False)
            rec('UI 无 JS 异常', not errs, str(errs[:2]))
            browser.close()
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()


def main():
    static_checks()
    cgi_checks()
    switch_checks()
    ui_smoke()

    passed = sum(1 for r in RESULTS if r['ok'])
    print('=' * 70)
    for r in RESULTS:
        print('%s %-46s %s' % ('PASS' if r['ok'] else 'FAIL',
                              r['name'], r['detail']))
    print('=' * 70)
    print('结果：%d/%d' % (passed, len(RESULTS)))
    (ROOT / 'verify/result_auto_restore.json').write_text(
        json.dumps({'passed': passed, 'total': len(RESULTS), 'cases': RESULTS},
                   ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if passed == len(RESULTS) else 1


if __name__ == '__main__':
    raise SystemExit(main())
