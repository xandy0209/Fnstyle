#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FnStyle「任务栏沉底 → 右下角图标弹出浮层」帧级验证（v15）

用户反馈：点右下角图标后「先弹出浮动窗，再移动位置」，视觉上是两次弹出。

本用例用真实 fnstyle-runtime.js + 与 fnOS 同构的最小桌面 DOM，
按 Semi UI 的真实时序**分帧**挂载 popover，并用 requestAnimationFrame
逐帧采样浮层的位置 / 可见性 / 内联定位值，量化「错误位置是否被绘制过」。

判定口径：
  1. 浮层必须真的显示出来（可见帧 >= 1）；
  2. 第一帧可见时就必须位于目标「左上方」，不能先出现在原生右下角；
  3. 整个过程中 inner.style.left/top 只允许有一个值（不允许"移动"）；
  4. 可见帧的布局位置（offsetLeft，不受入场动画 transform 影响）恒定。

覆盖三种挂载时序：
  A. 两帧式：先挂 wrapper（无 show）→ 下一帧加 show + 写入原生定位
  B. 三帧式：A 之后再隔一帧由 Semi 二次重算定位
  C. 宽面板：356px × 100vh，靠点击记忆识别触发器（v14 场景）
"""
import functools
import http.server
import json
import shutil
import socketserver
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
WWW = Path('/tmp/fnstyle_popover_probe')
CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
RESULTS = []

CFG = {
    'appTitle': '飞牛 fnOS', 'deviceLogo': '', 'loginLogo': '',
    'loginBackground': '', 'desktopWallpaper': '', 'webFavicon': '',
    'Fndesk_taskbar': 1, 'FndeskGlass': -1, 'nasNameDisplay': 1,
}

HTML = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="UTF-8"><title>飞牛 fnOS</title>
<style>
  html,body{margin:0;padding:0;width:100%;height:100%;background:#1c1c1e;}
  .fixed.inset-y-0.left-0{position:fixed;top:0;bottom:0;left:0;width:66px;}
  [data-fnstyle-tb="root"]{background:rgba(40,40,44,.85);}
  [data-fnstyle-tb="bottom-inner"] .flex.h-5.w-full{
      width:40px;height:40px;background:#4a4a50;border-radius:8px;flex-shrink:0;}
  .semi-portal-inner{position:absolute;z-index:9999;}
  .semi-popover-wrapper{
      position:absolute;width:220px;height:160px;background:#333338;
      border:1px solid #666;border-radius:10px;box-sizing:border-box;
      opacity:0;transform:scale(.9);
      transition:opacity .15s ease-out,transform .15s ease-out;
  }
  .semi-popover-wrapper.semi-popover-wrapper-show{opacity:1;transform:none;}
  .semi-popover-wrapper[data-fnstyle-wide]{width:356px;height:100vh;}
</style>
<script src="./fnstyle-runtime.js"></script>
</head><body>
<div class="fixed inset-y-0 left-0 z-[1]" style="width:66px">
 <div class="box-border size-full"><div class="h-full rounded-md">
  <div class="box-border flex h-full flex-col">
   <div class="flex flex-col items-center"></div>
   <div class="relative flex flex-1 flex-col"><div class="scrollbar-hidden absolute inset-0"></div></div>
   <div class="flex flex-col items-center justify-between">
     <div class="flex flex-col items-center justify-between">
       <div class="flex h-5 w-full" id="tb-icon"><span>设置</span></div>
     </div>
   </div>
  </div></div></div>
</div>
<div class="desktop"><div class="gradient-border"></div></div>
</body></html>"""

