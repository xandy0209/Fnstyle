#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证「固定单价地区不显示档位价目表」相关的后端改动（v1.1.58）

背景：用户反馈「都固定单价了为什么还有档位信息？」——
      flat 地区的 tiers_* 全是 [None, None, None]，前端据此渲染出
      「第1档 0–null」的三行表。修法：profile 增加 has_tiers 标记，
      前端据此整块隐藏价目表。

这里验证后端契约：
  · PROFILES 三个地区都有 has_tiers 字段
  · flat → has_tiers=False；sichuan / tianjin → True
  · profile_list() / describe() 都把 has_tiers 透出给前端
  · effective_profile() 不会把 has_tiers 弄丢
  · flat 的档位表数据本身仍然是「不限」（后端算费用）
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
    # ---------- ① PROFILES 里 has_tiers 字段齐全 ----------
    for key in ("sichuan", "tianjin", "flat"):
        ok("PROFILES[%s] 有 has_tiers 字段" % key,
           "has_tiers" in pt.PROFILES[key])

    # ---------- ② 取值正确 ----------
    ok("sichuan.has_tiers 为 True", pt.PROFILES["sichuan"]["has_tiers"] is True)
    ok("tianjin.has_tiers 为 True", pt.PROFILES["tianjin"]["has_tiers"] is True)
    ok("flat.has_tiers 为 False", pt.PROFILES["flat"]["has_tiers"] is False)

    # ---------- ③ 语义一致性：有阶梯的地区档位不能全是 None ----------
    for key in ("sichuan", "tianjin"):
        p = pt.PROFILES[key]
        tiers = p["tiers_summer"] + p["tiers_other"]
        ok("%s 声明 has_tiers=True 且档位有实际值" % key,
           p["has_tiers"] is True and any(t is not None for t in tiers))
    p = pt.PROFILES["flat"]
    ok("flat 声明 has_tiers=False 且档位全为 None",
       p["has_tiers"] is False and all(t is None for t in p["tiers_summer"]))

    # ---------- ④ profile_list() 透出 has_tiers ----------
    lst = pt.profile_list()
    ids = [x["id"] for x in lst]
    ok("profile_list 含三个地区", ids == ["sichuan", "tianjin", "flat"], str(ids))
    by_id = {x["id"]: x for x in lst}
    ok("profile_list flat.has_tiers=False",
       by_id["flat"].get("has_tiers") is False)
    ok("profile_list sichuan.has_tiers=True",
       by_id["sichuan"].get("has_tiers") is True)
    ok("profile_list tianjin.has_tiers=True",
       by_id["tianjin"].get("has_tiers") is True)

    # ---------- ⑤ describe() 透出 has_tiers ----------
    tmp = tempfile.mkdtemp()
    old_dir = os.environ.get("FNSTYLE_DATA_DIR")
    os.environ["FNSTYLE_DATA_DIR"] = tmp
    try:
        d = pt.describe()
        ok("describe.profile.has_tiers 存在", "has_tiers" in d["profile"])
        ok("describe.profile 默认地区(sichuan) has_tiers=True",
           d["profile"].get("has_tiers") is True)
        ok("describe.profiles 里 flat.has_tiers=False",
           {x["id"]: x for x in d["profiles"]}["flat"].get("has_tiers") is False)
    finally:
        if old_dir is None:
            os.environ.pop("FNSTYLE_DATA_DIR", None)
        else:
            os.environ["FNSTYLE_DATA_DIR"] = old_dir

    # ---------- ⑥ effective_profile 不丢 has_tiers ----------
    ep = pt.effective_profile("flat", {})
    ok("effective_profile(flat) 保留 has_tiers=False",
       ep.get("has_tiers") is False)
    ep2 = pt.effective_profile("sichuan", {"overrides": {"sichuan": {
        "wet_months": [6, 7, 8, 9, 10, 11]}}})
    ok("effective_profile 覆盖季节参数后仍保留 has_tiers=True",
       ep2.get("has_tiers") is True)
    ok("effective_profile 覆盖后 wet_months 生效",
       ep2["wet_months"] == [6, 7, 8, 9, 10, 11])

    # ---------- ⑦ flat 地区计算电费仍按固定单价（无阶梯加价） ----------
    tmp2 = tempfile.mkdtemp()
    old_dir = os.environ.get("FNSTYLE_DATA_DIR")
    os.environ["FNSTYLE_DATA_DIR"] = tmp2
    try:
        pt.save_config({"enabled": True, "region": "flat",
                        "rate_override": None, "valley_start": 23,
                        "valley_end": 7, "overrides": {}})
        b = pt.compute_bill(1.0, 0.0)
        # flat 三档单价都是 0.6 → 1kWh 应为 0.6 元，无加价
        ok("flat 地区 1kWh 电费 = 0.6（无阶梯加价）",
           abs(b["yuan"] - 0.6) < 1e-6, "实际 %.6f" % b["yuan"])
        ok("flat 地区不分时（has_time_of_use=False）",
           b["has_time_of_use"] is False)
    finally:
        if old_dir is None:
            os.environ.pop("FNSTYLE_DATA_DIR", None)
        else:
            os.environ["FNSTYLE_DATA_DIR"] = old_dir

    # ---------- ⑧ 前端契约：flat 的档位上限全是 None ----------
    # 前端 tariffTableHtml 用 bandLabel 拼档位，None 会渲染成「不限」
    # —— 这正是 bug 现象（第1档 0–null / 第2档 不限）。
    ok("flat.tiers_summer 全 None（前端会渲染成 'null'，故必须隐藏）",
       all(t is None for t in pt.PROFILES["flat"]["tiers_summer"]))

    print("=" * 60)
    print("verify_tariff_flat: PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  [FAIL] %s" % f)
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
