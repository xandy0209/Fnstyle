#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.1.63：「不存在档位信息」的后端契约验证

历史沿革
--------
v1.1.58：用户反馈「都固定单价了为什么还有档位信息？」——
      flat 地区的 tiers_* 全是 [None, None, None]，前端据此渲染出
      「第1档 0–null」的三行表。修法：profile 增加 has_tiers 标记，
      前端据此整块隐藏价目表。

v1.1.63：老板决定整个电价模型退化为**单一单价** ——
      「删除阶梯计费和档位信息等，也不区分区域，就是一个电费单价的配置」
      ⇒ 不再需要 has_tiers 这类「按地区区分是否显示档位」的开关，
      因为**所有地区都没有档位信息了**。

本脚本验证：
  ① 地区档案概念整体消失（PROFILES / profile_list / effective_profile）
  ② 档位概念整体消失（tiers / has_tiers / tier_*）
  ③ 计费结果里没有任何档位字段
  ④ describe() 不再透出 profile / profiles
  ⑤ 前端源码里不再有档位表的渲染入口
"""

import os
import sys
import json
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app", "ui"))

import power_tariff as pt  # noqa: E402

PASS = []
FAIL = []


def ok(name, cond, extra=""):
    if cond:
        PASS.append(name)
    else:
        FAIL.append("%s%s" % (name, (" | " + extra) if extra else ""))


def main():
    tmp = tempfile.mkdtemp(prefix="fnst_flat_")
    os.environ["FNSTYLE_DATA_DIR"] = tmp

    # ---------- ① 地区档案概念整体消失 ----------
    for fn in ("PROFILES", "profile_list", "effective_profile",
               "OVERRIDABLE_FIELDS"):
        ok("后端已无 %s" % fn, not hasattr(pt, fn))

    # ---------- ② 档位概念整体消失 ----------
    for fn in ("tiers_summer", "tiers_other", "has_tiers", "effective_tier_limits",
               "tier_derivation", "_tier_caps", "_tier_index", "_split_kwh_by_tier"):
        ok("后端已无 %s" % fn, not hasattr(pt, fn))

    # ---------- ③ 计费结果里没有任何档位字段 ----------
    pt.save_config({"rate": 0.5224})
    b = pt.compute_bill(1.0, 0.0)
    for k in ("tier_kwh", "tier_rates", "tiers", "current_tier", "tier_limits",
              "has_tiers", "tier1_remaining", "tier2_remaining"):
        ok("计费结果不含 %s" % k, k not in b, str(sorted(b.keys())))
    ok("1 kWh × 0.5224 = 0.5224 元",
       abs(b["yuan"] - 0.5224) < 1e-9, "实际 %.6f" % b["yuan"])

    # ---------- ④ describe() 不再透出 profile / profiles ----------
    d = pt.describe()
    ok("describe 不含 profile", "profile" not in d, str(sorted(d.keys())))
    ok("describe 不含 profiles", "profiles" not in d, str(sorted(d.keys())))
    ok("describe 含 rate", "rate" in d, str(sorted(d.keys())))
    ok("describe 含 note（口径说明）", "note" in d, str(sorted(d.keys())))
    ok("describe 可合法序列化", bool(json.dumps(d, ensure_ascii=False)))

    # ---------- ⑤ 前端源码里不再有档位表渲染入口 ----------
    root = os.path.join(os.path.dirname(__file__), "..", "app", "ui")
    mon = os.path.join(root, "static", "js", "monitor.js")
    with open(mon, "r", encoding="utf-8") as f:
        msrc = f.read()
    for token in ("tariffTableHtml", "tariffDerivationHtml", "has_tiers",
                  "tier_kwh", "current_tier", "tier_limits", "tariff-table",
                  "tariff-tb-", "data-tariff-voltage", "data-tariff-region",
                  "data-tariff-valley", "data-tariff-season"):
        ok("monitor.js 不含 %s" % token, token not in msrc)

    css = os.path.join(root, "static", "css", "app.css")
    with open(css, "r", encoding="utf-8") as f:
        csrc = f.read()
    for token in (".tariff-table", ".tariff-tb-", ".tariff-season",
                  ".tariff-switch", ".tariff-select", ".sub-split", ".sub-tier"):
        ok("app.css 不含 %s" % token, token not in csrc)

    print("=" * 60)
    print("verify_tariff_flat: PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  [FAIL] %s" % f)
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
