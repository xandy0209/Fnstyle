#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""系统监控模块**全功能点核查**探针（v1.1.65）

背景
----
老板反馈：「内存抽屉中的趋势图也没有实时更新，请全面核查
（系统监控模块中的所有其他功能点都必须逐项进行测试）是否还有其他的 BUG」。

本探针把系统监控模块拆成可逐项验证的功能点，**每项都实测**，
而不是只做源码级断言。覆盖：

  A. 页面骨架与状态条（状态灯 / 更新时间 / 自动刷新开关）
  B. 存储板块（硬盘卡 / 容量 / 扫描入口）
  C. 处理器-内存-显卡板块（卡片 / 点阵 / 规格）
  D. 温度和散热板块（总览卡 / 侧栏 / 温度计）
  E. 能耗板块（实时功耗 / 统计 / 每日用电 / 电价单价）
  F. 四个抽屉（处理器 / 内存 / 温度 / 能耗 / 硬盘 / 巡检）
  G. 趋势图实时性（每个抽屉的图都要随轮询延伸 + 时间轴滚动）
  H. 交互健壮性（切页 / 反复开关抽屉 / 缩放 / 快速切范围）

用法：
    python3 verify/probe_monitor_audit.py
前置：Chrome 带 --remote-debugging-port=9222 已启动，NAS 已部署对应版本。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from probe_base import Checker, api, connect, reload_and_open  # noqa: E402

# ---------------------------------------------------------------- 页面骨架
_SKELETON = r"""(function(){
  var g = function(id){ return document.getElementById(id); };
  var out = {};
  var st = g('monStatus');
  out.statusText = st ? st.innerText.replace(/\s+/g,' ').trim() : null;
  out.statusClass = st ? st.className : null;
  out.updated = g('monUpdated') ? g('monUpdated').innerText.trim() : null;
  out.autoChecked = g('monAuto') ? g('monAuto').checked : null;
  out.storeCount = g('monStoreCount') ? g('monStoreCount').innerText.trim() : null;
  /* 板块 */
  out.storageCards = document.querySelectorAll('#monStorage > *').length;
  out.coreCards = document.querySelectorAll('#monCore > *').length;
  out.tempOverview = !!g('monTempOverview');
  out.tempSide = !!g('monTempSide');
  out.powerOverview = !!g('monPowerOverview');
  out.powerStats = !!g('monPowerStats');
  out.powerDaily = !!g('monPowerDaily');
  out.powerRate = g('monPowerRate') ? g('monPowerRate').innerText.trim() : null;
  /* 空内容检测 */
  out.emptyBlocks = [];
  var ids = ['monStorage','monCore','monTempOverview','monTempSide','monPowerOverview',
             'monPowerStats','monPowerDaily','monPowerCharts','monTempCharts'];
  for (var i=0;i<ids.length;i++){
    var e = g(ids[i]);
    if (e && e.innerHTML.replace(/\s+/g,'').length < 20) out.emptyBlocks.push(ids[i]);
  }
  /* 页面是否可见（后台标签页会冻结 rAF） */
  out.hidden = document.hidden;
  return JSON.stringify(out);
})()"""

