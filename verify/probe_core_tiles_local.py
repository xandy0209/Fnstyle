#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CDP 实机探针（本地 preview）：量「每核使用率」点阵瓦片尺寸与卡片留白

用途：为「点阵瓦片高度调高占满卡片」这个需求提供**实测基线**，
改 CSS 前后各跑一次，对比数字。

两种模式：
  --url http://127.0.0.1:8768/   本地 preview（默认，可用 MON_FULL 出 mock 数据）
  --url http://192.168.5.8:9910/ NAS 实机

前置：Chrome 已带 --remote-debugging-port=9222 启动。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import probe_base  # noqa: E402
from probe_base import Checker  # noqa: E402

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
  // 点阵是否被拉伸成椭圆：
  // ⚠️ 要量的是 **内容**（viewBox 内 25 个圆）的渲染尺寸，不是 svg 元素盒子。
  //    svg 元素盒子可以是任意比例，preserveAspectRatio="xMidYMid meet"
  //    会保证内容是正方形。量错对象会误报「被拉成椭圆」。
  if (out.svg) {
    out.svgAspect = +(out.svg.w / out.svg.h).toFixed(3);
  }
  var s = document.querySelector('.mon-hw-core svg');
  if (s) {
    try {
      var bb = s.getBBox();
      var m = s.getScreenCTM();
      var xs = [], ys = [];
      [[bb.x,bb.y],[bb.x+bb.width,bb.y],
       [bb.x,bb.y+bb.height],[bb.x+bb.width,bb.y+bb.height]].forEach(function(p){
        var q = s.createSVGPoint(); q.x = p[0]; q.y = p[1];
        var r2 = q.matrixTransform(m);
        xs.push(r2.x); ys.push(r2.y);
      });
      var cw = Math.max.apply(null,xs) - Math.min.apply(null,xs);
      var ch = Math.max.apply(null,ys) - Math.min.apply(null,ys);
      out.contentW = +cw.toFixed(1);
      out.contentH = +ch.toFixed(1);
      out.contentAspect = +(cw/ch).toFixed(3);
    } catch (e) { out.contentErr = String(e); }
  }
  /* ⚠️ v1.1.64：单个圆点的渲染尺寸 → 判断「圆点是否还是正圆」。
     内容整体比例是 3:14（竖长是对的），但**每个圆本身**必须仍是正圆，
     否则点阵会被压成椭圆。用 getBBox 量圆的外接矩形。
     ⚠️ createSVGPoint 是 **SVGSVGElement** 上的方法，circle 元素没有，
        必须借道 ownerSVGElement。 */
  var s0 = document.querySelector('.mon-hw-core svg');
  var c0 = s0 ? s0.querySelector('circle') : null;
  if (c0 && s0) {
    try {
      var cb = c0.getBBox();
      var cm = c0.getScreenCTM();
      var xs2 = [], ys2 = [];
      [[cb.x,cb.y],[cb.x+cb.width,cb.y],
       [cb.x,cb.y+cb.height],[cb.x+cb.width,cb.y+cb.height]].forEach(function(p){
        var q = s0.createSVGPoint(); q.x = p[0]; q.y = p[1];
        var r3 = q.matrixTransform(cm);
        xs2.push(r3.x); ys2.push(r3.y);
      });
      var dw = Math.max.apply(null,xs2) - Math.min.apply(null,xs2);
      var dh = Math.max.apply(null,ys2) - Math.min.apply(null,ys2);
      out.dotW = +dw.toFixed(1);
      out.dotH = +dh.toFixed(1);
      out.dotAspect = +(dw/dh).toFixed(3);
    } catch (e) { out.dotErr = String(e); }
  }
  /* ⚠️ v1.1.64：脚注行数字是否在瓦片内。
     历史 bug：脚注行同时有 position:absolute + left/right + width:100%，
     CSS 里 width 优先于 right → 右边缘超出瓦片 9.6px，数字跑到卡片外。 */
  out.footInTile = [];
  var tiles = document.querySelectorAll('.mon-hw-core');
  for (var ti = 0; ti < tiles.length; ti++) {
    var tb = tiles[ti].getBoundingClientRect();
    var num = tiles[ti].querySelector('.mon-hw-core-f b');
    if (!num) { out.footInTile.push(false); continue; }
    var nb = num.getBoundingClientRect();
    out.footInTile.push(nb.right <= tb.right + 0.5 && nb.x >= tb.x - 0.5);
  }
  return JSON.stringify(out);
})()"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8768/")
    ap.add_argument("--host", default="192.168.5.8",
                    help="用于在 /json/list 里挑 tab 的匹配串")
    args = ap.parse_args()

    probe_base.URL = args.url
    ck = Checker("每核点阵瓦片尺寸实测")

    print("=== 连接（%s）===" % args.url)
    c = probe_base.connect(host=args.host)
    probe_base.reload_and_open(c, settle=6.0)

    m = json.loads(c.js(_MEASURE_JS) or "{}")
    print("\n实测:", json.dumps(m, ensure_ascii=False, indent=2))

    ck.section("基线数据")
    ck.check("找到 CPU 卡片", m.get("card") is not None)
    ck.check("找到点阵网格", m.get("grid") is not None)
    ck.check("找到单个瓦片", m.get("tile") is not None)
    ck.check("找到瓦片内 svg", m.get("svg") is not None)

    if m.get("card") and m.get("grid"):
        print("  卡片高度      : %.1f px" % m["card"]["h"])
        print("  网格高度      : %.1f px" % m["grid"]["h"])
        print("  网格底→卡片底 : %.1f px（下方留白）" % m.get("gridBottomGap", 0))
        ck.check("点阵网格已撑满卡片（底部留白 < 20px）",
                 m.get("gridBottomGap", 999) < 20,
                 "留白 %.1f px" % m.get("gridBottomGap", 0))
    if m.get("tile") and m.get("svg"):
        print("  瓦片高度      : %.1f px" % m["tile"]["h"])
        print("  点阵 svg 盒子 : %.1f × %.1f px" % (m["svg"]["w"], m["svg"]["h"]))
        print("  点阵内容尺寸  : %.1f × %.1f px"
              % (m.get("contentW", 0), m.get("contentH", 0)))
        # ⚠️ v1.1.64：点阵改用**抽屉同款的 SVG（3 列 × 14 行）**，
        #    内容区是 3:14 的竖长比例（≈0.214），不是正方形。
        #    所以「撑满」的判据要改成：
        #      · 内容高度 ≈ 瓦片高 − 脚注行预留(1.6rem≈25.6px) − 上下内边距
        #      · 内容比例 ≈ 3/14（不是 1）
        #    早期版本用 5×5 正方点阵，内容比例恒为 1.0。
        foot_h = m.get("foot", {}).get("h", 0) if m.get("foot") else 0
        pad = 0.65 * 16 + 1.6 * 16          # top .65rem + padding-bottom 1.6rem
        slack = m["tile"]["h"] - m.get("contentH", 0) - foot_h - pad
        print("  瓦片内余量    : %.1f px（扣掉脚注行与内边距后）" % slack)
        ck.check("点阵内容已撑满瓦片（扣除脚注/内边距后余量 < 8px）",
                 abs(slack) < 8,
                 "余量 %.1f px（脚注 %.1f px / 内边距 %.1f px）"
                 % (slack, foot_h, pad))
    if m.get("contentAspect"):
        print("  点阵内容宽高比: %s（3:14 ≈ 0.214 = 抽屉同款竖长比例）"
              % m.get("contentAspect"))
        ck.check("点阵内容比例 ≈ 3:14（与抽屉版同款，非正方）",
                 abs(m.get("contentAspect") - 3.0 / 14.0) < 0.04,
                 "宽高比 %s" % m.get("contentAspect"))
    if m.get("dotAspect"):
        print("  单个圆点宽高比: %s（1.000 = 正圆）" % m.get("dotAspect"))
        ck.check("单个圆点仍是正圆（宽高比 ≈ 1）",
                 abs(m.get("dotAspect") - 1.0) < 0.12,
                 "宽高比 %s" % m.get("dotAspect"))
    if m.get("footInTile"):
        bad_tiles = [i for i, ok in enumerate(m["footInTile"]) if not ok]
        ck.check("每核卡片右下角数字都在瓦片内（不溢出）",
                 not bad_tiles,
                 "越界瓦片: %s" % bad_tiles if bad_tiles
                 else "%d 个瓦片全部在内" % len(m["footInTile"]))

    # ---- ⚠️ v1.1.64：能耗两栏等高 + 四格等高 ----
    ck.section("能耗两栏 / 四格等高")
    r = json.loads(c.js("""(function(){
      function bb(e){ if(!e) return null; var r=e.getBoundingClientRect();
        return {h:+r.height.toFixed(1), bottom:+r.bottom.toFixed(1)}; }
      var left=document.querySelector('#monPowerOverview');
      var right=document.querySelector('#monPowerStats');
      var cells=document.querySelectorAll('.stat-cell');
      var hs=Array.prototype.map.call(cells,function(x){
        return +x.getBoundingClientRect().height.toFixed(1);});
      var head=document.querySelector('#monPowerRate');
      return JSON.stringify({
        left: bb(left), right: bb(right),
        cellHeights: hs,
        headRateHtml: head ? head.innerHTML : null,
        headNoteText: (head && head.querySelector('.rate-note'))
          ? head.querySelector('.rate-note').textContent : null,
        headInfoTitle: (head && head.querySelector('.rate-info'))
          ? head.querySelector('.rate-info').getAttribute('title') : null,
        pillW: head ? +head.getBoundingClientRect().width.toFixed(1) : 0,
        headerW: (head && head.closest('header'))
          ? +head.closest('header').getBoundingClientRect().width.toFixed(1) : 0,
        statHasRate: /sub-rate|sub-proj-rate/.test(
          right ? right.innerHTML : ''),
        statHasLbl: /rate-lbl/.test(right ? right.innerHTML : '')
      });
    })()""") or "{}")
    if r.get("left") and r.get("right"):
        print("  左栏高度      : %.1f px" % r["left"]["h"])
        print("  右栏高度      : %.1f px" % r["right"]["h"])
        ck.check("能耗左右两栏等高（底边齐平）",
                 abs(r["left"]["bottom"] - r["right"]["bottom"]) < 1,
                 "左 %.1f / 右 %.1f" % (r["left"]["h"], r["right"]["h"]))
    if r.get("cellHeights"):
        print("  四格高度      : %s" % r["cellHeights"])
        ck.check("右栏四格等高",
                 len(set(r["cellHeights"])) == 1,
                 "各格 %s" % r["cellHeights"])
    print("  标题栏单价    : %s" % r.get("headRateHtml"))
    ck.check("标题栏显示电费单价胶囊",
             bool(r.get("headRateHtml")) and "rate-lbl" in r["headRateHtml"],
             r.get("headRateHtml") or "")
    ck.check("右栏格内不再有重复的单价胶囊",
             r.get("statHasRate") is False and r.get("statHasLbl") is False)
    # ---- ⚠️ v1.1.64：标签内的电价说明（短句 + ⓘ 完整版） ----
    if r.get("headNoteText"):
        print("  标签内短句    : %s" % r["headNoteText"])
        ck.check("电价标签内显示说明短句",
                 len(r["headNoteText"]) <= 30,
                 "%d 字: %s" % (len(r["headNoteText"]), r["headNoteText"]))
    ck.check("电价标签带 ⓘ 图标（完整说明入口）",
             bool(r.get("headInfoTitle")),
             (r.get("headInfoTitle") or "")[:40])
    ck.check("ⓘ 悬停提示是**电价**的说明（不是电费计算规则）",
             bool(r.get("headInfoTitle")) and
             "默认" in r["headInfoTitle"] and
             "用电量 ×" not in r["headInfoTitle"],
             (r.get("headInfoTitle") or "")[:40])
    ck.check("标题栏胶囊宽度适中（不占满标题栏）",
             r.get("pillW", 9999) < r.get("headerW", 0) * 0.5,
             "胶囊 %.0fpx / 标题栏 %.0fpx" % (r.get("pillW", 0), r.get("headerW", 0)))

    ck.finish()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
