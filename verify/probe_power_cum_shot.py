#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实机截图：能耗抽屉里的「整机功耗」与「累计用电」两张图

用途：v1.1.65 修复「累计用电图把瓦数当 kWh」后，留一张对比证据图。
      修复前两张图形状完全一样、末值都是 31.00；修复后累计图是单调递增的阶梯。

用法：
    python3 verify/probe_power_cum_shot.py
输出：verify/shot_power_cum.png
"""
import base64
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from probe_base import connect, reload_and_open  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shot_power_cum.png")


def main():
    c = connect()
    # ⚠️ 必须硬刷新：否则浏览器里跑的还是旧 chart.js，
    #    左轴边距自适应 / marginGroup 对齐的代码根本没加载。
    reload_and_open(c, settle=8.0)

    # 把抽屉滚到能耗图表区域
    c.js("""(function(){
      var w = document.querySelector('#monComputeBody [data-charts="power"]');
      if (w) w.scrollIntoView({block:'start'});
      return 'ok';
    })()""")
    c.pump(2.0)

    box = c.js("""(function(){
      var w = document.querySelector('#monComputeBody [data-charts="power"]');
      if (!w) return JSON.stringify(null);
      var r = w.getBoundingClientRect();
      return JSON.stringify({x:r.x, y:r.y, width:r.width, height:r.height,
        sx:window.scrollX, sy:window.scrollY});
    })()""")

    import json
    b = json.loads(box) if box else None
    if not b:
        print("没找到能耗图表容器")
        return 1

    clip = {"x": max(0, b["x"]), "y": max(0, b["y"]),
            "width": b["width"], "height": b["height"], "scale": 2}
    r = c.send("Page.captureScreenshot",
               {"format": "png", "clip": clip, "captureBeyondViewport": True})
    data = r.get("data") or ""
    if not data:
        print("截图失败")
        return 1
    with open(OUT, "wb") as f:
        f.write(base64.b64decode(data))
    print("已保存: %s" % OUT)
    c.ws.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
