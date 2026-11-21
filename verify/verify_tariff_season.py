#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.1.63：「季节参数已整体废弃」的防回归验证

历史沿革
--------
v1.1.57：用户反馈「成都电价分丰水期、枯水期还有夏季，设置里为什么没有？」
      → 加了 overrides 季节参数覆盖 + 季节参数 UI 区块
      → 同期修掉了「地区下拉选择后总是恢复到成都」的联动 bug

v1.1.63：老板决定整个电价模型退化为**单一单价** ——
      「删除阶梯计费和档位信息等，也不区分区域，就是一个电费单价的配置」
      ⇒ 季节参数（丰水期/枯平期/夏季月份 + 各档电价）**整块废弃**
      ⇒ 「地区」概念同时消失 ⇒ 「切地区丢配置」的问题自动不存在

本脚本验证：
  ① 后端已无 overrides / 季节相关的任何 API
  ② describe() 不再透出季节字段
  ③ 旧配置里的 overrides 被**读取层丢弃**（升级不残留）
  ④ 前端不再有季节参数 UI 的渲染与事件绑定
  ⑤ 口径说明里不含季节概念
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI = os.path.join(ROOT, "app", "ui")

TMP = tempfile.mkdtemp(prefix="fnstyle_tariff_season_")
os.environ["FNSTYLE_DATA_DIR"] = TMP
sys.path.insert(0, UI)

import power_tariff as pt  # noqa: E402

PASS = 0
FAIL = 0


def check(label, ok, extra=None):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  ✓ " + label)
    else:
        FAIL += 1
        print("  ✗ " + label + ("  → " + str(extra) if extra else ""))


def section(t):
    print("\n=== " + t + " ===")


# ────────────────────────────────────────────────────────────
section("1. 后端已无季节参数 API")
for fn in ("normalize_overrides", "effective_profile", "_normalize_months"):
    check("后端已无 %s" % fn, not hasattr(pt, fn))

cfg = pt.default_config()
check("默认配置不含 overrides", "overrides" not in cfg, str(sorted(cfg.keys())))
check("默认配置不含 enabled", "enabled" not in cfg, str(sorted(cfg.keys())))
check("默认配置不含 region", "region" not in cfg, str(sorted(cfg.keys())))

# ────────────────────────────────────────────────────────────
section("2. describe() 不再透出季节字段")
d = pt.describe()
for k in ("overrides", "current_month", "is_summer_tier", "is_wet_season",
          "active_tiers", "active_valley_rates", "profiles", "profile"):
    check("describe 不含 %s" % k, k not in d, str(sorted(d.keys())))
check("describe 含 rate", "rate" in d, str(sorted(d.keys())))
check("describe 含 note", "note" in d, str(sorted(d.keys())))
note = d.get("note") or ""
check("口径说明不含季节概念", "丰水期" not in note and "夏季" not in note, note)

# ────────────────────────────────────────────────────────────
section("3. 旧配置里的 overrides 被读取层丢弃")
legacy = {
    "rate": 0.6,
    "overrides": {"sichuan": {
        "wet_months": [1, 2, 3],
        "summer_months": [6, 7, 8],
        "valley_rates_wet": [0.1, 0.2, 0.3],
        "tiers_summer": [300, 500, None],
    }},
    "region": "sichuan",
    "enabled": True,
}
with open(pt._cfg_path(), "w", encoding="utf-8") as f:
    json.dump(legacy, f, ensure_ascii=False)
back = pt.load_config()
check("旧配置升级后无 overrides", "overrides" not in back, str(sorted(back.keys())))
check("旧配置升级后无 region", "region" not in back, str(sorted(back.keys())))
check("旧配置升级后 rate 仍生效", back["rate"] == 0.6, back["rate"])
check("读出的配置只有 rate + updated",
      set(back.keys()) == {"rate", "updated"}, str(sorted(back.keys())))

# ────────────────────────────────────────────────────────────
section("4. 保存时也不会写回季节字段")
ok, msg = pt.save_config({"rate": 0.65, "overrides": {"x": {"y": 1}},
                           "region": "tianjin"})
check("保存成功", ok, msg)
saved = json.load(open(pt._cfg_path(), "r", encoding="utf-8"))
check("落盘文件不含 overrides", "overrides" not in saved, str(sorted(saved.keys())))
check("落盘文件不含 region", "region" not in saved, str(sorted(saved.keys())))
check("落盘文件只有 rate + updated",
      set(saved.keys()) == {"rate", "updated"}, str(sorted(saved.keys())))
check("落盘 rate 正确", saved["rate"] == 0.65, saved["rate"])

# ────────────────────────────────────────────────────────────
section("5. 前端不再有季节参数 UI")
mon = os.path.join(UI, "static", "js", "monitor.js")
with open(mon, "r", encoding="utf-8") as f:
    msrc = f.read()
for token in ("tariffSeasonHtml", "data-tariff-season", "wet_months",
              "summer_months", "valley_rates_wet", "valley_rates_dry",
              "season-panel", "seasonBlock"):
    check("monitor.js 不含 %s" % token, token not in msrc)
check("monitor.js 不再采集 season",
      "season:" not in msrc)

css = os.path.join(UI, "static", "css", "app.css")
with open(css, "r", encoding="utf-8") as f:
    csrc = f.read()
for token in (".tariff-season", ".tariff-month", ".season-", ".month-btn"):
    check("app.css 不含 %s" % token, token not in csrc)

# ────────────────────────────────────────────────────────────
section("6. 计费结果里没有季节维度")
b = pt.compute_bill(100.0, 50.0, pt.load_config())
check("150 kWh × 0.65 = 97.5",
      abs(b["yuan"] - 97.5) < 1e-6, b["yuan"])
check("计费结果不含季节字段",
      not any(k in b for k in ("season", "is_summer", "is_wet", "month")))

shutil.rmtree(TMP, ignore_errors=True)

print("\n" + "-" * 60)
print("通过 %d 项，失败 %d 项" % (PASS, FAIL))
if FAIL:
    print("❌ 有失败项")
    sys.exit(1)
print("全部通过")
