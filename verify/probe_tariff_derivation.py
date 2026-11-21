#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CDP 实机探针（v1.1.63）：验证「推导解释行 / 季节参数 / 地区 / 电压」已彻底消失

改动背景
--------
v1.1.60 时电价面板里有三组东西：
  ① 档位价目表 + 推导解释行（讲清 180/280 门槛怎么来的）
  ② 季节参数折叠区（丰水期/夏季的档位与谷价）
  ③ 地区下拉（四川/天津/合表/固定单价）+ 电压等级下拉

v1.1.63 老板决定「删除阶梯计费和档位信息等，也不区分区域，
就是一个电费单价的配置，并增加对应的说明即可」→
  ① ② ③ 全部消失，只剩「一个单价输入框 + 一段口径说明」。

本探针是**反向断言**：确认这些概念在实机上真的没了，
而且口径说明本身在（说明文字里提到「阶梯档位」「峰谷时段」「地区」
是因为在「**不区分**……」的否定语境里 —— 所以只能查
「是否作为**配置项**出现」，不能查「文本里有没有这个词」）。

逐项验证：
  A. 面板无任何废弃控件（推导行/季节/地区/电压/谷时段/人口/抄表/档位表）
  B. 口径说明存在且口径正确
  C. 后端 describe() 不再返回任何阶梯/季节/地区字段
  D. /api/power/stats 不含阶梯字段，tariff_mode = fixed
  E. 无 JS 异常

前置：Chrome 已带 --remote-debugging-port=9222 启动，
      且 NAS 上已部署 v1.1.63（http://192.168.5.8:9910/）。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from probe_base import (  # noqa: E402
    Checker, api, connect, open_drawer, reload_and_open, snap_panel,
)

# 后端 describe() 里**不应**出现的字段
DEAD_DESCRIBE_KEYS = (
    "tier_derivation", "profiles", "profile", "active_tiers",
    "active_valley_rates", "is_summer_tier", "is_wet_season",
    "effective_tiers", "current_month", "regions", "region",
    "voltage", "voltage_rates", "flat_rate", "flat_rates",
    "enabled", "valley_start", "valley_end", "overrides",
    "rate_override", "proj_rate", "household_people", "meter_months",
)

# /api/power/stats 里**不应**出现的字段
DEAD_STATS_KEYS = (
    "tier_ledger", "tiers", "tier", "valley_yuan", "flat_yuan",
    "valley_kwh", "flat_kwh", "carry_over", "carryover",
    "effective_tiers", "active_tiers", "valley_start", "valley_end",
    "season", "is_summer", "is_wet_season",
)

# 面板上**不应**出现的控件（作为配置项）
DEAD_CONTROLS = (
    "hasRegion", "hasVoltage", "hasVstart", "hasVend", "hasEnabled",
    "hasPeople", "hasMeter", "hasProjRate", "hasSeason",
    "hasDerive", "hasTierRow", "hasTable",
)

# 面板快照里**应当**存在的字段
WANT_CONTROLS = ("hasRate", "hasNote", "hasSave")


