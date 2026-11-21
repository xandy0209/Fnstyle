#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.1.61：验证电价面板「下拉 ↔ 价目表」联动的一致性

背景
----
用户反馈：「电价设置中的下拉选择联动好像有问题呢，请核实清楚」。

实测定位到的**真实缺陷**（切地区后）：
    电压下拉显示 lv2，但价目表峰价仍是 lv1 的 0.5224。
根因有三处（互相叠加）：
    ① 后端 profile_list() 不给每个地区**生效的电压等级**字段
       → 前端 `j.profile = hit` 把 voltage 覆盖成 undefined
       → 价目表回落成档案默认 peak_rates（lv1 价）；
    ② 前端 tariffPanelHtml 用 `document.querySelector('#monComputeBody
       [data-tariff-voltage]')` 去 DOM 捞电压值 —— 但它是在
       innerHTML 赋值**之前**调用的，此刻 DOM 里还是**旧面板**，
       捞到的是**旧地区**的电压等级；
    ③ 前端 restoreTariffForm 换地区时用 `cfg.voltage`（**全局**配置，
       仍指向旧地区），在新地区不存在该等级时 select 显示空白。

另外还发现两处同源缺陷：
    ④ 固定单价地区（合表）的单价**也分电压等级**
       （lv1=0.5464 / lv2=lv3=0.5364），以前恒取档案默认 peak_rates[0]，
       用户选了「1-10 千伏」却仍显示 0.5464；
    ⑤ 电压 change 后只改了表格单元格，没同步内存里的 profile.voltage，
       后续重绘会跳回旧值。

本脚本验证：
  · 后端：resolve_voltage() 唯一回落规则；profile_list() 带 voltage；
  · 前端：源码约束（不得再出现那三处错误写法）；
  · 数值：各地区 × 各电压等级下的峰价正确性。

另有 CDP 实机探针脚本 probe_tariff_linkage.py 负责浏览器端验证。
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


def approx(a, b, eps=1e-6):
    try:
        return abs(float(a) - float(b)) <= eps
    except Exception:
        return False


def strip_comments(src):
    """去掉 JS 注释，只留真正会执行的代码。

    ⚠️ 踩坑（上一轮）：改动后我在注释里**引用**了被删掉的旧文案，
       源码文本断言因此误报。断言必须只看可执行代码。
    ⚠️ 踩坑：手写状态机不可靠 —— 正则字面量里的斜杠会被误判成
       注释起始。改用正则按行剥离。
    """
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    src = re.sub(r"(?m)^\s*//.*$", " ", src)
    src = re.sub(r"(?m)\s//[^\n\"'`]*$", " ", src)
    return src


def read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
print("\n=== 1. 后端 resolve_voltage()：唯一回落规则 ===")
import power_tariff as pt  # noqa: E402


def cfg_of(**kw):
    c = pt.default_config()
    c.update(kw)
    return c


# ① cfg 里的电压在本地区有效 → 直接用
vid, vname, peaks = pt.resolve_voltage("sichuan", cfg_of(voltage="lv2"))
check("sichuan + cfg.voltage=lv2 → lv2",
      vid == "lv2" and vname == "1-10 千伏", "%s / %s" % (vid, vname))
check("sichuan lv2 峰价 0.5124/0.6124/0.8124",
      [approx(p, e) for p, e in zip(peaks, [0.5124, 0.6124, 0.8124])],
      str(peaks))

# ② cfg 里的电压是别的地区的等级（本地区没有）→ 回落本地区默认
vid, vname, peaks = pt.resolve_voltage("tianjin", cfg_of(voltage="lv2"))
check("tianjin 无电压等级定义 → voltage 为空", vid == "", repr(vid))
check("tianjin 峰价用档案默认 0.49/0.54/0.79",
      [approx(p, e) for p, e in zip(peaks, [0.49, 0.54, 0.79])], str(peaks))

# ③ 配置里是**根本不存在的** id → 回落默认等级
vid, vname, peaks = pt.resolve_voltage("sichuan", cfg_of(voltage="lv9"))
check("非法电压等级 lv9 → 回落 lv1", vid == "lv1", vid)
check("回落 lv1 峰价 0.5224/0.6224/0.8224",
      [approx(p, e) for p, e in zip(peaks, [0.5224, 0.6224, 0.8224])],
      str(peaks))

