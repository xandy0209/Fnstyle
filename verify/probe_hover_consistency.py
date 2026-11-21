#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CDP 探针：检查**可点击卡片**的悬停效果是否一致（v1.1.64）

背景
----
老板反馈：「鼠标指向可以点击的卡片上的效果不一致，需调整为一致（保留效果大一点的）」。

实测发现同屏有两套悬停效果：
  · .ov-tap（实时功耗 / 温度总览）→ 上浮 1px + 22px 投影
  · .mon-cmp-card（处理器 / 内存）   → 只改边框色 + 背景色
另外存储板块的盘位格 .mon-bay[data-clickable] 也是第三套（只变色 + 描边）。

修法：以「效果大的那套」（上浮 + 投影）为准，全部统一。

本探针用 CDP 的 CSS.forcePseudoState 强制触发 :hover，
然后读 getComputedStyle 的 transform / box-shadow / cursor 做逐项比对。

用法：
    python3 verify/probe_hover_consistency.py --url http://127.0.0.1:8769/ --host 127.0.0.1
    python3 verify/probe_hover_consistency.py --url http://192.168.5.8:9910/ --host 192.168.5.8
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import probe_base  # noqa: E402
from probe_base import Checker  # noqa: E402

# (选择器, 名称, 是否应当有悬停效果)
TARGETS = [
    ('.ov-tap', '实时功耗总览', True),
    ('.mon-cmp-card[data-cmp-open="cpu"]', '处理器卡', True),
    ('.mon-cmp-card[data-cmp-open="mem"]', '内存卡', True),
    ('.mon-bay[data-clickable="1"]', '盘位格', True),
    ('.mon-cmp-card-static', '显卡卡（静态不可点）', False),
]

# 基准（.ov-tap 那套）：上浮 1px + 22px 投影
REF_LIFT = -1.0
REF_SHADOW = "0px 6px 22px"


def _hover(c, sel, doc=None):
    """用**真实鼠标事件**触发 :hover 并读回计算样式。

    ⚠️ v1.1.65：改用 Input.dispatchMouseEvent（真实鼠标）替代 CSS.forcePseudoState。
       实测两个坑：
       ① CDP 的 CSS.forcePseudoState 对 nodeId 极敏感 ——
          一旦中间重取 DOM.getDocument，后续 nodeId 全部失效，
          forcePseudoState 静默打空 → 读到 transform=none / shadow 透明，
          表现为「第一个元素对、后面全错」的假红。
       ② **更关键**：页面里的 hover 效果带 CSS transition，
          在 CDP 无头/后台环境下 transition 不推进，
          getComputedStyle 永远读到**动画第一帧**
          （transform=matrix(1,0,0,1,0,0)、box-shadow=rgba(0,0,0,0) 0px 0px 0px），
          表现为「悬停效果全丢」的假红 —— 但 CSS 规则其实完好。
       → 解法：真实鼠标移到元素中心 + 注入禁用 transition 的样式。
          实测这样读到的就是终值：matrix(1,0,0,1,0,-1) + 0px 6px 22px。
    """
    pos = c.js("""(function(){
      var e = document.querySelector(%s);
      if (!e) return null;
      e.scrollIntoView({block:'center'});
      var b = e.getBoundingClientRect();
      return {x: b.x + b.width/2, y: b.y + b.height/2};
    })()""" % json.dumps(sel))
    if not pos:
        return None
    try:
        c.send("Input.dispatchMouseEvent",
               {"type": "mouseMoved", "x": pos["x"], "y": pos["y"],
                "button": "none", "clickCount": 0})
    except Exception as e:
        print("    （dispatchMouseEvent 失败：%s）" % e)
        return None
    c.pump(0.8)
    v = c.js("""(function(){
      var e = document.querySelector(%s);
      if (!e) return null;
      var cs = getComputedStyle(e);
      return {transform: cs.transform, shadow: cs.boxShadow,
              cursor: cs.cursor, border: cs.borderColor,
              hovered: e.matches(':hover')};
    })()""" % json.dumps(sel))
    return v