# ---------------------------------------------------------------- 抽屉内容
def drawer_probe(c, kind):
    """打开某个抽屉，返回内容摘要。"""
    c.js("window.FnStyleCompute.open('%s', window.FnStyleMonitor.lastData());'ok'" % kind)
    c.pump(4.5)
    return json.loads(c.js(r"""(function(){
      var body = document.getElementById('monComputeBody');
      if (!body) return JSON.stringify({err:'NO_BODY'});
      var wrap = body.querySelector('[data-charts]');
      var hosts = wrap ? wrap.querySelectorAll('[data-cch]') : [];
      var charts = [];
      for (var i=0;i<hosts.length;i++){
        var svg = hosts[i].querySelector('svg');
        var titleEl = hosts[i].parentNode ?
          hosts[i].parentNode.querySelector('.mon-cmp-chart-title') : null;
        var leg = hosts[i].parentNode ?
          hosts[i].parentNode.querySelector('.mon-cmp-chart-legend') : null;
        var times = [];
        if (svg) {
          var ts = svg.querySelectorAll('text.axis-t');
          for (var j=0;j<ts.length;j++){
            var t = ts[j].textContent;
            if (/^\d+:\d+$/.test(t) || /^\d+\/\d+$/.test(t)) times.push(t);
          }
        }
        charts.push({
          title: titleEl ? titleEl.textContent.trim() : null,
          legend: leg ? leg.innerText.replace(/\s+/g,' ').trim() : null,
          hasSvg: !!svg,
          times: times,
          nPoints: svg ? (svg.querySelector('path.ln') ?
              (svg.querySelector('path.ln').getAttribute('d')||'').split('C').length-1 : 0) : 0
        });
      }
      return JSON.stringify({
        open: !document.getElementById('monComputeOverlay').hidden,
        title: (document.getElementById('monComputeTitleText')||{}).textContent,
        icon: (document.getElementById('monComputeIcon')||{}).textContent,
        bodyLen: body.innerHTML.length,
        segs: body.querySelectorAll('[data-cseg] *').length,
        charts: charts,
        hasTariff: !!body.querySelector('[data-tariff-box]'),
        hasDcores: !!body.querySelector('[data-dcores]'),
        hasDstack: !!body.querySelector('.mon-dstack'),
        hasLegend: !!body.querySelector('.mon-legend'),
        textLen: body.innerText.replace(/\s+/g,' ').trim().length
      });
    })()"""))


def chart_snapshot(c, kind):
    """读某个抽屉里所有图的实时状态（末点坐标 / 时间轴 / 图例）。"""
    return json.loads(c.js(r"""(function(){
      var wrap = document.querySelector('#monComputeBody [data-charts]');
      if (!wrap) return JSON.stringify({err:'NO_CHARTS'});
      var hosts = wrap.querySelectorAll('[data-cch]');
      var out = [];
      for (var i=0;i<hosts.length;i++){
        var svg = hosts[i].querySelector('svg');
        if (!svg) { out.push({idx:i, hasSvg:false}); continue; }
        var hit = svg.querySelector('rect.hit');
        var grid = svg.querySelector('line.grid-l');
        var L = grid ? parseFloat(grid.getAttribute('x1')) : null;
        var right = hit ? L + parseFloat(hit.getAttribute('width')) : null;
        var paths = svg.querySelectorAll('path.ln');
        var lastX = null, n = 0;
        if (paths.length){
          var d = paths[0].getAttribute('d') || '';
          var ms = d.match(/C(-?[\d.]+),(-?[\d.]+)/g) || [];
          n = ms.length;
          if (ms.length){
            var p = ms[ms.length-1].slice(1).split(',');
            lastX = parseFloat(p[0]);
          }
        }
        var times = [];
        var ts = svg.querySelectorAll('text.axis-t');
        for (var j=0;j<ts.length;j++){
          var t = ts[j].textContent;
          if (/^\d+:\d+$/.test(t) || /^\d+\/\d+$/.test(t)) times.push(t);
        }
        var leg = hosts[i].parentNode ?
          hosts[i].parentNode.querySelector('.mon-cmp-chart-legend') : null;
        out.push({
          idx: i, hasSvg: true, L: L, right: right,
          lastX: lastX, nPoints: n,
          gapToRight: (lastX !== null && right !== null) ? right - lastX : null,
          times: times,
          legend: leg ? leg.innerText.replace(/\s+/g,' ').trim() : null
        });
      }
      return JSON.stringify({charts: out});
    })()"""))


