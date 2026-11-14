#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FnStyle「配置是否真的呈现」浏览器验证

serve 仿真 apply 之后的系统 www/ 目录，用真实 index.html（已由 apply 改写），
加载真实的 fnstyle-login.js / fnstyle-runtime.js，注入与 fnOS 同构的最小 DOM，
再逐项读取 DOM 结果，确认每项配置确实兑现。
"""
import functools
import http.server
import json
import socketserver
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

WWW = Path('/tmp/fnstyle_sandbox/www')
CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
BASE = json.loads((Path('/tmp/fnstyle_sandbox/appshare/fnstyle/deskdata/fnstyle.json')
                   .read_text(encoding='utf-8')))
IMG_DATA = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' "
            "width='64' height='64'%3E%3Crect width='64' height='64' "
            "fill='%23cccccc'/%3E%3C/svg%3E")

CFG_ON = dict(BASE, **{
    'appTitle': 'VerifyNas2026', 'loginOpacity': 25, 'nasNameDisplay': 2,
    'fndeskLoginVW': 15, 'fndeskLoginVH': -8, 'Fndesk_taskbar': 1,
    'FndeskGlass': 18,
})
CFG_OFF = dict(BASE, **{
    'appTitle': '飞牛 fnOS', 'loginOpacity': 10, 'nasNameDisplay': 1,
    'fndeskLoginVW': 0, 'fndeskLoginVH': 0, 'Fndesk_taskbar': 0,
    'FndeskGlass': -1,
})

# 静态 DOM（在 HTML 解析阶段就存在，用于区分「时序问题」与「选择器问题」）
DOM_STATIC = """
<div class="fixed inset-y-0 left-0 z-[1]" style="width:66px">
 <div class="box-border size-full"><div class="h-full rounded-md">
  <div class="box-border flex h-full flex-col">
   <div class="flex flex-col items-center"></div>
   <div class="relative flex flex-1 flex-col"><div class="scrollbar-hidden absolute inset-0"></div></div>
   <div class="flex flex-col items-center justify-between"></div>
  </div></div></div>
</div>
<div class="desktop"><div class="gradient-border">
 <img id="probe-login-logo" alt="logo" src="IMG_DATA">
 <p id="probe-nas-name" class="select-none">MyNas</p>
