# -*- coding: utf-8 -*-
"""
验证「还原后自禁用」：把 deskdata/fnstyle.json 换成还原脚本写的空配置，
用真实浏览器加载真实的 fnstyle-runtime.js / fnstyle-login.js，确认
  · 不会注入自定义壁纸 / 样式
  · 已打开的页面最多自动刷新一次（不会死循环）
  · 无 JS 异常
这是「先显示系统原生样式，随后又跳回软件配置样式」的浏览器侧回归防线。
"""
import functools
import http.server
import json
import shutil
import socketserver
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path('/Volumes/SSD-Data/trae/FnOsApp')
WWW = Path('/tmp/fnstyle_disabled_probe')
CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
NEUTRAL = {"__fnstyleDisabled": True}

DOM = """
<div class="fixed inset-y-0 left-0 z-[1]" style="width:66px">
 <div class="box-border size-full"><div class="h-full rounded-md">
  <div class="box-border flex h-full flex-col">
   <div class="flex flex-col items-center"></div>
   <div class="relative flex flex-1 flex-col"><div class="scrollbar-hidden absolute inset-0"></div></div>
   <div class="flex flex-col items-center justify-between"></div>
  </div></div></div>
</div>
<div class="desktop"><div class="gradient-border">
 <img alt="logo" src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='64' height='64'%3E%3C/svg%3E">
</div></div>
"""
# ⚠️ 必须声明 charset：fnOS 真机 index.html 里有 <meta charset="UTF-8">。
# 缺了它 Chrome 会按单字节编码解码外链 JS，把 /[,，]/ 里的中文逗号
# 解坏成「Invalid regular expression: missing /」——那是测试页的问题，不是产品缺陷。
HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8">
<title>飞牛 fnOS</title>
<script src="./fnstyle-runtime.js"></script>
<script src="./fnstyle-login.js"></script>
</head><body><div id="root"></div>%s</body></html>""" % DOM

results = []


def rec(item, ok, detail=''):
    results.append({'item': item, 'status': 'PASS' if ok else 'FAIL',
                    'detail': str(detail)[:200]})
    print('  %s %-48s %s' % ('✓' if ok else '✗', item,
                             detail if not ok else ''))


def build():
    if WWW.exists():
        shutil.rmtree(str(WWW))
    (WWW / 'deskdata').mkdir(parents=True)
    (WWW / 'index.html').write_text(HTML, encoding='utf-8')
    (WWW / 'deskdata/fnstyle.json').write_text(
        json.dumps(NEUTRAL), encoding='utf-8')
    for name in ('fnstyle-runtime.js', 'fnstyle-login.js'):
        shutil.copy2(str(ROOT / 'app/ui/static/js' / name), str(WWW / name))


def main():
    build()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=str(WWW))
    httpd = socketserver.TCPServer(('127.0.0.1', 0), handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    url = 'http://127.0.0.1:%d/index.html' % port

    errors, navigations = [], []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=CHROME, headless=True,
                                         args=['--no-sandbox'])
            page = browser.new_page()
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.on('framenavigated', lambda f: navigations.append(f.url)
                    if f == page.main_frame else None)
            page.goto(url, wait_until='domcontentloaded')
            page.wait_for_timeout(1800)
            state = page.evaluate("""() => ({
              wallpaper: !!document.getElementById('fnstyle_wallpaper'),
              bg: !!document.getElementById('fnstyle-login-bg'),
              styles: document.querySelectorAll('style[id^="fnstyle"]').length,
              flag: !!window.__fnstyleDisabled,
              title: document.title
            })""")
            rec('空配置下不注入自定义壁纸', not state['wallpaper'], state)
            rec('空配置下不注入登录背景层', not state['bg'], state)
            rec('空配置下无注入样式表', state['styles'] == 0, state)
            rec('运行时已进入禁用态', state['flag'] is True, state)
            rec('页面标题保持系统原生', state['title'] == '飞牛 fnOS', state)
            rec('自动刷新不超过 2 次（无死循环）', len(navigations) <= 2,
                navigations)
            rec('无 JS 异常', not errors, errors)
            browser.close()
    finally:
        httpd.shutdown()
        httpd.server_close()

    passed = sum(1 for x in results if x['status'] == 'PASS')
    print('\n===== 汇总：%d/%d PASS =====' % (passed, len(results)))
    (ROOT / 'verify/result_disabled_runtime.json').write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if passed == len(results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
