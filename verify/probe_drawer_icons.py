#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实机探针：抽屉标题栏必须有对应图标

背景
----
v1.1.65 老板需求：「所有抽屉窗口顶部标题栏前增加对应的图标显示」。

三个抽屉各配一个图标：
    计算抽屉（处理器/内存 🧠、温度与散热 🌡、能耗 ⚡）
    硬盘详情抽屉 💾
    巡检历史抽屉 🔍

图标做成 26×26 的圆角方块（底色随主题 accent 走）。

用法：
    python3 verify/probe_drawer_icons.py
前置：Chrome 带 --remote-debugging-port=9222 已启动，NAS 已部署 v1.1.65+。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from probe_base import Checker, connect  # noqa: E402


def measure(c, oid):
    """量某个抽屉里标题栏图标的尺寸与位置。"""
    r = c.js("""(function(){
      var ov = document.querySelector('#%s');
      if (!ov) return {err:'NO_OVERLAY'};
      var ico = ov.querySelector('.mon-drawer-ico');
      var ir = ico ? ico.getBoundingClientRect() : null;
      var h3 = ov.querySelector('h3');
      var hr = h3 ? h3.getBoundingClientRect() : null;
      return {
        hasIco: !!ico,
        icoText: ico ? ico.textContent.trim() : null,
        icoW: ir ? ir.width : null,
        icoH: ir ? ir.height : null,
        titleTop: hr ? hr.top : null,
        icoTop: ir ? ir.top : null,
        visible: ir ? ir.width > 0 : false
      };
    })()""" % oid)
    return r if isinstance(r, dict) else json.loads(r)


def main():
    ck = Checker()
    print("=== 抽屉标题栏图标探针 (v1.1.65) ===")

    c = connect()

    # ---- 计算抽屉：逐个 kind 检查图标与标题 ----
    for kind, want_ico, want_title in [
            ('cpu', '🧠', '处理器'), ('mem', '🧠', '内存'),
            ('temp', '🌡', '温度与散热'), ('power', '⚡', '能耗')]:
        c.js("(function(){var b=document.querySelector('[data-tab=monitor]');"
             "if(b)b.click();return 'ok';})()")
        c.pump(2)
        c.js("(function(){window.FnStyleCompute.open('%s', "
             "window.FnStyleMonitor.lastData());return 'ok';})()" % kind)
        c.pump(3)
        r = c.js("""(function(){
          var ico = document.querySelector('#monComputeIcon');
          var t = document.querySelector('#monComputeTitleText');
          var ir = ico ? ico.getBoundingClientRect() : null;
          return {ico: ico ? ico.textContent.trim() : null,
                  title: t ? t.textContent.trim() : null,
                  w: ir ? ir.width : null, h: ir ? ir.height : null};
        })()""")
        d = r if isinstance(r, dict) else json.loads(r)
        ck.check('%s 抽屉图标 = %s' % (kind, want_ico),
                 d.get("ico") == want_ico,
                 '实际 %r，标题 %r' % (d.get("ico"), d.get("title")))
        ck.check('%s 抽屉标题 = %s' % (kind, want_title),
                 d.get("title") == want_title, '实际 %r' % (d.get("title"),))
        ck.check('%s 抽屉图标尺寸 26x26' % kind,
                 d.get("w") and 24 <= d["w"] <= 28 and d.get("h") and 24 <= d["h"] <= 28,
                 '%sx%s' % (d.get("w"), d.get("h")))

    # ---- 巡检历史抽屉 ----
    c.js("(function(){var b=document.querySelector('#btnMonScanLog');"
         "if(b){b.click();return 'clicked';}return 'NO_BTN';})()")
    c.pump(3)
    d = measure(c, 'monScanLogOverlay')
    ck.check('巡检抽屉有标题栏图标', d.get("hasIco"), json.dumps(d, ensure_ascii=False))
    ck.check('巡检抽屉图标内容 = 🔍', d.get("icoText") == '🔍', repr(d.get("icoText")))
    ck.check('巡检抽屉图标尺寸 26x26',
             d.get("visible") and 24 <= d["icoW"] <= 28 and 24 <= d["icoH"] <= 28,
             '%sx%s' % (d.get("icoW"), d.get("icoH")))
    ck.check('巡检抽屉图标在标题左侧（垂直居中）',
             d.get("icoTop") is not None and d.get("titleTop") is not None
             and abs(d["icoTop"] - d["titleTop"]) < 14,
             'icoTop=%s titleTop=%s' % (d.get("icoTop"), d.get("titleTop")))
    c.js("(function(){document.querySelector('#btnCloseMonScanLog').click();"
         "return 'ok';})()")
    c.pump(2)

    # ---- 硬盘详情抽屉 ----
    c.js("""(function(){
      var cards = document.querySelectorAll('[data-disk], [data-dev]');
      for (var i=0;i<cards.length;i++){
        var dv = cards[i].getAttribute('data-disk')||cards[i].getAttribute('data-dev');
        if (dv){ cards[i].click(); return 'clicked'; }
      }
      return 'NO_CARD';
    })()""")
    c.pump(3)
    d2 = measure(c, 'monDrawerOverlay')
    ck.check('硬盘抽屉有标题栏图标', d2.get("hasIco"), json.dumps(d2, ensure_ascii=False))
    ck.check('硬盘抽屉图标内容 = 💾', d2.get("icoText") == '💾', repr(d2.get("icoText")))
    ck.check('硬盘抽屉图标尺寸 26x26',
             d2.get("visible") and 24 <= d2["icoW"] <= 28 and 24 <= d2["icoH"] <= 28,
             '%sx%s' % (d2.get("icoW"), d2.get("icoH")))

    c.ws.close()
    return ck.finish()


if __name__ == "__main__":
    sys.exit(main())
