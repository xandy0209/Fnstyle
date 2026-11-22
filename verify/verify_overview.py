#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_overview.py —— 「系统重要内容概览」前端渲染测试（v1.1.72）

在**真实 Chromium** 里跑（Playwright），加载真实的 app/ui/static/js/overview.js，
喂真实的监控快照 JSON，然后逐项断言 DOM 结果。

为什么用真浏览器而不是 jsdom / mini-DOM：
  · 概览用了 createElementNS / replaceChildren / WeakMap / SVG 几何属性，
    mini-DOM 很难忠实还原（内联 SVG 的 innerHTML 不解析 → 选择器失效）；
  · 阈值告警色、大数字字号、仪表弧长都要落到**真实渲染**上才算数。

⚠️ v1.1.70 变更：概览大数字**弃用 5×7 LED 点阵**，改用普通字体
   （--font-display）。原先「点阵字模 / 点亮点数」整组断言作废，
   改为断言：文本内容、字体族、字号分级、颜色分级。

⚠️ v1.1.71 变更：卡片高度压缩（265 → 215px）+ 左右水平对齐。
   · 删除 .ov-metric 的 min-height:236px，改为**逐行锁高**
     （m-top 26 / m-main 76 / m-sub 20 / m-foot 36）；
   · 仪表 92 → 76px，中心图标 24 → 20px（保持 26% 官方比例）；
   · .ov-m-main 的 align-items 从 baseline 改 center、
     .ov-dm 的 line-height 从 .8 改 1 —— baseline + .8 会让行盒比字形矮，
     字形整体下沉 19px，与仪表中心错位。

⚠️ v1.1.72 变更（老板 4 条反馈）：
   ① 「不要最外层的卡片，直接呈现内部的卡片即可」
      → 去掉 .card > header + .body 外壳，改 .ov-wrap 轻量标题行；
   ② 「一行默认显示 3 个卡片」→ 保持 repeat(3, 1fr)；
   ③ 「卡片高度减小，下方尽量不要留白」
      → 215 → ~176px；padding 底部收到 8px；m-foot 36 → 30px；
   ④ 「处理器、内存、温度、功耗卡片底部缺少趋势显示」
      → **真 bug**：setSpark 传的是裸数组 [live.cpu]，而 makeSpark.draw()
        取的是 s.values → undefined → vals=[] → 一条线都不画。
        只有网络那张碰巧用了 {values:[...]} 才正常。
        修复：统一传 {values:[...]}，并给 makeSpark 加 normSeries 容错。

运行：python3 verify/verify_overview.py
"""
import http.server
import json
import socketserver
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path('/Volumes/SSD-Data/trae/FnOsApp')
UI = ROOT / 'app/ui'

RESULTS = []


def rec(group, item, ok, detail=''):
    RESULTS.append((group, item, 'PASS' if ok else 'FAIL', detail))
    print(('  ✅' if ok else '  ❌') + ' ' + group + ' / ' + item +
          (('  → ' + str(detail)) if detail != '' else ''))
    return ok


def eq(group, item, actual, expected):
    ok = actual == expected
    return rec(group, item, ok,
               '实际=' + json.dumps(actual, ensure_ascii=False) +
               ' 期望=' + json.dumps(expected, ensure_ascii=False))


def truthy(group, item, v, detail=''):
    return rec(group, item, bool(v), detail)


def section(t):
    print('\n【' + t + '】')


HOST_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<link rel="stylesheet" href="static/css/app.css">
<style>
  :root {
    --bg: #14161a; --bg-elevated: #1b1e24; --bg-hover: #262a32;
    --border: #2a2f38; --border-strong: #3a4150;
    --accent: #4aa8ff; --accent-soft: rgba(74,168,255,.16);
    --success: #35c48a; --warning: #f0a92c; --warning-soft: rgba(240,169,44,.5);
    --danger: #e5554f;
    /* ⚠️ v1.1.71：概览色阶用的实心中间档（不能复用半透明的 --warning-soft） */
    --ramp-mid: #5b8fd6; --ramp-mid-2: #d18b16;
    --text-primary: #e8eaed; --text-secondary: #a9b0bb; --text-muted: #6f7784;
    --radius: 14px; --font-mono: ui-monospace, Menlo, monospace;
  }
  body { background: var(--bg); color: var(--text-primary);
         font-family: system-ui, -apple-system, "PingFang SC", sans-serif;
         margin: 0; padding: 16px; }
  #wrap { width: 1180px; }
</style></head>
<body><div id="wrap">
  <div class="ov-wrap" id="overviewCard">
    <div class="ov-wrap-head">
      <span class="ov-wrap-title">系统重要内容概览</span>
      <span class="ov-wrap-hint">六项核心指标 · 点击卡片查看详情</span>
    </div>
    <div id="monOverview"></div>
  </div>
  <div class="card" id="storageCard">
    <header><h2>存储</h2></header>
    <div class="body" id="monStorage"></div>
  </div>
</div>
<script src="static/js/overview.js"></script>
</body></html>
"""

SNAP = {
    "cpu": {"available": True, "percent": 32.4, "cores": 4,
            "freqText": "2.20 GHz", "loadavg": ["0.15", "0.12", "0.10"]},
    "mem": {"available": True, "percent": 42, "totalText": "7.8 GiB",
            "usedText": "3.3 GiB", "availText": "4.5 GiB"},
    "storage": {
        "summary": {"total": 8589934592, "used": 5926501888,
                    "percent": 69, "poolCount": 1, "diskCount": 2},
        "volumes": [{"name": "vol1", "total": 8589934592,
                     "used": 5926501888, "percent": 69}],
        "disks": [{"name": "sda", "percent": 70}, {"name": "sdb", "percent": 68}]},
    "network": {"available": True, "rxRate": 2048, "txRate": 512,
                "todayRx": 1048576, "todayTx": 524288, "ifaceCount": 2},
    "temp": {"temps": [{"type": "cpu", "value": 45}], "mbTemp": 38, "disks": []},
    "power": {"ok": True, "source": "rapl", "total": 29.0,
              "estimation_params": {"tdp_w": 65.0, "base_w": 20.0,
                                     "disk_typical_w": 8.0, "nic_fixed_w": 3.0,
                                     "disks": 2, "nics": 2}},
    "powerStats": {"today_kwh": 0.6, "today_yuan": 0.36},
}