SPAWN = r"""
(mode) => {
  var icon = document.getElementById('tb-icon');
  var neutral = (mode === 'D' || mode === 'E');
  // D 完全不点任务栏；E 故意点一下、再弹一个桌面中部菜单，
  // 用来验证「点击记忆」不会把无关浮层也挪走 / 隐藏掉。
  if (mode !== 'D') {
    icon.dispatchEvent(new PointerEvent('pointerdown', {bubbles:true, button:0}));
  }

  var t = icon.getBoundingClientRect();
  var wide = (mode === 'C');
  var w = wide ? 356 : 220;
  var h = wide ? window.innerHeight : 160;
  // Semi 原生 rightBottom 落点：触发器右下方（大部分出屏）；
  // D/E 换成视口中部（文件管理器右键菜单的典型位置）。
  var nativeLeft = neutral ? 420 : Math.round(t.right + 4);
  var nativeTop  = neutral ? 300 : (wide ? 0 : Math.round(t.top + 4));

  var portal = document.createElement('div');
  portal.className = 'semi-portal-inner';
  portal.id = 'probe-portal';
  portal.style.cssText = 'position:absolute;left:' + nativeLeft + 'px;top:' + nativeTop + 'px;';
  var wrap = document.createElement('div');
  wrap.className = 'semi-popover-wrapper';
  if (wide) wrap.setAttribute('data-fnstyle-wide', '');
  wrap.id = 'probe-wrap';
  wrap.innerHTML = '<div style="padding:16px;color:#fff">菜单内容</div>';
  portal.appendChild(wrap);
  document.body.appendChild(portal);

  // A/B/C 共同步骤：下一帧加 show（Semi 入场）+ 写一次定位
  requestAnimationFrame(function () {
    wrap.classList.add('semi-popover-wrapper-show');
    portal.style.left = nativeLeft + 'px';
    portal.style.top = nativeTop + 'px';
    if (mode === 'B') {
      // 三帧式：Semi 再隔一帧重算一次（UI 库常见）
      requestAnimationFrame(function () {
        portal.style.left = nativeLeft + 'px';
        portal.style.top = nativeTop + 'px';
      });
    }
  });
  return { trigger: {left:t.left, top:t.top, right:t.right, bottom:t.bottom},
           nativeLeft: nativeLeft, nativeTop: nativeTop, w: w, h: h };
}
"""

SAMPLER = r"""
() => {
  window.__frames = [];
  window.__sampling = true;
  function tick() {
    var w = document.getElementById('probe-wrap');
    var inner = document.getElementById('probe-portal');
    if (w && inner) {
      var r = w.getBoundingClientRect();
      var cs = getComputedStyle(w);
      window.__frames.push({
        t: Math.round(performance.now() * 10) / 10,
        vis: cs.visibility, op: Math.round(parseFloat(cs.opacity) * 100) / 100,
        styleLeft: inner.style.left, styleTop: inner.style.top,
        offLeft: inner.offsetLeft, offTop: inner.offsetTop,
        renderedLeft: Math.round(r.left * 10) / 10,
        renderedTop: Math.round(r.top * 10) / 10
      });
    }
    if (window.__sampling) requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}
"""


def rec(name, ok, detail):
    RESULTS.append({'name': name, 'ok': bool(ok), 'detail': detail})


def run_case(page, mode):
    page.evaluate(SAMPLER)
    geo = page.evaluate(SPAWN, mode)
    page.wait_for_timeout(700)
    page.evaluate('window.__sampling = false')
    frames = page.evaluate('window.__frames')
    # 清理，避免影响下一轮
    page.evaluate("""() => {
        var p = document.getElementById('probe-portal'); if (p) p.remove();
    }""")
    page.wait_for_timeout(60)

    gap = 12
    target_left = max(8, geo['trigger']['left'] - geo['w'] - gap)
    target_top = max(8, geo['trigger']['top'] - geo['h'] - gap)
    visible = [f for f in frames if f['vis'] != 'hidden' and f['op'] > 0.05]
    style_lefts = sorted({f['styleLeft'] for f in frames if f['styleLeft']})
    off_lefts = sorted({f['offLeft'] for f in visible})

    tag = '用例%s' % mode
    rec(tag + '：浮层确实显示', len(visible) > 0,
        '可见帧 %d / 总帧 %d' % (len(visible), len(frames)))
    if not visible:
        return geo, target_left, target_top, frames

    first_bad = [f for f in visible
                 if abs(f['offLeft'] - target_left) > 2]
    rec(tag + '：首帧即在目标左上方（无"先弹再移"）',
        len(first_bad) == 0,
        '目标 x=%s，出现过的布局 x=%s，越界帧 %d，首个可见 x=%s' %
        (round(target_left), off_lefts, len(first_bad), visible[0]['offLeft']))

    rec(tag + '：内联定位只写一次（位置不漂移）',
        len(style_lefts) <= 1,
        'styleLeft 取值集合 = %s' % style_lefts)

    rec(tag + '：可见帧布局位置恒定',
        len(off_lefts) <= 1,
        'offsetLeft 取值集合 = %s' % off_lefts)

    # 诊断信息：错误位置被绘制过的帧数
    native_x = geo['nativeLeft']
    err_frames = [f for f in visible if f['offLeft'] > target_left + 30]
    rec(tag + '：原生右下角落点从未被绘制',
        len(err_frames) == 0,
        '原生 x=%s，错误位置可见帧 %d' % (native_x, len(err_frames)))
    return geo, target_left, target_top, frames


