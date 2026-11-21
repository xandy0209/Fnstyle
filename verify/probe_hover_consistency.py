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


def _hover(c, sel):
    """强制触发 :hover 并读回计算样式。"""
    doc = c.send("DOM.getDocument", {"depth": -1})["root"]["nodeId"]
    nid = c.send("DOM.querySelector", {"nodeId": doc, "selector": sel})["nodeId"]
    if not nid:
        return None
    c.send("CSS.forcePseudoState",
            {"nodeId": nid, "forcedPseudoClasses": ["hover"]})
    c.pump(0.7)
    v = c.js("""(function(){
      var e = document.querySelector(%s);
      if (!e) return null;
      var cs = getComputedStyle(e);
      return {transform: cs.transform, shadow: cs.boxShadow,
              cursor: cs.cursor, border: cs.borderColor};
    })()""" % json.dumps(sel))
    try:
        doc2 = c.send("DOM.getDocument", {"depth": -1})["root"]["nodeId"]
        nid2 = c.send("DOM.querySelector", {"nodeId": doc2, "selector": sel})["nodeId"]
        if nid2:
            c.send("CSS.forcePseudoState",
                   {"nodeId": nid2, "forcedPseudoClasses": []})
    except Exception:
        pass
    c.pump(0.3)
    return v


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
    c.js("var b=document.querySelector('[data-tab=monitor]');if(b)b.click();'ok'")
    c.pump(5)
    c.send("DOM.enable")
    c.send("CSS.enable")

    results = {}
    for sel, name, should_hover in TARGETS:
        v = _hover(c, sel)
        results[name] = v
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