def run_tests(page):
    # ----------------------------------------------------------
    section('L 概览模块加载')
    # ----------------------------------------------------------
    eq('加载', 'window.FnStyleOverview 已定义',
       page.evaluate('typeof window.FnStyleOverview'), 'object')
    truthy('加载', '对外接口含 render',
           page.evaluate('typeof window.FnStyleOverview.render'), 'function')
    truthy('加载', '对外接口含 reset',
           page.evaluate('typeof window.FnStyleOverview.reset'), 'function')

    # ----------------------------------------------------------
    section('A 大数字（普通字体，非点阵）')
    # ----------------------------------------------------------
    # ⚠️ v1.1.70：老板指示「不使用点阵数字，保持与其他的数字字体一致」——
    #    概览大数字改用 .dnum > .dm 普通文本（--font-display，700，tabular-nums）。
    #    下面断言：文本内容正确 + **不含 SVG 点阵** + 字体族/字重正确。
    page.evaluate("""() => {
      const host = document.getElementById('monOverview');
      window.FnStyleOverview.render(host, %s);
    }""" % json.dumps(SNAP, ensure_ascii=False))

    res = page.evaluate("""() => {
      const host = document.getElementById('monOverview');
      const e = host.querySelector('[data-ov-val="cpuV"]');
      const cs = getComputedStyle(e);
      return {
        text: e.textContent,
        tag: e.tagName,
        hasSvg: !!e.querySelector('svg'),
        svgCount: e.querySelectorAll('svg').length,
        font: cs.fontFamily,
        weight: cs.fontWeight,
        tabular: cs.fontVariantNumeric,
        size: parseFloat(cs.fontSize),
        lineHeight: cs.lineHeight,
        letter: cs.letterSpacing,
      };
    }""")
    eq('数字', '处理器大数字文本 = 32', res['text'], '32')
    eq('数字', '是普通 span（不是 SVG）', res['tag'], 'SPAN')
    eq('数字', '不含点阵 SVG（v1.1.70 要求）', res['hasSvg'], False)
    eq('数字', '子元素里没有 svg', res['svgCount'], 0)
    truthy('数字', '字体族用 --font-display（SF Pro Display）',
           'SF Pro Display' in res['font'], res['font'])
    eq('数字', '字重 700', res['weight'], '700')
    truthy('数字', '等宽数字（tabular-nums）',
           'tabular-nums' in res['tabular'], res['tabular'])
    # ⚠️ v1.1.72：大数字 --dm-h 38 → 32px（配合卡片整体压缩）
    truthy('数字', '字号约 34px（--dm-h 32 × 1.08）',
           30 <= res['size'] <= 40, res['size'])
    truthy('数字', '负字距（-.04em，紧凑排版）',
           res['letter'] and float(res['letter'].replace('px', '')) < 0,
           res['letter'])

    # ----------------------------------------------------------
    section('A2 分级着色（rampInk / rampAt）')
    # ----------------------------------------------------------
    res = page.evaluate("""() => {
      const I = window.FnStyleOverview._internal;
      const r = {};
      // rampInk：4 档硬色
      r.cpuOk = I.rampInk(10, 'cpu');
      r.cpuMid = I.rampInk(70, 'cpu');
      r.cpuWarn = I.rampInk(85, 'cpu');
      r.cpuCrit = I.rampInk(95, 'cpu');
      r.memOk = I.rampInk(10, 'mem');
      r.capWarn = I.rampInk(85, 'cap');
      r.capCrit = I.rampInk(95, 'cap');
      r.tempOk = I.rampInk(40, 'temp');
      r.tempWarn = I.rampInk(80, 'temp');
      r.tempCrit = I.rampInk(90, 'temp');
      // rampAt：连续取色
      r.atLow = I.rampAt(5, 'cpu');
      r.atHigh = I.rampAt(95, 'cpu');
      r.atWarn = I.rampAt(85, 'cpu');
      r.atCrit = I.rampAt(99, 'cpu');
      // 阈值表
      r.th = I.TH;
      // inkTo 脏检查
      const el = document.createElement('span');
      I.inkTo(el, 'var(--accent)');
      const c1 = el.style.color;
      I.inkTo(el, 'var(--accent)');
      r.inkStable = (el.style.color === c1);
      // ⚠️ v1.1.71 防回归：色阶**绝不能**返回半透明的背景色变量
      //    （--warning-soft 是 12% 透明的背景色，当文字色用会发灰发淡）
      r.badInk = [];
      r.badAt = [];
      ['cpu', 'mem', 'cap', 'temp', 'gen'].forEach(function (k) {
        for (let v = 0; v <= 100; v += 1) {
          const ink = I.rampInk(v, k);
          if (String(ink).indexOf('--warning-soft') >= 0) r.badInk.push(k + ':' + v + '=' + ink);
          const at = I.rampAt(v, k);
          if (String(at).indexOf('--warning-soft') >= 0) r.badAt.push(k + ':' + v + '=' + at);
        }
      });
      // 色阶里出现的所有变量名（供人工核对）
      r.vars = (function () {
        const set = new Set();
        ['cpu', 'mem', 'cap', 'temp', 'gen'].forEach(function (k) {
          for (let v = 0; v <= 100; v += 5) {
            [I.rampInk(v, k), I.rampAt(v, k)].forEach(function (s) {
              const m = String(s).match(/var\((--[a-z0-9-]+)\)/);
              if (m) set.add(m[1]);
            });
          }
        });
        return [...set].sort();
      })();
      return r;
    }""")
    eq('着色', 'CPU 正常档 = 品牌蓝', res['cpuOk'], 'var(--accent)')
    # 70% 已越过 st[2]=68（蓝→橙的过渡端点）→ 取 st[2] 实色
    # （与 fnmonitor rampInk 的 t>0.98 → 返回 st[i] 同一套逻辑）
    # ⚠️ v1.1.71：st[2] 从 --warning-soft 改成 --ramp-mid ——
    #    --warning-soft 是 12% 透明度的**背景色**，当文字色用会与底色相混
    #    → 发灰发淡（实机实测存储 69% 渲染成米色，几乎看不清）。
    eq('着色', 'CPU 70% 越过过渡端点 = 橙调实色', res['cpuMid'],
       'var(--ramp-mid)')
    eq('着色', 'CPU 告警档 = 橙', res['cpuWarn'], 'var(--warning)')
    eq('着色', 'CPU 严重档 = 红', res['cpuCrit'], 'var(--danger)')
    eq('着色', '内存正常档 = 品牌蓝', res['memOk'], 'var(--accent)')
    eq('着色', '容量告警档 = 橙', res['capWarn'], 'var(--warning)')
    eq('着色', '容量严重档 = 红', res['capCrit'], 'var(--danger)')
    eq('着色', '温度正常档 = 品牌蓝', res['tempOk'], 'var(--accent)')
    eq('着色', '温度告警档 = 橙', res['tempWarn'], 'var(--warning)')
    eq('着色', '温度严重档 = 红', res['tempCrit'], 'var(--danger)')
    # 5% 落在 st[0]→st[1] 区间 → 混合色（同色混合视觉上仍是蓝）
    truthy('着色', 'rampAt 低位取到色值', res['atLow'] != '', res['atLow'])
    truthy('着色', 'rampAt 低位含品牌蓝', 'var(--accent)' in res['atLow'], res['atLow'])
    eq('着色', 'rampAt 告警线 = 橙', res['atWarn'], 'var(--warning)')
    eq('着色', 'rampAt 严重线 = 红', res['atCrit'], 'var(--danger)')
    truthy('着色', 'rampAt 高位为混合色或实色',
           res['atHigh'] != '', res['atHigh'])
    eq('着色', '阈值 CPU 告警 85', res['th']['cpuWarn'], 85)
    eq('着色', '阈值 CPU 严重 95', res['th']['cpuCrit'], 95)
    eq('着色', '阈值 温度告警 80', res['th']['tempWarn'], 80)
    eq('着色', '阈值 温度严重 90', res['th']['tempCrit'], 90)
    truthy('着色', 'inkTo 幂等（同色不重复写）', res['inkStable'])
    # ⚠️ v1.1.71 防回归：色阶不得返回半透明背景色变量
    #    （--warning-soft 是 12% 透明度背景色，当文字色用 → 发灰发淡）
    eq('着色', 'rampInk 全量程不返回 --warning-soft', res['badInk'], [])
    eq('着色', 'rampAt 全量程不返回 --warning-soft', res['badAt'], [])
    truthy('着色', '色阶只用实心色变量',
           res['vars'] and '--warning-soft' not in res['vars'],
           res['vars'])

    # ----------------------------------------------------------
    section('A3 官方图标（fnmonitor sprite 内联）')
    # ----------------------------------------------------------
    res = page.evaluate("""() => {
      const I = window.FnStyleOverview._internal;
      const r = {};
      // 图标构建器：24×24 坐标系
      const g = I.buildIcon(I.ICONS.cpu);
      r.cpuKinds = [...g.childNodes].map(n => n.tagName);
      r.cpuHasRect = !!g.querySelector('rect');
      r.cpuHasPath = !!g.querySelector('path');
      // 存储图标含 ellipse
      const gs = I.buildIcon(I.ICONS.storage);
      r.storageHasEllipse = !!gs.querySelector('ellipse');
      // 内存图标：1 rect + 1 path
      const gm = I.buildIcon(I.ICONS.mem);
      r.memN = gm.childNodes.length;
      // 闪电：1 path
      const gb = I.buildIcon(I.ICONS.power);
      r.boltN = gb.childNodes.length;
      // 全部描边、无填充
      const allStroke = [...g.childNodes].every(n =>
        n.getAttribute('stroke') === 'currentColor' &&
        n.getAttribute('fill') === 'none');
      r.allStroke = allStroke;
      r.strokeW = g.childNodes[0].getAttribute('stroke-width');
      // 箭头
      const gd = I.buildIcon(I.ARROWS.down);
      r.arrowD = gd.querySelector('path').getAttribute('d');
      const gu = I.buildIcon(I.ARROWS.up);
      r.arrowU = gu.querySelector('path').getAttribute('d');
      r.arrowDiff = r.arrowD !== r.arrowU;
      return r;
    }""")
    eq('图标', 'CPU 图标 = rect + rect + path', res['cpuKinds'],
       ['rect', 'rect', 'path'])
    truthy('图标', 'CPU 图标含 rect', res['cpuHasRect'])
    truthy('图标', 'CPU 图标含 path（引脚）', res['cpuHasPath'])
    truthy('图标', '存储图标含 ellipse（圆柱顶面）', res['storageHasEllipse'])
    eq('图标', '内存图标 = 2 个元素', res['memN'], 2)
    eq('图标', '闪电图标 = 1 个元素', res['boltN'], 1)
    truthy('图标', '图标全部描边 currentColor / fill:none', res['allStroke'])
    eq('图标', '描边宽度 1.6（对齐 fnmonitor）', res['strokeW'], '1.6')
    truthy('图标', '上下行箭头图形不同', res['arrowDiff'])
    truthy('图标', '下行箭头是竖线+下尖',
           'M12 5v14' in res['arrowD'], res['arrowD'])
    truthy('图标', '上行箭头是竖线+上尖',
           'M12 19V5' in res['arrowU'], res['arrowU'])

    # ----------------------------------------------------------
    section('B 环形仪表 makeGauge')
    # ----------------------------------------------------------
    res = page.evaluate("""() => {
      const I = window.FnStyleOverview._internal;
      const g = I.makeGauge(null);
      const val = g.el.childNodes[0].childNodes[1];
      const C = 2 * Math.PI * 40, A = C * 0.75;
      const r = { dasharray: val.getAttribute('stroke-dasharray'),
                  init: val.getAttribute('stroke-dashoffset'), A: A, C: C };
      g.set(0.5); r.half = parseFloat(val.style.strokeDashoffset);
      g.set(1);   r.full = parseFloat(val.style.strokeDashoffset);
      g.set(0);   r.zero = parseFloat(val.style.strokeDashoffset);
      g.set(2, 'var(--danger)'); r.over = parseFloat(val.style.strokeDashoffset);
      r.tone = val.style.stroke;
      g.set(-1);  r.under = parseFloat(val.style.strokeDashoffset);
      return r;
    }""")
    truthy('Gauge', 'dasharray = 弧长 + 周长',
           res['dasharray'] == '%.2f %.2f' % (res['A'], res['C']), res['dasharray'])
    truthy('Gauge', '初始 dashoffset = 满弧',
           abs(float(res['init']) - res['A']) < 0.02, res['init'])
    truthy('Gauge', 'set(0.5) → 一半弧长',
           abs(res['half'] - res['A'] * 0.5) < 0.5, res['half'])
    truthy('Gauge', 'set(1) → 0', abs(res['full']) < 0.01, res['full'])
    truthy('Gauge', 'set(0) → 满弧', abs(res['zero'] - res['A']) < 0.5, res['zero'])
    truthy('Gauge', 'set(2) 被 clamp 到 1', abs(res['over']) < 0.01, res['over'])
    eq('Gauge', 'tone 生效', res['tone'], 'var(--danger)')
    truthy('Gauge', 'set(-1) 被 clamp 到 0',
           abs(res['under'] - res['A']) < 0.5, res['under'])

    # ----------------------------------------------------------
    section('C 迷你趋势线')
    # ----------------------------------------------------------
    res = page.evaluate("""() => {
      const I = window.FnStyleOverview._internal;
      const p2 = I.monoPath([0, 1, 2], [0, 10, 0]);
      const ys = (p2.match(/[-\\d.]+,[-\\d.]+/g) || [])
        .map(s => parseFloat(s.split(',')[1]));
      const sp = I.makeSpark();
      sp.set([{ values: [1, 2, 3, 4] }], { min: 0, max: 10 });
      const h1 = sp.el.childNodes[0].innerHTML;
      sp.set([{ values: [1, 2] }, { values: [3, 4] }], { min: 0 });
      const h2 = sp.el.childNodes[0].innerHTML;
      return { ys: ys, area: h1.indexOf('ov-spark-area') >= 0,
               line: h1.indexOf('ov-spark-line') >= 0,
               dot: h1.indexOf('ov-spark-dot') >= 0,
               lines2: (h2.match(/ov-spark-line/g) || []).length,
               l2: h2.indexOf('l2') >= 0 };
    }""")
    over = [y for y in res['ys'] if y < -0.6 or y > 10.6]
    rec('Spark', '单调插值不产生回折（y 全在端点范围内）', len(over) == 0,
        'y=' + json.dumps(res['ys']))
    truthy('Spark', '生成面积路径', res['area'])
    truthy('Spark', '生成线路径', res['line'])
    truthy('Spark', '生成末端圆点', res['dot'])
    truthy('Spark', '双序列生成两条线', res['lines2'] >= 2, res['lines2'])
    truthy('Spark', '第二序列带 l2 修饰类', res['l2'])

    # ----------------------------------------------------------
    section('D 阈值分级')
    # ----------------------------------------------------------
    res = page.evaluate("""() => {
      const I = window.FnStyleOverview._internal;
      return {
        cpu: [I.lvCPU(0), I.lvCPU(84.9), I.lvCPU(85), I.lvCPU(94.9),
              I.lvCPU(95), I.lvCPU(null)],
        mem: [I.lvMem(84.9), I.lvMem(85), I.lvMem(95)],
        cap: [I.lvCap(84.9), I.lvCap(85), I.lvCap(95)],
        tmp: [I.lvTemp(79.9), I.lvTemp(80), I.lvTemp(90)],
      };
    }""")
    eq('阈值', 'CPU 分级（fnmonitor 口径 85/95）', res['cpu'],
       ['', '', 'warn', 'warn', 'crit', ''])
    eq('阈值', '内存分级（85/95）', res['mem'], ['', 'warn', 'crit'])
    eq('阈值', '容量分级（85/95）', res['cap'], ['', 'warn', 'crit'])
    eq('阈值', '温度分级（80/90）', res['tmp'], ['', 'warn', 'crit'])

    # ----------------------------------------------------------
    section('E 温度取值')
    # ----------------------------------------------------------
    res = page.evaluate("""() => {
      const I = window.FnStyleOverview._internal;
      return {
        t1: I.tempOf({ temp: { temps: [{ type: 'cpu', value: 45 }] } }).main,
        t2: I.tempOf({ temp: { cpuTemp: 50 } }).main,
        t3: I.tempOf({ temp: { mbTemp: 38 } }).main,
        t4: I.tempOf({ temp: { disks: [{ temperature: 41 }] } }).main,
        t5: I.tempOf({ temp: { temps: [{ type: 'board', value: 33 }],
                                cpuTemp: 44 } }).main,
        t6: I.tempOf({ temp: { temps: [{ type: 'cpu', value: 40 },
                                       { type: 'cpu', value: 55 }] } }).cpu,
        t7: I.tempOf({}).main,
        t8: I.tempOf({ temp: { temps: [{ type: 'cpu', value: 'x' }] } }).main,
        t9: I.tempOf({ temp: { sensors: [{ type: 'cpu', value: 42 }] } }).main,
      };
    }""")
    eq('温度', '从 temps 取 cpu', res['t1'], 45)
    eq('温度', '无 temps 时退回 cpuTemp', res['t2'], 50)
    eq('温度', '只有 mbTemp 时退回主板', res['t3'], 38)
    eq('温度', '只有硬盘温度时取硬盘', res['t4'], 41)
    eq('温度', 'cpuTemp 优先于 temps 里的 board', res['t5'], 44)
    eq('温度', '多个 cpu 测点取最高', res['t6'], 55)
    eq('温度', '空数据 main = null', res['t7'], None)
    eq('温度', '非法值被忽略', res['t8'], None)
    eq('温度', '兼容 sensors 字段名', res['t9'], 42)

    # ----------------------------------------------------------
    section('F 存储汇总')
    # ----------------------------------------------------------
    res = page.evaluate("""() => {
      const I = window.FnStyleOverview._internal;
      const r1 = I.diskOf({ storage: {
        summary: { total: 1000, used: 700, percent: 70,
                   poolCount: 2, diskCount: 4 },
        volumes: [], disks: [{ name: 'sda', percent: 50 }] } });
      const r2 = I.diskOf({ storage: {
        volumes: [{ total: 500, used: 200 }, { total: 500, used: 300 }],
        disks: [] } });
      const r3 = I.diskOf({});
      const r4 = I.diskOf({ storage: { summary: { total: 0, used: 0 },
                                       volumes: [] } });
      return { p1: r1.pct, t1: r1.total, pc1: r1.poolCount, dn1: r1.disks.length,
               t2: r2.total, u2: r2.used, p2: r2.pct, pc2: r2.poolCount,
               p3: r3.pct, dn3: r3.disks.length, p4: r4.pct };
    }""")
    eq('存储', 'summary.percent 直接采用', res['p1'], 70)
    eq('存储', 'summary.total', res['t1'], 1000)
    eq('存储', 'poolCount 取自 summary', res['pc1'], 2)
    eq('存储', 'disks 透传', res['dn1'], 1)
    eq('存储', '无 summary 时累加 total', res['t2'], 1000)
    eq('存储', '无 summary 时累加 used', res['u2'], 500)
    eq('存储', '无 summary 时算 percent', res['p2'], 50)
    eq('存储', '无 summary 时 poolCount = volumes 数', res['pc2'], 2)
    eq('存储', '空数据 pct = null', res['p3'], None)
    eq('存储', '空数据 disks = []', res['dn3'], 0)
    eq('存储', '零容量 pct = null（不出现 NaN）', res['p4'], None)

    # ----------------------------------------------------------
    section('G 格式化')
    # ----------------------------------------------------------
    res = page.evaluate("""() => {
      const I = window.FnStyleOverview._internal;
      return {
        i1: I.fmtInt(42.4), i2: I.fmtInt(null), i3: I.fmtInt(NaN),
        f1: I.fmt1(29.04), f2: I.fmt1(null),
        b1: I.fmtBytes(512), b2: I.fmtBytes(1024),
        b3: I.fmtBytes(1048576), b4: I.fmtBytes(0), b5: I.fmtBytes(null),
        r1: I.fmtRate(1024).v, r2: I.fmtRate(1024).u, r3: I.fmtRate(0).v,
        r4: I.fmtRate(null).v,
        g1: I.fmtGB(1073741824), e1: I.esc('<a&b>'),
        d1: I.fmtDSize(1760000000000), d2: I.fmtDSize(2560000000000),
        d3: I.fmtDSize(1500000000000), d4: I.fmtDSize(200000000000),
        d5: I.fmtDSize(50000000), d6: I.fmtDSize(null),
        d7: I.fmtDSize(8589934592),
      };
    }""")
    eq('格式', 'fmtInt(42.4)', res['i1'], '42')
    eq('格式', 'fmtInt(null)', res['i2'], '--')
    eq('格式', 'fmtInt(NaN)', res['i3'], '--')
    eq('格式', 'fmt1(29.04)', res['f1'], '29.0')
    eq('格式', 'fmt1(null)', res['f2'], '--')
    eq('格式', 'fmtBytes(512)', res['b1'], '512 B')
    eq('格式', 'fmtBytes(1024)', res['b2'], '1.0 KB')
    eq('格式', 'fmtBytes(1048576)', res['b3'], '1.0 MB')
    eq('格式', 'fmtBytes(0)', res['b4'], '0 B')
    eq('格式', 'fmtBytes(null)', res['b5'], '--')
    eq('格式', 'fmtRate(1024).v', res['r1'], '1.0')
    eq('格式', 'fmtRate(1024).u', res['r2'], 'KB/s')
    eq('格式', 'fmtRate(0).v', res['r3'], '0')
    eq('格式', 'fmtRate(null).v', res['r4'], '--')
    eq('格式', 'fmtGB(1GiB)', res['g1'], '1.0')
    eq('格式', 'esc 转义', res['e1'], '&lt;a&amp;b&gt;')
    # ⚠️ v1.1.70：存储容量改**十进制**口径（对齐 fnmonitor 的 F.dsize）
    eq('格式', 'fmtDSize(1.76 TB) → 1.76 TB', res['d1'], '1.76 TB')
    eq('格式', 'fmtDSize(2.56 TB) → 2.56 TB', res['d2'], '2.56 TB')
    # fnmonitor F.dsize：v>=10 → toFixed(1)，v<10 → toFixed(2)
    eq('格式', 'fmtDSize(1.5 TB) → 1.50 TB（fnmonitor 口径）', res['d3'], '1.50 TB')
    eq('格式', 'fmtDSize(200 GB) → 200 GB', res['d4'], '200 GB')
    eq('格式', 'fmtDSize(50 MB) → 50 MB', res['d5'], '50 MB')
    eq('格式', 'fmtDSize(null) → --', res['d6'], '--')
    # fnmonitor F.dsize：GB 段 v>=100 → 取整，否则 toFixed(1)
    eq('格式', 'fmtDSize(8 GiB) → 8.6 GB（非二进制）', res['d7'], '8.6 GB')

    # ----------------------------------------------------------
    section('H 六卡片渲染')
    # ----------------------------------------------------------
    page.evaluate("""(snap) => {
      const host = document.getElementById('monOverview');
      window.FnStyleOverview.render(host, snap);
    }""", SNAP)

    res = page.evaluate("""() => {
      const host = document.getElementById('monOverview');
      const cards = [...host.querySelectorAll('.ov-metric')];
      const q = s => { const e = host.querySelector(s); return e ? e.textContent : null; };
      /* ⚠️ v1.1.70：弃用点阵后，大数字是**普通文本** → 读 textContent */
      const aria = s => { const e = host.querySelector(s);
                          return e ? e.textContent : null; };
      const pips = host.querySelector('.ov-pips');
      const grid = host.querySelector('.ov-metrics');
      return {
        n: cards.length,
        labels: cards.map(c => c.querySelector('.ov-m-label').textContent),
        states: {
          cpu: q('[data-ov-state="cpu"]'), mem: q('[data-ov-state="mem"]'),
          disk: q('[data-ov-state="disk"]'), net: q('[data-ov-state="net"]'),
          temp: q('[data-ov-state="temp"]'), pwr: q('[data-ov-state="pwr"]'),
        },
        vals: {
          cpu: aria('[data-ov-val="cpuV"]'), mem: aria('[data-ov-val="memV"]'),
          disk: aria('[data-ov-val="diskV"]'), temp: aria('[data-ov-val="tempV"]'),
          pwr: aria('[data-ov-val="pwrV"]'), rx: aria('[data-ov-val="rxV"]'),
          tx: aria('[data-ov-val="txV"]'),
        },
        units: {
          cpu: q('[data-ov-unit="cpuU"]'), temp: q('[data-ov-unit="tempU"]'),
          pwr: q('[data-ov-unit="pwrU"]'), rx: q('[data-ov-unit="rxU"]'),
          tx: q('[data-ov-unit="txU"]'),
        },
        subs: {
          cpu: q('[data-ov-sub="cpu"]'), mem: q('[data-ov-sub="mem"]'),
          disk: q('[data-ov-sub="disk"]'), net: q('[data-ov-sub="net"]'),
          temp: q('[data-ov-sub="temp"]'), pwr: q('[data-ov-sub="pwr"]'),
        },
        gauges: host.querySelectorAll('[data-ov-gauge-el]').length,
        sparks: host.querySelectorAll('[data-ov-spark]').length,
        pipsHtml: pips ? pips.innerHTML : '',
        pipN: pips ? pips.querySelectorAll('.ov-pip').length : -1,
        pipColN: pips ? pips.querySelectorAll(':scope > div').length : -1,
        pipFillDirect: (() => {
          if (!pips) return false;
          const pip = pips.querySelector('.ov-pip');
          if (!pip) return false;
          const i = pip.querySelector('i');
          return !!(i && i.parentElement === pip);
        })(),
        pipFillPos: (() => {
          if (!pips) return null;
          const i = pips.querySelector('.ov-pip > i');
          return i ? getComputedStyle(i).position : null;
        })(),
        pipFillH: (() => {
          if (!pips) return null;
          const i = pips.querySelector('.ov-pip > i');
          return i ? i.style.height : null;
        })(),
        pipW: (() => {
          if (!pips) return -1;
          const pip = pips.querySelector('.ov-pip');
          return pip ? Math.round(pip.getBoundingClientRect().width) : -1;
        })(),
        pipH: (() => {
          if (!pips) return -1;
          const pip = pips.querySelector('.ov-pip');
          return pip ? Math.round(pip.getBoundingClientRect().height) : -1;
        })(),
        sheets: [...host.querySelectorAll('[data-sheet]')]
                 .map(c => c.getAttribute('data-ov')).sort(),
        sheetN: host.querySelectorAll('[data-sheet]').length,
        diskHasSheet: !!host.querySelector('[data-ov="disk"]')
                         .getAttribute('data-sheet'),
        netHasSheet: !!host.querySelector('[data-ov="net"]')
                        .getAttribute('data-sheet'),
        cpuTap: host.querySelector('[data-ov="cpu"]').classList.contains('ov-tap'),
        diskTap: host.querySelector('[data-ov="disk"]').classList.contains('ov-tap'),
        netTap: host.querySelector('[data-ov="net"]').classList.contains('ov-tap'),
        cardRect: (() => {
          const r = host.querySelector('[data-ov="cpu"]').getBoundingClientRect();
          return { w: Math.round(r.width), h: Math.round(r.height) };
        })(),
        /* ⚠️ v1.1.71：六卡必须**完全等高**（逐行锁高后仍会漂移） */
        cardHeights: cards.map(c => Math.round(c.getBoundingClientRect().height)),
        /* ⚠️ v1.1.71：左侧数字与右侧仪表图标必须**同一水平线**
           （.ov-m-main 用 baseline + line-height:.8 会让字形下沉 ~19px） */
        alignDiffs: (() => {
          const pairs = [['cpu', 'cpuV'], ['mem', 'memV'], ['disk', 'diskV'],
                         ['temp', 'tempV'], ['pwr', 'pwrV']];
          return pairs.map(([k, v]) => {
            const g = host.querySelector('[data-ov-gauge-el="' + k + '"]');
            const n = host.querySelector('[data-ov-val="' + v + '"]');
            if (!g || !n) return -999;
            const gr = g.getBoundingClientRect(), nr = n.getBoundingClientRect();
            /* 用**字形盒**而非行盒：数字元素是 inline-flex，取其内容盒中心 */
            const ncy = nr.top + nr.height / 2;
            const gcy = gr.top + gr.height / 2;
            return Math.round(Math.abs(ncy - gcy));
          });
        })(),
        /* 数字行与仪表行是否真的同行（top 差 < 半个仪表高） */
        sameRow: (() => {
          const g = host.querySelector('[data-ov-gauge-el="cpu"]');
          const n = host.querySelector('[data-ov-val="cpuV"]');
          if (!g || !n) return false;
          const gr = g.getBoundingClientRect(), nr = n.getBoundingClientRect();
          return Math.abs(gr.top - nr.top) < gr.height;
        })(),
        /* v1.1.70：不再是点阵 → 每个数值元素自身内部不应有 svg / circle */
        numSvg: [...host.querySelectorAll('[data-ov-val]')]
          .filter(e => e.querySelector('svg')).length,
        numCircles: host.querySelectorAll('[data-ov-val] circle').length,
        numFonts: [...host.querySelectorAll('[data-ov-val]')]
          .map(e => getComputedStyle(e).fontFamily),
        /* 分级着色：CPU 32% 正常 → 品牌蓝 */
        cpuInk: (() => {
          const e = host.querySelector('[data-ov-val="cpuV"]');
          return e ? e.style.color : null;
        })(),
        /* 网络卡字号分级 */
        netRxH: (() => {
          const e = host.querySelector('[data-ov-val="rxV"]');
          return e ? Math.round(e.getBoundingClientRect().height) : -1;
        })(),
        netTxH: (() => {
          const e = host.querySelector('[data-ov-val="txV"]');
          return e ? Math.round(e.getBoundingClientRect().height) : -1;
        })(),
        netTxColor: (() => {
          const e = host.querySelector('[data-ov-val="txV"]');
          return e ? getComputedStyle(e).color : null;
        })(),
        netArrows: host.querySelectorAll('.ov-arrow-svg').length,
        /* 盘位条填充色（分级着色） */
        pipColors: (() => {
          if (!pips) return [];
          return [...pips.querySelectorAll('.ov-pip > i')].map(i => i.style.background);
        })(),
        /* 存储副标题（十进制容量） */
        diskSubText: (() => {
          const e = host.querySelector('[data-ov-sub="disk"]');
          return e ? e.textContent : null;
        })(),
        /* 趋势线配色 */
        sparkLineStroke: (() => {
          const e = host.querySelector('.ov-spark-line');
          return e ? getComputedStyle(e).stroke : null;
        })(),
        sparkAreaFill: (() => {
          const e = host.querySelector('.ov-spark-area');
          return e ? getComputedStyle(e).fill : null;
        })(),
        /* 卡片圆角 */
        cardRadius: (() => {
          const e = host.querySelector('[data-ov="cpu"]');
          return e ? getComputedStyle(e).borderRadius : null;
        })(),
        cardMinH: (() => {
          const e = host.querySelector('[data-ov="cpu"]');
          return e ? Math.round(e.getBoundingClientRect().height) : -1;
        })(),
        /* ⚠️ v1.1.72：概览区**外层不再有 .card 卡片**
           （老板：「不要最外层的卡片，直接呈现内部的卡片即可」） */
        outerHasCard: (() => {
          const w = document.getElementById('overviewCard');
          if (!w) return false;
          return w.classList.contains('card');
        })(),
        outerHasHeader: (() => {
          const w = document.getElementById('overviewCard');
          return !!(w && w.querySelector(':scope > header'));
        })(),
        /* ⚠️ v1.1.72：每张卡**底部留白**（padding-bottom）
           —— 老板：「下方尽量不要留白」 */
        padBottoms: cards.map(c => {
          const pb = getComputedStyle(c).paddingBottom;
          return Math.round(parseFloat(pb) * 10) / 10;
        }),
        /* ⚠️ v1.1.72：每张卡的**底部内容底边**到卡片底边的距离
           （真正的视觉留白，不只是 padding） */
        bottomGaps: cards.map(c => {
          const cr = c.getBoundingClientRect();
          const foot = c.querySelector('.ov-m-foot');
          if (!foot) return -1;
          const fr = foot.getBoundingClientRect();
          const pb = parseFloat(getComputedStyle(c).paddingBottom) || 0;
          return Math.round((cr.bottom - pb - fr.bottom) * 10) / 10;
        }),
        /* ⚠️ v1.1.72：五张带趋势线的卡（cpu/mem/net/temp/pwr）底部内容高度 */
        footHeights: cards.map(c => {
          const foot = c.querySelector('.ov-m-foot');
          if (!foot) return -1;
          return Math.round(foot.getBoundingClientRect().height);
        }),
        /* 仪表图标子元素类型（官方图形） */
        gaugeIcoTags: (() => {
          const e = host.querySelector('[data-ov-gauge-el="cpu"] .ov-gauge-ico');
          return e ? [...e.childNodes].map(n => n.tagName) : [];
        })(),
        gaugeIcoMemoryTags: (() => {
          const e = host.querySelector('[data-ov-gauge-el="mem"] .ov-gauge-ico');
          return e ? [...e.childNodes].map(n => n.tagName) : [];
        })(),
        gaugeIcoStorageTags: (() => {
          const e = host.querySelector('[data-ov-gauge-el="disk"] .ov-gauge-ico');
          return e ? [...e.childNodes].map(n => n.tagName) : [];
        })(),
        gaugeDash: (() => {
          const e = host.querySelector('[data-ov-gauge-el="cpu"] .ov-gauge-val');
          return e ? e.style.strokeDashoffset : null;
        })(),
        gridCols: grid ? getComputedStyle(grid).gridTemplateColumns : null,
        gaugeW: (() => {
          const e = host.querySelector('[data-ov-gauge-el="cpu"]');
          return e ? Math.round(e.getBoundingClientRect().width) : -1;
        })(),
        gaugeH: (() => {
          const e = host.querySelector('[data-ov-gauge-el="cpu"]');
          return e ? Math.round(e.getBoundingClientRect().height) : -1;
        })(),
        gaugeLeft: (() => {
          const e = host.querySelector('[data-ov-gauge-el="cpu"]');
          return e ? Math.round(e.getBoundingClientRect().left) : -1;
        })(),
        numRight: (() => {
          const e = host.querySelector('[data-ov-val="cpuV"]');
          return e ? Math.round(e.getBoundingClientRect().right) : -1;
        })(),
        gaugeIcons: host.querySelectorAll('.ov-gauge-ico').length,
        gaugeIcoW: (() => {
          const e = host.querySelector('[data-ov-gauge-el="cpu"] .ov-gauge-ico');
          return e ? Math.round(e.getBoundingClientRect().width) : -1;
        })(),
        gaugeIcoCX: (() => {
          const e = host.querySelector('[data-ov-gauge-el="cpu"] .ov-gauge-ico');
          if (!e) return -1;
          const svg = e.ownerSVGElement;
          const r = e.getBoundingClientRect();
          const sr = svg.getBoundingClientRect();
          /* 换算回 100×100 的 viewBox 坐标 */
          return Math.round(((r.left + r.width / 2 - sr.left) / sr.width) * 100);
        })(),
        gaugeRightOfNum: (() => {
          const g = host.querySelector('[data-ov-gauge-el="cpu"]');
          const n = host.querySelector('[data-ov-val="cpuV"]');
          if (!g || !n) return false;
          const gr = g.getBoundingClientRect(), nr = n.getBoundingClientRect();
          /* 仪表左边缘应大于数字右边缘 → 同一行的右侧 */
          return gr.left >= nr.right - 2 && Math.abs(gr.top - nr.top) < 60;
        })(),
        netSpan2: (() => {
          const e = host.querySelector('.ov-net-vals');
          if (!e) return false;
          return getComputedStyle(e).gridColumn === '1 / -1';
        })(),
        netCol: (() => {
          const e = host.querySelector('.ov-net-vals');
          return e ? getComputedStyle(e).gridColumn : null;
        })(),
        sparkPaths: host.querySelectorAll('.ov-spark-line').length,
        sparkDots: host.querySelectorAll('.ov-spark-dot').length,
        /* ⚠️ v1.1.72：每张卡的 spark 是否**真的画出了内容**
           （v1.1.71 之前 setSpark 传的是裸数组 [live.cpu]，
            makeSpark 取 s.values 得到 undefined → 一条线都不画，
            表现为「处理器/内存/温度/功耗卡片底部没有趋势显示」。）
           这里逐卡统计：spark 元素数 / 线数 / 点数 / path 的 d 长度。 */
        perCardSpark: cards.map(c => {
          const k = c.getAttribute('data-ov');
          const sp = c.querySelector('[data-ov-spark]');
          if (!sp) return { k: k, spark: 0, lines: 0, dots: 0, dLen: 0 };
          const lines = sp.querySelectorAll('.ov-spark-line');
          const dots = sp.querySelectorAll('.ov-spark-dot');
          let dLen = 0;
          lines.forEach(l => { dLen += (l.getAttribute('d') || '').length; });
          return { k: k, spark: 1, lines: lines.length, dots: dots.length, dLen: dLen };
        }),
        /* spark 元素的实际渲染高度（0 说明没高度 → 看不见） */
        sparkHeights: cards.map(c => {
          const sp = c.querySelector('[data-ov-spark]');
          if (!sp) return -1;
          return Math.round(sp.getBoundingClientRect().height);
        }),
        gaugeStroke: (() => {
          const e = host.querySelector('[data-ov-gauge-el="cpu"] .ov-gauge-val');
          return e ? getComputedStyle(e).stroke : null;
        })(),
      };
    }""")
    eq('六卡', '卡片数量 = 6', res['n'], 6)
    eq('六卡', '卡片顺序',
       res['labels'], ['处理器', '内存', '存储', '网络', '温度', '功耗'])
    eq('状态词', '处理器 32% → 平稳（fnmonitor 口径 c<35）',
       res['states']['cpu'], '平稳')
    eq('状态词', '内存 42% → 充裕', res['states']['mem'], '充裕')
    eq('状态词', '存储 69% → 充足', res['states']['disk'], '充足')
    eq('状态词', '网络低速 → 空闲', res['states']['net'], '空闲')
    eq('状态词', '温度 45℃ → 正常', res['states']['temp'], '正常')
    eq('状态词', '功耗 RAPL → 实测 + 估算', res['states']['pwr'], '实测 + 估算')
    eq('数值', '处理器 32', res['vals']['cpu'], '32')
    eq('数值', '内存 42', res['vals']['mem'], '42')
    eq('数值', '存储 69', res['vals']['disk'], '69')
    eq('数值', '温度 45', res['vals']['temp'], '45')
    eq('数值', '功耗 29.0', res['vals']['pwr'], '29.0')
    eq('数值', '网络下行 2.0', res['vals']['rx'], '2.0')
    eq('数值', '网络上行 512', res['vals']['tx'], '512')
    eq('单位', '处理器 %', res['units']['cpu'], '%')
    eq('单位', '温度 ℃', res['units']['temp'], '℃')
    eq('单位', '功耗 W', res['units']['pwr'], 'W')
    eq('单位', '网络下行 KB/s', res['units']['rx'], 'KB/s')
    eq('单位', '网络上行 B/s', res['units']['tx'], 'B/s')
    truthy('副标题', '处理器含核数', '4 核' in (res['subs']['cpu'] or ''), res['subs']['cpu'])
    truthy('副标题', '处理器含频率', '2.20 GHz' in (res['subs']['cpu'] or ''), res['subs']['cpu'])
    truthy('副标题', '内存含用量/总量', '3.3 GiB' in (res['subs']['mem'] or ''), res['subs']['mem'])
    truthy('副标题', '存储含容量', 'GB' in (res['subs']['disk'] or ''), res['subs']['disk'])
    # ⚠️ v1.1.70：存储容量改十进制口径（对齐 fnmonitor 的 F.dsize）
    #    快照 total=8589934592 → 8.59 GB（不是 8 GiB）
    truthy('副标题', '存储容量用十进制 GB（非二进制 GiB）',
           '8.6 GB' in (res['subs']['disk'] or ''), res['subs']['disk'])
    truthy('副标题', '存储容量不含 GiB',
           'GiB' not in (res['subs']['disk'] or ''), res['subs']['disk'])
    truthy('副标题', '存储含硬盘数', '2 块硬盘' in (res['subs']['disk'] or ''), res['subs']['disk'])
    truthy('副标题', '网络含今日流量', '今日' in (res['subs']['net'] or ''), res['subs']['net'])
    truthy('副标题', '温度含主板温度', '主板 38℃' in (res['subs']['temp'] or ''), res['subs']['temp'])
    truthy('副标题', '功耗含今日 kWh', '0.6 kWh' in (res['subs']['pwr'] or ''), res['subs']['pwr'])
    truthy('副标题', '功耗含电费', '¥ 0.36' in (res['subs']['pwr'] or ''), res['subs']['pwr'])
    eq('仪表', '仪表数量 = 5（网络卡无仪表）', res['gauges'], 5)
    eq('趋势线', '趋势线数量 = 5', res['sparks'], 5)
    eq('盘位', '盘位数 = 2', res['pipN'], 2)
    truthy('盘位', '盘位名 sda 存在', 'sda' in res['pipsHtml'])
    truthy('盘位', '盘位高度 = 已用百分比 70',
           'height:70.0%' in res['pipsHtml'], res['pipsHtml'][:200])
    # ⚠️ v1.1.69 回归：盘位条结构必须与 fnmonitor 的 .drive-pips 一致 ——
    #    外层 flex → 每块盘一个**列容器** → 条（.ov-pip）+ 填充（i）。
    #    之前少一层列容器 → `inset:auto 0 0 0` 定位失效 → 条变成横铺大圆角块
    #    （截图实证：sdb 那条撑成一大块）。
    eq('盘位', '列容器数 = 2（每盘一个）', res['pipColN'], 2)
    truthy('盘位', '填充条是 .ov-pip 的直接子元素',
           res['pipFillDirect'], 'i 必须在 .ov-pip 内')
    truthy('盘位', '填充条 absolute 定位（贴底填充）',
           res['pipFillPos'] == 'absolute', res['pipFillPos'])
    truthy('盘位', '填充条有百分比高度',
           res['pipFillH'] not in (None, ''), res['pipFillH'])
    truthy('盘位', '条本身有实际宽度（不是撑满）',
           res['pipW'] > 4 and res['pipW'] < 200, 'width=%s' % res['pipW'])
    truthy('盘位', '条高度 18px（v1.1.72 压缩后）',
           abs(res['pipH'] - 18) < 1.5, 'height=%s' % res['pipH'])
    # ⚠️ v1.1.70 回归：概览**不再使用点阵数字**（老板明确指示
    #    「不使用点阵数字，保持与其他的数字字体一致」）——
    #    数字区里不能残留任何 SVG / circle，否则就是点阵漏网。
    # 网络卡的上下行箭头是合法的内联 SVG（官方 #i-down/#i-up），
    # 这里断言「除箭头外没有别的 SVG」+ 「没有任何 circle 点阵」。
    eq('真实渲染', '每个数值元素内部都没有 SVG（已弃用点阵）',
       res['numSvg'], 0)
    eq('真实渲染', '每个数值元素内部都没有 circle 点阵',
       res['numCircles'], 0)
    truthy('真实渲染', '所有大数字都用 --font-display',
           res['numFonts'] and all('SF Pro Display' in f for f in res['numFonts']),
           json.dumps(res['numFonts'], ensure_ascii=False))
    # 分级着色：CPU 32% 正常段 → 品牌蓝
    eq('真实渲染', 'CPU 32% 大数字着色 = 品牌蓝',
       res['cpuInk'], 'var(--accent)')
    # 网络卡字号分级：下行大字 / 上行小字
    truthy('真实渲染', '网络下行字号 > 上行字号',
           res['netRxH'] > res['netTxH'],
           'rx=%s tx=%s' % (res['netRxH'], res['netTxH']))
    truthy('真实渲染', '网络上行用次要文字色（分级）',
           res['netTxColor'] not in (None, ''), res['netTxColor'])
    eq('真实渲染', '网络上下行箭头图标 = 2', res['netArrows'], 2)
    # 盘位条填充色按占用率分级（sda 70% → 蓝，sdb 68% → 蓝）
    truthy('真实渲染', '盘位填充色按占用率分级',
           res['pipColors'] and all(c for c in res['pipColors']),
           json.dumps(res['pipColors']))
    # 趋势线配色：深色主线 + 浅灰面积
    truthy('真实渲染', '趋势线主线取到颜色',
           res['sparkLineStroke'] not in (None, ''), res['sparkLineStroke'])
    truthy('真实渲染', '趋势线面积取到填充色',
           res['sparkAreaFill'] not in (None, ''), res['sparkAreaFill'])
    # 卡片圆角（对齐 fnmonitor 的大圆角）
    truthy('真实渲染', '卡片圆角 ≥ 16px（对齐 fnmonitor）',
           res['cardRadius'] and float(res['cardRadius'].replace('px', '')) >= 16,
           res['cardRadius'])
    # ⚠️ v1.1.71：卡片高度**压缩**到 215px 以内（老板反馈「太高了太占空间」）。
    #    原 265px → 去掉 min-height:236px + 逐行锁高后 = 215px。
    truthy('真实渲染', '卡片高度 ≤ 220px（已压缩，对齐老板要求）',
           res['cardMinH'] <= 220, res['cardMinH'])
    truthy('真实渲染', '卡片高度 ≥ 150px（不能压扁）',
           res['cardMinH'] >= 150, res['cardMinH'])
    # ⚠️ v1.1.71：六卡**完全等高**（m-top/m-main/m-sub/m-foot 逐行锁高）
    hs = res['cardHeights']
    truthy('真实渲染', '六卡完全等高（max-min ≤ 1px）',
           hs and (max(hs) - min(hs)) <= 1, hs)
    # ⚠️ v1.1.71：数字与仪表**同一水平线**（中心差 = 0）
    ad = res['alignDiffs']
    truthy('真实渲染', '数字与仪表中心对齐（5 张仪表 diff ≤ 2px）',
           ad and all(d <= 2 for d in ad), ad)

    # ----------------------------------------------------------
    section('H2 v1.1.72：外层卡片 / 底部留白 / 趋势线（老板 4 条反馈）')
    # ----------------------------------------------------------
    # ① 「重要内容概览不要最外层的卡片，直接呈现内部的卡片即可」
    eq('外层', '概览区外层不再是 .card', res['outerHasCard'], False)
    eq('外层', '概览区外层没有 header 条', res['outerHasHeader'], False)
    # ② 「一行默认显示 3 个卡片」
    truthy('外层', '栅格仍为 3 列',
           res['gridCols'] and len(res['gridCols'].split()) == 3, res['gridCols'])
    # ③ 「卡片高度减小，下方尽量不要留白」
    truthy('留白', '卡片高度 ≤ 185px（v1.1.72 再压缩）',
           res['cardMinH'] <= 185, res['cardMinH'])
    truthy('留白', '卡片高度 ≥ 150px（不能压扁）',
           res['cardMinH'] >= 150, res['cardMinH'])
    truthy('留白', '卡片底部 padding ≤ 10px',
           res['padBottoms'] and all(p <= 10 for p in res['padBottoms']),
           res['padBottoms'])
    truthy('留白', '底部内容到卡片底边距离 ≤ 12px（无厚留白）',
           res['bottomGaps'] and all(g >= -1 and g <= 12 for g in res['bottomGaps']),
           res['bottomGaps'])
    # ④ 「处理器、内存、温度、功耗卡片底部缺少趋势显示」
    #    —— v1.1.71 的 bug：setSpark 传裸数组，makeSpark 取 s.values 得 undefined
    #       → 五张卡的趋势线实际一条都没画（只有网络那张碰巧格式对）。
    pcs = res['perCardSpark']
    byk = {x['k']: x for x in pcs}
    for k in ['cpu', 'mem', 'net', 'temp', 'pwr']:
        x = byk.get(k) or {}
        truthy('趋势线', '%s 卡挂载了 spark 元素' % k, x.get('spark') == 1, x)
        truthy('趋势线', '%s 卡趋势线已绘制（线或点）' % k,
               (x.get('lines', 0) + x.get('dots', 0)) >= 1, x)
    # spark 元素有实际高度（≥ 20px）；存储卡是盘位条（无 spark，值 -1）→ 跳过
    _sh = [h for h in res['sparkHeights'] if h > 0]
    truthy('趋势线', 'spark 元素有实际高度（≥ 20px）',
           len(_sh) == 5 and all(h >= 20 for h in _sh),
           res['sparkHeights'])
    # 存储卡底部是盘位条（不是趋势线），单独确认
    truthy('趋势线', '存储卡底部是盘位条（非空）',
           (byk.get('disk') or {}).get('spark') == 0, byk.get('disk'))
    # 官方图标形状：CPU=rect+rect+path / 内存=rect+path / 存储=ellipse+path
    eq('真实渲染', 'CPU 仪表图标 = rect+rect+path（官方 #i-cpu）',
       res['gaugeIcoTags'], ['rect', 'rect', 'path'])
    eq('真实渲染', '内存仪表图标 = rect+path（官方 #i-mem）',
       res['gaugeIcoMemoryTags'], ['rect', 'path'])
    eq('真实渲染', '存储仪表图标 = ellipse+path（官方 #i-storage）',
       res['gaugeIcoStorageTags'], ['ellipse', 'path'])
    truthy('真实渲染', '仪表 dashoffset 已计算',
           res['gaugeDash'] not in (None, ''), res['gaugeDash'])
    truthy('真实渲染', '仪表描边取到 accent 色',
           res['gaugeStroke'] not in (None, ''), res['gaugeStroke'])
    truthy('真实渲染', '卡片有实际尺寸', res['cardRect']['w'] > 100,
           json.dumps(res['cardRect']))
    truthy('真实渲染', '栅格为 3 列',
           res['gridCols'] and len(res['gridCols'].split()) == 3, res['gridCols'])
    # ⚠️ v1.1.69 回归：仪表应在数字**右侧同排**（对齐 fnmonitor 的
    #    .metric .m-gauge { grid-row:2; grid-column:2; width:92px; height:92px }）
    #    之前 flex 纵向 → 仪表掉到数字下方单独一行，卡片被拉高、右侧留白。
    truthy('真实渲染', '仪表在数字右侧同排',
           res['gaugeRightOfNum'], '仪表left=%s 数字right=%s' %
           (res['gaugeLeft'], res['numRight']))
    truthy('真实渲染', '仪表尺寸 64×64（v1.1.72 压缩后）',
           abs(res['gaugeW'] - 64) < 2 and abs(res['gaugeH'] - 64) < 2,
           '%sx%s' % (res['gaugeW'], res['gaugeH']))
    # ⚠️ v1.1.69 修正：fnmonitor 的仪表**有**中心图标（24×24 居中）。
    #    之前塞 16×16 未居中的 path → 缩到 92px 后只剩左上角一个小点。
    eq('真实渲染', '仪表中心图标数 = 5', res['gaugeIcons'], 5)
    # 图标路径的实际绘制宽度取决于 path 的坐标跨度（16×16 画布里
    # 不同图标画的范围不同），不能按固定像素卡。
    # 真正要保证的是：**在 100×100 的 viewBox 里居中、且尺寸合理**。
    truthy('真实渲染', '仪表图标居中（viewBox 中心 ≈ 50）',
           abs(res['gaugeIcoCX'] - 50) < 3, 'cx=%s' % res['gaugeIcoCX'])
    truthy('真实渲染', '仪表图标尺寸合理（6~26px）',
           res['gaugeIcoW'] >= 6 and res['gaugeIcoW'] <= 26,
           'w=%s' % res['gaugeIcoW'])
    truthy('真实渲染', '网络卡数字区横跨两列（不留 92px 空区）',
           res['netSpan2'], res.get('netCol', ''))
    truthy('真实渲染', '趋势线首帧单点已画出占位圆点',
           res['sparkPaths'] >= 1 or res['sparkDots'] >= 1,
           'paths=%s dots=%s' % (res['sparkPaths'], res['sparkDots']))

    # ----------------------------------------------------------
    section('I 可点性白名单')
    # ----------------------------------------------------------
    eq('白名单', '带 data-sheet 的卡片 = 4', res['sheetN'], 4)
    eq('白名单', '带 data-sheet 的是 cpu/mem/pwr/temp',
       res['sheets'], ['cpu', 'mem', 'pwr', 'temp'])
    bad = []
    if res['diskHasSheet']:
        bad.append('存储卡带 data-sheet')
    if res['netHasSheet']:
        bad.append('网络卡带 data-sheet')
    if res['diskTap']:
        bad.append('存储卡带 ov-tap')
    if res['netTap']:
        bad.append('网络卡带 ov-tap')
    eq('白名单', '存储/网络卡无 data-sheet 且无 ov-tap', bad, [])
    truthy('白名单', '处理器卡有 ov-tap', res['cpuTap'])

    # ----------------------------------------------------------
    section('J 告警态')
    # ----------------------------------------------------------
    page.evaluate("""() => {
      const host = document.getElementById('monOverview');
      window.FnStyleOverview.render(host, {
        cpu: { available: true, percent: 95 },
        mem: { available: true, percent: 85 },
        storage: { summary: { total: 100, used: 95, percent: 95 },
                   volumes: [], disks: [] },
        network: { available: true, rxRate: 100000, txRate: 50000 },
        temp: { temps: [{ type: 'cpu', value: 90 }] },
        power: { ok: true, source: 'estimated', total: 55.5 },
      });
    }""")
    res = page.evaluate("""() => {
      const host = document.getElementById('monOverview');
      const g = k => {
        const c = host.querySelector('[data-ov="' + k + '"]');
        return { warn: c.classList.contains('is-warn'),
                 crit: c.classList.contains('is-crit'),
                 state: host.querySelector('[data-ov-state="' + k + '"]').textContent };
      };
      return { cpu: g('cpu'), mem: g('mem'), disk: g('disk'),
               net: g('net'), temp: g('temp'), pwr: g('pwr') };
    }""")
    truthy('告警', 'CPU 95% → is-crit', res['cpu']['crit'])
    truthy('告警', 'CPU 95% → 状态词「高负载」', res['cpu']['state'] == '高负载',
           res['cpu']['state'])
    truthy('告警', '内存 85% → is-warn', res['mem']['warn'])
    truthy('告警', '内存 85% → 状态词「偏高」', res['mem']['state'] == '偏高',
           res['mem']['state'])
    truthy('告警', '存储 95% → is-crit', res['disk']['crit'])
    truthy('告警', '存储 95% → 状态词「将满」', res['disk']['state'] == '将满',
           res['disk']['state'])
    truthy('告警', '网络高速 → 状态词「活跃」', res['net']['state'] == '活跃',
           res['net']['state'])
    truthy('告警', '温度 90℃ → is-crit', res['temp']['crit'])
    truthy('告警', '温度 90℃ → 状态词「过热」', res['temp']['state'] == '过热',
           res['temp']['state'])
    truthy('告警', '功耗估算源 → 状态词「估算」', res['pwr']['state'] == '估算',
           res['pwr']['state'])

    # ⚠️ v1.1.70：告警态下大数字要**分级着色**（对齐 fnmonitor 的 rampInk）
    inks = page.evaluate("""() => {
      const host = document.getElementById('monOverview');
      const g = k => {
        const e = host.querySelector('[data-ov-val="' + k + 'V"]');
        return e ? e.style.color : null;
      };
      return { cpu: g('cpu'), mem: g('mem'), disk: g('disk'), temp: g('temp') };
    }""")
    eq('告警', 'CPU 95% 大数字 = 红色（严重）', inks['cpu'], 'var(--danger)')
    eq('告警', '内存 85% 大数字 = 橙色（告警）', inks['mem'], 'var(--warning)')
    eq('告警', '存储 95% 大数字 = 红色（严重）', inks['disk'], 'var(--danger)')
    eq('告警', '温度 90℃ 大数字 = 红色（严重）', inks['temp'], 'var(--danger)')

    # ----------------------------------------------------------
    section('K 边界与降级')
    # ----------------------------------------------------------
    res = page.evaluate("""() => {
      const out = {};
      const host = document.getElementById('monOverview');
      try { window.FnStyleOverview.render(host, null); out.nullOk = true; }
      catch (e) { out.nullOk = false; out.nullErr = e.message; }
      try { window.FnStyleOverview.render(host, {}); out.emptyOk = true; }
      catch (e) { out.emptyOk = false; out.emptyErr = e.message; }
      try {
        window.FnStyleOverview.render(host, {
          cpu: null, mem: null, storage: null, network: null,
          temp: null, power: null });
        out.nullsOk = true;
      } catch (e) { out.nullsOk = false; out.nullsErr = e.message; }
      const q = s => { const e = host.querySelector(s); return e ? e.textContent : null; };
      /* v1.1.70：大数字是普通文本 → 读 textContent */
      const aria = s => { const e = host.querySelector(s);
                          return e ? e.textContent : null; };
      out.states = {
        cpu: q('[data-ov-state="cpu"]'), mem: q('[data-ov-state="mem"]'),
        disk: q('[data-ov-state="disk"]'), net: q('[data-ov-state="net"]'),
        temp: q('[data-ov-state="temp"]'), pwr: q('[data-ov-state="pwr"]'),
      };
      out.vals = {
        cpu: aria('[data-ov-val="cpuV"]'), disk: aria('[data-ov-val="diskV"]'),
        rx: aria('[data-ov-val="rxV"]'),
      };
      try {
        window.FnStyleOverview.render(host, {
          cpu: { percent: NaN }, mem: { percent: Infinity },
          network: { available: true, rxRate: NaN, txRate: NaN } });
        out.nanOk = true;
      } catch (e) { out.nanOk = false; out.nanErr = e.message; }
      return out;
    }""")
    truthy('边界', 'render(null) 不抛异常', res['nullOk'], res.get('nullErr', ''))
    truthy('边界', 'render({}) 不抛异常', res['emptyOk'], res.get('emptyErr', ''))
    truthy('边界', '全 null 字段不抛异常', res['nullsOk'], res.get('nullsErr', ''))
    eq('边界', 'cpu 无数据显示「无数据」', res['states']['cpu'], '无数据')
    eq('边界', 'mem 无数据显示「无数据」', res['states']['mem'], '无数据')
    eq('边界', '存储无数据显示「无存储池」', res['states']['disk'], '无存储池')
    eq('边界', '网络无数据显示「无网卡」', res['states']['net'], '无网卡')
    eq('边界', '温度无数据显示「无传感器」', res['states']['temp'], '无传感器')
    eq('边界', '功耗无数据显示「不可用」', res['states']['pwr'], '不可用')
    eq('边界', 'cpu 数值占位 --', res['vals']['cpu'], '--')
    eq('边界', '存储数值占位 --', res['vals']['disk'], '--')
    eq('边界', '网络数值占位 --', res['vals']['rx'], '--')
    truthy('边界', 'NaN/Infinity 不抛异常', res['nanOk'], res.get('nanErr', ''))

    # ----------------------------------------------------------
    section('M 概览区块位置与布局')
    # ----------------------------------------------------------
    res = page.evaluate("""() => {
      const ov = document.getElementById('overviewCard');
      const st = document.getElementById('storageCard');
      const grid = document.querySelector('.ov-metrics');
      const cards = [...document.querySelectorAll('.ov-metric')];
      const r0 = cards[0].getBoundingClientRect();
      const r1 = cards[1].getBoundingClientRect();
      const r3 = cards[3].getBoundingClientRect();
      return {
        above: ov.getBoundingClientRect().top < st.getBoundingClientRect().top,
        ovTop: Math.round(ov.getBoundingClientRect().top),
        stTop: Math.round(st.getBoundingClientRect().top),
        gridW: Math.round(grid.getBoundingClientRect().width),
        row1: Math.round(r0.top), row2: Math.round(r1.top),
        col3Left: Math.round(r3.left),
        overflow: document.documentElement.scrollWidth >
                  document.documentElement.clientWidth,
      };
    }""")
    truthy('布局', '概览区块在存储卡片上方', res['above'],
           '概览top=%s 存储top=%s' % (res['ovTop'], res['stTop']))
    truthy('布局', '第 2 张卡与第 1 张卡同行', abs(res['row1'] - res['row2']) < 4,
           'row1=%s row2=%s' % (res['row1'], res['row2']))
    truthy('布局', '第 4 张卡换行（3 列栅格）', res['col3Left'] > 0, res['col3Left'])
    truthy('布局', '无横向溢出', not res['overflow'])
    truthy('布局', '栅格宽度正常', res['gridW'] > 500, res['gridW'])



