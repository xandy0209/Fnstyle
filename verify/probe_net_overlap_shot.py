#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""网络卡截图（v1.1.88 验收用）

用途：人工核对「网络卡片左侧两行速率文本上移后不再被趋势线穿过」。

⚠️ 沿用 probe_core_shot.py 的两个关键点：
   ① `clip` 用**文档坐标**（`getBoundingClientRect()` 给的是视口坐标，
      必须加上 `window.scrollX/Y`）
   ② 先 `scrollIntoView({block:'center'})` 再量，避免元素在视口外

用法：
    python3 verify/probe_net_overlap_shot.py --url http://192.168.5.8:9910/ \
        --host 192.168.5.8 --out /tmp/fnstyle_net_card_v1188.png
"""
import argparse
import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import probe_base  # noqa: E402

OUT = "/tmp/fnstyle_net_card_v1188.png"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://192.168.5.8:9910/")
    ap.add_argument("--host", default="192.168.5.8")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--old", action="store_true",
                    help="截「修复前」的样式（注入全局表单行 padding/border + top:10px）")
    args = ap.parse_args()

    probe_base.URL = args.url
    c = probe_base.connect(host=args.host)
    try:
        c.send("Network.clearBrowserCache")
    except Exception:
        pass

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

    # 概览网格在「系统监控」标签页里
    c.js("""(() => {
      const t = document.querySelector('.tab[data-tab="monitor"]');
      if (t) t.click();
      return !!t;
    })()""")
    c.pump(5)

    for _ in range(40):
        if c.js("!!document.querySelector('[data-ov=\"net\"]')"):
            break
        c.pump(1)
    c.pump(2)

    if args.old:
        # 注入「修复前」的样式，用于「之前 vs 之后」对照截图
        c.js("""(() => {
          const st = document.createElement('style');
          st.id = '__shot_old_net_style__';
          st.textContent =
            '.ov-net-vals { top: 10px !important; }' +
            '.ov-net-vals .row { padding: .7rem 0 !important; ' +
            'border-bottom: 1px dashed var(--border) !important; }';
          document.head.appendChild(st);
          return true;
        })()""")
        c.pump(2)

    sel = '[data-ov="net"]'
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
        print("未找到网络卡")
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

    # 顺带打印关键几何，方便文字核对
    m = c.js("""(() => {
      const card = document.querySelector('[data-ov="net"]');
      const vals = card.querySelector('.ov-net-vals');
      const rows = Array.from(vals.querySelectorAll('.row'));
      const sub = card.querySelector('.ov-m-sub');
      const foot = card.querySelector('.ov-m-foot');
      const rel = (el) => {
        if (!el) return null;
        const cb = card.getBoundingClientRect();
        const b = el.getBoundingClientRect();
        return {top: Math.round(b.top - cb.top), bottom: Math.round(b.bottom - cb.top)};
      };
      return JSON.stringify({
        vals: rel(vals), row1: rel(rows[0]), row2: rel(rows[1]),
        sub: rel(sub), foot: rel(foot),
        subText: (sub.textContent || '').trim(),
        rowText: rows.map(r => (r.textContent || '').trim()),
      });
    })()""")
    print("几何:", m)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
