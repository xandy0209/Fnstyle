#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.1.63：「下拉联动已不存在」的防回归验证

历史沿革
--------
v1.1.61：用户反馈「电价设置中的下拉选择联动好像有问题呢，请核实清楚」，
      定位到 5 处真实缺陷（切地区后电压下拉与价目表峰价不一致等）：
        ① profile_list() 不给每个地区「生效的电压等级」字段
        ② tariffPanelHtml 用 document.querySelector 去**旧面板**捞电压值
        ③ restoreTariffForm 换地区时用全局 cfg.voltage 回填
        ④ 固定单价地区（合表）的单价也分电压等级，表格没跟着变
        ⑤ 电压 change 后只改表格单元格，没同步内存里的 profile.voltage

v1.1.63：整个电价模型退化为**单一单价** ——
      「删除阶梯计费和档位信息等，也不区分区域，就是一个电费单价的配置」
      ⇒ 地区、电压等级、峰谷时段三个下拉**全部消失**
      ⇒ 只剩一个「电费单价」输入框 → **联动问题从根上不存在了**
      ⇒ 上面 5 处缺陷连同它们的载体（价目表 / 下拉）一并消失

本脚本验证：
  ① 后端已无地区/电压/时段相关 API
  ② 前端不再有地区下拉、电压下拉、谷时段下拉
  ③ 前端不再有「价目表」渲染与联动同步函数
  ④ 面板只剩一个单价输入框 → 结构上不可能再出现联动不同步
  ⑤ 单价是全局配置 ⇒ 不存在「切到别的地区单价变了」的可能
"""
import os
import sys
import json
import re
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


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


print("=" * 62)
print("v1.1.63 验证：下拉联动已不存在（单一单价模型）")
print("=" * 62)

import power_tariff as pt  # noqa: E402

JS = os.path.join(UI, "static", "js", "monitor.js")
js = read(JS)
CSS = os.path.join(UI, "static", "css", "app.css")
css = read(CSS)
HC = os.path.join(UI, "history_collect.py")
hc = read(HC)

# ── ① 后端：地区 / 电压 / 时段 API 全部不存在 ──────────────────────
print("\n=== 1. 后端：地区 / 电压 / 时段 API 已删除 ===")
for fn in ("resolve_voltage", "PROFILES", "profile_list", "effective_profile",
           "carry_in_for", "_in_hours", "load_ledger", "save_ledger"):
    check("后端已无 %s" % fn, not hasattr(pt, fn))

d = pt.describe()
for k in ("profiles", "profile", "voltage", "region", "valley_start",
          "valley_end", "active_tiers", "effective_tiers"):
    check("describe 不含 %s" % k, k not in d, str(sorted(d.keys())))

# ── ② 前端：三个下拉全部消失 ──────────────────────────────────────
print("\n=== 2. 前端：下拉控件已删除 ===")
for token in ("data-tariff-voltage", "data-tariff-region",
              "data-tariff-valley-start", "data-tariff-valley-end",
              "data-tariff-meter", "data-tariff-people"):
    check("monitor.js 不含 %s" % token, token not in js)
check("monitor.js 不再有 <select> 渲染电价下拉",
      "tariff-select" not in js and "tariff-switch" not in js)

# ── ③ 前端：价目表与联动同步函数已消失 ────────────────────────────
print("\n=== 3. 前端：价目表 / 联动同步函数已删除 ===")
for token in ("tariffTableHtml", "syncTariffVoltageTable", "syncTariffFlatRate",
              "tariffDerivationHtml", "tariffSeasonHtml",
              "tariff-tb-", "tariff-table", "bandLabel"):
    check("monitor.js 不含 %s" % token, token not in js)
for token in (".tariff-table", ".tariff-tb-", ".tariff-switch",
              ".tariff-select", ".tariff-season", ".tariff-month"):
    check("app.css 不含 %s" % token, token not in css)

# ── ④ 面板只剩一个输入框 → 结构上不可能再联动不同步 ──────────────
print("\n=== 4. 面板结构：只剩一个单价输入框 ===")
panel_fn = js[js.index("function tariffPanelHtml("):]
panel_fn = panel_fn[:panel_fn.index("\n  }\n") + 5]
inputs = re.findall(r"<input[^>]*>", panel_fn)
check("面板只有 1 个输入框", len(inputs) == 1, str(inputs))
check("唯一输入框是 data-tariff-rate",
      len(inputs) == 1 and "data-tariff-rate" in inputs[0], str(inputs))
check("面板没有 <select>", "<select" not in panel_fn)
check("面板渲染后端下发的口径说明",
      "data-tariff-note" in panel_fn)
check("面板有保存按钮", "data-tariff-save" in panel_fn)

# ── ⑤ 单价是全局配置 ──────────────────────────────────────────────
print("\n=== 5. 单价是全局配置（不存在「换地区单价变了」）===")
check("配置里没有 region 键", "region" not in pt.load_config(),
      str(sorted(pt.load_config().keys())))
check("captureTariffForm 只采集 rate",
      "rate:" in js and "region:" not in js and "voltage:" not in js)
check("restoreTariffForm 只回填 rate",
      "st.rate !== null" in js and "st.region" not in js
      and "st.voltage" not in js)
check("保存载荷只提交 rate",
      "payload.rate" in js and "payload.region" not in js
      and "payload.voltage" not in js)
check("history_collect 不再判谷段",
      "valley_hours" not in hc)
check("history_collect 不再按谷时段拆分",
      "out[\"valley\"] = 0.0" in hc.replace(" ", " ")
      or "out['valley'] = 0.0" in hc,
      "未见 valley 归零")

# ── 6. 语法 ────────────────────────────────────────────────────────
print("\n=== 6. 语法检查 ===")
for name, path in (("monitor.js", JS),):
    r = subprocess.run(["node", "--check", path], capture_output=True, text=True)
    check("%s 语法正确" % name, r.returncode == 0, r.stderr[:200])
r = subprocess.run([sys.executable, "-m", "py_compile",
                    os.path.join(UI, "power_tariff.py"),
                    os.path.join(UI, "history_collect.py")],
                   capture_output=True, text=True)
check("power_tariff.py / history_collect.py 语法正确",
      r.returncode == 0, r.stderr[:200])

# ── 汇总 ───────────────────────────────────────────────────────────
print("\n" + "=" * 62)
print("结果：%d 通过 / %d 失败" % (len(PASS), len(FAIL)))
print("=" * 62)
for name, detail in FAIL:
    print("  ❌ %s  ← %s" % (name, detail))
sys.exit(1 if FAIL else 0)