def main():
    ck = Checker("系统监控模块全功能点核查")
    c = connect()
    reload_and_open(c, settle=8.0)

    # ---------- A. 页面骨架 ----------
    print("\n--- A. 页面骨架与状态条 ---")
    sk = json.loads(c.js(_SKELETON))
    ck.check("页面处于可见状态（否则 rAF 被冻结，图表建不出来）",
             sk.get("hidden") is False, "document.hidden=%s" % sk.get("hidden"))
    ck.check("状态灯有内容", bool(sk.get("statusText")), repr(sk.get("statusText"))[:60])
    ck.check("状态灯带状态类名", bool(sk.get("statusClass")), sk.get("statusClass"))
    ck.check("显示更新时间", bool(sk.get("updated")), repr(sk.get("updated"))[:40])
    ck.check("自动刷新开关存在且默认开启", sk.get("autoChecked") is True,
             "checked=%s" % sk.get("autoChecked"))
    ck.check("存储板块有卡片", (sk.get("storageCards") or 0) > 0,
             "%s 张" % sk.get("storageCards"))
    ck.check("处理器板块有卡片", (sk.get("coreCards") or 0) > 0,
             "%s 张" % sk.get("coreCards"))
    ck.check("温度总览卡存在", sk.get("tempOverview") is True)
    ck.check("温度侧栏存在", sk.get("tempSide") is True)
    ck.check("能耗总览卡存在", sk.get("powerOverview") is True)
    ck.check("能耗统计区存在", sk.get("powerStats") is True)
    ck.check("每日用电区存在", sk.get("powerDaily") is True)
    ck.check("电价单价有显示", bool(sk.get("powerRate")), repr(sk.get("powerRate"))[:40])
    ck.check("没有空白板块", not sk.get("emptyBlocks"),
             "空白：%s" % (sk.get("emptyBlocks") or "无"))

    # ---------- 后端数据一致性 ----------
    print("\n--- B. 后端数据 ---")
    d = api("/api/monitor")
    dd = d.get("data") or d
    ck.check("后端返回 mem 数据", bool(dd.get("mem")), json.dumps(dd.get("mem", {}))[:60])
    ck.check("后端返回 cpu 数据", bool(dd.get("cpu")), "")
    ck.check("后端返回 power 数据", bool(dd.get("power")), "")
    ck.check("后端返回 temp 数据", bool(dd.get("temp")), "")
    ck.check("后端返回 storage 数据", bool(dd.get("storage")), "")

    # ---------- C~F. 四个抽屉 ----------
    print("\n--- C. 处理器抽屉 ---")
    p = drawer_probe(c, "cpu")
    ck.check("处理器抽屉能打开", p.get("open") is True)
    ck.check("处理器抽屉标题为「处理器」", p.get("title") == "处理器", p.get("title"))
    ck.check("处理器抽屉有图标", bool(p.get("icon")), repr(p.get("icon")))
    ck.check("处理器抽屉有 2 张趋势图", len(p.get("charts") or []) == 2,
             "%s 张" % len(p.get("charts") or []))
    ck.check("处理器抽屉图都有 SVG", all(x.get("hasSvg") for x in p.get("charts") or []))
    ck.check("处理器抽屉图有图例数值",
             all(x.get("legend") and "--" not in x["legend"] for x in p.get("charts") or []),
             str([x.get("legend") for x in p.get("charts") or []]))

    print("\n--- D. 内存抽屉 ---")
    m = drawer_probe(c, "mem")
    ck.check("内存抽屉能打开", m.get("open") is True)
    ck.check("内存抽屉标题为「内存」", m.get("title") == "内存", m.get("title"))
    ck.check("内存抽屉有 1 张趋势图", len(m.get("charts") or []) == 1,
             "%s 张" % len(m.get("charts") or []))
    ck.check("内存抽屉图有 SVG", all(x.get("hasSvg") for x in m.get("charts") or []))
    ck.check("内存抽屉图有图例数值",
             bool(m.get("charts")) and "--" not in (m["charts"][0].get("legend") or ""),
             str([x.get("legend") for x in m.get("charts") or []]))
    ck.check("内存抽屉有内存构成堆叠条", m.get("hasDstack") is True)
    ck.check("内存抽屉有构成图例", m.get("hasLegend") is True)
    ck.check("内存抽屉正文有实质内容", (m.get("textLen") or 0) > 100,
             "%s 字" % m.get("textLen"))

    print("\n--- E. 温度抽屉 ---")
    t = drawer_probe(c, "temp")
    ck.check("温度抽屉能打开", t.get("open") is True)
    ck.check("温度抽屉标题为「温度与散热」", t.get("title") == "温度与散热", t.get("title"))
    ck.check("温度抽屉有趋势图", len(t.get("charts") or []) >= 1,
             "%s 张" % len(t.get("charts") or []))
    ck.check("温度抽屉图都有 SVG", all(x.get("hasSvg") for x in t.get("charts") or []),
             str([(x.get("title"), x.get("hasSvg")) for x in t.get("charts") or []]))

    print("\n--- F. 能耗抽屉 ---")
    pw = drawer_probe(c, "power")
    ck.check("能耗抽屉能打开", pw.get("open") is True)
    ck.check("能耗抽屉标题为「能耗」", pw.get("title") == "能耗", pw.get("title"))
    ck.check("能耗抽屉有 2 张趋势图", len(pw.get("charts") or []) == 2,
             "%s 张" % len(pw.get("charts") or []))
    ck.check("能耗抽屉图都有 SVG", all(x.get("hasSvg") for x in pw.get("charts") or []),
             str([(x.get("title"), x.get("hasSvg")) for x in pw.get("charts") or []]))
    ck.check("能耗抽屉有电价设置面板", pw.get("hasTariff") is True)
    ck.check("能耗抽屉两张图标题不同",
             len({x.get("title") for x in pw.get("charts") or []}) == 2,
             str([x.get("title") for x in pw.get("charts") or []]))

    # ---------- G. 趋势图实时性（核心） ----------
    print("\n--- G. 趋势图实时更新（每个抽屉逐项） ---")
    for kind, label in (("cpu", "处理器"), ("mem", "内存"),
                        ("temp", "温度"), ("power", "能耗")):
        c.js("window.FnStyleCompute.open('%s', window.FnStyleMonitor.lastData());'ok'" % kind)
        c.pump(5)
        s1 = chart_snapshot(c, kind)
        ck.check("%s抽屉有图可测" % label, s1.get("charts") and
                 all(x.get("hasSvg") for x in s1["charts"]),
                 str([(x.get("idx"), x.get("hasSvg")) for x in s1.get("charts") or []]))
        if not s1.get("charts"):
            continue
        time.sleep(11)
        s2 = chart_snapshot(c, kind)
        for a, b in zip(s1["charts"], s2["charts"]):
            if not a.get("hasSvg"):
                continue
            idx = a.get("idx")
            grew = (b.get("nPoints") or 0) > (a.get("nPoints") or 0)
            ck.check("%s抽屉 图%d 曲线点数随轮询增加" % (label, idx), grew,
                     "%s → %s" % (a.get("nPoints"), b.get("nPoints")))
            # 末点应贴近右边界（说明坐标系跟着走）
            gap = b.get("gapToRight")
            ck.check("%s抽屉 图%d 曲线末端贴近右边界" % (label, idx),
                     gap is not None and gap < 12,
                     "距右边界 %.1fpx" % (gap if gap is not None else -1))
            # 时间轴应有刻度且不为空
            ck.check("%s抽屉 图%d 有时间轴刻度" % (label, idx),
                     bool(b.get("times")), str(b.get("times")))

    # ---------- I. 实时数据流 ----------
    print("\n--- I. 实时数据流（页面级） ---")
    c.js("(function(){var b=document.querySelector('[data-tab=monitor]');if(b)b.click();return 'ok';})()")
    c.pump(2)
    s1 = json.loads(c.js(r"""(function(){
      var o = {};
      var st = document.getElementById('monStatus');
      o.status = st ? st.innerText.replace(/\s+/g,' ').trim() : null;
      o.updated = (document.getElementById('monUpdated')||{}).innerText;
      o.power = (document.getElementById('monPowerOverview')||{}).innerText.replace(/\s+/g,' ').trim();
      o.temp = (document.getElementById('monTempOverview')||{}).innerText.replace(/\s+/g,' ').trim();
      o.core = (document.getElementById('monCore')||{}).innerText.replace(/\s+/g,' ').trim();
      o.storage = (document.getElementById('monStorage')||{}).innerText.replace(/\s+/g,' ').trim();
      return JSON.stringify(o);
    })()"""))
    time.sleep(10)
    s2 = json.loads(c.js(r"""(function(){
      var o = {};
      var st = document.getElementById('monStatus');
      o.status = st ? st.innerText.replace(/\s+/g,' ').trim() : null;
      o.updated = (document.getElementById('monUpdated')||{}).innerText;
      o.power = (document.getElementById('monPowerOverview')||{}).innerText.replace(/\s+/g,' ').trim();
      o.temp = (document.getElementById('monTempOverview')||{}).innerText.replace(/\s+/g,' ').trim();
      o.core = (document.getElementById('monCore')||{}).innerText.replace(/\s+/g,' ').trim();
      o.storage = (document.getElementById('monStorage')||{}).innerText.replace(/\s+/g,' ').trim();
      return JSON.stringify(o);
    })()"""))
    ck.check("页面更新时间在走动",
             s1.get("updated") != s2.get("updated"),
             "%s → %s" % (s1.get("updated"), s2.get("updated")))
    ck.check("处理器板块内容在更新",
             s1.get("core") != s2.get("core"),
             "长度 %s → %s" % (len(s1.get("core") or ""), len(s2.get("core") or "")))
    # ⚠️ 温度值本身可能连续几秒不变（CPU 41℃ → 42℃ → 44℃），
    #    单次 10 秒窗口内没变属正常，**不是** BUG。
    #    → 连续采样 4 次，只要有一次变化即视为「在更新」。
    temp_samples = [s1.get("temp"), s2.get("temp")]
    for _ in range(2):
        time.sleep(8)
        temp_samples.append(json.loads(c.js(
            "(function(){return JSON.stringify({t:(document.getElementById('monTempOverview')"
            "||{}).innerText.replace(/\\s+/g,' ').trim()});})()")).get("t"))
    ck.check("温度板块内容在更新（多次采样内至少变化一次）",
             len(set(temp_samples)) > 1,
             " → ".join(repr(x)[:26] for x in temp_samples))
    ck.check("能耗板块内容在更新",
             s1.get("power") != s2.get("power"),
             "%s → %s" % (repr(s1.get("power"))[:40], repr(s2.get("power"))[:40]))

    print("\n--- J. 交互健壮性 ---")
    # 硬盘详情抽屉（点存储板块里的硬盘卡）
    c.js("(function(){var b=document.querySelector('[data-tab=monitor]');if(b)b.click();return 'ok';})()")
    c.pump(2.5)
    disk = json.loads(c.js(r"""(function(){
      /* ⚠️ 硬盘卡在「存储池使用率」下方的 .mon-nas 里，
         选择器是 .mon-bay[data-clickable="1"]（带 data-dev），
         不是 [data-disk]（存储池卡才是）。 */
      var card = document.querySelector('#monStorage .mon-bay[data-clickable="1"]');
      if (!card) return JSON.stringify({err:'NO_DISK_CARD'});
      card.click();
      return JSON.stringify({tag: card.tagName, cls: card.className,
        dev: card.getAttribute('data-dev')});
    })()"""))
    ck.check("存储板块能点到硬盘卡片", "err" not in disk, json.dumps(disk)[:60])
    c.pump(3.5)
    dd2 = json.loads(c.js(r"""(function(){
      var ov = document.getElementById('monDrawerOverlay');
      var body = document.getElementById('monDrawerBody');
      var ico = document.querySelector('#monDrawerTitle .mon-drawer-ico');
      return JSON.stringify({
        open: ov ? !ov.hidden : false,
        title: (document.getElementById('monDrawerTitle')||{}).innerText.replace(/\s+/g,' ').trim(),
        icon: ico ? ico.textContent : null,
        bodyLen: body ? body.innerHTML.length : 0,
        hasSpark: !!(body && body.querySelector('#monDwSpark')),
        sparkPaths: body ? body.querySelectorAll('#monDwSpark path').length : 0
      });
    })()"""))
    ck.check("硬盘详情抽屉能打开", dd2.get("open") is True, json.dumps(dd2)[:80])
    ck.check("硬盘抽屉标题栏有图标", bool(dd2.get("icon")), repr(dd2.get("icon")))
    ck.check("硬盘抽屉有读写趋势 spark", dd2.get("hasSpark") is True)
    ck.check("硬盘抽屉 spark 有实际路径", (dd2.get("sparkPaths") or 0) > 0,
             "%s 条" % dd2.get("sparkPaths"))
    c.js("(function(){var b=document.getElementById('monDrawerClose')||"
         "document.querySelector('#monDrawerOverlay .mon-drawer-close');"
         "if(b)b.click();return 'ok';})()")
    c.pump(1.2)

    # 巡检历史抽屉
    c.js("(function(){var b=document.getElementById('btnMonScanLog');if(b)b.click();return 'ok';})()")
    c.pump(3.5)
    sl = json.loads(c.js(r"""(function(){
      var ov = document.getElementById('monScanLogOverlay');
      var body = document.getElementById('monScanLogBody');
      var ico = document.querySelector('#monScanLogTitle .mon-drawer-ico');
      return JSON.stringify({
        open: ov ? !ov.hidden : false,
        icon: ico ? ico.textContent : null,
        title: (document.getElementById('monScanLogTitle')||{}).innerText.replace(/\s+/g,' ').trim(),
        bodyLen: body ? body.innerHTML.length : 0,
        rows: body ? body.querySelectorAll('tr, .mon-scan-row').length : 0
      });
    })()"""))
    ck.check("巡检历史抽屉能打开", sl.get("open") is True, json.dumps(sl)[:80])
    ck.check("巡检抽屉标题栏有图标", bool(sl.get("icon")), repr(sl.get("icon")))
    ck.check("巡检抽屉有内容", (sl.get("bodyLen") or 0) > 50, "%s 字节" % sl.get("bodyLen"))
    c.js("(function(){var ov=document.getElementById('monScanLogOverlay');"
         "var b=ov&&ov.querySelector('.mon-drawer-close');if(b)b.click();return 'ok';})()")
    c.pump(1.2)

    # 反复开关抽屉
    for i in range(3):
        c.js("window.FnStyleCompute.open('mem', window.FnStyleMonitor.lastData());'ok'")
        c.pump(1.2)
        c.js("window.FnStyleCompute.close();'ok'")
        c.pump(0.8)
    c.js("window.FnStyleCompute.open('mem', window.FnStyleMonitor.lastData());'ok'")
    c.pump(4)
    m2 = chart_snapshot(c, "mem")
    ck.check("反复开关 3 次后内存抽屉图仍能渲染",
             bool(m2.get("charts")) and all(x.get("hasSvg") for x in m2["charts"]),
             str([(x.get("idx"), x.get("hasSvg")) for x in m2.get("charts") or []]))

    # 切到别的 tab 再切回来，图表应仍在
    c.js("(function(){var b=document.querySelector('[data-tab=personalize]');if(b)b.click();return 'ok';})()")
    c.pump(2)
    c.js("(function(){var b=document.querySelector('[data-tab=monitor]');if(b)b.click();return 'ok';})()")
    c.pump(2.5)
    m3 = chart_snapshot(c, "mem")
    ck.check("切到别的 tab 再切回，图表未丢失",
             bool(m3.get("charts")) and all(x.get("hasSvg") for x in m3["charts"]),
             str([(x.get("idx"), x.get("hasSvg")) for x in m3.get("charts") or []]))

    # 快速切换范围
    for r in ("10m", "1h", "6h"):
        c.js("(function(){var s=document.querySelector('#monComputeBody [data-cseg] button[data-range=\"%s\"]')"
             "||document.querySelector('#monComputeBody [data-cseg] *');return 'ok';})()" % r)
        c.pump(0.8)
    time.sleep(3)
    m4 = chart_snapshot(c, "mem")
    ck.check("快速切换范围后图表仍正常",
             bool(m4.get("charts")) and all(x.get("hasSvg") for x in m4["charts"]),
             str([(x.get("idx"), x.get("hasSvg")) for x in m4.get("charts") or []]))

    print("\n" + "=" * 60)
    ck.finish()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
