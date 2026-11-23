#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CDP 实机探针 v1.1.86：验证 NAS 实机上「恢复默认门限 / 保存阈值」按钮的位置

老板反馈：「恢复默认门限和保存阈值按钮的位置不对，请调整到阈值配置下方显示才对。」

本探针连 **NAS 实机**（http://192.168.5.8:9910/），验证：
  1. 配置管理抽屉能打开、阈值表单渲染完成
  2. 按钮区在 DOM 顺序上**位于所有阈值分组之后**
  3. 按钮区在**电费设置之前**、**告警通知之前**
  4. 几何上按钮区紧跟最后一个阈值分组（间距合理，不是被排到别处）
  5. 按钮区有实际尺寸（画出来了，不只是「元素存在」）
  6. 点「恢复默认门限」后按钮区**仍在**（局部重绘不丢按钮）

⚠️ 用子节点**下标**判顺序，不碰 compareDocumentPosition 位语义
   （那玩意儿的方向改错过两次，每次都是探针假红 ⇒ 产品被冤）。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from probe_base import CDP, Checker, connect  # noqa: E402

NAS_URL = "http://192.168.5.8:9910/"


def wait_for(cdp, expr, timeout=40.0, step=0.6):
    """轮询直到 expr 返回真值。返回 (ok, last)。"""
    import time
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            last = cdp.js(expr)
        except Exception as e:
            last = repr(e)
        if last:
            return True, last
        cdp.pump(step)
    return False, last


def _asdict(v):
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return {}
    return {}