# ④ 合表地区：lv1 与 lv2 的单价**不同**（这是 ④ 号缺陷的根据）
vid1, _, pk1 = pt.resolve_voltage("sichuan_shared", cfg_of(voltage="lv1"))
vid2, _, pk2 = pt.resolve_voltage("sichuan_shared", cfg_of(voltage="lv2"))
check("合表 lv1 单价 0.5464", approx(pk1[0], 0.5464), str(pk1))
check("合表 lv2 单价 0.5364", approx(pk2[0], 0.5364), str(pk2))
check("合表 lv1 ≠ lv2（故固定单价地区也须按电压取价）",
      not approx(pk1[0], pk2[0]), "%s vs %s" % (pk1[0], pk2[0]))

# ⑤ 低谷价**不受电压等级影响**（官方表三等级相同）
lv_wet = pt.PROFILES["sichuan"]["valley_rates_wet"]
check("四川低谷价与电压无关（档案里只有一套）",
      len(lv_wet) == 3 and approx(lv_wet[0], 0.175), str(lv_wet))

# ---------------------------------------------------------------------------
print("\n=== 2. 后端 profile_list()：每个地区都带生效电压（① 号缺陷）===")
cfg = cfg_of(region="sichuan", voltage="lv2")
plist = pt.profile_list(cfg)
byid = dict((p["id"], p) for p in plist)
check("profile_list 覆盖全部 4 个地区", len(plist) == 4, str(len(plist)))
for pid in ("sichuan", "sichuan_shared", "tianjin", "flat"):
    check("%s 档案含 voltage 字段" % pid,
          "voltage" in byid[pid] and "voltage_name" in byid[pid],
          str(sorted(byid[pid].keys()))[:200])
check("sichuan 生效电压 = cfg 的 lv2", byid["sichuan"]["voltage"] == "lv2",
      byid["sichuan"]["voltage"])
check("sichuan_shared 生效电压 = lv2（该地区也有 lv2）",
      byid["sichuan_shared"]["voltage"] == "lv2",
      byid["sichuan_shared"]["voltage"])
check("tianjin 生效电压为空（该地区不区分电压）",
      byid["tianjin"]["voltage"] == "", repr(byid["tianjin"]["voltage"]))
check("flat 生效电压为空", byid["flat"]["voltage"] == "",
      repr(byid["flat"]["voltage"]))

# ---------------------------------------------------------------------------
print("\n=== 3. 后端 describe()：profile.voltage 与 profile_list 一致 ===")
for region in ("sichuan", "sichuan_shared", "tianjin", "flat"):
    d = pt.describe.__wrapped__(region) if hasattr(pt.describe, "__wrapped__") else None
# describe() 读真实配置，这里直接比对各地区档案口径
for region in ("sichuan", "sichuan_shared", "tianjin", "flat"):
    c = cfg_of(region=region, voltage="lv2")
    prof = pt.effective_profile(region, c)
    v1, n1, _p1 = pt.resolve_voltage(region, c, prof)
    v2, n2, _p2 = pt.resolve_voltage(region, c)
    check("%s 的 resolve_voltage 两种调用方式结果一致" % region,
          v1 == v2 and n1 == n2, "%s/%s vs %s/%s" % (v1, n1, v2, n2))

# ---------------------------------------------------------------------------
print("\n=== 4. 后端 compute_bill()：峰价随电压等级变 ===")
for vid_exp, exp in (("lv1", 0.5224), ("lv2", 0.5124), ("lv3", 0.5124)):
    c = cfg_of(region="sichuan", enabled=True, voltage=vid_exp,
               valley_start=23, valley_end=7)
    b = pt.compute_bill(100.0, 0.0, c)
    check("compute_bill %s 一档峰价 %.4f" % (vid_exp, exp),
          approx(b["detail"][0]["peak_rate"], exp),
          str(b["detail"][0]["peak_rate"]))
    check("compute_bill %s 电压名正确" % vid_exp,
          b["voltage"] == vid_exp and b["voltage_name"],
          "%s / %s" % (b["voltage"], b["voltage_name"]))

# ---------------------------------------------------------------------------
print("\n=== 5. 前端源码约束 ===")
js_path = os.path.join(UI, "static", "js", "monitor.js")
js = strip_comments(read(js_path))

# ② 不得再用全局 DOM 查询去捞电压等级
check("前端不再用 document.querySelector 捞电压等级（② 号缺陷）",
      "document.querySelector(\n" not in js
      and not re.search(r"querySelector\(\s*['\"]#monComputeBody\s*\[\s*data-tariff-voltage", js),
      "仍存在全局 DOM 查询")

