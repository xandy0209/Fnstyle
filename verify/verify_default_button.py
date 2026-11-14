# -*- coding: utf-8 -*-
"""实测「图片与壁纸」5 个「默认」按钮：先把字段改成外链，再点默认，
核对输入框值、缩略图是否真能加载、后端配置是否落盘。"""
import json
import urllib.request
from playwright.sync_api import sync_playwright

B = 'http://127.0.0.1:8766/'
CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
EXPECT = {
    'loginBackground': '/deskdata/fnstyle/loginBackground.png',
    'loginLogo': '/deskdata/fnstyle/loginLogo.png',
    'deviceLogo': '/deskdata/fnstyle/deviceLogo.png',
    'webFavicon': '/deskdata/fnstyle/webFavicon.png',
    'desktopWallpaper': '/deskdata/fnstyle/desktopWallpaper.jpg',
}
FAKE = 'https://fndesk.imcq.top/images/theme/原神_t2.jpeg'


def set_cfg(patch):
    req = urllib.request.Request(
        B + 'api/config', data=json.dumps(patch).encode('utf-8'),
        headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode('utf-8'))


def get_cfg():
    with urllib.request.urlopen(B + 'api/config', timeout=10) as r:
        return json.loads(r.read().decode('utf-8'))


results = []
with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path=CHROME, headless=True,
                                 args=['--no-sandbox', '--disable-dev-shm-usage'])
    page = browser.new_page(viewport={'width': 1600, 'height': 1000})
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    # ⚠️ 用 domcontentloaded 而不是 networkidle：主题库外链图片在离线/受限网络下
    #    会一直挂起，networkidle 永远等不到（与「默认」按钮逻辑无关的偶发超时）。
    page.goto(B, wait_until='domcontentloaded', timeout=30000)
    page.wait_for_timeout(1200)

    for key, want in EXPECT.items():
        # 1) 模拟「用户应用了主题」：后端配置改成外链
        set_cfg({key: FAKE})
        # 2) 重新加载页面（模拟刷新后回到设置页）
        page.reload(wait_until='domcontentloaded', timeout=30000)
        page.wait_for_timeout(700)
        inp = page.locator('#f_' + key)
        before = inp.input_value()
        # 3) 点这一项的「默认」按钮
        page.locator('[data-default="%s"]' % key).click()
        page.wait_for_timeout(700)
        after = inp.input_value()
        saved = get_cfg().get(key)
        # 4) 缩略图是否真的加载出图（不是破图 / 空白框）
        img = page.locator('#p_' + key + ' img')
        loaded = False
        if img.count() > 0:
            loaded = page.evaluate(
                "() => { const i = document.querySelector('#p_%s img');"
                " return !!i && i.complete && i.naturalWidth > 0; }" % key)
        ok = (before == FAKE and after == want and saved == want and loaded)
        results.append((key, ok, '改前=%s 点默认后=%s 后端=%s 缩略图=%s'
                        % (before[:42], after, saved, 'OK' if loaded else '破图')))
    results.append(('无 JS 异常', not errors, str(errors[:2])))
    browser.close()

# 恢复出厂默认，避免给预览服务留下副作用
set_cfg(EXPECT)

print('=== 「默认」按钮实测 ===')
for k, ok, detail in results:
    print('  %-4s %-20s %s' % ('PASS' if ok else 'FAIL', k, detail))
n = sum(1 for _, ok, _ in results if ok)
print('\n=== 合计 %d/%d 通过 ===' % (n, len(results)))
print('恢复后配置:', {k: get_cfg().get(k) for k in EXPECT})
open('/Volumes/SSD-Data/trae/FnOsApp/verify/result_default_button.json', 'w',
     encoding='utf-8').write(json.dumps(
         [{'item': k, 'status': 'PASS' if ok else 'FAIL', 'detail': d}
          for k, ok, d in results], ensure_ascii=False, indent=2))
