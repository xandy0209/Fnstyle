#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""probe_thresholds.py —— 阈值门限配置抽屉实机探针（v1.1.79）

老板需求：「对软件中系统监控部分相关的阈值门限的配置提取出来进行单独的配置，
             配置页面以抽屉方式呈现，入口为页面顶部栏中增加配置管理按钮点击进入。」

在**真实 NAS**（192.168.5.8:9910）上验证：
  ① 顶栏有「配置管理」按钮
  ② 点按钮能打开配置抽屉（右侧滑出）
  ③ 抽屉里按分组列出全部门限（处理器/内存/存储/温度/显示着色）
  ④ 改一个门限并保存 → **概览卡的等级判定立刻跟着变**（核心价值）
  ⑤ 刷新页面后配置仍生效（**持久化**）
  ⑥ 恢复默认能还原
  ⑦ 页面无 JS 异常

用法：
    python3 verify/probe_thresholds.py
前置：Chrome 已带 --remote-debugging-port=9222 启动，且 NAS 已部署 v1.1.79。
"""
import json
import sys
import time

from probe_base import CDP, Checker, api, connect, find_tab

URL = "http://192.168.5.8:9910/"

# 各门限在「概览卡」上的可观测效应：改这些能直接看出等级变化
#   cpuWarn → CPU 卡右上角状态词（繁忙/高负载）
#   capWarn → 存储卡右上角状态词（偏满/将满）
#   tempWarn → 温度卡右上角状态词（偏高/过热）
PROBE_KEYS = ('cpuWarn', 'capWarn', 'tempWarn')


def main():
    ck = Checker()

    # ---------------------------------------------------------------
    ck.section('1. 顶栏「配置管理」按钮')
    # ---------------------------------------------------------------
    # ⚠️ connect(host, port) 内部自己会 find_tab —— 不能传 tab 对象进去
    #    （probe_base.find_tab 收到 dict 会报「in <string> requires string」）。
    c = connect()
    c.send("Page.enable")
    c.send("Runtime.enable")
    c.send("Network.enable")
    c.send("Network.setCacheDisabled", {"cacheDisabled": True})
    c.send("Network.clearBrowserCache")
    c.send("Page.navigate", {"url": URL})
    c.pump(4.0)
    c.send("Page.setWebLifecycleState", {"state": "active"})
    c.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
    c.pump(1.5)

    # ⚠️ 概览区是**懒加载**的 —— 必须切到「系统监控」标签才会渲染。
    #    不切标签的话 #monOverview 里没有卡片，后续「等级跟着变」的断言无从谈起。
    c.js("""(function(){
      var t = document.querySelector('.tab[data-tab="monitor"]');
      if (t) t.click();
      return 'ok';
    })()""")
    c.pump(3.5)

    btn = c.js("""(function(){
      var b = document.getElementById('tbOpenThresholds');
      if (!b) return null;
      var r = b.getBoundingClientRect();
      return JSON.stringify({
        exists: true,
        title: b.getAttribute('title'),
        aria: b.getAttribute('aria-label'),
        visible: r.width > 0 && r.height > 0,
        w: Math.round(r.width), h: Math.round(r.height),
        inTopbar: !!b.closest('.topbar'),
        hasIcon: !!b.querySelector('svg'),
        siblings: b.parentElement ? b.parentElement.children.length : 0
      });
    })()""")
    b = json.loads(btn) if btn else {}
    ck.check('顶栏存在「配置管理」按钮', bool(b.get('exists')))
    ck.check('按钮 title = 配置管理', b.get('title') == '配置管理', b.get('title'))
    ck.check('按钮有 aria-label', b.get('aria') == '配置管理', b.get('aria'))
    ck.check('按钮在 .topbar 内', bool(b.get('inTopbar')))
    ck.check('按钮可见（非零尺寸）', bool(b.get('visible')),
             '%sx%s' % (b.get('w'), b.get('h')))
    ck.check('按钮含 SVG 图标', bool(b.get('hasIcon')))
    ck.check('按钮与其它顶栏按钮同排', (b.get('siblings') or 0) >= 3,
             '同排元素数=' + str(b.get('siblings')))

    # ---------------------------------------------------------------
    ck.section('2. 点按钮打开配置抽屉')
    # ---------------------------------------------------------------
    c.js("""(function(){
      var b = document.getElementById('tbOpenThresholds');
      if (b) b.click();
      return 'ok';
    })()""")
    c.pump(2.0)
    d = c.js("""(function(){
      var ov = document.getElementById('monThresholdOverlay');
      var body = document.getElementById('monThresholdBody');
      return JSON.stringify({
        exists: !!ov,
        open: !!(ov && !ov.hasAttribute('hidden')),
        on: !!(ov && ov.classList.contains('on')),
        rows: document.querySelectorAll('#monThresholdBody .thr-row').length,
        secs: document.querySelectorAll('#monThresholdBody .thr-sec').length,
        inputs: document.querySelectorAll('#monThresholdBody [data-thr-input]').length,
        groups: Array.prototype.map.call(
          document.querySelectorAll('#monThresholdBody .thr-sec-t'),
          function(e){ return e.textContent.trim(); }),
        hasSave: !!document.querySelector('#monThresholdBody [data-thr-save]'),
        hasReset: !!document.querySelector('#monThresholdBody [data-thr-reset]'),
        bodyText: body ? body.innerText.replace(/\\s+/g,' ').trim().slice(0,200) : null
      });
    })()""")
    ov = json.loads(d)
    ck.check('抽屉 DOM 存在', bool(ov.get('exists')))
    ck.check('点按钮后抽屉已打开', bool(ov.get('open')))
    ck.check('抽屉带 .on 过渡类', bool(ov.get('on')))
    ck.check('抽屉渲染出门限行', (ov.get('rows') or 0) >= 13,
             '行数=' + str(ov.get('rows')))
    ck.check('抽屉按 5 个分组分段', (ov.get('secs') or 0) >= 5,
             '分组数=' + str(ov.get('secs')))
    ck.check('每个门限有输入框', (ov.get('inputs') or 0) >= 13,
             '输入框数=' + str(ov.get('inputs')))
    ck.check('分组标题正确', ov.get('groups') == ['处理器', '内存', '存储', '温度', '显示着色'],
             str(ov.get('groups')))
    ck.check('有保存按钮', bool(ov.get('hasSave')))
    ck.check('有恢复默认按钮', bool(ov.get('hasReset')))
    ck.check('抽屉内容非空', len(ov.get('bodyText') or '') > 40,
             (ov.get('bodyText') or '')[:60])

    # ---------------------------------------------------------------
    ck.section('3. 抽屉里的门限值与后端一致')
    # ---------------------------------------------------------------
    api_cfg = api('/api/monitor/thresholds')
    server_cfg = (api_cfg or {}).get('config') or {}
    ui_vals = c.js("""(function(){
      var out = {};
      Array.prototype.forEach.call(
        document.querySelectorAll('#monThresholdBody [data-thr-input]'),
        function(e){ out[e.getAttribute('data-thr-input')] = e.value; });
      return JSON.stringify(out);
    })()""")
    ui_map = json.loads(ui_vals)
    mismatch = []
    for k, v in ui_map.items():
        sv = server_cfg.get(k)
        if sv is None:
            continue
        if str(sv) != str(v):
            mismatch.append('%s: ui=%s server=%s' % (k, v, sv))
    ck.check('抽屉 13 个门限全部有值', len(ui_map) >= 13, '实际=' + str(len(ui_map)))
    ck.check('抽屉显示值 = 后端生效值', not mismatch, '; '.join(mismatch))

    # ---------------------------------------------------------------
    ck.section('4. 改门限 → 概览卡等级判定跟着变（核心价值）')
    # ---------------------------------------------------------------
    # 先记录原始等级
    before = c.js("""(function(){
      var host = document.getElementById('monOverview');
      if (!host) return JSON.stringify({err:'no host'});
      function st(k){
        var e = host.querySelector('[data-ov-state="'+k+'"]');
        var card = host.querySelector('[data-ov="'+k+'"]');
        return { txt: e ? e.textContent.trim() : null,
                 cls: card ? card.className : null };
      }
      return JSON.stringify({cpu:st('cpu'), disk:st('disk'), temp:st('temp')});
    })()""")
    b4 = json.loads(before)
    ck.check('概览区已渲染（cpu 卡有状态词）',
             b4.get('cpu', {}).get('txt') is not None, str(b4.get('cpu')))

    # 把 cpuWarn 调到极低（1）→ CPU 卡必然进入 warn/crit
    c.js("""(function(){
      var inp = document.querySelector('#monThresholdBody [data-thr-input="cpuWarn"]');
      if (!inp) return 'NO_INPUT';
      inp.value = '1';
      return 'ok';
    })()""")
    c.pump(0.3)
    c.js("""(function(){
      var btn = document.querySelector('#monThresholdBody [data-thr-save]');
      if (btn) btn.click();
      return 'ok';
    })()""")
    c.pump(2.5)

    after = c.js("""(function(){
      var host = document.getElementById('monOverview');
      function st(k){
        var e = host.querySelector('[data-ov-state="'+k+'"]');
        var card = host.querySelector('[data-ov="'+k+'"]');
        return { txt: e ? e.textContent.trim() : null,
                 cls: card ? card.className : null };
      }
      return JSON.stringify({
        cpu: st('cpu'),
        cur: (window.FnStyleThresholds ? window.FnStyleThresholds.get('cpuWarn') : null)
      });
    })()""")
    af = json.loads(after)
    ck.check('保存后前端读取层拿到新值 cpuWarn=1', af.get('cur') == 1, str(af.get('cur')))
    ck.check('概览 CPU 卡进入告警态',
             'is-warn' in (af.get('cpu', {}).get('cls') or '')
             or 'is-crit' in (af.get('cpu', {}).get('cls') or ''),
             af.get('cpu', {}).get('cls'))
    ck.check('概览 CPU 卡状态词变为告警文案',
             (af.get('cpu', {}).get('txt') or '') in ('繁忙', '高负载'),
             af.get('cpu', {}).get('txt'))

    # 后端也要落盘
    api_cfg2 = api('/api/monitor/thresholds')
    ck.check('后端已持久化 cpuWarn=1',
             ((api_cfg2 or {}).get('config') or {}).get('cpuWarn') == 1,
             str(((api_cfg2 or {}).get('config') or {}).get('cpuWarn')))

    # ---------------------------------------------------------------
    ck.section('5. 刷新页面后配置仍生效（持久化）')
    # ---------------------------------------------------------------
    c.send("Page.navigate", {"url": URL})
    c.pump(4.0)
    c.send("Page.setWebLifecycleState", {"state": "active"})
    c.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
    c.pump(1.0)
    # 刷新后同样要切到监控标签（概览区懒加载）
    c.js("""(function(){
      var t = document.querySelector('.tab[data-tab="monitor"]');
      if (t) t.click();
      return 'ok';
    })()""")
    c.pump(3.0)

    re = c.js("""(function(){
      var T = window.FnStyleThresholds;
      return JSON.stringify({
        loaded: T ? T.isLoaded() : false,
        cpuWarn: T ? T.get('cpuWarn') : null,
        tempWarn: T ? T.get('tempWarn') : null,
        capWarn: T ? T.get('capWarn') : null
      });
    })()""")
    rr = json.loads(re)
    ck.check('刷新后阈值读取层已加载', bool(rr.get('loaded')))
    ck.check('刷新后 cpuWarn 仍为 1（持久化生效）', rr.get('cpuWarn') == 1,
             str(rr.get('cpuWarn')))

    # ---------------------------------------------------------------
    ck.section('6. 恢复默认')
    # ---------------------------------------------------------------
    c.js("""(function(){
      var b = document.getElementById('tbOpenThresholds');
      if (b) b.click();
      return 'ok';
    })()""")
    c.pump(2.0)
    c.js("""(function(){
      var btn = document.querySelector('#monThresholdBody [data-thr-reset]');
      if (btn) btn.click();
      return 'ok';
    })()""")
    c.pump(2.5)
    rst = c.js("""(function(){
      var T = window.FnStyleThresholds;
      return JSON.stringify({cpuWarn: T ? T.get('cpuWarn') : null});
    })()""")
    rs = json.loads(rst)
    ck.check('恢复默认后 cpuWarn = 85', rs.get('cpuWarn') == 85, str(rs.get('cpuWarn')))

    api_cfg3 = api('/api/monitor/thresholds')
    ck.check('后端也已恢复默认',
             ((api_cfg3 or {}).get('config') or {}).get('cpuWarn') == 85,
             str(((api_cfg3 or {}).get('config') or {}).get('cpuWarn')))

    # ---------------------------------------------------------------
    ck.section('7. 无 JS 异常')
    # ---------------------------------------------------------------
    errs = []
    for ev in c.events:
        if ev.get('method') == 'Runtime.exceptionThrown':
            d = ev.get('params', {}).get('exceptionDetails', {})
            errs.append(d.get('text') or str(d))
        elif ev.get('method') == 'Runtime.consoleAPICalled':
            p = ev.get('params', {})
            if p.get('type') == 'error':
                errs.append(str(p.get('args'))[:200])
    ck.check('全程无 JS 异常', not errs, '; '.join(errs[:3]))

    ck.finish()


if __name__ == '__main__':
    main()