</div></div>
<div class="device-card"><img id="probe-device-logo" alt="logo" src="IMG_DATA"></div>
""".replace('IMG_DATA', IMG_DATA)

MOCK_DOM = r"""
var IMG = __IMG__;
var root = document.createElement('div');
root.className = 'fixed inset-y-0 left-0 z-[1]'; root.style.cssText = 'width:66px';
var l1 = document.createElement('div'); l1.className = 'box-border size-full';
var l2 = document.createElement('div'); l2.className = 'h-full rounded-md';
var l3 = document.createElement('div'); l3.className = 'box-border flex h-full flex-col';
var c0 = document.createElement('div'); c0.className = 'flex flex-col items-center';
var c1 = document.createElement('div'); c1.className = 'relative flex flex-1 flex-col';
var c1i = document.createElement('div'); c1i.className = 'scrollbar-hidden absolute inset-0';
c1.appendChild(c1i);
var c2 = document.createElement('div'); c2.className = 'flex flex-col items-center justify-between';
l3.appendChild(c0); l3.appendChild(c1); l3.appendChild(c2);
l2.appendChild(l3); l1.appendChild(l2); root.appendChild(l1);
var desk = document.createElement('div'); desk.className = 'desktop';
var card = document.createElement('div'); card.className = 'gradient-border';
var logo = document.createElement('img'); logo.alt = 'logo'; logo.src = IMG; logo.id = 'probe-login-logo';
var nasname = document.createElement('p'); nasname.className = 'select-none';
nasname.textContent = 'MyNas'; nasname.id = 'probe-nas-name';
card.appendChild(logo); card.appendChild(nasname); desk.appendChild(card);
var dev = document.createElement('div'); dev.className = 'device-card';
var devlogo = document.createElement('img'); devlogo.alt = 'logo'; devlogo.src = IMG;
devlogo.id = 'probe-device-logo'; dev.appendChild(devlogo);
var body = document.body;
if (!body.querySelector('#probe-login-logo')) {
  body.appendChild(root); body.appendChild(desk); body.appendChild(dev);
}
""".replace('__IMG__', json.dumps(IMG_DATA))

PROBE = r"""
() => {
  var q = function (s) { return document.querySelector(s); };
  var css = q('#fnstyle_login_card_css');
  var bg = q('#fnstyle_login_bg_css');
  var wp = q('#fnstyle_wallpaper');
  var wpimg = wp && wp.querySelector('img,video');
  var devimg = q('#probe-device-logo');
  var logo = q('#probe-login-logo');
  var tb = q('[data-fnstyle-tb-root]') || q('[data-fnstyle-tb]');
  var styles = Array.prototype.slice.call(document.querySelectorAll('style'))
      .map(function (s) { return (s.id || '') + '|' + (s.textContent || '').slice(0, 400); });
  return {
    title: document.title,
    favicon: (q('link[rel=icon]') || {}).href || '',
    logo: logo ? logo.getAttribute('src') : '',
    deviceLogo: devimg ? devimg.getAttribute('src') : '',
    bgCss: bg ? bg.textContent : '',
    bgDiv: !!q('#fnstyle-login-bg'),
    cardCss: css ? css.textContent : '',
    wallpaper: wpimg ? wpimg.getAttribute('src') : '',
    nasNameNodes: document.querySelectorAll('p.select-none').length,
    taskbarMarked: !!tb,
    taskbarStyles: styles.filter(function (x) { return /fnstyle_taskbar|flex-direction/i.test(x); })
  };
}
"""


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def serve():
    handler = functools.partial(Quiet, directory=str(WWW))
    srv = socketserver.TCPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def run(browser, url, cfg, label, results, dom_ready=False):
    page = browser.new_page(viewport={'width': 1440, 'height': 900})
    errors, bad = [], []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.on('console', lambda m: errors.append('console.error: ' + m.text)
            if m.type == 'error' else None)
    page.on('response', lambda r: bad.append('%d %s' % (r.status, r.url))
            if r.status >= 400 else None)
    payload = json.dumps(cfg, ensure_ascii=False).encode('utf-8')
    page.route('**/deskdata/fnstyle.json*', lambda route: route.fulfill(
        status=200, content_type='application/json', body=payload))
    page.goto(url, wait_until='domcontentloaded')
    if not dom_ready:
        page.evaluate(MOCK_DOM)
    page.evaluate("document.title = 'Stone‘s Wish'")  # 模拟原生 SPA 覆盖标题
    page.wait_for_timeout(3500)
    d = page.evaluate(PROBE)
    page.close()

    def rec(item, ok, detail=''):
        results.append((label, item, 'PASS' if ok else 'FAIL', detail))

    print('\n===== %s =====' % label)
    print(json.dumps(d, ensure_ascii=False, indent=2)[:2200])

    if cfg['appTitle'] != '飞牛 fnOS':
        rec('网站标题（原生覆盖后纠正）', d['title'] == cfg['appTitle'], d['title'])
    else:
        rec('网站标题（原生默认不接管）', d['title'] == 'Stone‘s Wish',
            'appTitle=飞牛 fnOS ⇒ 运行时不接管标题，属设计行为；title=%s' % d['title'])
    rec('网页 favicon', 'webFavicon.png' in d['favicon'], d['favicon'])
    rec('登录页 LOGO', 'loginLogo.png?v=' in (d['logo'] or ''), d['logo'])
    rec('登录页背景', (cfg['loginBackground'] or '') in (d['bgCss'] or '') and d['bgDiv'],
        (d['bgCss'] or '')[:120])
    rec('设备 LOGO', 'deviceLogo.png?v=' in (d['deviceLogo'] or ''), d['deviceLogo'])
    rec('桌面壁纸', 'desktopWallpaper.jpg?v=' in (d['wallpaper'] or ''), d['wallpaper'])
    card = d['cardCss'] or ''
    rec('登录框透明度',
        ('rgba(255,255,255,0.2500)' in card) if cfg['loginOpacity'] == 25 else not card,
        card[:120])
    rec('窗口毛玻璃',
        ('blur(18px)' in card) if cfg['FndeskGlass'] != -1 else not card, card[:120])
    rec('登录框水平位置',
        ('margin-left:15vw' in card) if cfg['fndeskLoginVW'] else not card, card[:120])
    rec('登录框垂直位置',
        ('margin-top:-8vh' in card) if cfg['fndeskLoginVH'] else not card, card[:120])
    rec('登录框名字显示/隐藏',
        d['nasNameNodes'] == 0 if cfg['nasNameDisplay'] == 2 else d['nasNameNodes'] == 1,
        'p.select-none 剩余=%d' % d['nasNameNodes'])
    rec('任务栏沉底横置',
        d['taskbarMarked'] if cfg['Fndesk_taskbar'] == 1 else not d['taskbarMarked'],
        'data 标记=%s taskbar 样式数=%d' % (d['taskbarMarked'], len(d['taskbarStyles'])))
    # 只把「应用自身资源」404 当错误；模拟页面不存在的静态图不计
    app404 = [x for x in bad if '/deskdata/' in x or 'fnstyle-' in x]
    rec('应用资源无 4xx', not app404, ','.join(app404[:3]) or ('页面其它404=%d（模拟素材）' % len(bad)))
    rec('无 JS 异常', not errors, '; '.join(errors[:2]) or '无')


def main():
    # 造一份「DOM 在解析阶段就存在」的页面，用于区分时序问题
    idx = (WWW / 'index.html').read_text(encoding='utf-8')
    (WWW / 'verify_dom.html').write_text(idx.replace('</body>', DOM_STATIC + '</body>'),
                                         encoding='utf-8')
    srv, port = serve()
    base = 'http://127.0.0.1:%d/' % port
    print('serve', base)
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=CHROME, headless=True,
                                     args=['--no-sandbox'])
        run(browser, base + 'verify_dom.html', CFG_ON, '轮0 DOM 先于脚本存在', results, True)
        run(browser, base + 'index.html', CFG_ON, '轮1 全部开启/非默认值', results)
        run(browser, base + 'index.html', CFG_OFF, '轮2 全部关闭/恢复原生', results)
        browser.close()
    srv.shutdown()

    print('\n=== 浏览器逐项验收 ===')
    last = None
    for g, item, st, detail in results:
        if g != last:
            print('\n[%s]' % g)
            last = g
        print('  %-4s %-28s %s' % (st, item, detail[:120]))
    n = sum(1 for r in results if r[2] == 'PASS')
    print('\n=== 合计: %d/%d 通过 ===' % (n, len(results)))
    Path('/tmp/fnstyle_sandbox/browser_result.json').write_text(
        json.dumps([{'round': g, 'item': i, 'status': s, 'detail': d}
                    for g, i, s, d in results], ensure_ascii=False, indent=2),
        encoding='utf-8')


if __name__ == '__main__':
    main()
