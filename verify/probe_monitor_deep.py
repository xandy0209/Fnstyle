#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""系统监控模块**深度核查**探针（v1.1.65 追加）

背景
----
老板反馈「内存抽屉中的趋势图也没有实时更新，请全面核查
（系统监控模块中的所有其他功能点都必须逐项进行测试）是否还有其他的 BUG」。

probe_monitor_audit.py 覆盖「有没有」，本探针覆盖「对不对」——
逐项校验页面呈现的**数值与后端 API 一致**、格式正确、逻辑自洽；
并覆盖**交互链路**（电价保存、巡检历史、硬盘抽屉换盘、自动刷新开关、柱状图浮层）。

覆盖：
  A. 能耗统计数值一致性（今日/本月用电、电费、预计）
  B. 每日用电柱状图（SVG 结构、日期标签、kWh 浮层数值）
  C. 电价单价展示（与后端 tariff.rate 一致）
  D. 温度板块（dek 摘要、传感器列表）
  E. 存储板块（池数/硬盘数/容量文字）
  F. 状态条（监控项数、运行时长）
  G. 处理器板块（CPU 型号）
  H. 交叉自洽（电费 = 用电 × 单价；今日用电与后端一致）
  I. 交互链路（电价保存 / 巡检历史 / 硬盘换盘 / 自动刷新开关 / 柱状图浮层）

⚠️ v1.1.65：每日用电柱状图的取数方式已修正。
   原断言「柱子带 title 属性」「标签 innerText 匹配 M/D」全是**探针自身的错**：
     · 柱子是 SVG `<rect class="b">`，**本来就没有 title**（数值走 hover 浮层 .pwr-tip）
     · 日期标签是 SVG `<text class="axis-t">`，**SVG 元素没有 innerText**
   → 3 项失败是探针误报，产品无 BUG。

用法：
    python3 verify/probe_monitor_deep.py
前置：Chrome 带 --remote-debugging-port=9222 已启动，NAS 已部署对应版本。
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from probe_base import Checker, api, connect, reload_and_open  # noqa: E402

_PAGE = r"""(function(){
  var g = function(id){ return document.getElementById(id); };
  var txt = function(id){
    var e = g(id); return e ? e.innerText.replace(/\s+/g,' ').trim() : null; };
  var daily = g('monPowerDaily');
  return JSON.stringify({
    status: txt('monStatus'),
    updated: txt('monUpdated'),
    storeCount: txt('monStoreCount'),
    tempDek: txt('monTempDek'),
    tempOverview: txt('monTempOverview'),
    tempSide: txt('monTempSide'),
    powerOverview: txt('monPowerOverview'),
    powerStats: txt('monPowerStats'),
    powerDaily: txt('monPowerDaily'),
    powerRate: txt('monPowerRate'),
    storage: txt('monStorage'),
    core: txt('monCore'),
    /* 每日用电柱状图 —— SVG 结构（v1.1.65 修正取数方式） */
    daily: (function(){
      if (!daily) return {err:'NO BOX'};
      var bars = daily.querySelectorAll('rect.b');
      var out = {svg: daily.querySelectorAll('svg').length,
                 barCount: bars.length,
                 groups: daily.querySelectorAll('g[data-i]').length,
                 labels: [], heights: [], hasLivePt: false};
      var ts = daily.querySelectorAll('text.axis-t');
      for (var i=0;i<ts.length;i++) out.labels.push(ts[i].textContent);
      for (var j=0;j<bars.length;j++) out.heights.push(Number(bars[j].getAttribute('height')));
      if (daily.querySelector('circle.live-pt')) out.hasLivePt = true;
      return out;
    })()
  });
})()"""


def num(s):
    """从字符串里取第一个数字（支持小数）。"""
    if not s:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", s.replace(",", ""))
    return float(m.group()) if m else None


