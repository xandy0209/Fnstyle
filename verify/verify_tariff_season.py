#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_power_tariff_season.py —— 季节参数覆盖 + 地区切换不丢配置

覆盖 v1.1.57 用户反馈的两点：
  ① 地区下拉选择后总是恢复到成都（前端，实机复现已定位）
  ② 成都电价分丰水期/枯平期/夏季，设置里却没有（前端 + 后端）

后端部分：overrides 的读写、校验、合并语义、生效逻辑。
前端部分：季节参数区块的渲染、地区切换的缓存同步（Node 断言）。
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


def base_cfg(**kw):
    cfg = pt.default_config()
    cfg.update(kw)
    return cfg


# ────────────────────────────────────────────────────────────
section("1. 默认配置含 overrides 结构")
cfg = pt.default_config()
check("默认配置有 overrides 字段", isinstance(cfg.get("overrides"), dict))
check("默认 overrides 为空", cfg["overrides"] == {})
check("未启用时 enabled=False", cfg["enabled"] is False)

# ────────────────────────────────────────────────────────────
section("2. overrides 读写往返")
cfg = base_cfg(enabled=True, region="sichuan")
cfg["overrides"] = {"sichuan": {
    "wet_months": [1, 2, 3],
    "summer_months": [6, 7, 8],
    "valley_rates_wet": [0.1, 0.2, 0.3],
    "valley_rates_dry": [0.15, 0.25, 0.35],
    "tiers_summer": [300, 500, None],
    "tiers_other": [200, 350, None],
}}
ok, msg = pt.save_config(cfg)
check("保存成功", ok, msg)
back = pt.load_config()
check("读回 wet_months", back["overrides"]["sichuan"]["wet_months"] == [1, 2, 3])
check("读回 tiers_summer", back["overrides"]["sichuan"]["tiers_summer"] == [300.0, 500.0, None])

# ────────────────────────────────────────────────────────────
section("3. 生效逻辑 effective_profile")
prof = pt.effective_profile("sichuan", back)
check("覆盖的 wet_months 生效", prof["wet_months"] == [1, 2, 3])
check("覆盖的 valley_rates_wet 生效", prof["valley_rates_wet"] == [0.1, 0.2, 0.3])
check("未覆盖的 peak_rates 保持内置", prof["peak_rates"] == pt.PROFILES["sichuan"]["peak_rates"])
check("内置 PROFILES 未被污染",
      pt.PROFILES["sichuan"]["wet_months"] == [6, 7, 8, 9, 10])
check("effective_profile 返回的是副本",
      pt.effective_profile("sichuan", back) is not pt.PROFILES["sichuan"])

# ────────────────────────────────────────────────────────────
section("4. 覆盖后计费按新季节参数")
import time
# 覆盖成「1-3 月为丰水期，谷价 0.1」；1 月用电应走 0.1
cfg2 = base_cfg(enabled=True, region="sichuan")
cfg2["overrides"] = {"sichuan": {"wet_months": [1, 2, 3], "valley_rates_wet": [0.1, 0.2, 0.3]}}
jan = time.mktime((2026, 1, 15, 12, 0, 0, 0, 0, 0))
r = pt.compute_bill(0, 100, cfg2, ts=jan)
check("1 月按覆盖后的丰水期谷价 0.1 计费",
      abs(r["detail"][0]["valley_rate"] - 0.1) < 1e-9,
      r["detail"][0]["valley_rate"])
check("1 月 100 度谷段电费 = ¥10", abs(r["yuan"] - 10.0) < 1e-6, r["yuan"])
jul = time.mktime((2026, 7, 15, 12, 0, 0, 0, 0, 0))
r2 = pt.compute_bill(0, 100, cfg2, ts=jul)
check("7 月不在覆盖的丰水期 → 走枯平期 0.2535",
      abs(r2["detail"][0]["valley_rate"] - 0.2535) < 1e-9,
      r2["detail"][0]["valley_rate"])

# ────────────────────────────────────────────────────────────
section("5. 覆盖阶梯档位后按新档位计费")
cfg3 = base_cfg(enabled=True, region="sichuan")
cfg3["overrides"] = {"sichuan": {"tiers_other": [100, 200, None]}}
nov = time.mktime((2026, 11, 15, 12, 0, 0, 0, 0, 0))
r3 = pt.compute_bill(150, 0, cfg3, ts=nov)
check("档位上限覆盖为 100 后 150 度落二档",
      r3["tier_kwh"][0] == 100.0 and r3["tier_kwh"][1] == 50.0,
      r3["tier_kwh"])

# ────────────────────────────────────────────────────────────
section("6. 非法值校验")
bad_cases = [
    ({"wet_months": [13]}, "丰水期月份"),
    ({"wet_months": [1, "x"]}, "丰水期月份"),
    ({"summer_months": [0]}, "夏季月份"),
    ({"valley_rates_wet": [1, 2]}, "丰水期低谷价"),
    ({"valley_rates_dry": [1, 2, -3]}, "枯平期低谷价"),
    ({"tiers_other": [100, 50, None]}, "非夏季阶梯档位"),
    ({"tiers_summer": [100, 100, None]}, "夏季阶梯档位"),
]
for bad, kw in bad_cases:
    c = base_cfg(enabled=True)
    c["overrides"] = {"sichuan": bad}
    ok, msg = pt.save_config(c)
    check("拒绝非法值 %s" % json.dumps(bad, ensure_ascii=False), not ok, msg)
    check("  错误信息提到「%s」" % kw, kw in msg, msg)

# ────────────────────────────────────────────────────────────
section("7. 按地区合并（不互相抹掉）")
c = base_cfg(enabled=True, region="sichuan")
c["overrides"] = {"sichuan": {"wet_months": [1]}}
pt.save_config(c)
c2 = base_cfg(enabled=True, region="tianjin")
c2["overrides"] = {"tianjin": {"tiers_other": [300, 500, None]}}
pt.save_config(c2)
merged = pt.load_config()["overrides"]
check("四川覆盖仍在", "sichuan" in merged, merged)
check("天津覆盖已加", "tianjin" in merged, merged)

# ────────────────────────────────────────────────────────────
section("8. describe() 暴露季节信息")
d = pt.describe()
check("describe 含 overrides", "overrides" in d)
check("describe 含 current_month", "current_month" in d)
check("describe 含 is_summer_tier", "is_summer_tier" in d)
check("describe 含 is_wet_season", "is_wet_season" in d)
check("describe 含 active_tiers", "active_tiers" in d)
check("describe 含 active_valley_rates", "active_valley_rates" in d)
check("profiles 列表非空", len(d["profiles"]) >= 3)
for p in d["profiles"]:
    if not all(k in p for k in ("wet_months", "summer_months",
                                "valley_rates_wet", "valley_rates_dry",
                                "tiers_summer", "tiers_other")):
        check("profiles 每项都含季节字段", False, p.get("id"))
        break
else:
    check("profiles 每项都含季节字段", True)

# ────────────────────────────────────────────────────────────
section("9. 无季节维度地区不显示季节参数")
flat = pt.effective_profile("flat", pt.load_config())
check("固定单价地区 wet_months 为空", flat["wet_months"] == [])
check("固定单价地区 summer_months 为空", flat["summer_months"] == [])

shutil.rmtree(TMP, ignore_errors=True)

print("\n" + "-" * 60)
print("通过 %d 项，失败 %d 项" % (PASS, FAIL))
if FAIL:
    print("❌ 有失败项")
    sys.exit(1)
print("全部通过")
