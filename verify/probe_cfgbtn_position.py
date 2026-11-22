#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实机探针：阈值「配置管理」按钮位置（v1.1.80）

背景
----
v1.1.79 把阈值配置入口做在**顶栏**（与「检查更新」「在新页面打开」同排）。
v1.1.80 按老板反馈（2026-11-22，截图带红框+箭头）把按钮**移到监控面板
状态条右端**（「自动刷新」右侧）。

这个探针用 CDP 实测真实几何，确认：
  1. 按钮确实在**监控状态条内**（.mon-toolbar-row 的后代）
  2. 按钮确实**不在顶栏**了（.topbar 里查不到）
  3. 按钮**在「自动刷新」右侧**（x 更大）
  4. 按钮**在状态条卡片右内边距之内**（不越界）
  5. 按钮**垂直居中**于状态条行
  6. 按钮**没有被压缩变形**（宽高合理，齿轮 15×15）
  7. 点击按钮**仍能打开抽屉**（改位置没改坏功能）
  8. 切换到别的标签页时按钮**不可见**（它在监控面板里）
  9. 全程无 JS 异常

用法：
    python3 verify/probe_cfgbtn_position.py
前置：Chrome 带 --remote-debugging-port=9222 启动，且 NAS 已部署 v1.1.80。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe_base import connect  # noqa: E402


class Checker:
    def __init__(self, title):
        self.title = title
        self.passed = 0
        self.failed = []
        print("\n=== %s ===" % title)

    def check(self, name, cond, detail=""):
        if cond:
            self.passed += 1
            print("  [PASS] %s" % name)
        else:
            self.failed.append((name, detail))
            print("  [FAIL] %s   %s" % (name, detail))
        return cond

    def summary(self):
        total = self.passed + len(self.failed)
        print("\n" + "-" * 60)
        print("通过 %d / %d" % (self.passed, total))
        if self.failed:
            print("\n失败明细：")
            for n, d in self.failed:
                print("  ✗ %s  %s" % (n, d))
            return 1
        print("全部通过 ✅")
        return 0


# 取元素几何信息的 JS
JS_GEOM = """
(() => {
  const r = (el) => {
    if (!el) return null;
    const b = el.getBoundingClientRect();
    return {x: Math.round(b.left), y: Math.round(b.top),
            w: Math.round(b.width), h: Math.round(b.height),
            cx: Math.round(b.left + b.width / 2),
            cy: Math.round(b.top + b.height / 2)};
  };
  const btn = document.getElementById('tbOpenThresholds');
  const row = document.querySelector('.mon-toolbar-row');
  const auto = document.querySelector('.mon-auto');
  const toolbar = document.querySelector('.mon-toolbar');
  const topbar = document.querySelector('.topbar');
  const card = toolbar ? toolbar.closest('.card') : null;
  const body = card ? card.querySelector('.body') : null;
  const svg = btn ? btn.querySelector('.cfg-ico') : null;
  return {
    btn: r(btn),
    row: r(row),
    auto: r(auto),
    toolbar: r(toolbar),
    topbar: r(topbar),
    card: r(card),
    body: r(body),
    svg: r(svg),
    inRow: !!(btn && row && row.contains(btn)),
    inTopbar: !!(btn && topbar && topbar.contains(btn)),
    btnClass: btn ? btn.className : null,
    btnTitle: btn ? btn.getAttribute('title') : null,
    panelMonitorActive: (() => {
      const p = document.getElementById('panel-monitor');
      return !!(p && p.classList.contains('active'));
    })(),
    rowChildren: row ? Array.from(row.children).map(e => ({
      tag: e.tagName, id: e.id || '', cls: e.className || '',
      x: Math.round(e.getBoundingClientRect().left),
      w: Math.round(e.getBoundingClientRect().width)
    })) : []
  };
})()
"""


