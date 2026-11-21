#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.1.60：验证「本月生效档位推导明细」

背景
----
用户反馈电价面板上方的档位表（0–490 / 491–690）和下面两个「档位」输入框
（夏季 260/460、其他月 180/280）看着像重复，其实是「结果 vs 参数」；
但表格下方那行说明文字（7–9 月档位 260/460、6–10 月低谷价 0.175）
**全是下方输入框已有的数字** → 纯重复。
而真正该说的「490/690 是怎么算出来的」反而没说。

本轮改动：
  ① 后端 power_tariff.tier_derivation() 输出推导明细
  ② describe() / profile_list() 带上推导明细
  ③ 前端用推导解释行取代重复说明文字
  ④ 季节参数区块收成可折叠

本脚本验证后端推导链的数值正确性 + 前端渲染逻辑的关键约束。
"""
import os
import sys
import json
import time
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
    """去掉 JS 注释，只留真正会执行的代码。

    ⚠️ 上一轮栽过：改动后我在注释里**引用**了被删掉的旧文案，
       源码文本断言因此误报「旧说明还在」。断言必须只看可执行代码。

    ⚠️ 实现踩坑：手写状态机不可靠 —— 正则字面量（/^\\d+/）里的
       斜杠会被误判成注释起始，字符串里的转义引号也会让状态错乱。
       改用正则按行剥离（够用且稳定）。
    """
    import re
    no_block = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    no_line = re.sub(r"(?m)^\s*//.*$", " ", no_block)
    # 去掉行尾注释（前面不能是引号/反斜杠，简单起见只处理明显的）
    no_line = re.sub(r"(?m)\s//[^\n\"'`]*$", " ", no_line)
    return no_line


print("=" * 70)
print("v1.1.60 验证：阶梯电价推导明细")
print("=" * 70)

import power_tariff as pt

# ── ① 默认配置（单月抄表、无人口加成）────────────────────────────
print("\n[1] 默认配置推导明细")
cfg0 = dict(pt.load_config())
cfg0.update({"region": "sichuan", "household_people": 0, "meter_months": 1})
d0 = pt.tier_derivation("sichuan", cfg0)

check("默认返回推导明细", isinstance(d0, dict) and bool(d0))
check("基准档位 = 其他月 180/280",
      d0["base_tiers"][:2] == [180.0, 280.0], str(d0["base_tiers"]))
check("基准标签 = 其他月档位", d0["base_label"] == "其他月档位", d0["base_label"])
check("抄表周期 = 1", d0["meter_months"] == 1, str(d0["meter_months"]))
check("人口加成 = 0", approx(d0["mp_extra"], 0), str(d0["mp_extra"]))
check("结转 = 0", approx(d0["carry1"], 0) and approx(d0["carry2"], 0))
check("生效档位 = 180/280/不限",
      d0["limits"][:2] == [180.0, 280.0] and d0["limits"][2] is None,
      str(d0["limits"]))

# ── ② 抄表周期 ×2 ────────────────────────────────────────────────
print("\n[2] 抄表周期 2 个月 → 门槛 ×2")
cfg2 = dict(cfg0)
cfg2["meter_months"] = 2
d2 = pt.tier_derivation("sichuan", cfg2)
check("乘倍数后基准 = 360/560",
      d2["base_after_cycle"][:2] == [360.0, 560.0], str(d2["base_after_cycle"]))
check("生效档位 = 360/560（无人口加成）",
      d2["limits"][:2] == [360.0, 560.0], str(d2["limits"]))

# ── ③ 一户多人口加成（6 人 = +130）──────────────────────────────
print("\n[3] 一户多人口加成")
cfg6 = dict(cfg0)
cfg6["household_people"] = 6
d6 = pt.tier_derivation("sichuan", cfg6)
check("6 人加成 = 130 度", approx(d6["mp_extra"], 130), str(d6["mp_extra"]))
check("生效一档 = 180+130 = 310", approx(d6["limits"][0], 310), str(d6["limits"]))
check("生效二档 = 310+100 = 410", approx(d6["limits"][1], 410), str(d6["limits"]))

cfg5 = dict(cfg0)
cfg5["household_people"] = 5
d5 = pt.tier_derivation("sichuan", cfg5)
check("5 人加成 = 100 度", approx(d5["mp_extra"], 100), str(d5["mp_extra"]))
check("不足 5 人不加成",
      approx(pt.multi_person_extra(4), 0))

# ── ④ 截图复现：6 人 + 双月抄表 = 490/690 ───────────────────────
print("\n[4] 复现用户截图（6 人 + 两个月抄表）")
cfgS = dict(cfg0)
cfgS["household_people"] = 6
cfgS["meter_months"] = 2
dS = pt.tier_derivation("sichuan", cfgS)
check("乘抄表周期后 = 360/560",
      dS["base_after_cycle"][:2] == [360.0, 560.0], str(dS["base_after_cycle"]))
check("生效一档 = 360+130 = 490", approx(dS["limits"][0], 490), str(dS["limits"]))
check("生效二档 = 490+200 = 690", approx(dS["limits"][1], 690), str(dS["limits"]))
check("三档不限", dS["limits"][2] is None, str(dS["limits"]))

# ── ⑤ 一致性：推导明细的 limits 必须等于 effective_tier_limits ──
print("\n[5] 推导明细与生效档位严格一致")
for tag, c in (("默认", cfg0), ("双月", cfg2), ("6人", cfg6), ("截图组合", cfgS)):
    lim, _caps, _extra = pt.effective_tier_limits("sichuan", c)
    dv = pt.tier_derivation("sichuan", c)
    same = (lim[:2] == dv["limits"][:2]) and (lim[2] is None) == (dv["limits"][2] is None)
    check("%s 组合 limits 一致" % tag, same, "%s vs %s" % (lim, dv["limits"]))

# ── ⑥ describe() 带上推导明细 ───────────────────────────────────
print("\n[6] describe() 输出")
desc = pt.describe()
check("describe 含 tier_derivation", "tier_derivation" in desc)
check("tier_derivation 非空", bool(desc.get("tier_derivation")))
check("tier_derivation 含 base_tiers",
      "base_tiers" in (desc.get("tier_derivation") or {}))
# JSON 必须合法（不能出现 Infinity/NaN）
try:
    json.dumps(desc, allow_nan=False)
    check("describe() 是合法 JSON（无 Infinity/NaN）", True)
except ValueError as e:
    check("describe() 是合法 JSON（无 Infinity/NaN）", False, str(e))

# ── ⑦ profile_list() 每个地区都带推导明细 ───────────────────────
print("\n[7] profile_list() 按地区给推导明细")
plist = pt.profile_list(cfg0)
ok_all = True
detail = ""
for p in plist:
    d = p.get("effective_derivation")
    if not isinstance(d, dict) or "base_tiers" not in d:
        ok_all = False
        detail = "%s 缺 effective_derivation" % p["id"]
        break
    if not p.get("effective_tiers"):
        ok_all = False
        detail = "%s 缺 effective_tiers" % p["id"]
        break
check("所有地区都有 effective_derivation + effective_tiers", ok_all, detail)

# 各地区的 limits 应与其 effective_tiers 一致
inconsistent = []
for p in plist:
    et = p.get("effective_tiers") or []
    ed = (p.get("effective_derivation") or {}).get("limits") or []
    if et[:2] != ed[:2]:
        inconsistent.append("%s: %s vs %s" % (p["id"], et, ed))
check("各地区 effective_tiers 与推导明细一致", not inconsistent, "; ".join(inconsistent))

# ── ⑧ 天津（不支持四川地方政策）不受人口/抄表影响 ───────────────
print("\n[8] 天津：地方政策开关隔离")
cfgT = dict(cfg0)
cfgT["region"] = "tianjin"
cfgT["household_people"] = 6
cfgT["meter_months"] = 2
dT = pt.tier_derivation("tianjin", cfgT)
check("天津不受人口加成", approx(dT["mp_extra"], 0), str(dT["mp_extra"]))
check("天津抄表周期仍为 1", dT["meter_months"] == 1, str(dT["meter_months"]))
check("天津生效档位 = 220/400",
      dT["limits"][:2] == [220.0, 400.0], str(dT["limits"]))

# ── ⑨ 前端：重复说明文字必须消失，推导行必须存在 ───────────────
print("\n[9] 前端源码约束")
JS = os.path.join(UI, "static", "js", "monitor.js")
with open(JS, encoding="utf-8") as f:
    js = f.read()

check("已移除旧的重复说明（7–9 月档位）",
      "7–9 月档位" not in strip_comments(js), "仍存在重复说明文字")
check("已移除旧的重复说明（6–10 月低谷价）",
      "6–10 月低谷价" not in strip_comments(js), "仍存在重复说明文字")
check("新增推导行函数 tariffDerivationHtml",
      "function tariffDerivationHtml" in js)
check("推导行读取 tier_derivation",
      "tier_derivation" in js)
check("推导行 class = tariff-tb-derive",
      "tariff-tb-derive" in js)
check("季节参数有折叠开关",
      "data-tariff-season-toggle" in js)
check("切换地区同步推导明细",
      "effective_derivation" in js)

# 折叠默认状态
check("季节参数默认折叠",
      "collapsed" in js)

# ── ⑩ CSS ────────────────────────────────────────────────────────
print("\n[10] CSS 约束")
CSS = os.path.join(UI, "static", "css", "app.css")
with open(CSS, encoding="utf-8") as f:
    css = f.read()
check("折叠态隐藏 body/cur",
      ".tariff-season.collapsed .tariff-season-body" in css)
check("有折叠箭头样式",
      ".tariff-season-caret" in css)
check("button 头样式复位",
      "button.tariff-season-head" in css)
check("推导行样式存在",
      ".tariff-tb-derive" in css)

# ── ⑪ JS 语法 ───────────────────────────────────────────────────
print("\n[11] JS 语法检查")
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
