#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实机探针：左轴刻度标签必须完整可见，且同屏图表左边界必须对齐

背景
----
v1.1.65 修复「累计用电图把瓦数当 kWh」后，截图自查发现新问题：
累计用电图的左轴刻度被左边缘裁掉：
    0.0400 kWh → 显示成 "4000 kWh"（前面的 "0.0" 没了）
    0.0000 kWh → 显示成 "000 kWh"
数值完全看错，必须修。

根因：chart.js 写死 L = o.yTick ? 40 : 8，而 "0.0400 kWh" 宽 72px > 40px。

修复：左轴边距按刻度标签**实际宽度**自适应（离屏 canvas measureText）。
但自适应会引入**同屏对齐**问题：「整机功耗」标签窄（L=40），
「累计用电」标签宽（L=81）→ 两张图的绘图区左边界错位，
曲线起点不在一条垂直线上 → 用 marginGroup 共享组取组内最大边距。

本探针验证：
  ① 所有左轴刻度标签的 bbox 左边界 ≥ 0（没有被裁）
  ② 同屏所有图的绘图区左边界（L）严格相等

用法：
    python3 verify/probe_axis_margin.py
前置：Chrome 带 --remote-debugging-port=9222 已启动，NAS 已部署 v1.1.65。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from probe_base import connect, reload_and_open  # noqa: E402

# 量一个 SVG 里所有左轴刻度文本 + 绘图区左边界
_SNAP = r"""(function(){
  var wrap = document.querySelector('#monComputeBody [data-charts="power"]');
  if (!wrap) return JSON.stringify({err:'NO_POWER_CHARTS'});
  var hosts = wrap.querySelectorAll('[data-cch]');
  var out = {charts: []};
  for (var i = 0; i < hosts.length; i++) {
    var svg = hosts[i].querySelector('svg');
    if (!svg) { out.charts.push({idx:i, err:'NO_SVG'}); continue; }
    /* 绘图区左边界 = 第一条纵向网格线的 x1（grid-l 的 x1 就是 L） */
    var grid = svg.querySelector('line.grid-l');
    var L = grid ? parseFloat(grid.getAttribute('x1')) : null;
    var texts = svg.querySelectorAll('text.axis-t');
    var ts = [];
    for (var j = 0; j < texts.length; j++) {
      var t = texts[j];
      var cr = t.getBoundingClientRect();
      var sr = svg.getBoundingClientRect();
      /* 只看左轴刻度（x 靠左），底部时间刻度在中间，不参与裁切判定 */
      var isAxis = parseFloat(t.getAttribute('x')) < 200;
      ts.push({
        text: t.textContent,
        isAxis: isAxis,
        relLeft: cr.x - sr.x,
        attrX: parseFloat(t.getAttribute('x'))
      });
    }
    out.charts.push({
      idx: i,
      title: (hosts[i].parentNode ?
        (hosts[i].parentNode.querySelector('.mon-cmp-chart-title')||{}).textContent : '') || '',
      L: L,
      texts: ts
    });
  }
  return JSON.stringify(out);
})()"""


def main():
    c = connect()
    # ⚠️ 必须硬刷新：否则浏览器里跑的还是旧 chart.js，
    #    自适应左边距的代码根本没加载 → 测出来还是 attrX=32。
    reload_and_open(c, settle=8.0)
    raw = c.js(_SNAP)
    data = json.loads(raw or "{}")
    if data.get("err"):
        print("❌ " + data["err"])
        return 1
    bad = 0
    charts = data.get("charts", [])
    for ch in charts:
        print("\n=== 图 #%d %s（绘图区左边界 L=%s）===" % (
            ch["idx"], ch.get("title", "").strip(), ch.get("L")))
        for t in ch.get("texts", []):
            if not t.get("isAxis"):
                continue
            rel = t.get("relLeft", 0)
            clipped = rel < -0.5
            if clipped:
                bad += 1
            print("  %s 刻度 %-16r 左边界距 SVG 左沿 %.1fpx" % (
                "✗ 被裁" if clipped else "✓", t.get("text"), rel))
    # ② 同屏左边界对齐
    Ls = [ch.get("L") for ch in charts if ch.get("L") is not None]
    if len(Ls) > 1:
        same = len(set(Ls)) == 1
        if not same:
            bad += 1
        print("\n%s 同屏 %d 张图的绘图区左边界：%s → %s" % (
            "✓" if same else "✗", len(Ls), Ls,
            "严格对齐" if same else "错位（曲线起点不在一条垂直线上）"))
    print("\n%s 失败项：%d" % ("❌" if bad else "✅", bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