def run_neutral_case(page, mode):
    """桌面中部浮层：必须保持原生位置、且始终可见（不被接管 / 不被抑制）。"""
    page.evaluate(SAMPLER)
    geo = page.evaluate(SPAWN, mode)
    page.wait_for_timeout(700)
    page.evaluate('window.__sampling = false')
    frames = page.evaluate('window.__frames')
    page.evaluate("""() => {
        var p = document.getElementById('probe-portal'); if (p) p.remove();
    }""")
    page.wait_for_timeout(60)

    tag = '用例%s' % mode
    visible = [f for f in frames if f['vis'] != 'hidden' and f['op'] > 0.05]
    style_lefts = sorted({f['styleLeft'] for f in frames if f['styleLeft']})
    hidden = [f for f in frames if f['vis'] == 'hidden']

    rec(tag + '：普通浮层正常显示', len(visible) > 0,
        '可见帧 %d / 总帧 %d' % (len(visible), len(frames)))
    rec(tag + '：原生定位未被改写', style_lefts == [str(geo['nativeLeft']) + 'px'],
        'styleLeft 取值集合 = %s，原生 = %spx' % (style_lefts, geo['nativeLeft']))
    rec(tag + '：未被抑制隐藏', len(hidden) == 0,
        'hidden 帧 %d' % len(hidden))
    rec(tag + '：布局位置保持原生', all(f['offLeft'] == geo['nativeLeft'] for f in visible),
        'offsetLeft 集合 = %s' % sorted({f['offLeft'] for f in visible}))


def main():
    if WWW.exists():
        shutil.rmtree(str(WWW))
    (WWW / 'deskdata').mkdir(parents=True)
    (WWW / 'deskdata' / 'fnstyle.json').write_text(
        json.dumps(CFG, ensure_ascii=False), encoding='utf-8')
    (WWW / 'index.html').write_text(HTML, encoding='utf-8')
    shutil.copy(str(ROOT / 'app/ui/static/js/fnstyle-runtime.js'),
                str(WWW / 'fnstyle-runtime.js'))

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(WWW))
    httpd = socketserver.TCPServer(('127.0.0.1', 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=CHROME, headless=True,
                                         args=['--no-sandbox'])
            page = browser.new_page(viewport={'width': 1280, 'height': 800})
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto('http://127.0.0.1:%d/index.html' % port,
                      wait_until='domcontentloaded')
            page.wait_for_function("!!document.querySelector('[data-fnstyle-tb=\"bottom-inner\"]')",
                                   timeout=10000)
            page.wait_for_timeout(300)

            rec('任务栏沉底已生效',
                page.evaluate("!!document.getElementById('fnstyle_taskbar_css')"),
                '注入样式表存在')

            for mode in ('A', 'B', 'C'):
                run_case(page, mode)
            for mode in ('D', 'E'):
                run_neutral_case(page, mode)

            rec('无 JS 运行时异常', not errors, str(errors[:3]))
            browser.close()
    finally:
        httpd.shutdown()

    passed = sum(1 for r in RESULTS if r['ok'])
    print('=' * 68)
    for r in RESULTS:
        print('%s %-45s %s' % ('PASS' if r['ok'] else 'FAIL',
                              r['name'], r['detail']))
    print('=' * 68)
    print('结果：%d/%d' % (passed, len(RESULTS)))
    out = ROOT / 'verify/result_popover_single_shot.json'
    out.write_text(json.dumps({'passed': passed, 'total': len(RESULTS),
                              'cases': RESULTS}, ensure_ascii=False, indent=2),
                   encoding='utf-8')
    return 0 if passed == len(RESULTS) else 1


if __name__ == '__main__':
    raise SystemExit(main())
