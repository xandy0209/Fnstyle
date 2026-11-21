#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CDP 截图：抓「处理器 / 内存 / 显卡」三卡整块（本地 preview 或 NAS 实机）

用途：人工核对「每核点阵瓦片高度调高占满卡片」的视觉效果。

⚠️ 关键点：**不要开抽屉**。抽屉打开时主页面的三卡会被 overlay 遮住，
   .mon-cmp-card[data-cmp-open="cpu"] 仍可测量，但截图会拍到遮罩层。
   所以这里走「切到系统监控 tab → 等渲染 → 定位卡片 → 截图」。

用法：
    python3 verify/probe_core_shot.py --url http://127.0.0.1:8769/ --host 127.0.0.1
    python3 verify/probe_core_shot.py --url http://192.168.5.8:9910/ --host 192.168.5.8
"""
import argparse
import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import probe_base  # noqa: E402

OUT = "/tmp/fnstyle_core_cores.png"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8769/")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--open-drawer", action="store_true",
                    help="改截抽屉内的「每核使用率」区块（.mon-dcores）")
    args = ap.parse_args()

    probe_base.URL = args.url
    c = probe_base.connect(host=args.host)
    try:
        c.send("Network.clearBrowserCache")
    except Exception:
        pass

    # 重载 → 切到「系统监控」tab（不开抽屉）
    c.send("Page.navigate", {"url": args.url})
    c.pump(7)
    try:
        c.send("Page.reload", {"ignoreCache": True})
        c.pump(5)
    except Exception:
        pass
    c.send("Emulation.setDeviceMetricsOverride",
           {"width": 1440, "height": 1400, "deviceScaleFactor": 2,
            "mobile": False})
    c.pump(2)

    if args.open_drawer:
        probe_base.open_drawer(c, settle=6.0)
        sel = ".mon-dcores"
    else:
        c.js("var b=document.querySelector('[data-tab=monitor]');if(b)b.click();'ok'")
        c.pump(5)
        sel = '.mon-cmp-card[data-cmp-open="cpu"]'

    c.js("""(function(){
      var el = document.querySelector('%s');
      if (el) el.scrollIntoView({block:'center'});
      return 'ok';
    })()""" % sel)
    c.pump(1.5)

    box = c.js("""(function(){
      var el = document.querySelector('%s');
      if (!el) return '';
      var r = el.getBoundingClientRect();
      return JSON.stringify({x:Math.max(0,r.x-14), y:Math.max(0,r.y-14),
                              w:r.width+28, h:r.height+28});
    })()""" % sel)
    if not box:
        print("未找到目标元素:", sel)
        return 2
    b = json.loads(box)
    scroll = json.loads(c.js("JSON.stringify({x:window.scrollX,y:window.scrollY})")
                        or '{"x":0,"y":0}')
    b["x"] += scroll["x"]
    b["y"] += scroll["y"]
    print("文档坐标:", b)

    c.send("Emulation.setDeviceMetricsOverride",
           {"width": 1440, "height": 1500, "deviceScaleFactor": 2,
            "mobile": False})
    c.pump(1)
    shot = c.send("Page.captureScreenshot",
                  {"format": "png", "captureBeyondViewport": True,
                   "clip": {"x": b["x"], "y": b["y"], "width": b["w"],
                            "height": b["h"], "scale": 2}})
    with open(args.out, "wb") as f:
        f.write(base64.b64decode(shot["data"]))
    print("截图已保存:", args.out)

    # 顺带打印关键尺寸，方便文字核对
    m = c.js("""(function(){
      function b(e){ if(!e) return null; var r=e.getBoundingClientRect();
        return [+r.width.toFixed(1),+r.height.toFixed(1)];}
      var card=document.querySelector('.mon-cmp-card[data-cmp-open="cpu"]');
      var grid=document.querySelector('[data-cores]');
      var tile=document.querySelector('.mon-hw-core');
      var dots=document.querySelectorAll('.mon-hw-dot');
      var d0=dots.length?dots[0]:null;
      var last=dots.length?dots[dots.length-1]:null;
      return JSON.stringify({
        card: b(card), grid: b(grid), tile: b(tile),
        dotsContainer: b(tile?tile.querySelector('.mon-hw-core-dots'):null),
        dot0: b(d0), dotLast: b(last),
        dotCount: dots.length,
        tileCount: document.querySelectorAll('.mon-hw-core').length,
        cols: grid?grid.style.getPropertyValue('--cols'):null
      });
    })()""")
    print("尺寸:", m)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
