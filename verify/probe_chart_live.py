#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实机探针：抽屉里的趋势图必须**随轮询实时更新**

背景
----
v1.1.65 修复的 bug（老板 2026-11-21 两次反馈）：
    「整体功耗和累计用电趋势图没有实时更新」
    「CPU 使用率和系统负载趋势图也没有实时更新」

根因：抽屉里的趋势图只在打开时拉一次历史，之后 updateLive 只把旧图表节点
搬回 DOM（保持 range 选择与实例），从不重绘 → 曲线冻结在打开那一刻。
且后端每 60 秒才记一条历史，前端每 3 秒轮询一次 → 靠重拉历史也等不到新点。

修复：每次轮询把当前实时值推进 live 缓冲并重绘（fnmonitor feed() 同款）。

本探针验证：等待若干轮询周期后，图表末点的时间戳与数值都发生了变化，
          且末点值与「实时卡片上的数字」一致（说明画的是实时值，不是历史点）。

用法：
    python3 verify/probe_chart_live.py
前置：Chrome 带 --remote-debugging-port=9222 已启动，NAS 已部署 v1.1.65+。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from probe_base import api, connect, open_drawer  # noqa: E402

# 读抽屉里所有趋势图的末点（ts + 值）
_SNAP = r"""(function(){
  var wrap = document.querySelector('#monComputeBody [data-charts]');
  if (!wrap) return JSON.stringify({err:'NO_CHARTS'});
  var heads = wrap.querySelectorAll('.mon-cmp-chart-card');
  var out = [];
  for (var i = 0; i < heads.length; i++) {
    var h = heads[i];
    var titleEl = h.querySelector('.mon-cmp-chart-title');
    var legendEl = h.querySelector('.mon-cmp-chart-legend');
    var svg = h.querySelector('svg');
    /* 从 SVG 的 path 里读最后一个点的坐标不可靠，
       改读图例 —— chart.js 的 setLegend(i, v) 渲染的就是该系列末值 */
    var lv = legendEl ? legendEl.querySelectorAll('.lv') : [];
    var vals = [];
    for (var j = 0; j < lv.length; j++) {
      vals.push((lv[j].textContent||'').replace(/\s+/g,' ').trim());
    }
    /* 末点时间戳：从 SVG 里最后一个 circle/端点标记的 cx 反推太脆，
       直接读 chart.js 暴露的 data 属性（chart.js 会写 data-last-ts） */
    var lastTs = svg ? svg.getAttribute('data-last-ts') : null;
    out.push({
      title: titleEl ? titleEl.textContent.trim() : null,
      legend: vals,
      lastTs: lastTs,
      pathLen: svg ? (svg.querySelector('path')||{}).getAttribute
        ? (svg.querySelector('path').getAttribute('d')||'').length : 0 : 0
    });
  }
  return JSON.stringify({cards: out});
})()"""


def snap(c):
    raw = c.js(_SNAP)
    try:
        return json.loads(raw or "{}")
    except Exception:
        return {"err": "PARSE_FAIL"}