def main():
    ck = Checker("系统监控模块深度核查")
    c = connect()
    reload_and_open(c, settle=8.0)
    c.js("(function(){var b=document.querySelector('[data-tab=monitor]');if(b)b.click();return 'ok';})()")
    c.pump(3.5)

    pg = json.loads(c.js(_PAGE))
    mon = api("/api/monitor")
    dd = mon.get("data") or mon

    # ---------- A. 能耗统计数值一致性 ----------
    print("\n--- A. 能耗统计 ---")
    ps = pg.get("powerStats") or ""
    ck.check("能耗统计区有内容", len(ps) > 20, repr(ps)[:70])
    ck.check("显示今日用电", "今日用电" in ps, repr(ps)[:50])
    ck.check("显示本月用电", "本月用电" in ps)
    ck.check("显示本月预计电费", "本月预计电费" in ps)
    ck.check("显示全年估算", "全年" in ps or "261" in ps, repr(ps)[-70:])
    m_today_kwh = re.search(r"今日用电\s*([\d.]+)\s*kWh", ps)
    ck.check("今日用电有 kWh 数值", bool(m_today_kwh), repr(m_today_kwh.group(1)) if m_today_kwh else ps[:60])
    if m_today_kwh:
        v = float(m_today_kwh.group(1))
        ck.check("今日用电量级合理（0 < v < 100 kWh）", 0 < v < 100, "%.4f kWh" % v)
    m_today_fee = re.search(r"今日用电.*?已计电费\s*¥\s*([\d.]+)", ps)
    ck.check("今日电费有数值", bool(m_today_fee), m_today_fee.group(1) if m_today_fee else "")

    # ---------- B. 每日用电柱状图（v1.1.65 修正取数方式） ----------
    print("\n--- B. 每日用电柱状图 ---")
    dy = pg.get("daily") or {}
    ck.check("每日用电有 SVG 容器", (dy.get("svg") or 0) >= 1, "svg=%s" % dy.get("svg"))
    ck.check("每日用电有柱子（rect.b）", (dy.get("barCount") or 0) >= 1,
             "%s 根" % dy.get("barCount"))
    ck.check("柱子数与数据组数一致",
             dy.get("barCount") == dy.get("groups"),
             "bar=%s group=%s" % (dy.get("barCount"), dy.get("groups")))
    labels = dy.get("labels") or []
    m_label = [t for t in labels if re.match(r"^\d{1,2}/\d{1,2}$", t)]
    ck.check("每日用电有日期标签（M/D 格式）", len(m_label) >= 1, str(labels))
    ck.check("末位标签是「今天」", labels and labels[-1] == "今天", str(labels))
    hs = dy.get("heights") or []
    ck.check("所有柱子高度 > 0", hs and all(h > 0 for h in hs), str(hs))
    ck.check("今日柱有 live 圆点（当天进行中）", bool(dy.get("hasLivePt")))

    # ---------- C. 电价单价 ----------
    print("\n--- C. 电价单价 ---")
    pr = pg.get("powerRate") or ""
    ck.check("显示电价单价", "电价" in pr, repr(pr)[:60])
    m_rate = re.search(r"电价\s*([\d.]+)\s*元/kWh", pr)
    ck.check("电价有数值", bool(m_rate), m_rate.group(1) if m_rate else repr(pr)[:60])
    if m_rate:
        r = float(m_rate.group(1))
        ck.check("电价量级合理（0.1 ~ 2 元/kWh）", 0.1 <= r <= 2, "%.4f" % r)
        try:
            tf = api("/api/power/tariff")
            bd = tf.get("data") or tf
            br = bd.get("rate")
            if br is not None:
                ck.check("页面电价与后端 tariff.rate 一致",
                         abs(float(br) - r) < 0.0001,
                         "页面 %.4f vs 后端 %.4f" % (r, float(br)))
        except Exception as e:
            ck.check("能取到后端 tariff", False, str(e)[:60])

    # ---------- D. 温度板块 ----------
    print("\n--- D. 温度板块 ---")
    dek = pg.get("tempDek") or ""
    ck.check("温度摘要非空", len(dek) > 3, repr(dek)[:60])
    ck.check("温度摘要含 CPU 温度", "CPU" in dek, repr(dek)[:60])
    m_c = re.search(r"CPU\s*(\d+)\s*℃", dek)
    ck.check("CPU 温度数值合法", bool(m_c) and 0 <= int(m_c.group(1)) <= 120,
             m_c.group(1) if m_c else dek[:40])
    ov = pg.get("tempOverview") or ""
    ck.check("温度总览有 CPU/主板温度", "CPU" in ov and "主板" in ov, repr(ov)[:70])
    ck.check("温度总览含阈值说明", "阈值" in ov, repr(ov)[:70])

    # ---------- E. 存储板块 ----------
    print("\n--- E. 存储板块 ---")
    sc = pg.get("storeCount") or ""
    ck.check("存储计数有内容", len(sc) > 3, repr(sc)[:50])
    ck.check("存储计数含存储池数", "存储池" in sc, repr(sc)[:50])
    ck.check("存储计数含硬盘数", "硬盘" in sc, repr(sc)[:50])
    st = pg.get("storage") or ""
    ck.check("存储板块有容量文字", ("TB" in st or "GB" in st), repr(st)[:80])
    ck.check("存储板块有盘位信息", "盘" in st, repr(st)[:60])

    # ---------- F. 状态条 ----------
    print("\n--- F. 状态条 ---")
    stt = pg.get("status") or ""
    ck.check("状态条显示运行中", "运行中" in stt or "监控" in stt, repr(stt)[:60])
    m_n = re.search(r"(\d+)\s*项数据", stt)
    ck.check("状态条显示可用数据项数", bool(m_n), m_n.group(1) if m_n else repr(stt)[:40])
    if m_n:
        ck.check("可用数据项数 ≥ 4", int(m_n.group(1)) >= 4, m_n.group(1))
    upd = pg.get("updated") or ""
    ck.check("状态条显示更新时间", "更新于" in upd, repr(upd)[:50])
    m_run = re.search(r"运行\s*(\d+)\s*天\s*(\d+)\s*小时", upd)
    ck.check("状态条显示运行时长", bool(m_run), repr(upd)[:50])

    # ---------- G. 处理器板块 ----------
    print("\n--- G. 处理器板块 ---")
    core = pg.get("core") or ""
    ck.check("处理器板块有内容", len(core) > 30, repr(core)[:80])
    ck.check("处理器板块显示 CPU 型号或核心数",
             ("核" in core) or ("Intel" in core) or ("J3455" in core) or ("CPU" in core),
             repr(core)[:80])

    # ---------- H. 交叉自洽 ----------
    print("\n--- H. 交叉自洽 ---")
    if m_today_kwh and m_rate:
        kwh = float(m_today_kwh.group(1))
        r2 = float(m_rate.group(1))
        expect = kwh * r2
        if m_today_fee:
            fee = float(m_today_fee.group(1))
            ck.check("今日电费 ≈ 今日用电 × 单价（±0.02）",
                     abs(fee - expect) <= 0.02,
                     "电费 %.2f vs 期望 %.2f（%.4f kWh × %.4f）" % (fee, expect, kwh, r2))
    pw = dd.get("power") or {}
    tk = pw.get("today_kwh")
    if tk is not None and m_today_kwh:
        ck.check("今日用电与后端 today_kwh 一致（±1%）",
                 abs(float(tk) - float(m_today_kwh.group(1))) <= max(0.01, float(tk) * 0.01),
                 "页面 %s vs 后端 %.4f" % (m_today_kwh.group(1), float(tk)))

    # ---------- I. 交互链路（v1.1.65 新增） ----------
    print("\n--- I. 交互链路 ---")

    # ⚠️ v1.1.65：**必须先关掉所有抽屉/浮层**再测底层元素的交互。
    #    reload_and_open() 会打开能耗抽屉 → .mon-drawer-overlay 全屏遮罩
    #    盖住页面 → elementFromPoint 返回遮罩而不是柱子
    #    → 鼠标事件打在遮罩上 → hover 浮层不出现（探针误报为产品 BUG）。
    c.js(r"""(function(){
      ['monDrawerOverlay','monComputeOverlay','monScanLogOverlay'].forEach(function(id){
        var e = document.getElementById(id);
        if (e) e.hidden = true;
      });
      document.querySelectorAll('.mon-drawer-overlay,.mon-scanlog-overlay')
        .forEach(function(e){ e.hidden = true; });
      document.body.classList.remove('drawer-open','overlay-open','no-scroll');
      return 'ok';
    })()""")
    c.pump(1.0)

    # I-1 柱状图 hover 浮层：逐柱应显示日期 + kWh 数值
    #     ⚠️ v1.1.65：**必须先 scrollIntoView 把柱子滚进视口**。
    #     每日用电在页面靠下位置，柱子的 viewport y 可能是 2233（视口高 1400）
    #     → 鼠标派发到视口外 → pointermove 不触发 → 浮层不出现（探针误报）。
    hb = c.js(r"""(function(){
      var host = document.getElementById('monPowerBars');
      if (!host) return 'NO HOST';
      var g0 = host.querySelector('g[data-i]');
      if (!g0) return 'NO GROUP';
      var r = g0.querySelector('rect.b');
      if (!r) return 'NO BAR';
      r.scrollIntoView({block:'center'});
      var b = r.getBoundingClientRect();
      return JSON.stringify({x: b.x + b.width/2, y: b.y + b.height/2});
    })()""")
    if hb and hb.startswith("{"):
        pos = json.loads(hb)
        c.send("Input.dispatchMouseEvent",
               {"type": "mouseMoved", "x": pos["x"], "y": pos["y"],
                "button": "none", "clickCount": 0})
        c.pump(1.0)
        tip = c.js(r"""(function(){
          var t = document.querySelector('#monPowerBars .pwr-tip');
          if (!t) return '';
          return (t.classList.contains('show') ? 'SHOW:' : 'HIDE:') +
                 t.innerText.replace(/\s+/g,' ').trim();
        })()""")
        ck.check("柱状图 hover 浮层出现", bool(tip) and tip.startswith("SHOW:"), tip[:60])
        ck.check("浮层含 kWh 数值", bool(tip) and "kWh" in tip, tip[:60])
        m_tk = re.search(r"([\d.]+)\s*kWh", tip or "")
        ck.check("浮层 kWh 数值可解析且非负",
                 bool(m_tk) and float(m_tk.group(1)) >= 0,
                 m_tk.group(1) if m_tk else tip[:50])
        c.send("Input.dispatchMouseEvent",
               {"type": "mouseMoved", "x": 2, "y": 2,
                "button": "none", "clickCount": 0})
        c.pump(0.4)
    else:
        ck.check("柱状图 hover 浮层出现", False, str(hb)[:60])

    # I-2 自动刷新开关：关掉应暂停（状态条提示），再打开应恢复
    auto_state = c.js(r"""(function(){
      var a = document.getElementById('monAuto');
      if (!a) return JSON.stringify({err:'NO AUTO'});
      return JSON.stringify({checked: a.checked});
    })()""")
    ast = json.loads(auto_state or "{}")
    ck.check("自动刷新开关存在", "checked" in ast, str(ast))
    if "checked" in ast:
        c.js(r"""(function(){
          var a = document.getElementById('monAuto');
          a.checked = false;
          a.dispatchEvent(new Event('change', {bubbles:true}));
          return 'off';
        })()""")
        c.pump(1.2)
        st_off = c.js("(function(){var e=document.getElementById('monStatus');"
                      "return e?e.innerText.replace(/\\s+/g,' ').trim():'';})()")
        ck.check("关闭自动刷新后状态提示「已暂停」", "暂停" in (st_off or ""), repr(st_off)[:50])
        c.js(r"""(function(){
          var a = document.getElementById('monAuto');
          a.checked = true;
          a.dispatchEvent(new Event('change', {bubbles:true}));
          return 'on';
        })()""")
        c.pump(2.5)
        st_on = c.js("(function(){var e=document.getElementById('monStatus');"
                     "return e?e.innerText.replace(/\\s+/g,' ').trim():'';})()")
        ck.check("重新开启自动刷新后不再提示暂停", "暂停" not in (st_on or ""), repr(st_on)[:50])

    # I-3 硬盘详情抽屉：打开 → 下拉换盘 → 内容随盘变化 → Esc 关闭
    #     ⚠️ overlay 的 id 是 **monDrawerOverlay**（不是 monDiskDrawer）。
    c.js(r"""(function(){
      var bay = document.querySelector('#monStorage .mon-bay[data-clickable="1"]');
      if (bay) bay.click();
      return 'ok';
    })()""")
    c.pump(2.5)
    dw = c.js(r"""(function(){
      var ov = document.getElementById('monDrawerOverlay');
      var sel = document.getElementById('monDrawerDiskSel');
      var body = document.getElementById('monDrawerBody');
      return JSON.stringify({
        open: ov ? !ov.hidden : false,
        hasSel: !!sel,
        options: sel ? Array.prototype.map.call(sel.options, function(o){return o.value;}) : [],
        bodyLen: body ? (body.innerText||'').trim().length : 0,
        bodyHead: body ? (body.innerText||'').replace(/\s+/g,' ').trim().slice(0,60) : ''
      });
    })()""")
    dwv = json.loads(dw or "{}")
    ck.check("硬盘详情抽屉能打开", bool(dwv.get("open")), str(dwv)[:60])
    ck.check("硬盘抽屉有换盘下拉", bool(dwv.get("hasSel")), str(dwv.get("options"))[:60])
    ck.check("硬盘抽屉内容非空", (dwv.get("bodyLen") or 0) > 20, str(dwv.get("bodyHead"))[:60])
    opts = dwv.get("options") or []
    if len(opts) >= 2:
        first_head = dwv.get("bodyHead") or ""
        c.js(r"""(function(){
          var sel = document.getElementById('monDrawerDiskSel');
          sel.selectedIndex = 1;
          sel.dispatchEvent(new Event('change', {bubbles:true}));
          return 'ok';
        })()""")
        c.pump(2.0)
        head2 = c.js(r"""(function(){
          var body = document.getElementById('monDrawerBody');
          return body ? (body.innerText||'').replace(/\s+/g,' ').trim().slice(0,60) : '';
        })()""")
        ck.check("切换硬盘后抽屉内容随之变化", head2 != first_head,
                 "%r → %r" % (first_head[:30], (head2 or "")[:30]))
    else:
        ck.check("切换硬盘后抽屉内容随之变化", False, "下拉不足 2 项：%s" % opts)

    # I-4 巡检历史抽屉：打开 → 有内容（巡检流水 / 盘位快照）
    c.js(r"""(function(){
      var b = document.getElementById('btnMonScanLog');
      if (b) b.click();
      return 'ok';
    })()""")
    c.pump(2.0)
    sl = c.js(r"""(function(){
      var ov = document.getElementById('monScanLogOverlay');
      var body = document.getElementById('monScanLogBody');
      return JSON.stringify({
        open: ov ? !ov.hidden : false,
        bodyLen: body ? (body.innerText||'').trim().length : 0,
        bodyText: body ? (body.innerText||'').replace(/\s+/g,' ').trim().slice(0,160) : ''
      });
    })()""")
    slv = json.loads(sl or "{}")
    ck.check("巡检历史抽屉能打开", bool(slv.get("open")), str(slv)[:60])
    ck.check("巡检历史内容非空", (slv.get("bodyLen") or 0) > 10, str(slv.get("bodyText"))[:80])
    ck.check("巡检历史含「巡检」字样", "巡检" in (slv.get("bodyText") or ""),
             str(slv.get("bodyText"))[:80])

    # I-5 电价保存链路：输入新单价 → 保存 → 后端 tariff 变更 → 页面回显
    #     ⚠️ 先读当前值，保存后立刻改回原值，避免污染 NAS 上的真实配置。
    c.js(r"""(function(){
      var b = document.getElementById('btnMonScanLog');
      var ov = document.getElementById('monScanLogOverlay');
      if (ov && !ov.hidden) ov.hidden = true;
      var x = document.getElementById('btnCloseMonScanLog');
      if (x) x.click();
      return 'ok';
    })()""")
    c.pump(1.0)
    # 打开能耗抽屉（电价面板在能耗抽屉里）
    c.js("(function(){window.FnStyleCompute.open('power', "
         "window.FnStyleMonitor.lastData());return 'ok';})()")
    c.pump(3.0)
    tb = c.js(r"""(function(){
      var box = document.querySelector('#monComputeBody [data-tariff-box]') ||
                document.querySelector('#monComputeBody .tariff-box');
      if (!box) return 'NO BOX';
      var rt = box.querySelector('[data-tariff-rate]');
      return JSON.stringify({
        hasBox: true,
        rate: rt ? rt.value : null,
        hasSave: !!box.querySelector('[data-tariff-save]'),
        hasMsg: !!box.querySelector('[data-tariff-msg]')
      });
    })()""")
    tbv = json.loads(tb or "{}")
    ck.check("能耗抽屉里有电价面板", bool(tbv.get("hasBox")), str(tbv)[:60])
    ck.check("电价面板有单价输入框", tbv.get("rate") is not None, str(tbv.get("rate")))
    ck.check("电价面板有保存按钮", bool(tbv.get("hasSave")))
    ck.check("电价面板有提示位", bool(tbv.get("hasMsg")))

    if tbv.get("rate") is not None:
        try:
            before = float(tbv["rate"])
            test_rate = round(before + 0.0137, 4)
            c.js(r"""(function(){
              var box = document.querySelector('#monComputeBody [data-tariff-box]') ||
                        document.querySelector('#monComputeBody .tariff-box');
              var rt = box.querySelector('[data-tariff-rate]');
              rt.value = %s;
              rt.dispatchEvent(new Event('input', {bubbles:true}));
              return rt.value;
            })()""" % json.dumps(str(test_rate)))
            c.pump(0.6)
            c.js(r"""(function(){
              var box = document.querySelector('#monComputeBody [data-tariff-box]') ||
                        document.querySelector('#monComputeBody .tariff-box');
              var b = box.querySelector('[data-tariff-save]');
              b.click();
              return 'clicked';
            })()""")
            # ⚠️ 「已保存」提示 2.5 秒后被 setTimeout 清空
            #    （monitor.js applyTariff 之后那段），
            #    读太晚会读到空串 → 误判「无提示」。
            #    → 在 1.2 秒内读（fetch + 重渲染已完成）。
            c.pump(1.2)
            msg = c.js(r"""(function(){
              var box = document.querySelector('#monComputeBody [data-tariff-box]') ||
                        document.querySelector('#monComputeBody .tariff-box');
              var m = box.querySelector('[data-tariff-msg]');
              return m ? m.innerText.replace(/\s+/g,' ').trim() : '';
            })()""")
            ck.check("保存后有成功/失败提示", bool(msg), repr(msg)[:60])
            ck.check("保存后提示为「已保存」", "已保存" in (msg or ""), repr(msg)[:60])
            c.pump(2.0)
            tf = api("/api/power/tariff")
            bd = tf.get("data") or tf
            ck.check("保存后后端 tariff.rate 已更新",
                     abs(float(bd.get("rate")) - test_rate) < 0.0001,
                     "期望 %.4f，后端 %.4f" % (test_rate, float(bd.get("rate"))))
            # 立刻改回原值
            c.js(r"""(function(){
              var box = document.querySelector('#monComputeBody [data-tariff-box]') ||
                        document.querySelector('#monComputeBody .tariff-box');
              var rt = box.querySelector('[data-tariff-rate]');
              rt.value = %s;
              rt.dispatchEvent(new Event('input', {bubbles:true}));
              var b = box.querySelector('[data-tariff-save]');
              b.click();
              return 'restored';
            })()""" % json.dumps(str(before)))
            c.pump(3.0)
            tf2 = api("/api/power/tariff")
            bd2 = tf2.get("data") or tf2
            ck.check("已把电价还原为原值", abs(float(bd2.get("rate")) - before) < 0.0001,
                     "原值 %.4f，现在 %.4f" % (before, float(bd2.get("rate"))))
        except Exception as e:
            ck.check("电价保存链路可测", False, str(e)[:80])

    print("\n" + "=" * 60)
    ck.finish()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
