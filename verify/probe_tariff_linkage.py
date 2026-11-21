#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CDP 实机探针（v1.1.63）：「下拉 ↔ 价目表」联动已彻底不存在

改动背景
--------
v1.1.61 时这个探针验的是 8 处联动缺陷：
  ① 切地区后电压下拉与表格峰价一致
  ② 表格峰价取自当前地区
  ③ 换地区时电压下拉不空白
  ④ 固定单价地区单价随电压等级更新
  ⑤ 电压切换后表格峰价即时更新
  ⑥⑦ 切地区保存后，电压/人口/抄表周期不被清零
  ⑧ 低谷时段起=止 → 不是全天谷段

v1.1.63 老板决定「删除阶梯计费和档位信息等，也不区分区域，
就是一个电费单价的配置」→ 整条联动链**从根上不存在了**：
  · 没有地区下拉 → 不存在「切地区」
  · 没有电压下拉 → 不存在「电压 ↔ 峰价」
  · 没有档位表   → 不存在「表格峰价」
  · 没有谷时段   → 不存在「起=止 → 空区间」

本探针做两件事：
  ① 反向断言：联动链上每个「触发器」都真的不存在了
  ② 正向断言：**剩下的唯一一条链**是「单价 → 电费」，
     它必须端到端连通（改单价 → 面板/接口/电费同步变化）

逐项验证：
  A. 联动触发器全部消失（地区/电压/档位表/谷时段/档位行）
  B. 面板没有任何「随外部状态变化」的重算残留钩子
  C. 单一单价链路端到端：改单价 → 接口 → stats → 电费
  D. 改回默认单价后完全复原
  E. 无 JS 异常

前置：Chrome 已带 --remote-debugging-port=9222 启动，
      且 NAS 上已部署 v1.1.63（http://192.168.5.8:9910/）。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from probe_base import (  # noqa: E402
    Checker, api, connect, reload_and_open, set_rate, snap_panel,
)

# 联动链上的「触发器」——v1.1.63 后一个都不该有
DEAD_TRIGGERS = (
    "hasRegion", "hasVoltage", "hasVstart", "hasVend", "hasTable",
    "hasTierRow", "hasSeason", "hasDerive", "hasProjRate",
    "hasEnabled", "hasPeople", "hasMeter",
)

# 联动链上被联动方
DEAD_LINKED = (
    "flat_rate", "flat_rate_html", "rows", "tier_rows",
    "active_tiers", "valley_rate", "peak_rate",
)