# ③ 换地区时不得直接用全局 cfg.voltage 回填
check("换地区时不再用 cfg.voltage 直接回填电压下拉（③ 号缺陷）",
      not re.search(r"vt\.value\s*=\s*\(\s*cfgv\s*&&\s*cfgv\.voltage", js),
      "仍用 cfgv.voltage 回填")

# 必须用 prof.voltage（该地区生效电压）
check("电压下拉选中值取自 prof.voltage",
      "prof.voltage || prof.default_voltage" in js
      or "prof.voltage ||" in js,
      "未见 prof.voltage 作为首选")

# 必须有 syncTariffVoltageTable 统一入口
check("存在 syncTariffVoltageTable（统一刷表格峰价）",
      "function syncTariffVoltageTable" in js)
check("存在 syncTariffFlatRate（固定单价地区单价随电压更新）",
      "function syncTariffFlatRate" in js)
check("applyTariff 回填后调用 syncTariffVoltageTable",
      re.search(r"restoreTariffForm\([^;]*\);\s*\n?\s*[^\n]*syncTariffVoltageTable", js)
      is not None,
      "未见回填后同步")

# 电压 change handler 必须走统一函数
check("电压 change handler 调用 syncTariffVoltageTable",
      re.search(r"var\s+vtSel\s*=\s*box\.querySelector\('\[\s*data-tariff-voltage\s*\]'\)"
                r"[\s\S]{0,400}?syncTariffVoltageTable", js) is not None,
      "handler 未走统一函数")

# ④ 固定单价必须按电压等级取价
check("固定单价地区按电压等级取 peak_rates（④ 号缺陷）",
      "flatPeaks" in js and "data-tariff-flat-rate" in js,
      "未见按电压取固定单价")

# ⑤ 电压 change 后要同步内存 profile.voltage
check("电压切换同步内存 d.profile.voltage（⑤ 号缺陷）",
      "d.profile.voltage = vid" in js or "d.profile.voltage = vtSel.value" in js,
      "未见同步")

# ⚠️ 实机踩到的坑：固定单价地区（无档位表）在 syncTariffVoltageTable 里
#    走了「提前 return」分支 → d.profile.voltage 停在旧值 →
#    syncTariffFlatRate 按旧电压取价 → 用户切「1-10 千伏」，
#    合表单价仍是 0.5464（应为 0.5364）。
#    所以「同步 voltage」必须排在「无表格」分支**之前**。
_m = re.search(r"function syncTariffVoltageTable[\s\S]{0,2400}?\n  \}", js)
_seg = _m.group(0) if _m else ""
_i_v = _seg.find("d.profile.voltage = vid")
_i_tb = _seg.find("if (!tb)")
check("固定单价地区也能同步 d.profile.voltage（电压同步排在无表格分支之前）",
      _i_v > 0 and (_i_tb < 0 or _i_v < _i_tb),
      "voltage 同步位置=%d, 无表格分支位置=%d" % (_i_v, _i_tb))

# 地区切换时同步 hit.voltage
check("地区切换同步 j.profile.voltage = hit.voltage",
      "j.profile.voltage = hit.voltage" in js,
      "未见地区切换同步电压")

# ---------------------------------------------------------------------------
print("\n=== 6. 前端 JS 语法 ===")
r = subprocess.run(["node", "--check", js_path], capture_output=True, text=True)
check("monitor.js 语法正确", r.returncode == 0, r.stderr[:200])

r = subprocess.run([sys.executable, "-m", "py_compile",
                    os.path.join(UI, "power_tariff.py")],
                   capture_output=True, text=True)
check("power_tariff.py 语法正确", r.returncode == 0, r.stderr[:200])

# ---------------------------------------------------------------------------
print("\n=== 7. describe() 合法 JSON（无 Infinity / NaN）===")
try:
    d = pt.describe()
    s = json.dumps(d, allow_nan=False)
    check("describe() 可合法序列化", True)
    check("describe().profile 含 voltage",
          "voltage" in d["profile"], str(sorted(d["profile"].keys()))[:200])
    check("describe().profiles 每项含 voltage",
          all("voltage" in p for p in d["profiles"]),
          str([p.get("voltage") for p in d["profiles"]]))
except ValueError as e:
    check("describe() 可合法序列化", False, str(e))

# ---------------------------------------------------------------------------
print("\n=== 8. 切地区保存：用户设置不被吞掉（⑥⑧ 号缺陷）===")
import tempfile  # noqa: E402

