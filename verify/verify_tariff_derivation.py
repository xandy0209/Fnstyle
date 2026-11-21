#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.1.63：「推导明细已整体废弃」+「口径说明取代推导」的防回归验证

历史沿革
--------
v1.1.60：用户反馈电价面板上的档位表与输入框看着像重复 → 加了
      power_tariff.tier_derivation() 输出「本月生效档位推导明细」，
      前端用推导解释行取代重复说明文字。

v1.1.63：老板决定整个电价模型退化为**单一单价** ——
      「删除阶梯计费和档位信息等，也不区分区域，就是一个电费单价的配置，
        并增加对应的说明即可」
      ⇒ 没有档位，就没有「档位怎么推导出来的」这回事
      ⇒ tier_derivation() 整体废弃
      ⇒ 取而代之的是**一段口径说明**（describe().note），直接讲清楚
        「用电量 × 电费单价」这个算法和默认单价的来源

本脚本验证：
  ① tier_derivation / effective_tier_limits 等推导 API 全部不存在
  ② describe() 不再透出 tier_derivation
  ③ describe().note 承担起「说明」职责，含关键口径要素
  ④ 前端不再有推导行 UI（tariffDerivationHtml / tariff-tb-derive）
  ⑤ 前端改为渲染后端下发的 note
  ⑥ CSS 里推导行样式已清理
"""
import os
import sys
import json
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
UI = os.path.join(os.path.dirname(HERE), "app", "ui")
sys.path.insert(0, UI)

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  ✅ %s" % name)
    else:
        FAIL.append((name, detail))
        print("  ❌ %s  ← %s" % (name, detail))


def approx(a, b, eps=1e-6):
    try:
        return abs(float(a) - float(b)) <= eps
    except Exception:
        return False


def strip_comments(src):
    """去掉 JS 注释，只留真正会执行的代码。"""
    import re
    no_block = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    no_line = re.sub(r"(?m)^\s*//.*$", " ", no_block)
    no_line = re.sub(r"(?m)\s//[^\n\"'`]*$", " ", no_line)
    return no_line


print("=" * 70)
print("v1.1.63 验证：推导明细已废弃，口径说明取代之")
print("=" * 70)

import power_tariff as pt  # noqa: E402

# ── ① 推导 API 全部不存在 ─────────────────────────────────────
print("\n[1] 后端推导 API 已删除")
for fn in ("tier_derivation", "effective_tier_limits", "_effective_caps",
           "carry_in_for", "load_ledger", "save_ledger", "update_tier_ledger",
           "_month_key", "_prev_month_key"):
    check("后端已无 %s" % fn, not hasattr(pt, fn))

# ── ② describe() 不再透出推导明细 ───────────────────────────────
print("\n[2] describe() 输出")
desc = pt.describe()
check("describe 不含 tier_derivation", "tier_derivation" not in desc,
      str(sorted(desc.keys())))
for k in ("profiles", "profile", "active_tiers", "effective_tiers"):
    check("describe 不含 %s" % k, k not in desc, str(sorted(desc.keys())))
check("describe 含 rate", "rate" in desc, str(sorted(desc.keys())))
check("describe 含 note", "note" in desc, str(sorted(desc.keys())))
try:
    json.dumps(desc, allow_nan=False)
    check("describe() 是合法 JSON（无 Infinity/NaN）", True)
except ValueError as e:
    check("describe() 是合法 JSON（无 Infinity/NaN）", False, str(e))

# ── ③ note 承担说明职责 ─────────────────────────────────────────
print("\n[3] 口径说明（note）的内容要素")
note = desc.get("note") or ""
check("note 非空", bool(note))
for kw in ("用电量", "电费单价", "0.5224", "成都", "不区分阶梯档位",
           "不区分峰谷时段", "不区分地区"):
    check("note 含「%s」" % kw, kw in note, note)
check("note 说明可自行调整", "调整" in note, note)
check("note 不含阶梯档位数字（如 180/280）", "180" not in note, note)
check("note 不含结转概念", "结转" not in note, note)

# ── ④ 前端不再有推导行 UI ──────────────────────────────────────
print("\n[4] 前端源码约束")
JS = os.path.join(UI, "static", "js", "monitor.js")
with open(JS, encoding="utf-8") as f:
    js = f.read()
code = strip_comments(js)

check("已移除 tariffDerivationHtml",
      "function tariffDerivationHtml" not in js)
check("已移除推导行 class tariff-tb-derive",
      "tariff-tb-derive" not in js)
check("已移除 tier_derivation 读取", "tier_derivation" not in code)
check("已移除 effective_derivation", "effective_derivation" not in code)
check("已移除旧的重复说明（7–9 月档位）",
      "7–9 月档位" not in code)
check("已移除旧的重复说明（6–10 月低谷价）",
      "6–10 月低谷价" not in code)

# 新形态：渲染后端下发的 note
check("前端渲染 note（data-tariff-note）",
      "data-tariff-note" in js)
check("前端从 d.note 取说明",
      "d && d.note" in js or "(d && d.note)" in js)
check("说明有独立样式类 tariff-note",
      "tariff-note" in js)

# ── ⑤ CSS 清理 ──────────────────────────────────────────────────
print("\n[5] CSS 约束")
CSS = os.path.join(UI, "static", "css", "app.css")
with open(CSS, encoding="utf-8") as f:
    css = f.read()
check("已移除推导行样式 .tariff-tb-derive",
      ".tariff-tb-derive" not in css)
check("已移除推导说明 .tariff-tb-note", ".tariff-tb-note" not in css)
check("新增 .tariff-note 样式", ".tariff-note" in css)

# ── ⑥ JS 语法 ───────────────────────────────────────────────────
print("\n[6] JS 语法检查")
r = subprocess.run(["node", "--check", JS], capture_output=True, text=True)
check("monitor.js 语法正确", r.returncode == 0, r.stderr[:300])

# ── 汇总 ─────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("结果：%d 通过 / %d 失败（共 %d）" % (len(PASS), len(FAIL), len(PASS) + len(FAIL)))
print("=" * 70)
if FAIL:
    print("\n失败明细：")
    for n, d in FAIL:
        print("  ❌ %s ← %s" % (n, d))
    sys.exit(1)
print("全部通过 ✅")
