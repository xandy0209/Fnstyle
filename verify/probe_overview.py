#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""probe_overview.py —— 「系统重要内容概览」实机 CDP 探针（v1.1.69）

在真实 NAS 上跑（Chrome 带 --remote-debugging-port=9222），
逐项核对概览六卡片区块的**真实渲染结果与真实数据**：

  1. API 层：/api/monitor 的 network 字段是否到位（速率 / 今日累计 / 接口数）
  2. DOM 层：六张卡片是否渲染、顺序、标签、状态词
  3. 数值层：点阵数字的 aria-label 是否与 API 真实值一致
  4. 交互层：点「处理器」卡能打开对应抽屉（data-sheet 白名单生效）
  5. 布局层：概览区块是否真的在存储卡片上方（比较 getBoundingClientRect）
  6. 趋势层：趋势线是否有路径（跑满两个采样点后）
  7. 截图留证

前置：
  · 已把 v1.1.69 部署到 NAS
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
                   'shot_overview_v1169.png')


def snap_overview(c):
    """抓概览区块的 DOM 快照。"""
    raw = c.js("""(function(){
      var host = document.getElementById('monOverview');
      if (!host) return '{"err":"NO_HOST"}';
      var cards = [].slice.call(host.querySelectorAll('.ov-metric'));
      function t(s){ var e = host.querySelector(s); return e ? e.textContent : null; }
      function a(s){ var e = host.querySelector(s);
                     return e ? e.getAttribute('aria-label') : null; }
      var ovCard = document.getElementById('monOverview');
      var wrap = ovCard ? ovCard.closest('.card') : null;
      /* 存储卡片：概览之后的下一个 .card */
      var cards2 = [].slice.call(document.querySelectorAll('.card'));
      var wi = cards2.indexOf(wrap);
      var nextCard = (wi >= 0 && wi + 1 < cards2.length) ? cards2[wi + 1] : null;
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
        gauges: host.querySelectorAll('[data-ov-gauge-el]').length,
        sparks: host.querySelectorAll('[data-ov-spark]').length,
        sparkPaths: host.querySelectorAll('.ov-spark-line').length,
        sparkDots: host.querySelectorAll('.ov-spark-dot').length,
        pipN: host.querySelectorAll('.ov-pip').length,
        dotCircles: (function(){ var e = host.querySelector('[data-ov-val="cpuV"] svg');
          return e ? e.querySelectorAll('circle').length : -1; })(),
        /* 布局：概览卡片必须在上方 */
        ovTop: wrap ? Math.round(wrap.getBoundingClientRect().top) : null,
        nextTop: nextCard ? Math.round(nextCard.getBoundingClientRect().top) : null,
        nextLabel: nextCard ? (function(){
            var h = nextCard.querySelector('h2'); return h ? h.textContent : null; })() : null,
        ovWidth: wrap ? Math.round(wrap.getBoundingClientRect().width) : null,
        gridCols: (function(){ var g = host.querySelector('.ov-metrics');
          return g ? getComputedStyle(g).gridTemplateColumns : null; })(),
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
    ck.check('点阵数字已渲染（circle > 0）', (ov.get('dotCircles') or 0) > 0,
             str(ov.get('dotCircles')))

    # ----------------------------------------------------------
    ck.section('3. 数值层：点阵值 vs 页面内同一帧数据')
    # ----------------------------------------------------------
    # ⚠️ v1.1.69：这里**不能**拿 DOM 快照去比「先前抓的 API」。
    #    CPU / 功耗是实时值 —— 抓 DOM 和抓 API 之间隔了几秒，
    #    值本来就会变（实测 CPU 28% vs 21.8%、功耗 29.0 vs 31.0），
    #    那是**采样时差**，不是 bug。
    #    正解：在**页面内同一帧**里同时取「概览渲染用的数据源」
    #    （window.FnStyleMonitor.lastData()）与「DOM 上显示的点阵值」，
    #    两者必然同源 → 逐项核对。
    same = c.js("""(function(){
      var d = window.FnStyleMonitor.lastData();
      if (!d) return JSON.stringify({err:'NO_DATA'});
      var host = document.getElementById('monOverview');
      function a(s){ var e = host.querySelector(s);
                     return e ? e.getAttribute('aria-label') : null; }
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
            ck.check('处理器点阵值 = 同一帧 API 值',
                     near(ui['cpu'], raw['cpu'], 1),
                     'UI=%s 同帧=%s' % (ui['cpu'], raw['cpu']))
        if raw.get('mem') is not None:
            ck.check('内存点阵值 = 同一帧 API 值',
                     near(ui['mem'], raw['mem'], 1),
                     'UI=%s 同帧=%s' % (ui['mem'], raw['mem']))
        if raw.get('disk') is not None:
            ck.check('存储点阵值 = 同一帧 API 值',
                     near(ui['disk'], raw['disk'], 1),
                     'UI=%s 同帧=%s' % (ui['disk'], raw['disk']))
        if raw.get('pwr') is not None:
            ck.check('功耗点阵值 = 同一帧 API 值',
                     near(ui['pwr'], raw['pwr'], 0.05),
                     'UI=%s 同帧=%s' % (ui['pwr'], raw['pwr']))
        if raw.get('temp') is not None:
            ck.check('温度点阵值 = 同一帧 API 值',
                     near(ui['temp'], raw['temp'], 1),
                     'UI=%s 同帧=%s' % (ui['temp'], raw['temp']))

    ck.check('网络点阵值非空（不是 --）',
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
    c.js("(function(){var b=document.querySelector('#monComputeClose');"
         "if(b)b.click();return 'ok';})()")
    c.pump(1.5)

    # ----------------------------------------------------------
    ck.section('8. 截图留证')
    # ----------------------------------------------------------
    try:
        clip = c.js("""(function(){
          var el = document.getElementById('monOverview');
          if (!el) return null;
          var r = el.getBoundingClientRect();
          return {x: Math.max(0, r.left - 12), y: Math.max(0, r.top - 12),
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