def main():
    ck = Checker("v1.1.63 联动消失 + 单一单价链路探针")

    print("=== 连接实机 ===")
    c = connect()
    reload_and_open(c)

    orig = api("/api/power/tariff").get("config", {})
    orig_rate = orig.get("rate")
    print("原配置:", json.dumps(orig, ensure_ascii=False)[:160])

    try:
        s = snap_panel(c)

        # ── A. 联动触发器全部消失 ──────────────────────────────
        ck.section("A. 联动链上的触发器全部消失")
        for k in DEAD_TRIGGERS:
            ck.check("%s 不存在" % k, s.get(k) is False, str(s.get(k)))
        ck.check("select 数为 0（没有任何下拉可联动）",
                 s.get("selectCount") == 0, str(s.get("selectCount")))
        ck.check("input 数为 1（只有单价框）",
                 s.get("inputCount") == 1, str(s.get("inputCount")))

        # ── B. 面板无「随外部状态变化」的重算残留 ──────────────
        ck.section("B. 面板无联动重算残留")
        pt = s.get("panelText") or ""
        # 说明文字里可能提到「阶梯档位」「峰谷时段」「地区」（否定语境），
        # 所以**不能**查文本里有没有这些词 —— 只能查「有没有第二处单价」
        # 与「有没有价目表」。
        ck.check("面板无档位价目表", s.get("hasTable") is False)
        ck.check("面板无档位行（tariff-tb-row）", s.get("hasTierRow") is False)
        ck.check("面板无推导解释行", s.get("hasDerive") is False)
        # ⚠️ 「元/kWh」会正常出现 2 次（标签上的单位 + 说明里的「默认单价 0.5224 元/kWh」），
        #    这**不是**「第二处单价」→ 不能拿它当断言。
        #    真正的判据是：面板里不能再出现旧价目表上的那些电价数字。
        for old in ("0.5124", "0.5364", "0.5464", "0.2535", "0.175"):
            ck.check("面板不再出现旧价目表电价 %s" % old, old not in pt,
                     pt[:160])
        ck.check("面板文本里没有表格分隔符（无价目表）",
                 "|" not in pt and "｜" not in pt, pt[:160])

        # ── C. 单一单价链路端到端 ──────────────────────────────
        ck.section("C. 单一单价链路端到端")
        NEW = 0.6188
        set_rate(c, NEW)
        c.pump(1.0)
        # 点保存
        c.js("(function(){var tb=%s;var b=tb.querySelector('[data-tariff-save]');"
             "if(b)b.click();return 'CLICKED';})()"
             % "document.querySelector('#monComputeBody [data-tariff-box]')"
             " || document.querySelector('#monComputeBody .tariff-box')")
        c.pump(2.5)

        d = api("/api/power/tariff")
        ck.check("保存后接口 rate = %s" % NEW,
                 abs((d.get("rate") or 0) - NEW) < 1e-9, repr(d.get("rate")))
        ck.check("保存后 config.rate = %s" % NEW,
                 abs(((d.get("config") or {}).get("rate") or 0) - NEW) < 1e-9,
                 repr((d.get("config") or {}).get("rate")))
        ck.check("保存后 config 仍只有 rate + updated",
                 set((d.get("config") or {}).keys()) == {"rate", "updated"},
                 sorted((d.get("config") or {}).keys()))
        ck.check("保存后 config 无任何废弃键",
                 not any(k in (d.get("config") or {})
                         for k in ("region", "voltage", "enabled",
                                   "valley_start", "valley_end",
                                   "rate_override", "proj_rate")),
                 sorted((d.get("config") or {}).keys()))

        st = api("/api/power/stats")
        ck.check("stats.tariff_rate 跟随新单价",
                 abs((st.get("tariff_rate") or 0) - NEW) < 1e-9,
                 repr(st.get("tariff_rate")))
        ck.check("stats.rate_yuan_per_kwh 跟随新单价",
                 abs((st.get("rate_yuan_per_kwh") or 0) - NEW) < 1e-9,
                 repr(st.get("rate_yuan_per_kwh")))
        ck.check("stats.eff_rate_yuan_per_kwh 跟随新单价",
                 abs((st.get("eff_rate_yuan_per_kwh") or 0) - NEW) < 1e-9,
                 repr(st.get("eff_rate_yuan_per_kwh")))
        ck.check("stats.tariff_mode 恒为 fixed",
                 st.get("tariff_mode") == "fixed", repr(st.get("tariff_mode")))

        mk = st.get("month_kwh") or 0.0
        my = st.get("month_yuan") or 0.0
        if mk > 1e-6:
            ck.check("月电费 = 月电量 × 新单价",
                     abs(my - mk * NEW) < 0.02,
                     "月电费=%.4f 期望=%.4f" % (my, mk * NEW))
        else:
            print("  （本月无用电采样，跳过电费恒等式校验）")

        # 面板应回显新单价
        s2 = snap_panel(c)
        ck.check("面板单价框回显 %s" % NEW,
                 abs(float(s2.get("rate") or 0) - NEW) < 1e-9,
                 repr(s2.get("rate")))

        # ── D. 复原 ────────────────────────────────────────────
        ck.section("D. 改回默认单价后完全复原")
        set_rate(c, orig_rate if orig_rate is not None else 0.5224)
        c.pump(1.0)
        c.js("(function(){var tb=%s;var b=tb.querySelector('[data-tariff-save]');"
             "if(b)b.click();return 'CLICKED';})()"
             % "document.querySelector('#monComputeBody [data-tariff-box]')"
             " || document.querySelector('#monComputeBody .tariff-box')")
        c.pump(2.5)
        d2 = api("/api/power/tariff")
        ck.check("复原后接口 rate = %s" % (orig_rate if orig_rate is not None else 0.5224),
                 abs((d2.get("rate") or 0) - (orig_rate or 0.5224)) < 1e-9,
                 repr(d2.get("rate")))

        # 重载后仍是原值（说明确实落盘）
        reload_and_open(c)
        s3 = snap_panel(c)
        ck.check("重载页面后单价仍为 %s" % (orig_rate if orig_rate is not None else 0.5224),
                 abs(float(s3.get("rate") or 0) - (orig_rate or 0.5224)) < 1e-9,
                 repr(s3.get("rate")))

        # ── E. 页面健康 ────────────────────────────────────────
        ck.section("E. 页面健康")
        errs = [e for e in c.events
                if e.get("method") == "Runtime.exceptionThrown"]
        ck.check("无 JS 异常", len(errs) == 0, "%d 个" % len(errs))

    finally:
        try:
            payload = {"rate": orig_rate if orig_rate is not None else 0.5224}
            api("/api/power/tariff", "POST", payload)
            print("\n已还原原配置:", json.dumps(payload, ensure_ascii=False))
        except Exception as e:
            print("\n⚠ 还原配置失败:", e)

    ck.finish()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