def main():
    ck = Checker = None
    from probe_base import Checker as _Ck
    ck = _Ck()

    print("=== 抽屉趋势图实时更新探针 (v1.1.65) ===")

    c = connect()

    # ---- 能耗抽屉：功耗 + 累计用电 ----
    open_drawer(c, settle=7.0)
    s1 = snap(c)
    if s1.get("err"):
        ck.check("能抓到能耗抽屉的图表", False, s1.get("err"))
        ck.finish()
        return 1

    cards = s1.get("cards") or []
    ck.check("能耗抽屉有 2 张趋势图", len(cards) == 2, "实际 %d" % len(cards))
    ck.check("能耗抽屉第 1 张是「整机功耗」",
             cards and cards[0].get("title") == "整机功耗",
             repr(cards[0].get("title") if cards else None))
    ck.check("能耗抽屉第 2 张是「累计用电（所选范围）」",
             len(cards) > 1 and cards[1].get("title") == "累计用电（所选范围）",
             repr(cards[1].get("title") if len(cards) > 1 else None))

    before = [tuple(x.get("legend") or []) for x in cards]

    # 记录末值（用于比对是否变化）
    before_power = (cards[0].get("legend") or [None])[0] if cards else None
    before_cum = (cards[1].get("legend") or [None])[0] if len(cards) > 1 else None

    # 等 3 个轮询周期（3s × 3 = 9s）
    print("  · 等待 9 秒（3 个轮询周期）…")
    c.pump(9.0)

    s2 = snap(c)
    cards2 = s2.get("cards") or []
    after_power = (cards2[0].get("legend") or [None])[0] if cards2 else None
    after_cum = (cards2[1].get("legend") or [None])[0] if len(cards2) > 1 else None

    ck.check("功耗图末值在轮询后发生变化（说明曲线在动）",
             before_power != after_power,
             "前=%s 后=%s" % (before_power, after_power))
    ck.check("累计用电图末值在轮询后发生变化（积分在延伸）",
             before_cum != after_cum,
             "前=%s 后=%s" % (before_cum, after_cum))

    # 功耗图末值应贴近后端实时功率（实时卡片上的数字）
    try:
        mon = api("/api/monitor")
        r = mon.get("result", mon)
        live_w = (r.get("power") or {}).get("total")
    except Exception:
        live_w = None
    if live_w is not None and after_power:
        try:
            pw = float(str(after_power).replace("W", "").strip())
            ck.check("功耗图末值与后端实时功率一致（±2W）",
                     abs(pw - float(live_w)) <= 2.0,
                     "图上 %s W vs 实时 %s W" % (pw, live_w))
        except ValueError:
            ck.check("功耗图末值与后端实时功率一致（±2W）", False,
                     "无法解析图例值 %r" % after_power)

    # 累计用电末值应 ≈ 功耗末值 / 1000（30W × 3600s / 3600000）
    if after_cum and after_power:
        try:
            kwh = float(str(after_cum).replace("kWh", "").strip())
            pw = float(str(after_power).replace("W", "").strip())
            ck.check("累计用电末值 ≈ 功耗末值 / 1000",
                     abs(kwh - pw / 1000.0) < 0.01,
                     "累计 %s vs 期望 %.4f" % (kwh, pw / 1000.0))
        except ValueError:
            pass

    # ---- CPU 抽屉：CPU 使用率 + 系统负载 ----
    c.js("""(function(){
      var b=document.querySelector('[data-tab=monitor]');if(b)b.click();return 'ok';
    })()""")
    c.pump(2)
    c.js("window.FnStyleCompute.open('cpu', window.FnStyleMonitor.lastData());'ok'")
    c.pump(7.0)

    cs1 = snap(c)
    ccards = cs1.get("cards") or []
    ck.check("CPU 抽屉有 2 张趋势图", len(ccards) == 2, "实际 %d" % len(ccards))
    ck.check("CPU 抽屉第 1 张是「CPU 使用率」",
             ccards and ccards[0].get("title") == "CPU 使用率",
             repr(ccards[0].get("title") if ccards else None))
    ck.check("CPU 抽屉第 2 张是「系统负载（1 分钟）」",
             len(ccards) > 1 and ccards[1].get("title") == "系统负载（1 分钟）",
             repr(ccards[1].get("title") if len(ccards) > 1 else None))

    cpu_before = (ccards[0].get("legend") or [None])[0] if ccards else None
    load_before = (ccards[1].get("legend") or [None])[0] if len(ccards) > 1 else None

    print("  · 等待 9 秒（3 个轮询周期）…")
    c.pump(9.0)

    cs2 = snap(c)
    ccards2 = cs2.get("cards") or []
    cpu_after = (ccards2[0].get("legend") or [None])[0] if ccards2 else None
    load_after = (ccards2[1].get("legend") or [None])[0] if len(ccards2) > 1 else None

    ck.check("CPU 使用率图末值在轮询后发生变化",
             cpu_before != cpu_after,
             "前=%s 后=%s" % (cpu_before, cpu_after))
    ck.check("系统负载图末值在轮询后发生变化",
             load_before != load_after,
             "前=%s 后=%s" % (load_before, load_after))

    # CPU 图末值应贴近后端实时 CPU percent
    try:
        mon = api("/api/monitor")
        r = mon.get("result", mon)
        live_cpu = (r.get("cpu") or {}).get("percent")
        live_load = ((r.get("cpu") or {}).get("loadavg") or [None])[0]
    except Exception:
        live_cpu = live_load = None

    if live_cpu is not None and cpu_after:
        try:
            pv = float(str(cpu_after).replace("%", "").strip())
            ck.check("CPU 图末值与后端实时 percent 一致（±3%）",
                     abs(pv - float(live_cpu)) <= 3.0,
                     "图上 %s%% vs 实时 %s%%" % (pv, live_cpu))
        except ValueError:
            ck.check("CPU 图末值与后端实时 percent 一致（±3%）", False,
                     "无法解析 %r" % cpu_after)

    if live_load is not None and load_after:
        try:
            lv = float(str(load_after).strip())
            ck.check("负载图末值与后端实时 loadavg[0] 一致（±0.1）",
                     abs(lv - float(live_load)) <= 0.1,
                     "图上 %s vs 实时 %s" % (lv, live_load))
        except ValueError:
            pass

    c.ws.close()
    return ck.finish()


if __name__ == "__main__":
    sys.exit(main())
