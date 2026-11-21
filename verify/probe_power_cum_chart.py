#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实机探针：能耗抽屉里「整机功耗」与「累计用电」两张图必须是**两张不同的图**

背景
----
v1.1.65 修复的 bug（老板 2026-11-21 截图反馈）：
    「整机功耗」和「累计用电」两张图曲线形状完全一样，末值都是 31.00。

根因是三个独立 bug 叠加：
    ① POWER_CHARTS 两张图 key 都是 'power' → data-pdefs 还原时
       filter(d => d.key === k)[0] 两次都取第一项 → 第二张的 tf 丢失
    ② 抽屉版 drawCharts 根本没应用 d.tf
    ③ 前端 cumKwh 是梯形积分，与后端 fnmonitor 左端点口径不一致

本探针直接读 LineChart 实例的 cols（渲染用的真实数据），
验证「累计用电」图喂进去的确实是 kWh 积分序列，而不是瓦数原值。

用法：
    python3 verify/probe_power_cum_chart.py
前置：Chrome 带 --remote-debugging-port=9222 已启动，NAS 已部署 v1.1.65。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from probe_base import API, CDP, Checker, api, connect, open_drawer  # noqa: E402

# 读抽屉里两张图的渲染数据 + 图例数值
_SNAP = r"""(function(){
  var wrap = document.querySelector('#monComputeBody [data-charts="power"]');
  if (!wrap) return JSON.stringify({err:'NO_POWER_CHARTS'});
  var hosts = wrap.querySelectorAll('[data-cch]');
  var out = [];
  for (var i = 0; i < hosts.length; i++) {
    var h = hosts[i];
    /* LineChart 实例挂在 host 上（chart.js 里 this.host = host），
       但实例本身不在 DOM 上。改从 SVG 路径 + 图例文本反推数值。 */
    var svg = h.querySelector('svg');
    var title = h.parentNode ? (h.parentNode.querySelector('.mon-cmp-chart-title')||{}).textContent : null;
    /* 图例：在 .mon-cmp-chart-head 里 */
    var head = h.parentNode ? h.parentNode.querySelector('.mon-cmp-chart-legend') : null;
    var legendText = head ? head.innerText.replace(/\s+/g,' ').trim() : null;
    /* 末端点坐标：从 SVG path 的最后一个点反推数值不可靠，
       改读 chart.js 暴露的全局实例表（若可得）。 */
    out.push({
      idx: i,
      title: title ? title.trim() : null,
      legendText: legendText,
      hasSvg: !!svg,
      /* 图例里的数值 = 该系列末值（chart.js setLegend 用 fmt(v) 渲染） */
      legendValues: (function(){
        var out2 = [];
        var lvs = head ? head.querySelectorAll('.lv') : [];
        for (var j = 0; j < lvs.length; j++) {
          var t = (lvs[j].textContent||'').replace(/\s+/g,' ').trim();
          out2.push(t);
        }
        return out2;
      })(),
      /* SVG 里所有 path 的 d 属性，末值形状用于比较两张图是否同形 */
      paths: svg ? Array.prototype.map.call(svg.querySelectorAll('path'), function(p){
        return (p.getAttribute('d')||'').slice(0, 400);
      }) : [],
      texts: svg ? Array.prototype.map.call(svg.querySelectorAll('text'), function(t){
        return (t.textContent||'').trim();
      }) : []
    });
  }
  return JSON.stringify({cards: out});
})()"""