def _unhover(c, sel):
    """把鼠标移开，清除 hover 态。"""
    try:
        c.send("Input.dispatchMouseEvent",
               {"type": "mouseMoved", "x": 2, "y": 2,
                "button": "none", "clickCount": 0})
        c.pump(0.3)
    except Exception:
        pass


def _lift(transform):
    """从 matrix(a,b,c,d,tx,ty) 里取出纵向位移（ty）。"""
    if not transform or transform == "none":
        return 0.0
    try:
        parts = transform.replace("matrix(", "").replace(")", "").split(",")
        return float(parts[5])
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8769/")
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    probe_base.URL = args.url
    ck = Checker("可点击卡片悬停效果一致性")

    c = probe_base.connect(host=args.host)
    c.send("Page.navigate", {"url": args.url})
    c.pump(7)
    try:
        c.send("Page.reload", {"ignoreCache": True})
        c.pump(5)
    except Exception:
        pass
    # ⚠️ 必须用 IIFE 包裹：裸 `var b=...` 会在页面全局作用域重复声明，
    #    同页跑多个探针时报 "Identifier 'b' has already been declared"，
    #    而且异常会中断后面的 DOM.enable / CSS.enable → forcePseudoState 失效。
    c.js("(function(){var b=document.querySelector('[data-tab=monitor]');"
         "if(b)b.click();return 'ok';})()")
    c.pump(5)
    # ⚠️ v1.1.65：必须把标签页提到**前台**。
    #    Chrome 对后台标签页的 CSS transition / animation 会节流，
    #    forcePseudoState 虽然生效，但 getComputedStyle 读到的是
    #    **动画第一帧**（transform = matrix(1,0,0,1,0,0) = 位移 0，
    #    box-shadow = rgba(0,0,0,0) 0px 0px 0px），
    #    表现为「悬停效果全丢」的假红 —— 但代码里的 :hover 规则其实完好。
    #    实测：bringToFront 后 transform = matrix(1,0,0,1,0,-1)、
    #    box-shadow = rgba(0,0,0,.18) 0px 6px 22px → 断言全过。
    try:
        c.send("Page.bringToFront")
        c.pump(0.6)
    except Exception:
        pass
    c.send("DOM.enable")
    c.send("CSS.enable")

    results = {}
    # ⚠️ v1.1.65：注入禁用 transition / animation 的样式。
    #    hover 效果带 CSS transition，而 CDP 环境下动画不推进，
    #    getComputedStyle 只能读到动画第一帧 → 全红假象。
    #    禁用 transition 后读到的就是终值。
    c.js("(function(){"
         "var st=document.createElement('style');st.id='__probe_notrans';"
         "st.textContent='*,*::before,*::after{transition:none !important;"
         "animation:none !important}';"
         "document.head.appendChild(st);return 'ok';})()")
    c.pump(0.5)
    for sel, name, should_hover in TARGETS:
        v = _hover(c, sel)
        results[name] = v
        _unhover(c, sel)
        print("\n【%s】%s" % (name, sel))
        if v is None:
            print("    （页面上没找到该元素，跳过）")
            continue
        lift = _lift(v.get("transform"))
        print("    transform : %s（纵向位移 %s px）" % (v.get("transform"), lift))
        print("    box-shadow: %s" % v.get("shadow"))
        print("    cursor    : %s" % v.get("cursor"))
        if should_hover:
            ck.check("%s 悬停上浮 1px" % name,
                     lift is not None and abs(lift - REF_LIFT) < 0.2,
                     "位移 %s px" % lift)
            ck.check("%s 悬停有 22px 投影" % name,
                     REF_SHADOW in (v.get("shadow") or ""),
                     (v.get("shadow") or "")[:60])
            ck.check("%s 悬停光标是 pointer" % name,
                     v.get("cursor") == "pointer", v.get("cursor"))
        else:
            ck.check("%s 不可点 → 无上浮无投影" % name,
                     (lift in (None, 0.0)) and (v.get("shadow") in ("none", None)),
                     "位移 %s / shadow %s" % (lift, (v.get("shadow") or "")[:30]))
            ck.check("%s 不可点 → 光标是 default" % name,
                     v.get("cursor") == "default", v.get("cursor"))

    print("\n" + "=" * 60)
    print("实测汇总:")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    ck.finish()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