def main():
    handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(
        *a, directory=str(UI), **kw)
    httpd = socketserver.TCPServer(('127.0.0.1', 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = 'http://127.0.0.1:%d' % port

    host_path = UI / '_verify_overview_host.html'
    host_path.write_text(HOST_HTML, encoding='utf-8')

    console_errors = []
    page_errors = []

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=['--no-sandbox'])
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            page.on('console',
                    lambda m: console_errors.append(m.text)
                    if m.type == 'error' else None)
            page.on('pageerror', lambda e: page_errors.append(str(e)))
            page.goto(base + '/_verify_overview_host.html')
            page.wait_for_function('window.FnStyleOverview !== undefined')

            run_tests(page)

            # 控制台干净（放最后判定，前面已收集）
            section('N 控制台无报错')
            eq('控制台', '页面无 JS 异常', page_errors, [])
            eq('控制台', '控制台无 error 输出', console_errors, [])

            browser.close()
    finally:
        try:
            host_path.unlink()
        except Exception:
            pass
        httpd.shutdown()

    print('\n' + '=' * 72)
    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r[2] == 'PASS')
    failed = total - passed
    print('结果：%d/%d 通过，%d 失败' % (passed, total, failed))
    if failed:
        print('\n失败项：')
        for g, i, s, d in RESULTS:
            if s == 'FAIL':
                print('  ❌ %s / %s  %s' % (g, i, d))
    print('=' * 72)
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