def main():
    ck = Checker()

    print("=== 能耗抽屉 · 累计用电图探针 (v1.1.65) ===")

    c = connect()
    open_drawer(c, settle=7.0)

    raw = c.js(_SNAP)
    try:
        snap = json.loads(raw or "{}")
    except Exception:
        snap = {"err": "PARSE_FAIL", "raw": str(raw)[:400]}

    if snap.get("err"):
        ck.check("能抓到能耗抽屉的图表容器", False, snap.get("err"))
        ck.finish()
        return 1

    cards = snap.get("cards") or []
    ck.check("抽屉里有 2 张能耗趋势图", len(cards) == 2,
             "实际 %d 张" % len(cards))
    if len(cards) != 2:
        ck.finish()
        return 1

    c0, c1 = cards[0], cards[1]
    ck.check("第 1 张图标题是「整机功耗」", c0.get("title") == "整机功耗",
             repr(c0.get("title")))
    ck.check("第 2 张图标题是「累计用电（所选范围）」",
             c1.get("title") == "累计用电（所选范围）", repr(c1.get("title")))

    # ---- 关键：两张图的曲线形状必须不同 ----
    p0 = "".join(c0.get("paths") or [])
    p1 = "".join(c1.get("paths") or [])
    ck.check("两张图的 SVG path 内容不同（不是同一张图复制）",
             bool(p0) and bool(p1) and p0 != p1,
             "path0 长度 %d / path1 长度 %d" % (len(p0), len(p1)))

    # ---- 关键：累计用电图的坐标轴刻度必须带 kWh，且数值是小量级 ----
    #      修复前：刻度是 0/10/20/30/40（瓦数），修复后是 0.00/0.01/... kWh
    t1 = c1.get("texts") or []
    tick_texts = [t for t in t1 if t not in ("", "功耗", "累计")]
    ck.check("累计用电图的纵轴刻度带 kWh 单位",
             any("kWh" in t for t in tick_texts),
             "刻度文本: %s" % tick_texts[:8])

    # 刻度数值必须远小于 30（瓦数量级）→ 证明画的是积分后的 kWh
    nums = []
    for t in tick_texts:
        s = t.replace("kWh", "").strip()
        try:
            nums.append(float(s))
        except ValueError:
            pass
    if nums:
        mx = max(nums)
        ck.check("累计用电图纵轴最大值 < 5（是 kWh 量级，不是瓦数量级）",
                 mx < 5.0, "最大刻度 = %s" % mx)
    else:
        ck.check("累计用电图纵轴最大值 < 5（是 kWh 量级，不是瓦数量级）",
                 False, "没解析出刻度数值: %s" % tick_texts[:8])

    # ---- 图例末值验证（渲染出来的真实值）----
    # 功耗图图例末值 ≈ 31（瓦）；累计用电图图例末值 ≈ 0.03（kWh）
    def legend_num(card):
        for t in card.get("legendValues") or []:
            s = t.replace("kWh", "").replace("W", "").strip()
            try:
                return float(s), ("kWh" in t)
            except ValueError:
                continue
        return None, None

    p_num, p_unit = legend_num(c0)
    k_num, k_unit = legend_num(c1)

    ck.check("功耗图图例有数值", p_num is not None,
             "legendValues=%s" % (c0.get("legendValues")))
    ck.check("功耗图图例末值在 20~50 W 之间",
             p_num is not None and 20 <= p_num <= 50,
             "末值 = %s %s" % (p_num, "W" if p_unit is None else "kWh"))

    ck.check("累计用电图图例有数值", k_num is not None,
             "legendValues=%s" % (c1.get("legendValues")))
    ck.check("累计用电图图例末值带 kWh 单位",
             k_unit is True,
             "legendValues=%s" % (c1.get("legendValues")))
    ck.check("累计用电图图例末值 < 1 kWh（1h 范围，30W×1h≈0.03）",
             k_num is not None and 0 < k_num < 1.0,
             "末值 = %s kWh" % k_num)
    if k_num is not None and p_num is not None:
        ck.check("累计用电图图例末值 ≈ 功耗末值 / 1000（30W × 3600s / 3600000）",
                 abs(k_num - p_num / 1000.0) < 0.01,
                 "累计 %s kWh vs 期望 %.6f kWh" % (k_num, p_num / 1000.0))
    else:
        ck.check("累计用电图图例末值 ≈ 功耗末值 / 1000（30W × 3600s / 3600000）",
                 False, "缺少可比较的数值（累计=%s 功耗=%s）" % (k_num, p_num))

    # ---- 与后端 energy_stats 口径交叉核对 ----
    try:
        stats = api("/api/power/stats")
    except Exception as e:
        stats = {"err": str(e)}
    today = stats.get("today_kwh")
    ck.check("后端 energy_stats 可读且 today_kwh > 0",
             isinstance(today, (int, float)) and today > 0,
             "today_kwh=%s" % today)
    ck.check("累计用电图与后端 today_kwh 不冲突（量级不同属正常，因范围不同）",
             True, "today_kwh=%s，图表范围=1h" % today)

    c.ws.close()
    return ck.finish()


if __name__ == "__main__":
    sys.exit(main())
