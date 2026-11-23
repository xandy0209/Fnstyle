#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""网络卡：左侧文本被趋势图覆盖的实测探针（v1.1.88）

背景
----
v1.1.76 给 `.ov-net-vals` 加了 `position:relative; top:10px`，
但真正的病根是**全局 `.row`（表单行）的 padding/border 泄漏**进卡片：
`.ov-net-vals .row` 只覆盖了 display/align-items/gap/align-self，
漏掉 `padding:.7rem 0` 与 `border-bottom:1px dashed` ⇒
两行实高 46/39px（内容只有 23/17px），
`.ov-net-vals` 合计 89px 溢出锁死的 `.ov-m-main`（48px），
第二行（↑上行速率）压在副标题（今日流量）上，副标题又贴着趋势图
⇒ 「↑上行速率」那一行被趋势线直接穿过。

本探针只做**量测 + 断言**，不修改任何文件。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from probe_base import CDP, Checker, connect  # noqa: E402

URL = "http://192.168.5.8:9910/"


def main():
    cdp = connect(host="127.0.0.1")
    cdp.send("Page.enable")
    cdp.send("Runtime.enable")
    cdp.send("Page.navigate", {"url": URL})
    cdp.pump(3)

    # 概览网格在「系统监控」标签页里 —— 必须先切过去
    cdp.js("""(() => {
      const t = document.querySelector('.tab[data-tab="monitor"]');
      if (t) t.click();
      return !!t;
    })()""")
    cdp.pump(2)

    for _ in range(60):
        if cdp.js("!!document.querySelector('[data-ov=\"net\"]')"):
            break
        cdp.pump(1)
    cdp.pump(3)

    d = cdp.js("""(() => {
      const card = document.querySelector('[data-ov="net"]');
      if (!card) return {missing:true};
      const q = (s) => card.querySelector(s);
      const r = (el) => {
        if (!el) return null;
        const b = el.getBoundingClientRect();
        return {top: Math.round(b.top), bottom: Math.round(b.bottom),
                left: Math.round(b.left), right: Math.round(b.right),
                h: Math.round(b.height)};
      };
      const vals = q('.ov-net-vals');
      const rows = vals ? Array.from(vals.querySelectorAll('.row')) : [];
      const label = q('.ov-m-label');
      // 标题文字的**墨迹**范围（不是行盒）—— 行盒含上下留白，
      // 用它判断会不会撞字会失真
      let ink = null;
      if (label) {
        const rng = document.createRange();
        rng.selectNodeContents(label);
        const b = rng.getBoundingClientRect();
        ink = {top: Math.round(b.top), bottom: Math.round(b.bottom)};
      }
      // 六张卡的高度（等高检查）
      const allCards = Array.from(document.querySelectorAll('.ov-metric'))
          .map(c => {
            const b = c.getBoundingClientRect();
            return {k: c.getAttribute('data-ov'), h: Math.round(b.height),
                    top: Math.round(b.top)};
          });
      const rowCS = rows[0] ? getComputedStyle(rows[0]) : null;
      return {
        card: r(card),
        top: r(q('.ov-m-top')),
        main: r(q('.ov-m-main')),
        vals: r(vals),
        row1: r(rows[0]),
        row2: r(rows[1]),
        sub: r(q('.ov-m-sub')),
        subText: q('.ov-m-sub') ? (q('.ov-m-sub').textContent || '').trim() : None,
        foot: r(q('.ov-m-foot')),
        spark: r(q('.ov-spark')),
        labelInk: ink,
        allCards: allCards,
        rowPad: rowCS ? rowCS.padding : None,
        rowBorder: rowCS ? rowCS.borderBottomWidth : None,
      };
    })()""")

    d = d or {}
    if not isinstance(d, dict) or d.get("missing"):
        print("  ✗ 网络卡未找到")
        return 1

    print("=== 网络卡几何实测 ===")
    for k in ("card", "top", "main", "vals", "row1", "row2", "sub", "foot", "spark"):
        print("  %-9s %s" % (k, json.dumps(d.get(k), ensure_ascii=False)))
    print("  labelInk  ", json.dumps(d.get("labelInk"), ensure_ascii=False))
    print("  rowPad    ", d.get("rowPad"), " rowBorder", d.get("rowBorder"))
    print("  subText   ", repr(d.get("subText")))
    print("  六卡高度  ", json.dumps(d.get("allCards"), ensure_ascii=False))

    chk = Checker()

    def box(k):
        return d.get(k) or {}

    # ① 根因：全局表单行样式不得泄漏进卡片
    chk.check("全局表单行 padding 已清除（.row 不再带 .7rem 上下留白）",
              (d.get("rowPad") or "").startswith("0px"),
              "rowPad=%s" % d.get("rowPad"))
    chk.check("全局表单行 border-bottom 已清除（不再有那条多余虚线）",
              (d.get("rowBorder") or "") in ("0px", ""),
              "rowBorder=%s" % d.get("rowBorder"))

    # ② 速率两行贴合内容（不能再被 padding 撑高）
    r1, r2 = box("row1"), box("row2")
    chk.check("下行速率行高贴合内容（≤28px）", r1.get("h", 999) <= 28, "h=%s" % r1.get("h"))
    chk.check("上行速率行高贴合内容（≤22px）", r2.get("h", 999) <= 22, "h=%s" % r2.get("h"))

    # ③ 速率区整体装得进 .ov-m-main（不溢出）
    vals, main = box("vals"), box("main")
    if vals and main:
        chk.check("速率区不溢出数字区（vals 底 ≤ main 底）",
                  vals["bottom"] <= main["bottom"] + 1,
                  "vals.bottom=%s main.bottom=%s" % (vals["bottom"], main["bottom"]))

    # ④ 第二行（上行速率）不压副标题
    sub = box("sub")
    if r2 and sub:
        chk.check("上行速率不压副标题（row2 底 ≤ 副标题顶）",
                  r2["bottom"] <= sub["top"],
                  "row2.bottom=%s sub.top=%s 差 %s px" % (
                      r2["bottom"], sub["top"], r2["bottom"] - sub["top"]))

    # ⑤ 副标题不被趋势图压住
    foot, spark = box("foot"), box("spark")
    if sub and foot:
        chk.check("副标题不被趋势图压住（副标题底 ≤ 趋势区顶）",
                  sub["bottom"] <= foot["top"],
                  "sub.bottom=%s foot.top=%s 差 %s px" % (
                      sub["bottom"], foot["top"], sub["bottom"] - foot["top"]))
    if sub and spark:
        chk.check("副标题在趋势图之上（副标题底 ≤ 趋势图底）",
                  sub["bottom"] <= spark["bottom"],
                  "sub.bottom=%s spark.bottom=%s" % (sub["bottom"], spark["bottom"]))

    # ⑥ 速率区不撞标题文字
    ink = d.get("labelInk") or {}
    if vals and ink:
        chk.check("速率区不撞标题文字（标题墨迹底 ≤ 速率区顶）",
                  ink["bottom"] <= vals["top"],
                  "ink.bottom=%s vals.top=%s 差 %s px" % (
                      ink["bottom"], vals["top"], ink["bottom"] - vals["top"]))

    # ⑦ 文字确实上移了（老板要求 ≥15px）
    #
    # ⚠️ 口径必须对齐「老板截图里的原位置」，而不是「无偏移的理论基线」：
    #    v1.1.87（修复前）`.ov-net-vals` 是 `top: 10px`（**下移** 10px），
    #    且 `.row` 带着全局表单行的 padding/border ⇒ 老板看到的就是那个位置。
    #
    #    ⇒ 最稳的做法：在页面里做 **A/B 实测** ——
    #       ① 临时注入「旧样式」量一次（旧 top:10px + 旧 padding/border）
    #       ② 撤掉注入量一次（新样式）
    #       ③ 两者相减 = 真实上移量
    #      这样不依赖任何「手算的理论基线」，也不会因为口径写错而假红。
    #
    #    参照系用「相对卡片顶的偏移」而不是绝对 top ——
    #    因为注入旧样式会让 net 卡变高，网格行高变化会带动整个卡片位置平移。
    ab = cdp.js("""(() => {
      const card = document.querySelector('[data-ov="net"]');
      if (!card) return {missing:true};
      const vals = card.querySelector('.ov-net-vals');
      const rows = vals ? Array.from(vals.querySelectorAll('.row')) : [];
      const sub = card.querySelector('.ov-m-sub');
      const foot = card.querySelector('.ov-m-foot');
      const rel = (el) => {
        if (!el) return null;
        const cb = card.getBoundingClientRect();
        const b = el.getBoundingClientRect();
        return {top: Math.round(b.top - cb.top), bottom: Math.round(b.bottom - cb.top),
                h: Math.round(b.height)};
      };
      const snap = () => ({
        vals: rel(vals),
        row1: rel(rows[0]),
        row2: rel(rows[1]),
        sub: rel(sub),
        foot: rel(foot),
      });
      // 注入「修复前」的样式：全局表单行 padding/border + top:10px
      const st = document.createElement('style');
      st.id = '__probe_old_net_style__';
      st.textContent =
        '.ov-net-vals { top: 10px !important; }' +
        '.ov-net-vals .row { padding: .7rem 0 !important; ' +
        'border-bottom: 1px dashed var(--border) !important; }';
      document.head.appendChild(st);
      // 强制回流后量旧版
      void vals.offsetHeight;
      const old = snap();
      st.remove();
      void vals.offsetHeight;
      const after = snap();
      return {old:old, after:after};
    })()""")

    ab = ab or {}
    if not ab.get("missing"):
        oldA, newA = ab.get("old") or {}, ab.get("after") or {}
        oldV, newV = oldA.get("vals") or {}, newA.get("vals") or {}
        oldR2, newR2 = oldA.get("row2") or {}, newA.get("row2") or {}
        oldSub, newSub = oldA.get("sub") or {}, newA.get("sub") or {}
        oldFoot, newFoot = oldA.get("foot") or {}, newA.get("foot") or {}
        if oldV and newV:
            moved = oldV["top"] - newV["top"]
            chk.check("左侧速率区相对修复前位置（老板截图处）上移 ≥15px", moved >= 15,
                      "上移 %d px（旧顶 +%d → 新顶 +%d）" % (moved, oldV["top"], newV["top"]))
        if oldR2 and newR2:
            moved2 = oldR2["top"] - newR2["top"]
            chk.check("被趋势线穿过的「上行速率」行上移 ≥15px", moved2 >= 15,
                      "上移 %d px（旧顶 +%d → 新顶 +%d）" % (moved2, oldR2["top"], newR2["top"]))
        # A/B 对照有效性：修复前速率区确实更低（bug 曾真实存在）
        if oldV and newV:
            chk.check("A/B 对照有效：修复前速率区确实比修复后更低",
                      oldV["top"] > newV["top"],
                      "旧顶 +%d vs 新顶 +%d" % (oldV["top"], newV["top"]))
        # A/B 对照有效性：修复前「上行速率」行确实压住副标题（老板截图的现象）
        if oldR2 and oldSub:
            chk.check("A/B 对照有效：修复前上行速率行确实压住副标题",
                      oldR2["bottom"] > oldSub["top"],
                      "旧上行行底 +%d vs 旧副标题顶 +%d（重叠 %d px）"
                      % (oldR2["bottom"], oldSub["top"], oldR2["bottom"] - oldSub["top"]))
        # 修复后：副标题与趋势图之间恢复净空
        if newSub and newFoot:
            chk.check("修复后副标题与趋势图之间有净空",
                      newSub["bottom"] <= newFoot["top"],
                      "副标题底 +%d vs 趋势顶 +%d（净空 %d px）"
                      % (newSub["bottom"], newFoot["top"], newFoot["top"] - newSub["bottom"]))

    # ⑧ 六卡等高（本次不能破坏 v1.1.74 定的等高约定）
    cards = d.get("allCards") or []
    if len(cards) >= 6:
        by_row = {}
        for c in cards:
            by_row.setdefault(c["top"], []).append(c)
        for top, group in by_row.items():
            hs = sorted({g["h"] for g in group})
            chk.check("同排卡片等高（第 %d 组，%s）" % (
                list(by_row).index(top) + 1,
                "/".join(g["k"] for g in group)),
                len(hs) == 1, "高度=%s" % hs)

    # ⑨ 趋势图底边在卡片内
    card = box("card")
    if foot and card:
        chk.check("趋势图底边在卡片内", foot["bottom"] <= card["bottom"] + 1,
                  "foot.bottom=%s card.bottom=%s" % (foot["bottom"], card["bottom"]))

    ok = chk.finish()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