def main():
    ck = Checker("v1.1.63 废弃概念消失探针")

    print("=== 连接实机 ===")
    c = connect()
    reload_and_open(c)

    s = snap_panel(c)
    print("面板快照:", json.dumps(s, ensure_ascii=False)[:600])

    # ── A. 面板无任何废弃控件 ──────────────────────────────────
    ck.section("A. 面板已无废弃控件")
    for k in DEAD_CONTROLS:
        ck.check("%s 不存在" % k, s.get(k) is False, str(s.get(k)))

    ck.check("面板内 input 控件恰为 1 个（只有单价框）",
             s.get("inputCount") == 1, str(s.get("inputCount")))
    ck.check("面板内 select 控件为 0 个",
             s.get("selectCount") == 0, str(s.get("selectCount")))

    for k in WANT_CONTROLS:
        ck.check("%s 存在" % k, s.get(k) is True, str(s.get(k)))

    # ── B. 口径说明 ────────────────────────────────────────────
    ck.section("B. 口径说明")
    note = s.get("noteText") or ""
    ck.check("说明非空", len(note) > 20, note[:120])
    ck.check("说明含「用电量」", "用电量" in note, note[:80])
    ck.check("说明含「电费单价」", "电费单价" in note, note[:80])
    ck.check("说明含默认单价 0.5224", "0.5224" in note, note[:80])
    ck.check("说明含「成都」", "成都" in note, note[:80])
    ck.check("说明含「不区分阶梯档位」", "不区分阶梯档位" in note, note[:80])
    ck.check("说明含「不区分峰谷时段」", "不区分峰谷时段" in note, note[:80])
    ck.check("说明含「不区分地区」", "不区分地区" in note, note[:80])
    ck.check("说明含「元/kWh」单位", "元/kWh" in note, note[:80])

    # ── C. 后端 describe() ─────────────────────────────────────
    ck.section("C. 后端 describe()")
    d = api("/api/power/tariff")
    ck.check("接口调用成功", d.get("success") is True, str(d.get("success")))
    for k in DEAD_DESCRIBE_KEYS:
        ck.check("describe() 无「%s」" % k, k not in d, sorted(d.keys()))

    cfg = d.get("config") or {}
    ck.check("config 只含 rate + updated",
             set(cfg.keys()) == {"rate", "updated"}, sorted(cfg.keys()))
    ck.check("config.rate = 0.5224", cfg.get("rate") == 0.5224,
             repr(cfg.get("rate")))
    ck.check("顶层 rate = 0.5224", d.get("rate") == 0.5224,
             repr(d.get("rate")))
    ck.check("default_rate = 0.5224", d.get("default_rate") == 0.5224,
             repr(d.get("default_rate")))
    ck.check("unit = 元/kWh", d.get("unit") == "元/kWh", repr(d.get("unit")))
    ck.check("note 非空", len(d.get("note") or "") > 20)

    # ── D. /api/power/stats ────────────────────────────────────
    ck.section("D. /api/power/stats")
    st = api("/api/power/stats")
    ck.check("接口调用成功", st.get("success") is True, str(st.get("success")))
    for k in DEAD_STATS_KEYS:
        ck.check("stats 无「%s」" % k, k not in st, sorted(st.keys()))
    ck.check("tariff_mode = fixed", st.get("tariff_mode") == "fixed",
             repr(st.get("tariff_mode")))
    ck.check("tariff_rate = 0.5224", st.get("tariff_rate") == 0.5224,
             repr(st.get("tariff_rate")))
    ck.check("eff_rate_yuan_per_kwh = 0.5224（恒等于单价）",
             st.get("eff_rate_yuan_per_kwh") == 0.5224,
             repr(st.get("eff_rate_yuan_per_kwh")))
    ck.check("rate_yuan_per_kwh = 0.5224",
             st.get("rate_yuan_per_kwh") == 0.5224,
             repr(st.get("rate_yuan_per_kwh")))

    # 电费 = 电量 × 单价（不再有分段累加）
    mk = st.get("month_kwh") or 0.0
    my = st.get("month_yuan") or 0.0
    if mk > 1e-6:
        ck.check("月电费 = 月电量 × 单价",
                 abs(my - mk * 0.5224) < 0.02,
                 "月电费=%.4f 期望=%.4f" % (my, mk * 0.5224))
    else:
        print("  （本月无用电采样，跳过电费恒等式校验）")

    # ── E. 页面健康 ────────────────────────────────────────────
    ck.section("E. 页面健康")
    errs = [e for e in c.events
            if e.get("method") == "Runtime.exceptionThrown"]
    ck.check("无 JS 异常", len(errs) == 0, "%d 个" % len(errs))

    ck.finish()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
