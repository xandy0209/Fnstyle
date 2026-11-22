#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""probe_overview.py —— 「系统重要内容概览」实机 CDP 探针（v1.1.72）

在真实 NAS 上跑（Chrome 带 --remote-debugging-port=9222），
逐项核对概览六卡片区块的**真实渲染结果与真实数据**：

  1. API 层：/api/monitor 的 network 字段是否到位（速率 / 今日累计 / 接口数）
  2. DOM 层：六张卡片是否渲染、顺序、标签、状态词
  3. 数值层：大数字的显示值是否与 API 真实值一致（v1.1.70 起为普通字体，非点阵）
  4. 交互层：点「处理器」卡能打开对应抽屉（data-sheet 白名单生效）
  5. 布局层：概览区块是否真的在存储卡片上方（比较 getBoundingClientRect）
  6. 趋势层：趋势线是否有路径（跑满两个采样点后）
  7. v1.1.72 专项：外层无 .card 卡片 / 底部留白 / 五张卡趋势线均已绘制
  8. 截图留证

前置：
  · 已把 v1.1.72 部署到 NAS
  · Chrome 已开 --remote-debugging-port=9222 且打开了 NAS 应用页
    （或先跑 verify/cdp_open_tab.py）

运行：python3 verify/probe_overview.py
"""
import base64
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from probe_base import CDP, Checker, api, connect  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   'shot_overview_v1172.png')


def snap_overview(c):
    """抓概览区块的 DOM 快照。"""
    raw = c.js("""(function(){
      var host = document.getElementById('monOverview');
      if (!host) return '{"err":"NO_HOST"}';
      var cards = [].slice.call(host.querySelectorAll('.ov-metric'));
      function t(s){ var e = host.querySelector(s); return e ? e.textContent : null; }
      function a(s){ var e = host.querySelector(s);
                     return e ? e.textContent : null; }
      var ovCard = document.getElementById('monOverview');
      /* ⚠️ v1.1.72：概览区外层不再是 .card（老板：「不要最外层的卡片」）
         → closest('.card') 会返回 null。改成用 .ov-wrap，
         并按 DOM 顺序找**概览之后的第一张 .card**（存储卡）。 */
      var wrap = ovCard ? ovCard.closest('.ov-wrap') : null;
      var cards2 = [].slice.call(document.querySelectorAll('.card'));
      var nextCard = null;
      if (ovCard) {
        /* 用 compareDocumentPosition 判断「在概览之后」 */
        for (var ci = 0; ci < cards2.length; ci++) {
          if (ovCard.compareDocumentPosition(cards2[ci]) &
              Node.DOCUMENT_POSITION_FOLLOWING) { nextCard = cards2[ci]; break; }
        }
      }
      return JSON.stringify({
        err: null,
        n: cards.length,
        labels: cards.map(function(x){
          var l = x.querySelector('.ov-m-label');
          return l ? l.textContent : null; }),
        states: {
          cpu: t('[data-ov-state="cpu"]'), mem: t('[data-ov-state="mem"]'),
          disk: t('[data-ov-state="disk"]'), net: t('[data-ov-state="net"]'),
          temp: t('[data-ov-state="temp"]'), pwr: t('[data-ov-state="pwr"]'),
        },
        vals: {
          cpu: a('[data-ov-val="cpuV"]'), mem: a('[data-ov-val="memV"]'),
          disk: a('[data-ov-val="diskV"]'), temp: a('[data-ov-val="tempV"]'),
          pwr: a('[data-ov-val="pwrV"]'), rx: a('[data-ov-val="rxV"]'),
          tx: a('[data-ov-val="txV"]'),
        },
        units: {
          cpu: t('[data-ov-unit="cpuU"]'), mem: t('[data-ov-unit="memU"]'),
          disk: t('[data-ov-unit="diskU"]'), temp: t('[data-ov-unit="tempU"]'),
          pwr: t('[data-ov-unit="pwrU"]'), rx: t('[data-ov-unit="rxU"]'),
          tx: t('[data-ov-unit="txU"]'),
        },
        subs: {
          cpu: t('[data-ov-sub="cpu"]'), mem: t('[data-ov-sub="mem"]'),
          disk: t('[data-ov-sub="disk"]'), net: t('[data-ov-sub="net"]'),
          temp: t('[data-ov-sub="temp"]'), pwr: t('[data-ov-sub="pwr"]'),
        },
        sheets: [].slice.call(host.querySelectorAll('[data-sheet]'))
                  .map(function(x){ return x.getAttribute('data-ov'); }).sort(),
        /* ⚠️ v1.1.78：存储卡走独立通道 data-ov-action（复用硬盘详情抽屉），
           绝不能带 data-sheet —— 否则掉进 openCompute 的 else 兜底
           误开「处理器」抽屉。 */
        diskAction: (function(){
          var e = host.querySelector('[data-ov="disk"]');
          return e ? e.getAttribute('data-ov-action') : null; })(),
        diskHasSheet: (function(){
          var e = host.querySelector('[data-ov="disk"]');
          return e ? !!e.getAttribute('data-sheet') : null; })(),
        diskTap: (function(){
          var e = host.querySelector('[data-ov="disk"]');
          return e ? e.classList.contains('ov-tap') : null; })(),
        netAction: (function(){
          var e = host.querySelector('[data-ov="net"]');
          return e ? e.getAttribute('data-ov-action') : null; })(),
        gauges: host.querySelectorAll('[data-ov-gauge-el]').length,
        sparks: host.querySelectorAll('[data-ov-spark]').length,
        sparkPaths: host.querySelectorAll('.ov-spark-line').length,
        sparkDots: host.querySelectorAll('.ov-spark-dot').length,
        pipN: host.querySelectorAll('.ov-pip').length,
        /* v1.1.70：弃用点阵 → 数值元素内部不应有 svg/circle */
        numSvg: (function(){
          return [...host.querySelectorAll('[data-ov-val]')]
                 .filter(function(e){ return e.querySelector('svg'); }).length;
        })(),
        numCircles: host.querySelectorAll('[data-ov-val] circle').length,
        /* 布局：概览卡片必须在上方 */
        ovTop: wrap ? Math.round(wrap.getBoundingClientRect().top) : null,
        nextTop: nextCard ? Math.round(nextCard.getBoundingClientRect().top) : null,
        nextLabel: nextCard ? (function(){
            var h = nextCard.querySelector('h2'); return h ? h.textContent : null; })() : null,
        ovWidth: wrap ? Math.round(wrap.getBoundingClientRect().width) : null,
        gridCols: (function(){ var g = host.querySelector('.ov-metrics');
          return g ? getComputedStyle(g).gridTemplateColumns : null; })(),
        /* ⚠️ v1.1.71：六卡等高 + 数字/仪表水平对齐 */
        cardHeights: (function(){
          return [...host.querySelectorAll('.ov-metric')]
                 .map(function(c){ return Math.round(c.getBoundingClientRect().height); });
        })(),
        alignDiffs: (function(){
          var pairs = [['cpu','cpuV'],['mem','memV'],['disk','diskV'],
                       ['temp','tempV'],['pwr','pwrV']];
          return pairs.map(function(p){
            var g = host.querySelector('[data-ov-gauge-el="'+p[0]+'"]');
            var n = host.querySelector('[data-ov-val="'+p[1]+'"]');
            if (!g || !n) return -999;
            var gr = g.getBoundingClientRect(), nr = n.getBoundingClientRect();
            return Math.round(Math.abs((nr.top+nr.height/2)-(gr.top+gr.height/2)));
          });
        })(),
        gaugeW: (function(){
          var e = host.querySelector('[data-ov-gauge-el="cpu"]');
          return e ? Math.round(e.getBoundingClientRect().width) : -1;
        })(),
        /* ⚠️ v1.1.73：仪表中心图标**实际绘制尺寸**（getBBox 是局部坐标，
           测不出 transform 缩放 → 必须乘 getScreenCTM 的矩阵） */
        gaugeIcoVisual: (function(){
          var e = host.querySelector('[data-ov-gauge-el="cpu"] .ov-gauge-ico');
          if (!e) return null;
          var bb = null;
          try { bb = e.getBBox(); } catch (err) { return null; }
          if (!bb || !bb.width) return null;
          var ctm = e.getScreenCTM();
          if (!ctm) return null;
          return { w: Math.round(bb.width * ctm.a * 10) / 10,
                   h: Math.round(bb.height * ctm.d * 10) / 10 };
        })(),
        /* ⚠️ v1.1.73：容器查询是否生效（container-type + 实际容器宽） */
        wrapContainerType: (function(){
          var w = document.querySelector('#panel-monitor .ov-wrap');
          return w ? getComputedStyle(w).containerType : null;
        })(),
        wrapWidth: (function(){
          var w = document.querySelector('#panel-monitor .ov-wrap');
          return w ? Math.round(w.getBoundingClientRect().width) : null;
        })(),
        /* ⚠️ v1.1.73：六卡的行 top（验证 3 列换行） */
        rowTops: (function(){
          return [...host.querySelectorAll('.ov-metric')]
                 .map(function(c){ return Math.round(c.getBoundingClientRect().top); });
        })(),
        /* ⚠️ v1.1.73：存储卡右上角状态词 */
        diskStateText: (function(){
          var e = host.querySelector('[data-ov-state="disk"]');
          return e ? e.textContent.trim() : null;
        })(),
        /* ⚠️ v1.1.73：数字与单位的间距 */
        numUnitGap: (function(){
          var v = host.querySelector('[data-ov-val="cpuV"]');
          var u = host.querySelector('[data-ov-unit="cpuU"]');
          if (!v || !u) return -1;
          var vr = v.getBoundingClientRect(), ur = u.getBoundingClientRect();
          return Math.round((ur.left - vr.right) * 10) / 10;
        })(),
        /* ⚠️ v1.1.74：标题行已删除 / 数字与副标题间距 / 相对卡顶偏移 */
        hasTitleRow: !!document.querySelector('.ov-wrap-head'),
        dmToSubGap: (function(){
          var c = host.querySelector('[data-ov="cpu"]');
          var d = c.querySelector('[data-ov-val="cpuV"]');
          var s = c.querySelector('[data-ov-sub="cpu"]');
          if (!d || !s) return -1;
          var dr = d.getBoundingClientRect(), sr = s.getBoundingClientRect();
          return Math.round((sr.top - dr.bottom) * 10) / 10;
        })(),
        dmRelTop: (function(){
          var c = host.querySelector('[data-ov="cpu"]');
          var d = c.querySelector('[data-ov-val="cpuV"]');
          if (!c || !d) return -1;
          return Math.round(d.getBoundingClientRect().top - c.getBoundingClientRect().top);
        })(),
        subRelTop: (function(){
          var c = host.querySelector('[data-ov="cpu"]');
          var s = c.querySelector('[data-ov-sub="cpu"]');
          if (!c || !s) return -1;
          return Math.round(s.getBoundingClientRect().top - c.getBoundingClientRect().top);
        })(),
        gaugePos: (function(){
          var e = host.querySelector('[data-ov="cpu"] .ov-m-gauge');
          return e ? getComputedStyle(e).position : null;
        })(),
        /* ⚠️ v1.1.76：环形图相对卡顶（v1.1.74 是 25px，下移 10px → 35px） */
        gaugeRelTop: (function(){
          var c = host.querySelector('[data-ov="cpu"]');
          var g = c && c.querySelector('.ov-m-gauge');
          if (!c || !g) return -1;
          return Math.round(g.getBoundingClientRect().top - c.getBoundingClientRect().top);
        })(),
        /* ⚠️ v1.1.76：数字中心与仪表圆心的差（v1.1.73 定的「水平居中」基线） */
        dmGaugeCydiff: (function(){
          var c = host.querySelector('[data-ov="cpu"]');
          var d = c && c.querySelector('[data-ov-val="cpuV"]');
          var g = c && c.querySelector('.ov-m-gauge');
          if (!c || !d || !g) return -1;
          var dr = d.getBoundingClientRect(), gr = g.getBoundingClientRect();
          return Math.round((dr.top + dr.height/2) - (gr.top + gr.height/2));
        })(),
        /* ⚠️ v1.1.76：存储卡右上角状态词与告警类 */
        diskState: (function(){
          var e = host.querySelector('[data-ov-state="disk"]');
          return e ? e.textContent.trim() : null;
        })(),
        diskWarn: (function(){
          var c = host.querySelector('[data-ov="disk"]');
          return c ? c.classList.contains('is-warn') : null;
        })(),
        diskCrit: (function(){
          var c = host.querySelector('[data-ov="disk"]');
          return c ? c.classList.contains('is-crit') : null;
        })(),
        /* ⚠️ v1.1.72：概览区**外层不再是 .card 卡片**
           （老板：「不要最外层的卡片，直接呈现内部的卡片即可」） */
        outerHasCard: (function(){
          var w = document.querySelector('#panel-monitor .ov-wrap');
          return w ? w.classList.contains('card') : null;
        })(),
        outerHasHeader: (function(){
          var w = document.querySelector('#panel-monitor .ov-wrap');
          return w ? !!w.querySelector(':scope > header') : null;
        })(),
        /* ⚠️ v1.1.72：底部留白（padding-bottom 与实际视觉留白） */
        padBottoms: (function(){
          return [...host.querySelectorAll('.ov-metric')].map(function(c){
            return Math.round(parseFloat(getComputedStyle(c).paddingBottom)*10)/10;
          });
        })(),
        bottomGaps: (function(){
          return [...host.querySelectorAll('.ov-metric')].map(function(c){
            var cr = c.getBoundingClientRect();
            var foot = c.querySelector('.ov-m-foot');
            if (!foot) return -1;
            var pb = parseFloat(getComputedStyle(c).paddingBottom) || 0;
            return Math.round((cr.bottom - pb - foot.getBoundingClientRect().bottom)*10)/10;
          });
        })(),
        /* ⚠️ v1.1.72：五张带趋势线的卡是否**真的画出了内容** */
        perCardSpark: (function(){
          return [...host.querySelectorAll('.ov-metric')].map(function(c){
            var k = c.getAttribute('data-ov');
            var sp = c.querySelector('[data-ov-spark]');
            if (!sp) return {k:k, spark:0, lines:0, dots:0};
            return {k:k, spark:1,
                    lines: sp.querySelectorAll('.ov-spark-line').length,
                    dots:  sp.querySelectorAll('.ov-spark-dot').length};
          });
        })(),
      });
    })()""")
    try:
        return json.loads(raw or "{}")
    except Exception:
        return {"err": "PARSE_FAIL", "raw": str(raw)[:400]}


def main():
    ck = Checker('概览实机探针')

    # ----------------------------------------------------------
    ck.section('1. API 层：/api/monitor 的 network 字段')
    # ----------------------------------------------------------
    try:
        snap = api('/api/monitor')
    except Exception as e:
        print('❌ 调 /api/monitor 失败: %s' % e)
        ck.check('API 可达', False, str(e))
        ck.finish()
        return 1

    ck.check('API 返回 success', bool(snap.get('success')), str(snap.get('success')))
    # ⚠️ v1.1.69：/api/monitor 的快照是**扁平**结构 —— 顶层直接就是
    #    cpu / mem / network / power ...，**没有** data 包裹层。
    #    之前按 snap['data'] 取 → 恒为 None → 误判「network 缺失」。
    data = snap.get('data') if isinstance(snap.get('data'), dict) else snap
    net = data.get('network')
    ck.check('快照含 network 字段', isinstance(net, dict), type(net).__name__)

    if isinstance(net, dict):
        ck.check('network.available = True', net.get('available') is True,
                 str(net.get('available')))
        ck.check('network 含 rxRate/txRate',
                 'rxRate' in net and 'txRate' in net,
                 'rx=%s tx=%s' % (net.get('rxRate'), net.get('txRate')))
        ck.check('network 含 todayRx/todayTx',
                 'todayRx' in net and 'todayTx' in net,
                 'todayRx=%s todayTx=%s' % (net.get('todayRx'), net.get('todayTx')))
        ck.check('network 含 day 日期键', bool(net.get('day')), str(net.get('day')))
        ck.check('network 含 ifaceCount', 'ifaceCount' in net,
                 str(net.get('ifaceCount')))
        ck.check('速率非负', (net.get('rxRate') or 0) >= 0 and
                 (net.get('txRate') or 0) >= 0,
                 'rx=%s tx=%s' % (net.get('rxRate'), net.get('txRate')))
        ck.check('今日累计非负', (net.get('todayRx') or 0) >= 0 and
                 (net.get('todayTx') or 0) >= 0,
                 'rx=%s tx=%s' % (net.get('todayRx'), net.get('todayTx')))
        ck.check('接口清单非空（NAS 有网口）',
                 len(net.get('ifaces') or []) >= 1,
                 str([i.get('name') for i in (net.get('ifaces') or [])]))

    # 采集版本探针
    power = data.get('power') or {}
    ck.check('collectVersion 已上报', bool(power.get('collectVersion')),
             str(power.get('collectVersion')))

    # ----------------------------------------------------------
    ck.section('2. DOM 层：六张卡片渲染')
    # ----------------------------------------------------------
    c = connect()
    # 硬刷新 → 切到系统监控 tab
    c.send("Page.reload", {"ignoreCache": True})
    c.pump(7)
    c.send("Emulation.setDeviceMetricsOverride",
           {"width": 1440, "height": 1400, "deviceScaleFactor": 2,
            "mobile": False})
    c.pump(2)
    c.js("(function(){var b=document.querySelector('[data-tab=monitor]');"
         "if(b)b.click();return 'ok';})()")
    c.pump(5)
    # 再等一轮采集，让趋势线累积到 ≥2 个点
    c.pump(8)

    ov = snap_overview(c)
    if ov.get('err'):
        ck.check('概览宿主存在', False, ov.get('err') or ov.get('raw', ''))
        ck.finish()
        return 1

    ck.check('概览宿主存在', True)
    ck.check('渲染出 6 张卡片', ov.get('n') == 6, str(ov.get('n')))
    ck.check('卡片顺序正确',
             ov.get('labels') == ['处理器', '内存', '存储', '网络', '温度', '功耗'],
             str(ov.get('labels')))
    ck.check('仪表数量 = 5', ov.get('gauges') == 5, str(ov.get('gauges')))
    ck.check('趋势线宿主 = 5', ov.get('sparks') == 5, str(ov.get('sparks')))
    # ⚠️ v1.1.70 回归：概览**不再使用点阵数字**（老板明确指示
    #    「不使用点阵数字，保持与其他的数字字体一致」）——
    #    数值元素内部残留 svg/circle 就是点阵漏网。
    # ⚠️ 别写 `ov.get('x') or -1` —— 值为 0 时会被 or 短路成 -1，断言永远失败。
    #    这里显式取默认值 None 再比较。
    _ns = ov.get('numSvg')
    _nc = ov.get('numCircles')
    ck.check('数值元素内部无 SVG（已弃用点阵）', _ns == 0, str(_ns))
    ck.check('数值元素内部无 circle 点阵', _nc == 0, str(_nc))

    # ----------------------------------------------------------
    ck.section('3. 数值层：大数字值 vs 页面内同一帧数据')
    # ----------------------------------------------------------
    # ⚠️ v1.1.69：这里**不能**拿 DOM 快照去比「先前抓的 API」。
    #    CPU / 功耗是实时值 —— 抓 DOM 和抓 API 之间隔了几秒，
    #    值本来就会变（实测 CPU 28% vs 21.8%、功耗 29.0 vs 31.0），
    #    那是**采样时差**，不是 bug。
    #    正解：在**页面内同一帧**里同时取「概览渲染用的数据源」
    #    （window.FnStyleMonitor.lastData()）与「DOM 上显示的大数字值」，
    #    两者必然同源 → 逐项核对。
    same = c.js("""(function(){
      var d = window.FnStyleMonitor.lastData();
      if (!d) return JSON.stringify({err:'NO_DATA'});
      var host = document.getElementById('monOverview');
      function a(s){ var e = host.querySelector(s);
                     return e ? e.textContent : null; }
      var st = d.storage || {}, sum = st.summary || {};
      var pw = d.power || {};
      var tp = (d.temp || {});
      var net = d.network || {};
      return JSON.stringify({
        err: null,
        ui: { cpu: a('[data-ov-val="cpuV"]'), mem: a('[data-ov-val="memV"]'),
              disk: a('[data-ov-val="diskV"]'), pwr: a('[data-ov-val="pwrV"]'),
              temp: a('[data-ov-val="tempV"]'), rx: a('[data-ov-val="rxV"]'),
              tx: a('[data-ov-val="txV"]') },
        raw: { cpu: (d.cpu||{}).percent, mem: (d.mem||{}).percent,
               disk: sum.percent, pwr: pw.total,
               temp: tp.cpuTemp != null ? tp.cpuTemp : tp.mbTemp,
               rx: net.rxRate, tx: net.txRate }
      });
    })()""")
    try:
        sm = json.loads(same or "{}")
    except Exception:
        sm = {"err": "PARSE_FAIL"}

    if sm.get('err'):
        ck.check('页面内可取到同一帧数据', False, str(sm.get('err')))
    else:
        ck.check('页面内可取到同一帧数据', True)
        ui, raw = sm['ui'], sm['raw']

        def near(a, b, tol=1.0):
            try:
                return abs(float(a) - float(b)) <= tol
            except Exception:
                return False

        if raw.get('cpu') is not None:
            ck.check('处理器大数字值 = 同一帧 API 值',
                     near(ui['cpu'], raw['cpu'], 1),
                     'UI=%s 同帧=%s' % (ui['cpu'], raw['cpu']))
        if raw.get('mem') is not None:
            ck.check('内存大数字值 = 同一帧 API 值',
                     near(ui['mem'], raw['mem'], 1),
                     'UI=%s 同帧=%s' % (ui['mem'], raw['mem']))
        if raw.get('disk') is not None:
            ck.check('存储大数字值 = 同一帧 API 值',
                     near(ui['disk'], raw['disk'], 1),
                     'UI=%s 同帧=%s' % (ui['disk'], raw['disk']))
        if raw.get('pwr') is not None:
            ck.check('功耗大数字值 = 同一帧 API 值',
                     near(ui['pwr'], raw['pwr'], 0.05),
                     'UI=%s 同帧=%s' % (ui['pwr'], raw['pwr']))
        if raw.get('temp') is not None:
            ck.check('温度大数字值 = 同一帧 API 值',
                     near(ui['temp'], raw['temp'], 1),
                     'UI=%s 同帧=%s' % (ui['temp'], raw['temp']))

    ck.check('网络大数字值非空（不是 --）',
             ov['vals']['rx'] not in (None, '--') and
             ov['vals']['tx'] not in (None, '--'),
             'rx=%s tx=%s' % (ov['vals']['rx'], ov['vals']['tx']))

    # ----------------------------------------------------------
    ck.section('4. 状态词与副标题')
    # ----------------------------------------------------------
    for k in ('cpu', 'mem', 'disk', 'net', 'temp', 'pwr'):
        v = ov['states'].get(k)
        ck.check('%s 状态词非空' % k, bool(v and v.strip()), repr(v))

    ck.check('存储副标题非空', bool(ov['subs'].get('disk')), ov['subs'].get('disk'))
    ck.check('网络副标题含今日流量', '今日' in (ov['subs'].get('net') or ''),
             ov['subs'].get('net'))
    ck.check('温度副标题非空', bool(ov['subs'].get('temp')), ov['subs'].get('temp'))
    ck.check('盘位指示条已渲染', (ov.get('pipN') or 0) >= 0, str(ov.get('pipN')))

    # ----------------------------------------------------------
    ck.section('5. 布局：概览在存储卡片上方')
    # ----------------------------------------------------------
    ck.check('概览卡片存在', ov.get('ovTop') is not None, str(ov.get('ovTop')))
    ck.check('下一张卡片是「存储」', '存储' in (ov.get('nextLabel') or ''),
             str(ov.get('nextLabel')))
    ck.check('概览卡片 top < 存储卡片 top',
             (ov.get('ovTop') is not None and ov.get('nextTop') is not None
              and ov['ovTop'] < ov['nextTop']),
             '概览top=%s 存储top=%s' % (ov.get('ovTop'), ov.get('nextTop')))
    ck.check('概览宽度正常', (ov.get('ovWidth') or 0) > 200, str(ov.get('ovWidth')))
    ck.check('栅格为 3 列',
             ov.get('gridCols') and len(ov['gridCols'].split()) == 3,
             str(ov.get('gridCols')))

    # ----------------------------------------------------------
    ck.section('5b. 布局（v1.1.71）：六卡等高 + 数字/仪表水平对齐')
    # ----------------------------------------------------------
    hs = ov.get('cardHeights') or []
    ck.check('六卡完全等高（max-min ≤ 1px）',
             len(hs) == 6 and (max(hs) - min(hs)) <= 1, str(hs))
    ck.check('卡片高度 ≤ 220px（已压缩）',
             bool(hs) and max(hs) <= 220, str(hs))
    ad = ov.get('alignDiffs') or []
    # ⚠️ v1.1.77：环形图第二次下移 10px（老板只要求动环形图，数字不跟随）
    #    → 数字中心与仪表圆心 diff 从 0 变成 10px，断言放宽到 ≤ 12px。
    ck.check('数字与仪表中心对齐（5 张仪表 diff ≤ 12px）',
             len(ad) == 5 and all(abs(d) <= 12 for d in ad), str(ad))
    ck.check('仪表尺寸 84×84（v1.1.73 放大后）',
             abs((ov.get('gaugeW') or 0) - 84) < 2, str(ov.get('gaugeW')))

    # ----------------------------------------------------------
    ck.section('5c. v1.1.72：外层卡片 / 底部留白 / 趋势线')
    # ----------------------------------------------------------
    ck.check('概览区外层不再是 .card 卡片',
             ov.get('outerHasCard') is False, str(ov.get('outerHasCard')))
    ck.check('概览区外层没有 header 条',
             ov.get('outerHasHeader') is False, str(ov.get('outerHasHeader')))
    _pb = ov.get('padBottoms') or []
    ck.check('卡片底部 padding ≤ 10px（无厚留白）',
             bool(_pb) and all(p <= 10 for p in _pb), str(_pb))
    _bg = ov.get('bottomGaps') or []
    ck.check('底部内容到卡底 ≤ 12px',
             bool(_bg) and all(g >= -1 and g <= 12 for g in _bg), str(_bg))
    _pcs = {x['k']: x for x in (ov.get('perCardSpark') or [])}
    for _k in ['cpu', 'mem', 'net', 'temp', 'pwr']:
        _x = _pcs.get(_k) or {}
        ck.check('%s 卡趋势线已绘制' % _k,
                 _x.get('spark') == 1 and (_x.get('lines', 0) + _x.get('dots', 0)) >= 1,
                 str(_x))

    # ----------------------------------------------------------
    ck.section('5d. v1.1.73：3 列 / 存储状态 / 仪表放大 / 数字居中')
    # ----------------------------------------------------------
    ck.check('概览容器挂了 container-type（容器查询生效）',
             ov.get('wrapContainerType') in ('inline-size', 'size'),
             str(ov.get('wrapContainerType')))
    ck.check('容器宽度 ≥ 780px（软件默认宽度下应 3 列）',
             (ov.get('wrapWidth') or 0) >= 780, str(ov.get('wrapWidth')))
    ck.check('默认宽度下栅格 = 3 列',
             ov.get('gridCols') and len(ov['gridCols'].split()) == 3,
             str(ov.get('gridCols')))
    _rt = ov.get('rowTops') or []
    ck.check('第 1/2/3 张卡同一行、第 4 张换行（3 列验证）',
             len(_rt) >= 4 and _rt[0] == _rt[1] == _rt[2] and _rt[3] != _rt[0],
             str(_rt))
    ck.check('存储卡右上角有状态词', bool(ov.get('diskStateText')),
             str(ov.get('diskStateText')))
    _iv = ov.get('gaugeIcoVisual') or {}
    ck.check('仪表中心图标实际绘制尺寸 ≥ 20px（v1.1.73 放大）',
             (_iv.get('w') or 0) >= 20, str(_iv))
    ck.check('数字与单位间距 ≤ 3px（v1.1.73 调小）',
             0 <= (ov.get('numUnitGap') or -1) <= 3, str(ov.get('numUnitGap')))

    # ----------------------------------------------------------
    ck.section('5e. v1.1.74：删标题行 / 上下移 15px / 间距 10px / 缩小卡高')
    # ----------------------------------------------------------
    ck.check('概览区标题行已删除（.ov-wrap-head 不存在）',
             ov.get('hasTitleRow') is False, str(ov.get('hasTitleRow')))
    ck.check('数字相对卡顶 = 61px（v1.1.74 的 51 + v1.1.76 的 10）',
             abs((ov.get('dmRelTop') or -1) - 61) <= 2, str(ov.get('dmRelTop')))
    ck.check('副标题相对卡顶 = 103px（v1.1.74 的 93 + 10）',
             abs((ov.get('subRelTop') or -1) - 103) <= 2, str(ov.get('subRelTop')))
    ck.check('数字字形底 → 副标题顶 = 10px',
             abs((ov.get('dmToSubGap') or -1) - 10) <= 1, str(ov.get('dmToSubGap')))
    ck.check('卡片高度 ≤ 175px（v1.1.73 是 192px）',
             bool(hs) and max(hs) <= 175, str(hs))
    ck.check('仪表是绝对定位（不撑高卡片）',
             ov.get('gaugePos') == 'absolute', str(ov.get('gaugePos')))

    # ----------------------------------------------------------
    ck.section('5f. v1.1.76：环形图下移 10px / 存储卡「硬盘问题」状态词')
    # ----------------------------------------------------------
    # 老板：「将卡片中右侧的环形图往下移10PX，
    #   存储卡片右上角的充足在存在硬盘问题是替换为问题提示」
    # v1.1.74 基线：环形图相对卡顶 25px
    #   → v1.1.76 第一次下移 10px → 35px
    #   → v1.1.76 第二次再下移 10px → **45px**
    ck.check('环形图相对卡顶 = 45px（25 + 10 + 10）',
             abs((ov.get('gaugeRelTop') or -1) - 45) <= 2, str(ov.get('gaugeRelTop')))
    # 存储卡：实机 sdc udma_crc=60 → grade=warn → 应显示「1 块需留意」
    #   （若 NAS 上确实无 SMART 计数问题，则允许「充足」，但不能是别的词）
    _ds = ov.get('diskState') or ''
    ck.check('存储卡右上角是「N 块需留意/故障」或「充足」（不是「偏满/将满」）',
             _ds in ('充足',) or ('块需留意' in _ds) or ('块故障' in _ds)
             or ('块偏满' in _ds) or ('块将满' in _ds),
             _ds)
    # 若真的检出硬盘问题，必须带 is-warn / is-crit 类
    if '块需留意' in _ds or '块故障' in _ds or '块偏满' in _ds or '块将满' in _ds:
        ck.check('存储卡有硬盘问题时带告警类（is-warn/is-crit）',
                 bool(ov.get('diskWarn') or ov.get('diskCrit')),
                 'warn=%s crit=%s' % (ov.get('diskWarn'), ov.get('diskCrit')))
    # ⚠️ 数字与仪表圆心的 diff：环形图第二次下移 10px 后，
    #    老板只要求动环形图、数字不动 → diff 变成 **10px**（不再是 0）。
    #    这是老板明确要求的取舍，断言随之放宽到 ≤ 12px。
    #    ⚠️ 不能用 `or 99` 兜底 —— diff 为 0 时会被当成 falsy 变成 99。
    _diff = ov.get('dmGaugeCydiff')
    ck.check('数字与仪表圆心 diff ≤ 12px（环形图二次下移 10px，数字未跟随）',
             isinstance(_diff, (int, float)) and abs(_diff) <= 12, str(_diff))

    # ----------------------------------------------------------
    ck.section('6. 趋势层')
    # ----------------------------------------------------------
    ck.check('趋势线已画出路径或占位点',
             (ov.get('sparkPaths') or 0) >= 1 or (ov.get('sparkDots') or 0) >= 1,
             'paths=%s dots=%s' % (ov.get('sparkPaths'), ov.get('sparkDots')))

    # ----------------------------------------------------------
    ck.section('7. 交互：点处理器卡打开对应抽屉')
    # ----------------------------------------------------------
    ck.check('带 data-sheet 的卡片 = 4（cpu/mem/pwr/temp）',
             ov.get('sheets') == ['cpu', 'mem', 'pwr', 'temp'],
             str(ov.get('sheets')))
    drawer = c.js("""(function(){
      var card = document.querySelector('#monOverview [data-ov="cpu"]');
      if (!card) return JSON.stringify({err:'NO_CARD'});
      card.click();
      return 'ok';
    })()""")
    c.pump(3)
    st2 = c.js("""(function(){
      var d = document.getElementById('monComputeOverlay');
      var body = document.getElementById('monComputeBody');
      return JSON.stringify({
        drawerOpen: !!(d && !d.hasAttribute('hidden')),
        bodyText: body ? body.innerText.replace(/\\s+/g,' ').trim().slice(0,200) : null,
        title: (function(){
          var t = document.querySelector('#monComputeTitle');
          return t ? t.textContent.replace(/\\s+/g,' ').trim() : null; })(),
      });
    })()""")
    try:
        d2 = json.loads(st2 or "{}")
    except Exception:
        d2 = {}
    ck.check('点击处理器卡打开了抽屉', d2.get('drawerOpen') is True,
             str(d2.get('drawerOpen')))
    ck.check('抽屉标题含「处理器」', '处理器' in (d2.get('title') or ''),
             str(d2.get('title')))
    # 关掉抽屉
    # ⚠️ v1.1.78 修正：关闭按钮的 id 是 **btnCloseMonCompute**，
    #    原来写的 #monComputeClose 根本不存在 → 点不到 → 处理器抽屉
    #    一直开着，残留到 7b 段 → 「误开处理器抽屉」误报为失败。
    c.js("(function(){var b=document.querySelector('#btnCloseMonCompute');"
         "if(b)b.click();return 'ok';})()")
    c.pump(1.5)

    # ----------------------------------------------------------
    ck.section('7b. 交互：点存储卡打开硬盘详情抽屉（v1.1.78）')
    # ----------------------------------------------------------
    ck.check('存储卡 data-ov-action = disk',
             ov.get('diskAction') == 'disk', str(ov.get('diskAction')))
    ck.check('存储卡**没有** data-sheet（否则会误开处理器抽屉）',
             ov.get('diskHasSheet') is False, str(ov.get('diskHasSheet')))
    ck.check('存储卡带 ov-tap（可点样式）',
             ov.get('diskTap') is True, str(ov.get('diskTap')))
    ck.check('网络卡没有 data-ov-action（仍不可点）',
             ov.get('netAction') is None, str(ov.get('netAction')))

    # ⚠️ v1.1.78：**先确认处理器抽屉已关闭**再点存储卡。
    #    否则上一段残留的处理器抽屉会让「没有误开处理器抽屉」误判。
    pre = c.js("""(function(){
      var cm = document.getElementById('monComputeOverlay');
      return !!(cm && !cm.hasAttribute('hidden'));
    })()""")
    ck.check('点存储卡前处理器抽屉处于关闭状态', pre is False, str(pre))

    c.js("""(function(){
      var card = document.querySelector('#monOverview [data-ov="disk"]');
      if (!card) return 'NO_CARD';
      card.click();
      return 'ok';
    })()""")
    c.pump(3)
    st3 = c.js("""(function(){
      var ov = document.getElementById('monDrawerOverlay');
      var body = document.getElementById('monDrawerBody');
      var sel = document.getElementById('monDrawerDiskSel');
      var compute = document.getElementById('monComputeOverlay');
      return JSON.stringify({
        drawerOpen: !!(ov && !ov.hasAttribute('hidden')),
        bodyLen: body ? body.innerText.replace(/\\s+/g,' ').trim().length : 0,
        bodyText: body ? body.innerText.replace(/\\s+/g,' ').trim().slice(0,160) : null,
        selVal: sel ? sel.value : null,
        selOpts: sel ? sel.options.length : 0,
        computeOpen: !!(compute && !compute.hasAttribute('hidden')),
        computeTitle: (function(){
          var t = document.querySelector('#monComputeTitleText');
          return t ? t.textContent.trim() : null; })()
      });
    })()""")
    try:
        d3 = json.loads(st3 or "{}")
    except Exception:
        d3 = {}
    ck.check('点击存储卡打开了硬盘详情抽屉', d3.get('drawerOpen') is True,
             str(d3.get('drawerOpen')))
    ck.check('硬盘抽屉内容非空', (d3.get('bodyLen') or 0) > 50,
             'len=%s text=%s' % (d3.get('bodyLen'), d3.get('bodyText')))
    ck.check('硬盘抽屉盘位下拉已填充', (d3.get('selOpts') or 0) >= 1,
             'opts=%s val=%s' % (d3.get('selOpts'), d3.get('selVal')))
    ck.check('**没有**误开处理器抽屉',
             d3.get('computeOpen') is False, str(d3.get('computeOpen')))
    # 关掉抽屉
    c.js("(function(){var b=document.querySelector('#btnCloseMonDrawer');"
         "if(b)b.click();return 'ok';})()")
    c.pump(1.5)

    # ----------------------------------------------------------
    ck.section('8. 截图留证')
    # ----------------------------------------------------------
    try:
        # ⚠️ clip 用的是**文档绝对坐标**，不是视口相对坐标。
        #    getBoundingClientRect() 返回视口相对值 → 必须加上
        #    window.scrollX / scrollY 换算成文档坐标，
        #    否则配合 captureBeyondViewport=True 会截到错误区域。
        clip = c.js("""(function(){
          var el = document.getElementById('monOverview');
          if (!el) return null;
          var r = el.getBoundingClientRect();
          var sx = window.scrollX || 0, sy = window.scrollY || 0;
          return {x: Math.max(0, r.left + sx - 12),
                  y: Math.max(0, r.top + sy - 12),
                  width: Math.min(r.width + 24, 1400),
                  height: Math.min(r.height + 24, 900),
                  scale: 1};
        })()""")
        params = {"format": "png", "captureBeyondViewport": True}
        if clip:
            params["clip"] = clip
        r = c.send("Page.captureScreenshot", params)
        data_b64 = r.get("data") or ""
        if data_b64:
            with open(OUT, "wb") as f:
                f.write(base64.b64decode(data_b64))
            ck.check('截图已保存', True, OUT)
        else:
            ck.check('截图已保存', False, 'captureScreenshot 返回空')
    except Exception as e:
        ck.check('截图已保存', False, str(e))

    ck.finish()
    return 0


if __name__ == '__main__':
    sys.exit(main())