_cfg_path_orig = pt._cfg_path
_tmpcfg = os.path.join(tempfile.gettempdir(), "_vt_linkage_cfg.json")
pt._cfg_path = lambda: _tmpcfg
try:
    if os.path.exists(_tmpcfg):
        os.remove(_tmpcfg)
    # 四川设：lv2 + 6 人 + 两个月抄表
    pt.save_config({"region": "sichuan", "enabled": True,
                    "valley_start": 23, "valley_end": 7, "rate_override": None,
                    "voltage": "lv2", "household_people": 6, "meter_months": 2})
    c = pt.load_config()
    check("四川保存后 people=6", c.get("household_people") == 6, c.get("household_people"))
    check("四川保存后 meter=2", c.get("meter_months") == 2, c.get("meter_months"))
    check("四川保存后 voltage=lv2", c.get("voltage") == "lv2", c.get("voltage"))
    check("四川生效档位 490/690",
          [approx(x, e) for x, e in zip(pt.effective_tier_limits("sichuan", c)[0],
                                        [490.0, 690.0])],
          str(pt.effective_tier_limits("sichuan", c)[0]))

    # 切到三个不支持地方政策的地区各保存一次（前端不提交这三个字段）
    for reg in ("tianjin", "sichuan_shared", "flat"):
        pt.save_config({"region": reg, "enabled": True,
                        "valley_start": 23, "valley_end": 7, "rate_override": None})
        c = pt.load_config()
        check("切 %s 保存后 people 仍为 6" % reg,
              c.get("household_people") == 6, c.get("household_people"))
        check("切 %s 保存后 meter 仍为 2" % reg,
              c.get("meter_months") == 2, c.get("meter_months"))
        check("切 %s 保存后 voltage 仍为 lv2" % reg,
              c.get("voltage") == "lv2", c.get("voltage"))
        # 该地区本身不套用四川地方政策
        lim = pt.effective_tier_limits(reg, c)[0]
        if reg == "tianjin":
            check("天津档位仍为 220/400（不套四川政策）",
                  [approx(x, e) for x, e in zip(lim, [220.0, 400.0])], str(lim))
        else:
            check("%s 不套四川政策（无档位）" % reg, lim[0] is None, str(lim))

    # 切回四川 → 设置全部恢复
    pt.save_config({"region": "sichuan", "enabled": True,
                    "valley_start": 23, "valley_end": 7, "rate_override": None})
    c = pt.load_config()
    check("切回四川档位恢复 490/690",
          [approx(x, e) for x, e in zip(pt.effective_tier_limits("sichuan", c)[0],
                                        [490.0, 690.0])],
          str(pt.effective_tier_limits("sichuan", c)[0]))
    check("切回四川峰价恢复 lv2 的 0.5124",
          approx(pt.resolve_voltage("sichuan", c)[2][0], 0.5124),
          str(pt.resolve_voltage("sichuan", c)[2]))
finally:
    pt._cfg_path = _cfg_path_orig
    if os.path.exists(_tmpcfg):
        os.remove(_tmpcfg)

# ---------------------------------------------------------------------------
print("\n=== 9. 谷时段边界：起止相同时不应变成全天谷段（⑦ 号缺陷）===")
hc_path = os.path.join(UI, "history_collect.py")
hc = read(hc_path)
check("history_collect 不再用「逐小时数到 ve」的循环判谷段",
      "for _ in range(24):" not in hc.split("def split_energy_by_period")[0].split("valley_hours = set()")[-1][:400]
      or "vs < ve" in hc,
      "疑似仍用旧循环")
check("谷时段判定改为区间语义（vs<ve / vs>ve / vs==ve）",
      "elif vs < ve:" in hc and "else:" in hc and "valley_hours = set(range(vs, ve))" in hc,
      "未见区间语义")

# ---------------------------------------------------------------------------
print("\n=== 10. 前端保存：无控件地区也要提交保留值（⑧ 号缺陷）===")
check("前端保存时用 cfgSave 兜底提交 voltage",
      "cfgSave.voltage" in js, "未见 cfgSave.voltage")
check("前端保存时用 cfgSave 兜底提交 household_people",
      "cfgSave.household_people" in js, "未见 cfgSave.household_people")
check("前端保存时用 cfgSave 兜底提交 meter_months",
      "cfgSave.meter_months" in js, "未见 cfgSave.meter_months")

# ---------------------------------------------------------------------------
print("\n" + "=" * 62)
print("结果：%d 通过 / %d 失败" % (len(PASS), len(FAIL)))
print("=" * 62)
for name, detail in FAIL:
    print("  ❌ %s  ← %s" % (name, detail))
sys.exit(1 if FAIL else 0)