def main():
    c = connect()
    c.send("Page.navigate", {"url": "http://192.168.5.8:9910/"})
    c.pump(4)

    # 收集 JS 异常
    c.send("Runtime.enable")
    c.send("Log.enable")
    errors = []

    # 切到「系统监控」标签（概览区是懒加载的）
    c.js("""(() => {
      const t = document.querySelector('.tab[data-tab="monitor"]');
      if (t) t.click();
      return true;
    })()""")
    c.pump(3)

    ck = Checker("A. 按钮已在监控状态条内")

    g = c.js(JS_GEOM)
    if not g or not g.get("btn"):
        print("  [FAIL] 找不到 #tbOpenThresholds 按钮")
        return 1

    ck.check("按钮存在于 DOM", g["btn"] is not None, str(g.get("btn")))
    ck.check("按钮在 .mon-toolbar-row 内（状态条行）", g["inRow"] is True)
    ck.check("按钮已不在顶栏 .topbar 内", g["inTopbar"] is False)
    ck.check("按钮 title = 配置管理", g.get("btnTitle") == "配置管理", str(g.get("btnTitle")))
    ck.check("按钮带 .mon-cfg-btn 修饰类",
             "mon-cfg-btn" in (g.get("btnClass") or ""), str(g.get("btnClass")))

    # 位置关系
    b, a, row = g["btn"], g["auto"], g["row"]
    ck.check("按钮在「自动刷新」右侧（x 更大）",
             a is not None and b["x"] > a["x"],
             "btn.x=%s auto.x=%s" % (b.get("x"), a.get("x") if a else None))
    ck.check("按钮是状态条行的最后一个子元素（右端）",
             bool(g["rowChildren"]) and g["rowChildren"][-1].get("id") == "tbOpenThresholds",
             str([x.get("id") or x.get("cls") for x in g["rowChildren"]]))

    # 不越界：在卡片 body 的右内边距之内
    body = g.get("body")
    if body:
        pad = 1.15 * 16 * 2  # .mon-toolbar .body 的左右 padding（1.15rem 两侧）
        right_limit = body["x"] + body["w"]
        ck.check("按钮未超出状态条卡片右边缘",
                 b["x"] + b["w"] <= right_limit + 1,
                 "btn.right=%s body.right=%s" % (b["x"] + b["w"], right_limit))
        ck.check("按钮距卡片右边缘留白合理（8~40px）",
                 8 <= (right_limit - (b["x"] + b["w"])) <= 40,
                 "gap=%s" % (right_limit - (b["x"] + b["w"])))
    else:
        ck.check("找到状态条 body", False)

    # 垂直居中
    ck.check("按钮垂直居中于状态条行（偏差 ≤ 3px）",
             abs(b["cy"] - row["cy"]) <= 3,
             "btn.cy=%s row.cy=%s" % (b["cy"], row["cy"]))
    # 尺寸不被压缩
    ck.check("按钮宽度未被压缩（24~48px）", 24 <= b["w"] <= 48, "w=%s" % b["w"])
    ck.check("按钮高度未被压缩（20~40px）", 20 <= b["h"] <= 40, "h=%s" % b["h"])
    svg = g.get("svg")
    if svg:
        ck.check("齿轮图标尺寸 15×15（未被拉伸）",
                 13 <= svg["w"] <= 17 and 13 <= svg["h"] <= 17,
                 "svg=%sx%s" % (svg["w"], svg["h"]))
    else:
        ck.check("找到齿轮 svg", False)

    ck = Checker("B. 位置调整后功能未坏")

    # 点击打开抽屉
    opened = c.js("""(() => {
      const b = document.getElementById('tbOpenThresholds');
      if (!b) return 'no-btn';
      b.click();
      return 'clicked';
    })()""")
    c.pump(1.5)
    dr = c.js("""(() => {
      const ov = document.getElementById('monThresholdOverlay');
      const rows = document.querySelectorAll('#monThresholdBody .thr-row');
      const secs = document.querySelectorAll('#monThresholdBody .thr-sec');
      return {
        hidden: ov ? ov.hasAttribute('hidden') : null,
        on: ov ? ov.classList.contains('on') : null,
        rows: rows.length, secs: secs.length,
        saveBtn: !!document.querySelector('[data-thr-save]'),
        resetBtn: !!document.querySelector('[data-thr-reset]')
      };
    })()""")
    ck.check("点击齿轮按钮能打开抽屉", opened == "clicked")
    ck.check("抽屉已展开（非 hidden）", dr.get("hidden") is False, str(dr))
    ck.check("抽屉含 13 个门限行", dr.get("rows") == 13, "rows=%s" % dr.get("rows"))
    ck.check("抽屉含 5 个分组", dr.get("secs") == 5, "secs=%s" % dr.get("secs"))
    ck.check("底部有「保存阈值」按钮", dr.get("saveBtn") is True)
    ck.check("底部有「恢复默认」按钮", dr.get("resetBtn") is True)

    # 关闭抽屉
    c.js("""(() => {
      const ov = document.getElementById('monThresholdOverlay');
      if (ov && ov.classList.contains('on')) {
        const c = ov.querySelector('.mon-drawer-close') || ov.querySelector('[data-thr-close]');
        if (c) c.click();
        else ov.click();
      }
      return true;
    })()""")
    c.pump(1)
    closed = c.js("""(() => {
      const ov = document.getElementById('monThresholdOverlay');
      return ov ? (ov.hasAttribute('hidden') || !ov.classList.contains('on')) : false;
    })()""")
    ck.check("抽屉可正常关闭", closed is True)

    ck = Checker("C. 切到别的标签页时按钮不可见")

    c.js("""(() => {
      const t = document.querySelector('.tab[data-tab="personalize"]');
      if (t) t.click();
      return true;
    })()""")
    c.pump(2)
    vis = c.js("""(() => {
      const b = document.getElementById('tbOpenThresholds');
      if (!b) return {missing: true};
      const r = b.getBoundingClientRect();
      const st = getComputedStyle(b);
      // 祖先 display:none ⇒ 几何为 0
      return {w: Math.round(r.width), h: Math.round(r.height),
              display: st.display, visibility: st.visibility};
    })()""")
    ck.check("切到「个性化设置」后按钮不可见（尺寸为 0）",
             vis.get("w") == 0 and vis.get("h") == 0, str(vis))

    # 切回监控
    c.js("""(() => {
      const t = document.querySelector('.tab[data-tab="monitor"]');
      if (t) t.click();
      return true;
    })()""")
    c.pump(2)
    back = c.js("""(() => {
      const b = document.getElementById('tbOpenThresholds');
      if (!b) return null;
      const r = b.getBoundingClientRect();
      return {w: Math.round(r.width), h: Math.round(r.height)};
    })()""")
    ck.check("切回「系统监控」后按钮恢复可见",
             back is not None and back["w"] > 0, str(back))

    ck = Checker("D. 无 JS 异常")

    for ev in c.events:
        if ev.get("method") == "Runtime.exceptionThrown":
            d = ev.get("params", {}).get("exceptionDetails", {})
            errors.append(d.get("text", "") + " " +
                          (d.get("exception", {}) or {}).get("description", ""))
        elif ev.get("method") == "Log.entryAdded":
            e = ev.get("params", {}).get("entry", {})
            if e.get("level") == "error":
                errors.append(e.get("text", ""))

    ck.check("无 JS 异常 / console error", len(errors) == 0,
             "; ".join(errors[:3]))

    return ck.summary()


if __name__ == "__main__":
    sys.exit(main())
