#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CDP 实机探针：量「每核使用率」点阵瓦片的实际尺寸与卡片留白

用途：为「点阵瓦片高度调高占满卡片」这个需求提供**实测基线**，
改 CSS 前后各跑一次，对比数字。

前置：Chrome 已带 --remote-debugging-port=9222 启动，
      且 NAS 上已部署对应版本（http://192.168.5.8:9910/）。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from probe_base import Checker, connect, reload_and_open  # noqa: E402

# 量三卡容器、CPU 卡、点阵网格、单个瓦片、瓦片内 svg、脚注行
_MEASURE_JS = """(function(){
  function box(sel){
    var e = document.querySelector(sel);
    if (!e) return null;
    var r = e.getBoundingClientRect();
    return {x:+r.x.toFixed(1), y:+r.y.toFixed(1),
            w:+r.width.toFixed(1), h:+r.height.toFixed(1)};
  }
  var card = document.querySelector('.mon-cmp-card[data-cmp-open="cpu"]');
  var grid = document.querySelector('[data-cores]');
  var tile = document.querySelector('.mon-hw-core');
  var svg  = tile ? tile.querySelector('svg') : null;
  var foot = tile ? tile.querySelector('.mon-hw-core-f') : null;
  var out = {
    card: box('.mon-cmp-card[data-cmp-open="cpu"]'),
    grid: box('[data-cores]'),
    tile: box('.mon-hw-core'),
    svg: svg ? box('.mon-hw-core svg') : null,
    foot: foot ? box('.mon-hw-core-f') : null,
    tileCount: document.querySelectorAll('.mon-hw-core').length,
    cols: grid ? grid.style.getPropertyValue('--cols') : null,
    cardText: card ? (card.innerText||'').replace(/\\s+/g,' ').trim() : null
  };
  // 卡片底部留白 = 卡片底 - 网格底
  if (out.card && out.grid) {
    out.gridBottomGap = +(out.card.y + out.card.h - (out.grid.y + out.grid.h)).toFixed(1);
  }
  return JSON.stringify(out);
})()"""


def main():
    ck = Checker("每核点阵瓦片尺寸实测")

    print("=== 连接实机 ===")
    c = connect()
    reload_and_open(c)

    m = json.loads(c.js(_MEASURE_JS) or "{}")
    print("\n实测:", json.dumps(m, ensure_ascii=False, indent=2))

    ck.section("基线数据")
    ck.check("找到 CPU 卡片", m.get("card") is not None)
    ck.check("找到点阵网格", m.get("grid") is not None)
    ck.check("找到单个瓦片", m.get("tile") is not None)
    ck.check("找到瓦片内 svg", m.get("svg") is not None)

    if m.get("card") and m.get("grid"):
        ck.check("网格在卡片内", m["grid"]["h"] <= m["card"]["h"],
                 "网格 h=%.1f 卡片 h=%.1f" % (m["grid"]["h"], m["card"]["h"]))
        print("  卡片高度      : %.1f px" % m["card"]["h"])
        print("  网格高度      : %.1f px" % m["grid"]["h"])
        print("  网格底→卡片底 : %.1f px（下方留白）" % m.get("gridBottomGap", 0))
    if m.get("tile") and m.get("svg"):
        print("  瓦片高度      : %.1f px" % m["tile"]["h"])
        print("  点阵 svg 高度 : %.1f px" % m["svg"]["h"])
        ck.check("瓦片内 svg 未撑满瓦片（存在上下留白）",
                 m["svg"]["h"] < m["tile"]["h"] - 20,
                 "svg=%.1f tile=%.1f" % (m["svg"]["h"], m["tile"]["h"]))

    ck.finish()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