def main():
    cdp = connect(host="127.0.0.1")
    ck = Checker("NAS 实机：阈值按钮位置（v1.1.86）")

    cdp.send("Page.navigate", {"url": NAS_URL})

    ok, last = wait_for(cdp, "!!document.getElementById('tbOpenThresholds')")
    ck.check("实机页面就绪（齿轮按钮已渲染）", ok, str(last))

    r = cdp.js("""(() => {
      const t = document.querySelector('.tab[data-tab="monitor"]');
      if (t) { t.click(); return 'clicked-tab'; }
      return 'no-tab';
    })()""")
    cdp.pump(1.8)
    ck.check("切到系统监控标签页", r == "clicked-tab", str(r))

    opened = cdp.js("""(() => {
      const b = document.getElementById('tbOpenThresholds');
      if (!b) return 'no-btn';
      b.click();
      return 'clicked';
    })()""")
    cdp.pump(2.5)
    ck.check("点齿轮按钮打开配置管理抽屉", opened == "clicked", str(opened))

    ok2, last2 = wait_for(
        cdp, "!!document.querySelector('#monThresholdBody [data-tariff-rate]')")
    ck.check("抽屉内容已渲染（电价输入框已出现）", ok2, str(last2))

    d = cdp.js("""(() => {
      const body = document.getElementById('monThresholdBody');
      if (!body) return JSON.stringify({missing:true});
      const kids = Array.prototype.slice.call(body.children);
      const idxOf = (x) => x ? kids.indexOf(x) : -1;
      const secs = Array.from(body.querySelectorAll('.thr-sec'));
      const acts = body.querySelectorAll('.thr-actions-thresholds');
      const actEl = acts[0];
      const tariff = body.querySelector('.thr-sec-tariff');
      const alerts = body.querySelector('.thr-sec-alerts');
      const thrSecs = secs.filter(s => !s.classList.contains('thr-sec-tariff')
                                     && !s.classList.contains('thr-sec-alerts'));
      const iAct = idxOf(actEl);
      const iTariff = idxOf(tariff);
      const iAlerts = idxOf(alerts);
      const iThrLast = thrSecs.length ? idxOf(thrSecs[thrSecs.length-1]) : -1;
      const ar = actEl ? actEl.getBoundingClientRect() : null;
      const tr = tariff ? tariff.getBoundingClientRect() : null;
      const lastThr = thrSecs.length ? thrSecs[thrSecs.length-1].getBoundingClientRect() : null;
      return JSON.stringify({
        missing: false,
        actCount: acts.length,
        secCount: secs.length,
        iAct: iAct, iTariff: iTariff, iAlerts: iAlerts, iThrLast: iThrLast,
        childOrder: kids.map(k => (k.className||'').split(' ')[0]),
        secTitles: secs.map(s => ((s.querySelector('.thr-sec-t')||{}).textContent||'').trim()),
        hasReset: !!body.querySelector('[data-thr-reset]'),
        hasSave: !!body.querySelector('[data-thr-save]'),
        hasMsg: !!body.querySelector('[data-thr-msg]'),
        resetText: (body.querySelector('[data-thr-reset]')||{}).textContent || '',
        saveText: (body.querySelector('[data-thr-save]')||{}).textContent || '',
        actTop: ar ? Math.round(ar.top) : 0,
        actBottom: ar ? Math.round(ar.bottom) : 0,
        actH: ar ? Math.round(ar.height) : 0,
        actRight: ar ? Math.round(ar.right) : 0,
        tariffTop: tr ? Math.round(tr.top) : 0,
        lastThrBottom: lastThr ? Math.round(lastThr.bottom) : 0,
        bodyRight: Math.round(body.getBoundingClientRect().right)
      });
    })()""")
    h = _asdict(d)

    ck.check("抽屉内容存在", h.get("missing") is False)
    ck.check("阈值按钮区存在（且只有一个）",
             h.get("actCount") == 1, "count=%s" % h.get("actCount"))
    ck.check("分段数为 7（阈值 5 + 电费设置 + 告警通知）",
             h.get("secCount") == 7, "secCount=%s" % h.get("secCount"))
    ck.check("按钮区在**所有阈值分组之后**",
             0 < h.get("iAct", -1) and h.get("iAct", -1) > h.get("iThrLast", -1),
             "iAct=%s iThrLast=%s order=%s" % (h.get("iAct"), h.get("iThrLast"),
                                              h.get("childOrder")))
    ck.check("按钮区在「电费设置」之前",
             0 < h.get("iAct", -1) < h.get("iTariff", -1),
             "iAct=%s iTariff=%s" % (h.get("iAct"), h.get("iTariff")))
    ck.check("按钮区在「告警通知」之前",
             0 < h.get("iAct", -1) < h.get("iAlerts", -1),
             "iAct=%s iAlerts=%s" % (h.get("iAct"), h.get("iAlerts")))
    ck.check("有「恢复默认门限」按钮", h.get("hasReset") is True)
    ck.check("有「保存阈值」按钮", h.get("hasSave") is True)
    ck.check("有提示位", h.get("hasMsg") is True)
    ck.check("按钮文案是「恢复默认门限」（不是笼统的「恢复默认」）",
             "恢复默认门限" in (h.get("resetText") or ""), h.get("resetText"))
    ck.check("按钮区顶边紧跟最后一个阈值分组底边（间距 0~40px）",
             0 <= h.get("actTop", 0) - h.get("lastThrBottom", 0) <= 40,
             "actTop=%s lastThrBottom=%s" % (h.get("actTop"), h.get("lastThrBottom")))
    ck.check("按钮区在电价段上方（actBottom ≤ tariffTop）",
             h.get("actBottom", 10 ** 9) <= h.get("tariffTop", 0) + 1,
             "actBottom=%s tariffTop=%s" % (h.get("actBottom"), h.get("tariffTop")))
    ck.check("按钮区有实际高度（画出来了，不是空容器）",
             h.get("actH", 0) > 15, "h=%s" % h.get("actH"))
    ck.check("按钮区未溢出抽屉右界",
             h.get("actRight", 0) <= h.get("bodyRight", 0) + 1,
             "act.right=%s body.right=%s" % (h.get("actRight"), h.get("bodyRight")))

    # ---- 点「恢复默认门限」后按钮区不能消失 ----
    d = cdp.js("""(() => {
      const b = document.querySelector('#monThresholdBody [data-thr-reset]');
      if (!b) return JSON.stringify({missing:true});
      b.scrollIntoView({block:'center'});
      b.click();
      return JSON.stringify({clicked:true});
    })()""")
    cdp.pump(2.5)
    d = cdp.js("""(() => {
      const body = document.getElementById('monThresholdBody');
      if (!body) return JSON.stringify({missing:true});
      const kids = Array.prototype.slice.call(body.children);
      const a = body.querySelector('.thr-actions-thresholds');
      const tariff = body.querySelector('.thr-sec-tariff');
      const secs = body.querySelectorAll('.thr-sec');
      const msg = body.querySelector('[data-thr-msg]');
      return JSON.stringify({
        missing: false,
        count: body.querySelectorAll('.thr-actions-thresholds').length,
        hasReset: !!body.querySelector('[data-thr-reset]'),
        hasSave: !!body.querySelector('[data-thr-save]'),
        hasMsg: !!msg,
        h: a ? Math.round(a.getBoundingClientRect().height) : 0,
        secCount: secs.length,
        iAct: a ? kids.indexOf(a) : -1,
        iTariff: tariff ? kids.indexOf(tariff) : -1,
        msgText: msg ? (msg.textContent || '') : ''
      });
    })()""")
    r = _asdict(d)
    ck.check("重绘后按钮区仍在（没有凭空消失）", r.get("count") == 1,
             "count=%s" % r.get("count"))
    ck.check("重绘后「恢复默认门限」按钮仍在", r.get("hasReset") is True)
    ck.check("重绘后「保存阈值」按钮仍在", r.get("hasSave") is True)
    ck.check("重绘后提示位仍在", r.get("hasMsg") is True)
    ck.check("重绘后按钮区仍有实际高度", r.get("h", 0) > 15, "h=%s" % r.get("h"))
    ck.check("重绘后分段数仍为 7（没有多出重复的告警段）",
             r.get("secCount") == 7, "secCount=%s" % r.get("secCount"))
    ck.check("重绘后按钮区仍在电价段之前（位置没跑掉）",
             0 < r.get("iAct", -1) < r.get("iTariff", -1),
             "iAct=%s iTariff=%s" % (r.get("iAct"), r.get("iTariff")))
    ck.check("重绘给出了结果提示", bool(r.get("msgText")), str(r.get("msgText")))

    print()
    ck.finish()


if __name__ == "__main__":
    main()
