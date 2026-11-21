#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ---------------------------------------------------------------------------
# FnStyle —— 电价模型（居民阶梯电价 + 分时电价）
#
# 背景：
#   FnStyle 原来的电费算法只有一个标量「每 kWh 单价」（硬编码 0.6），
#   与国内居民电费的真实计费方式不符。国内居民电费通常是
#   **阶梯电价**（用电量越多，超出部分单价越高，且阶梯按月/年清算）
#   叠加 **分时电价**（谷段优惠、峰段平价），
#   两者相乘才是国网账单上的真实金额。
#
# 本模块的定位：
#   1. 提供可内置的地区电价表（目前含四川省居民一户一表）；
#   2. 提供用户可覆盖的配置（谷时段、单价微调）；
#   3. 用 FnStyle 自己 60 秒一采的功率历史，把电量拆成「谷段/平段」，
#      再按阶梯规则算出电费 —— 与国网账单口径一致。
#
# 四川口径的关键规则（依据川发改价格〔2026〕255号 等）：
#   · 一户一表、不满 1kV，高峰/平段 7:00-23:00 执行阶梯电价
#   · 低谷 23:00-次日 7:00 执行居民低谷优惠电价
#   · 阶梯加价按**平段电量**计算；低谷电量计入阶梯总电量但不加价
#   · 阶梯电量按**月**清算：夏季(7-9月) 0-260/261-460/460+；
#     其他月 0-180/181-280/280+
#   · 低谷电价随丰枯水期变动：丰水期(6-10月) 低于枯平期(11-5月)
#   · 自 2027 年 1 月起第一/二档电量逐月结转，��末清零
#
# 免责：本模块给出的是**估算值**。国网账单是按计量表读数结算的，
#       FnStyle 用的是 NAS 自身功耗推算，两者必然有偏差（线路损耗、
#       计量点不同、NAS 之外的负载不计入等）。界面需明示这是估算。
# ---------------------------------------------------------------------------

import json
import os
import time

# ---------------------------------------------------------------------------
# 配置存储
# ---------------------------------------------------------------------------

def _data_dir():
    """配置目录（与 history_collect._data_dir 同一套解析规则）。"""
    for env in ("FNSTYLE_DATA_DIR", "TRIM_PKGVAR"):
        p = os.environ.get(env)
        if p and os.path.isdir(p):
            return p
    for cand in ("/var/apps/fnstyle/var", "/usr/local/apps/@appcenter/fnstyle/var",
                 os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "var")):
        try:
            if os.path.isdir(cand):
                return os.path.abspath(cand)
        except Exception:
            continue
    return "/tmp"


def _cfg_path():
    return os.path.join(_data_dir(), "power_tariff.json")


# ---------------------------------------------------------------------------
# 内置地区电价表
#
# 字段说明：
#   region        地区标识
#   name          显示名
#   tiers         阶梯：[{upto_kwh, rate}, ...] 最后一档 upto=None 表示无上限
#                 按**平段电量**判定档位（四川口径）
#   summer_tiers  夏季档位（可选）；None 表示全年同一套
#   peak_rates    峰/平段各档单价 [一档, 二档, 三档]
#   valley_rates  低谷段各档单价 [一档, 二档, 三档]
#   peak_hours    峰/平段时段区间列表 [[起, 止), ...]（小时整数，24 小时制）
#   valley_hours  低谷时段区间列表
#   summer_months 夏季月份集合（用于阶梯档位切换）
#   wet_months    丰水期月份集合（用于低谷电价切换）
# ---------------------------------------------------------------------------

PROFILES = {
    "sichuan": {
        "name": "四川省居民（成都）",
        "unit": "元/kWh",
        "note": "一户一表。高峰/平段 7:00-23:00，低谷 23:00-次日 7:00。"
                "档位按**月总电量**判定。",
        # 峰/平段阶梯单价（三档）—— 默认「不满 1kV」
        "peak_rates": [0.5224, 0.6224, 0.8224],
        # 低谷优惠单价（丰水期 / 枯平期 各三档）—— 三个电压等级相同
        "valley_rates_wet": [0.1750, 0.2750, 0.4750],
        "valley_rates_dry": [0.2535, 0.3535, 0.5535],
        # 阶梯电量上限（kWh，按月）：夏季 0-260/261-460/460+；其他月 0-180/181-280/280+
        "tiers_summer": [260.0, 460.0, None],
        "tiers_other": [180.0, 280.0, None],
        "summer_months": [7, 8, 9],
        # 丰水期（6-10月）执行更低的低谷价；其余月份（1-5月、11-12月）为枯平期
        "wet_months": [6, 7, 8, 9, 10],
        "peak_hours": [[7, 23]],
        "valley_hours": [[23, 7]],
        # 执行居民低谷优惠电价（分时）
        "has_time_of_use": True,
        # 执行阶梯电价
        "has_tiers": True,
        # 2027年1月起一/二档**未使用完的**电量逐月结转
        "carry_over_since": "2027-01",
        # ⚠️ v1.1.59：电压等级（官方表列为「电度电价（元/千瓦时）」的三列）
        #    每个等级给一套高峰/平段三档价；低谷价三档**不受电压等级影响**
        #    （官方表里三个电压等级的低谷价完全相同）。
        "voltage_levels": [
            {"id": "lv1", "name": "不满 1 千伏",
             "peak_rates": [0.5224, 0.6224, 0.8224]},
            {"id": "lv2", "name": "1-10 千伏",
             "peak_rates": [0.5124, 0.6124, 0.8124]},
            {"id": "lv3", "name": "35-110 千伏",
             "peak_rates": [0.5124, 0.6124, 0.8124]},
        ],
        # 默认电压等级
        "default_voltage": "lv1",
    },
    "sichuan_shared": {
        "name": "四川合表居民（多户共用计量表）",
        "unit": "元/kWh",
        "note": "合表居民生活用电按**单一电价**计费，"
                "不执行阶梯电价，也不执行分时（无低谷优惠）。",
        # 合表电价：不满 1kV 0.5464 / 1-10kV 与 35-110kV 0.5364
        "peak_rates": [0.5464, 0.5464, 0.5464],
        "valley_rates_wet": [0.5464, 0.5464, 0.5464],
        "valley_rates_dry": [0.5464, 0.5464, 0.5464],
        "tiers_summer": [None, None, None],
        "tiers_other": [None, None, None],
        "summer_months": [],
        "wet_months": [],
        "peak_hours": [[0, 24]],
        "valley_hours": [],
        # 合表不分时、不分阶梯
        "has_time_of_use": False,
        "has_tiers": False,
        "carry_over_since": None,
        "voltage_levels": [
            {"id": "lv1", "name": "不满 1 千伏",
             "peak_rates": [0.5464, 0.5464, 0.5464]},
            {"id": "lv2", "name": "1-10 千伏",
             "peak_rates": [0.5364, 0.5364, 0.5364]},
            {"id": "lv3", "name": "35-110 千伏",
             "peak_rates": [0.5364, 0.5364, 0.5364]},
        ],
        "default_voltage": "lv1",
    },
    "tianjin": {
        "name": "天津市居民",
        "unit": "元/kWh",
        "note": "居民按**年**周期阶梯（0-220/221-400/400+），"
                "居民**不执行**峰谷分时电价 —— 所有时段同价。",
        "peak_rates": [0.4900, 0.5400, 0.7900],
        "valley_rates_wet": [0.4900, 0.5400, 0.7900],
        "valley_rates_dry": [0.4900, 0.5400, 0.7900],
        "tiers_summer": [220.0, 400.0, None],
        "tiers_other": [220.0, 400.0, None],
        "summer_months": [],
        "wet_months": [],
        "peak_hours": [[0, 24]],
        "valley_hours": [],
        # 居民不执行峰谷分时 ⇒ 无谷段概念
        "has_time_of_use": False,
        # 执行阶梯电价（按年周期）
        "has_tiers": True,
        "carry_over_since": None,
    },
    "flat": {
        "name": "固定单价（不分时不分阶梯）",
        "unit": "元/kWh",
        "note": "按单一固定单价计费，不区分时段与阶梯。",
        "peak_rates": [0.6000, 0.6000, 0.6000],
        "valley_rates_wet": [0.6000, 0.6000, 0.6000],
        "valley_rates_dry": [0.6000, 0.6000, 0.6000],
        "tiers_summer": [None, None, None],
        "tiers_other": [None, None, None],
        "summer_months": [],
        "wet_months": [],
        "peak_hours": [[0, 24]],
        "valley_hours": [],
        "has_time_of_use": False,
        # ⚠️ v1.1.58：固定单价**没有阶梯概念** —— 加这个标记，
        #    前端据此把整张档位价目表隐藏掉（否则会显示「第1档 0–null」）。
        "has_tiers": False,
        "carry_over_since": None,
    },
}

DEFAULT_REGION = "sichuan"

# 未启用阶梯电价时使用的「单一均价」默认值。
# ⚠️ 这是 FnStyle 历史遗留的固定值（fnmonitor 的 power_rate_yuan 默认也是 0.6），
#    保持不变以免用户未启用时看到的历史数据发生跳变。
DEFAULT_FIXED_RATE = 0.6

# ---------------------------------------------------------------------------
# 「预计电费单价」默认值（v1.1.62）
#
# 用途：「本月预计电费」格子的估算单价。
# ⚠️ 老板决定：**不按阶梯算、不区分地区**，统一用成都（四川）最低档
#    的第一档峰价，再让用户按自己的实际电价手动改。
#
# 为什么不用均价倒推：
#   旧算法是「预计电量 × 本月实际均价」，而均价是**已经发生的电费**除以
#   **已发生的电量**得到的 —— 把它乘到「预计的更大电量」上，隐含假设
#   「剩下的电量和已用的电价结构完全一样」。可一旦预计电量跨过档位门槛
#   （比如本月已用 170 度 + 预计 30 度 = 200 度跨过 180 进入第二档），
#   均价倒推就完全不准了，而且数字不会随档位跳变。
#
# 为什么默认值取成都最低档：
#   成都一户一表居民不满 1 千伏第一档高峰平段电价 = 0.5224 元/kWh。
#   估算是粗口径，用**偏低**的一档价可以避免把估算值抬得过高。
# ---------------------------------------------------------------------------
DEFAULT_PROJ_RATE = 0.5224

# ---------------------------------------------------------------------------
# ⚠️ v1.1.62：「一户多人口」阶梯电量加成 —— **已移除**
#
# 依据：川发改价格〔2024〕668号《关于居民阶梯电价"一户多人口"政策
#       有关事项的通知》（2024-12-31 发布，2025-01-01 起执行）
#   「实行居民阶梯电价且一户家庭人口满5人的家庭，可申请每户每月
#     增加100度阶梯电量基数。超出5人的家庭，阶梯电量基数按
#     30度/人·月递增。」
#
# 移除原因（v1.1.62，老板决定）：
#   ① 这项政策**需要用户主动向供电企业申报**（凭户口簿/居住证，
#      在国网营业厅或网上办理），并**自申报成功的次月起执行**，
#      有效期原则上不超过两年 → 光在软件里填个数字**不代表生效**，
#      填了却没申报 = 门槛凭空多出 100 度 → 电费算高；
#   ② 自动抄表时代，用户根本无法确认自己是否已申报成功；
#   ③ 保留两个选项框反而让用户误以为「填了就生效」。
#
# 所以这里**彻底删掉**该输入项与相关加成逻辑 —— 需要加成的用户
# 直接把自家档位门槛按供电企业批件改到「季节参数」里更可控。
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 可被用户覆盖的季节参数
#
# 四川（成都）电价有两个季节维度：
#   · 丰水期 / 枯平期 → 决定**低谷价**（丰水期更便宜）
#   · 夏季 / 非夏季   → 决定**阶梯档位电量**（夏季一档更宽）
# 这两套都是按月份自动切换的，用户一般不用改；但：
#   · 地方电网的丰枯水期划分可能与国网四川不同；
#   · 政策调整后档位会变（如 2026 年 7 月夏季档扩到 260 度）。
# 所以把它们做成**可覆盖项**：默认取内置值，用户可按当地口径或最新政策调整。
#
# 覆盖值按地区分别存放（cfg["overrides"][region]），切地区不会互相污染。
# ---------------------------------------------------------------------------

OVERRIDABLE_FIELDS = (
    "wet_months",       # 丰水期月份（1-12）
    "summer_months",    # 夏季月份（1-12）
    "valley_rates_wet", # 丰水期低谷价 [一档, 二档, 三档]
    "valley_rates_dry", # 枯平期低谷价
    "tiers_summer",     # 夏季阶梯档位累计上限
    "tiers_other",      # 非夏季阶梯档位累计上限
)

# 月份集合的合法取值
_MONTHS = set(range(1, 13))


def _normalize_months(v):
    """把月份集合规范成升序列表；非法值返回 None。"""
    if not isinstance(v, (list, tuple)):
        return None
    out = []
    for x in v:
        try:
            m = int(x)
        except Exception:
            return None
        if m not in _MONTHS:
            return None
        if m not in out:
            out.append(m)
    return sorted(out)


def _normalize_rates(v, n=3):
    """把电价数组规范成 n 个非负浮点；非法值返回 None。"""
    if not isinstance(v, (list, tuple)) or len(v) != n:
        return None
    out = []
    for x in v:
        try:
            f = float(x)
        except Exception:
            return None
        if f < 0:
            return None
        out.append(round(f, 6))
    return out


def _normalize_tiers(v, n=3):
    """把阶梯档位规范成 n 个「累计上限」；None 表示不限。非法值返回 None。"""
    if not isinstance(v, (list, tuple)) or len(v) != n:
        return None
    out = []
    prev = None
    for x in v:
        if x is None:
            out.append(None)
            continue
        try:
            f = float(x)
        except Exception:
            return None
        if f < 0:
            return None
        if prev is not None and f <= prev:
            # 累计上限必须严格递增，否则档位无意义
            return None
        prev = f
        out.append(f)
    return out


def normalize_overrides(raw):
    """校验并规范化某个地区的季节覆盖值。返回 (ok, message, normalized)。"""
    if raw is None:
        return True, "", {}
    if not isinstance(raw, dict):
        return False, "季节参数格式错误", {}
    out = {}
    if "wet_months" in raw:
        m = _normalize_months(raw["wet_months"])
        if m is None:
            return False, "丰水期月份必须是 1-12 的整数列表", {}
        out["wet_months"] = m
    if "summer_months" in raw:
        m = _normalize_months(raw["summer_months"])
        if m is None:
            return False, "夏季月份必须是 1-12 的整数列表", {}
        out["summer_months"] = m
    if "valley_rates_wet" in raw:
        r = _normalize_rates(raw["valley_rates_wet"])
        if r is None:
            return False, "丰水期低谷价必须是 3 个非负数", {}
        out["valley_rates_wet"] = r
    if "valley_rates_dry" in raw:
        r = _normalize_rates(raw["valley_rates_dry"])
        if r is None:
            return False, "枯平期低谷价必须是 3 个非负数", {}
        out["valley_rates_dry"] = r
    if "tiers_summer" in raw:
        t = _normalize_tiers(raw["tiers_summer"])
        if t is None:
            return False, "夏季阶梯档位必须是 3 个递增的电量上限（末档可留空）", {}
        out["tiers_summer"] = t
    if "tiers_other" in raw:
        t = _normalize_tiers(raw["tiers_other"])
        if t is None:
            return False, "非夏季阶梯档位必须是 3 个递增的电量上限（末档可留空）", {}
        out["tiers_other"] = t
    return True, "", out


def effective_profile(region, cfg=None):
    """取某地区的**实际生效**配置 = 内置默认值 + 用户覆盖值。

    返回一个新的 dict（不改动 PROFILES）。
    """
    prof = dict(PROFILES.get(region) or PROFILES["flat"])
    if cfg:
        ov = (cfg.get("overrides") or {}).get(region) or {}
        for k, v in ov.items():
            if k in OVERRIDABLE_FIELDS and v is not None:
                prof[k] = v
    # 拷贝可变字段，避免污染 PROFILES
    for k in ("wet_months", "summer_months", "valley_rates_wet",
              "valley_rates_dry", "tiers_summer", "tiers_other"):
        if isinstance(prof.get(k), list):
            prof[k] = list(prof[k])
    return prof


def default_config():
    """默认配置（未配置时的兜底）。"""
    return {
        "region": DEFAULT_REGION,
        "enabled": False,        # 未开启动用前，一律沿用「单一固定单价」口径
        "valley_start": 23,
        "valley_end": 7,
        "rate_override": None,   # 用户手工指定的单一单价（覆盖一切）
        # 电压等级（不满1kV / 1-10kV / 35-110kV），见各 profile 的 voltage_levels
        "voltage": "",
        # 「本月预计电费」的估算单价（元/kWh）
        # ⚠️ v1.1.62：不按阶梯算、不区分地区，默认取成都最低档一档价
        #    （0.5224），用户可按自己的实际电价改。
        "proj_rate": DEFAULT_PROJ_RATE,
        # 各地区的季节参数覆盖（丰枯水期 / 夏季档位），见 OVERRIDABLE_FIELDS
        "overrides": {},
        "updated": 0,
    }


def load_config():
    """读取电价配置；文件不存在或损坏时返回默认配置。"""
    cfg = default_config()
    p = _cfg_path()
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            cfg.update(data)
    except Exception:
        pass
    if cfg.get("region") not in PROFILES:
        cfg["region"] = DEFAULT_REGION
    # ⚠️ v1.1.62：主动丢弃 v1.1.61 之前遗留的这两个已废弃键。
    #    save_config() 也会丢，但那只在「用户保存过」之后才生效；
    #    而 describe() 里的 "config": cfg 会**原样透出**读到的键，
    #    → 升版后若用户没打开过电价面板，/api/power/tariff 仍会带出
    #      household_people / meter_months，看着像功能还在。
    #    在读取层就丢，保证「读出来就是干净的」。
    cfg.pop("household_people", None)
    cfg.pop("meter_months", None)
    return cfg


def save_config(cfg):
    """写入电价配置。返回 (ok, message)。"""
    if not isinstance(cfg, dict):
        return False, "配置格式错误"
    region = cfg.get("region")
    if region not in PROFILES:
        return False, "未知的地区标识：%s" % region
    # 谷时段合法性：起止小时必须是 0-23 的整数
    for k in ("valley_start", "valley_end"):
        try:
            v = int(cfg.get(k))
        except Exception:
            return False, "%s 必须是整数" % k
        if not (0 <= v <= 23):
            return False, "%s 必须在 0-23 之间" % k
    cfg["region"] = region
    cfg["valley_start"] = int(cfg["valley_start"])
    cfg["valley_end"] = int(cfg["valley_end"])
    # 电压等级（v1.1.59）：必须是该地区 `voltage_levels` 里存在的一个 id；
    # 地区没有电压等级定义时忽略该字段。
    #
    # ⚠️ v1.1.61 修复（真实缺陷）：电压等级 id（lv1/lv2/lv3）是
    #    **跨地区通用**的（四川与合表的 levels 完全一致），
    #    原来这段逻辑在「前端没提交 voltage」时直接把它**删掉**或
    #    **回落成默认值**：
    #      · 天津/固定单价（无 levels）→ cfg.pop("voltage")
    #        ⇒ 用户在四川选了「1-10 千伏」，切到天津点一次保存，
    #          再切回四川电压就变回「不满 1 千伏」—— **设置被吞掉**；
    #      · 合表（有 levels）→ 回落成 lv1，同样吞掉设置。
    #    现在改为：**先继承旧配置里的值，再按新地区的 levels 校验**，
    #    只有确实非法时才回落。
    try:
        old_cfg = load_config()
    except Exception:
        old_cfg = {}
    old_voltage = old_cfg.get("voltage") or ""
    if not cfg.get("voltage"):
        cfg["voltage"] = old_voltage
    levels = (PROFILES.get(region) or {}).get("voltage_levels") or []
    if levels:
        vid = cfg.get("voltage")
        valid = [lv.get("id") for lv in levels if lv.get("id")]
        if vid not in valid:
            vid = (PROFILES[region].get("default_voltage")
                   or (valid[0] if valid else "")
                   or "")
        cfg["voltage"] = vid
    else:
        # 该地区不区分电压等级（天津 / 固定单价）
        # → 保留原值（切回有 levels 的地区时还会用到），不删。
        cfg["voltage"] = cfg.get("voltage") or old_voltage or ""

    # ⚠️ v1.1.62：「一户多人口」与「抄表周期」两个配置项**已删除**。
    #   · 「一户多人口」（川发改价格〔2024〕668号）需**主动申报**才生效，
    #     软件里填个数字不代表供电企业已批准 → 留着反而误导；
    #   · 「抄表周期」（川发改价格〔2012〕560号）针对两个月抄表的用户，
    #     现在电表基本都是自动抄表（远程采集），该场景已不适用。
    #   这里**主动丢弃**旧配置里残留的这两个键（升版后第一次保存时
    #   自动清理干净），档位门槛回到官方基准值。
    cfg.pop("household_people", None)
    cfg.pop("meter_months", None)

    # 「本月预计电费」的估算单价（v1.1.62）
    # 不按阶梯算、不区分地区 → 一个全局数值，用户可按自己的实际电价改。
    # 前端没提交时继承旧配置（与电压等级同样的处理，避免被清零）。
    if cfg.get("proj_rate") in (None, ""):
        cfg["proj_rate"] = old_cfg.get("proj_rate")
        if cfg["proj_rate"] in (None, ""):
            cfg["proj_rate"] = DEFAULT_PROJ_RATE
    try:
        pr = float(cfg.get("proj_rate"))
    except Exception:
        return False, "预计电费单价必须是数字"
    if pr < 0 or pr > 10:
        return False, "预计电费单价必须在 0-10 之间"
    cfg["proj_rate"] = round(pr, 6)

    cfg["updated"] = int(time.time())
    ro = cfg.get("rate_override")
    if ro is not None:
        try:
            ro = float(ro)
            if ro < 0:
                raise ValueError
            cfg["rate_override"] = round(ro, 6)
        except Exception:
            cfg["rate_override"] = None
    # 季节参数覆盖：按地区分别存储。
    # ⚠️ 前端一次只提交**当前地区**的覆盖值，所以必须与已有配置**合并**，
    #    否则保存一次就把其他地区的自定义值抹掉。
    existing = {}
    try:
        old = load_config().get("overrides")
        if isinstance(old, dict):
            existing = dict(old)
    except Exception:
        existing = {}
    ov_all = cfg.get("overrides")
    if ov_all is None:
        ov_all = {}
    if not isinstance(ov_all, dict):
        return False, "季节参数格式错误"
    merged = dict(existing)
    for reg, raw in ov_all.items():
        if reg not in PROFILES:
            continue
        ok, msg, norm = normalize_overrides(raw)
        if not ok:
            return False, "「%s」%s" % (PROFILES[reg]["name"], msg)
        if norm:
            merged[reg] = norm
        else:
            merged.pop(reg, None)
    cfg["overrides"] = merged
    p = _cfg_path()
    try:
        d = os.path.dirname(p)
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
        return True, "电价配置已保存"
    except Exception as e:
        return False, "写入失败：%s" % e


# ---------------------------------------------------------------------------
# 阶梯月度台账
#
# 四川口径自 2027 年 1 月起，第一/二档电量**逐月结转**（年末清零）。
# 这意味着「本月用掉多少度」不能只看当月，还要把上月结转的余量算进来。
# 这里把台账存在 data-share 下，按月一个文件，简单可靠。
# ---------------------------------------------------------------------------

def _ledger_path(month_key):
    return os.path.join(_data_dir(), "power_tier_ledger_%s.json" % month_key)


def load_ledger(month_key):
    """读取某月的阶梯台账。

    ⚠️ v1.1.59：台账存的是**未使用完的剩余额度**（官方注4 口径），
    不是已用量。字段名同步改为 tier1_remaining / tier2_remaining。
    兼容旧字段（tier1_used / tier2_used）：旧值语义是「已用」，
    不能直接当剩余额度用 → 读取时**忽略**旧字段。
    """
    try:
        with open(_ledger_path(month_key), "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return {"month": month_key, "tier1_remaining": 0.0,
            "tier2_remaining": 0.0, "updated": 0}


def save_ledger(month_key, tier1_remaining, tier2_remaining):
    """写入某月的阶梯台账（记录**未使用完的剩余额度**，供下月结转用）。
    ⚠️ v1.1.59 语义修正：官方注4 —— 「执行居民阶梯电价的用户
    **未使用完的**第一档、第二档电量可逐月滚动结转至年内后续月份
    对应阶梯档位，年末清零」。
    之前这里记的是本月**已用**量 —— 方向反了。
    """
    d = {
        "month": month_key,
        "tier1_remaining": round(float(tier1_remaining or 0.0), 4),
        "tier2_remaining": round(float(tier2_remaining or 0.0), 4),
        "updated": int(time.time()),
    }
    try:
        p = _ledger_path(month_key)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
        return True
    except Exception:
        return False


def _month_key(ts=None):
    lt = time.localtime(ts if ts is not None else time.time())
    return "%04d-%02d" % (lt.tm_year, lt.tm_mon)


def _prev_month_key(mk):
    """给定月份 key（'YYYY-MM'），返回上一个月的 key。"""
    try:
        y = int(mk[:4])
        m = int(mk[5:7])
    except Exception:
        return ""
    m -= 1
    if m <= 0:
        y -= 1
        m = 12
    return "%04d-%02d" % (y, m)


def _effective_caps(tiers, carry1=0.0, carry2=0.0):
    """算出**本月实际生效**的每档增量容量。

    ⚠️ v1.1.59 关键：结转额度必须加在**增量容量**上，
       不能加在累计上限上 —— 否则一档上限会超过二档上限，
       `_tier_caps` 里 max(0, up - prev) 会把二档增量压成 0。

    ⚠️ v1.1.62：原先的 `extra1`（一户多人口加成）参数已删除。

    这个模型等价于「年度额度 + 月度结算」：
      年度一档额度 = 12 × 180 = 2160 度
      年度二档额度 = 12 × 100 = 1200 度
    例：每月固定用 150 度 → 12 个月后一档剩余正好 2160 − 1800 = 360 度，
        与年度额度严格对齐（已由 verify_tariff_official_pdf.py 数值验证）。
    """
    caps = _tier_caps(tiers)
    if carry1:
        caps[0] = caps[0] + max(0.0, float(carry1))
    if carry2 and len(caps) > 1:
        caps[1] = caps[1] + max(0.0, float(carry2))
    return caps


def carry_in_for(month_key, prof):
    """取某月可用的**结转额度**（来自**上个月**的台账）。

    ⚠️ v1.1.59 修复（致命缺陷）：原来读的是**当月**台账，
       而台账写的是「本月未用完的额度」→ 跨月结转永远读到 0，
       结转功能形同虚设（实测 carry_in 恒为 0）。
       现在改为读**上个月**的台账。

    官方注4：「未使用完的第一档、第二档电量可逐月滚动结转至
    **年内**后续月份对应阶梯档位，**年末清零**」
      → 跨年（12月 → 次年1月）的结转一律作废。
    """
    since = prof.get("carry_over_since")
    if not since:
        return 0.0, 0.0
    if month_key < since:
        return 0.0, 0.0
    pmk = _prev_month_key(month_key)
    if not pmk or pmk < since:
        return 0.0, 0.0
    # 年末清零：跨年的结转不生效
    if pmk[:4] != month_key[:4]:
        return 0.0, 0.0
    led = load_ledger(pmk)
    return (float(led.get("tier1_remaining") or 0.0),
            float(led.get("tier2_remaining") or 0.0))


def update_tier_ledger(flat_kwh, valley_kwh, ts=None):
    """把本月**未使用完的阶梯电量**写进台账（供下月结转用）。

    ⚠️ v1.1.59 重写：原来这个函数在 history_collect.py 里另写一份，
       与 compute_bill 的分档口径**不一致**（它没算结转、也没算人口加成），
       导致台账数据自相矛盾、同月内重复调用还会自我污染。
       现在统一放到本模块，与 compute_bill 共用同一套分档逻辑，
       保证「算出来的账」与「记下来的账」严格一致。

    官方注4：台账记的是**未使用完的剩余额度**（不是已用量）。
    """
    try:
        cfg = load_config()
        if not cfg.get("enabled"):
            return False
        region = cfg.get("region") or DEFAULT_REGION
        prof = effective_profile(region, cfg)
        since = prof.get("carry_over_since")
        if not since:
            return False
        mk = _month_key(ts)
        if mk < since:
            return False

        lt = time.localtime(ts if ts is not None else time.time())
        month = lt.tm_mon
        tiers = (prof["tiers_summer"] if month in (prof.get("summer_months") or [])
                 else prof["tiers_other"])
        carry1, carry2 = carry_in_for(mk, prof)
        caps = _effective_caps(tiers, carry1, carry2)

        # ⚠️ 档位按**月总电量**判定（官方口径，v1.1.59）
        flat = max(0.0, float(flat_kwh or 0.0))
        valley = max(0.0, float(valley_kwh or 0.0))
        total = flat + valley
        used = [0.0, 0.0, 0.0]
        left = total
        for i, cap in enumerate(caps):
            if cap <= 0:
                continue
            a = min(left, cap)
            used[i] += a
            left -= a
            if left <= 1e-12:
                break
        # 剩余 = 生效容量 − 已用（未用完的电量滚入下月）
        remain1 = max(0.0, caps[0] - used[0])
        remain2 = max(0.0, caps[1] - used[1]) if len(caps) > 1 else 0.0
        save_ledger(mk, remain1, remain2)
        return True
    except Exception:
        return False


def resolve_voltage(region, cfg=None, prof=None):
    """解析某地区**当前生效**的电压等级。

    ⚠️ v1.1.61：以前这段逻辑在 compute_bill / describe / profile_list
       里各写了一遍，三处的**回落规则不一致**：
         · compute_bill 找不到匹配就回落到 levels[0]
         · describe  找不到匹配就**保持 cfg 里的原值**（可能是个
           在本地区不存在的 id）
         · profile_list 干脆**不给**这个字段
       结果：前端切地区后，电压下拉与价目表峰价对不上
       （下拉显示 lv2，表格却是 lv1 的 0.5224）。

       现在统一到这一个函数，三处共用，**回落规则唯一**：
         1. cfg 里的 voltage 在本地区 levels 里存在 → 用它
         2. 否则用本地区 default_voltage
         3. 再否则用 levels[0]
         4. 本地区没有 levels（天津/固定单价）→ 返回空

    返回 (voltage_id, voltage_name, peak_rates)：
      voltage_id     生效的电压等级 id（无则 ""）
      voltage_name   该等级的显示名（无则 ""）
      peak_rates     该等级对应的峰/平三档价；无等级定义时取档案默认
    """
    cfg = cfg if cfg is not None else load_config()
    if prof is None:
        prof = effective_profile(region, cfg)
    levels = prof.get("voltage_levels") or []
    default_peaks = list(prof.get("peak_rates") or [])
    if not levels:
        # 本地区不区分电压等级（天津 / 固定单价）
        return "", "", default_peaks

    vid = cfg.get("voltage")
    hit = None
    for lv in levels:
        if lv.get("id") == vid:
            hit = lv
            break
    if hit is None:
        # cfg 里的值在本地区无效 → 落到该地区默认等级
        dv = prof.get("default_voltage")
        for lv in levels:
            if lv.get("id") == dv:
                hit = lv
                break
    if hit is None:
        hit = levels[0]
    out_id = hit.get("id") or ""
    out_name = hit.get("name") or ""
    out_peaks = list(hit.get("peak_rates") or default_peaks)
    return out_id, out_name, out_peaks


# ---------------------------------------------------------------------------
# 核心：时段判定与电费计算
# ---------------------------------------------------------------------------

def _in_hours(hour, ranges):
    """判断小时是否落在区间列表内。区间为 [[start, end), ...]。

    支持跨零点（如 [23, 7] 表示 23:00-次日 7:00）。
    """
    for rng in ranges or []:
        if not rng or len(rng) < 2:
            continue
        s = int(rng[0]) % 24
        e = int(rng[1]) % 24
        if s == e:
            # 0-24 全天
            if s == 0:
                return True
            continue
        if s < e:
            if s <= hour < e:
                return True
        else:
            if hour >= s or hour < e:
                return True
    return False


def _tier_index(kwh, tiers):
    """按阶梯容量判定档位索引（0/1/2）。

    tiers 形如 [180, 280, None]（每档容量，None = 无上限）；
    返回该电量落在哪一档。
    """
    left = max(0.0, float(kwh or 0.0))
    for i in range(3):
        up = tiers[i] if len(tiers) > i else None
        if up is None:
            return i
        if left < float(up):
            return i
        left -= float(up)
    return 2


def _tier_caps(tiers):
    """把「累计上限」形式的阶梯表转成「每档增量容量」。

    tiers = [180, 280, None] 表示 0-180 / 181-280 / 280+
    → 每档容量 = 180 / 100 / ∞
    """
    caps = []
    prev = 0.0
    for i in range(3):
        up = tiers[i] if len(tiers) > i else None
        if up is None:
            caps.append(float("inf"))
            break
        up = float(up)
        caps.append(max(0.0, up - prev))
        prev = up
    while len(caps) < 3:
        caps.append(0.0)
    return caps


def _split_kwh_by_tier(flat_kwh, valley_kwh, tiers, carry1=0.0, carry2=0.0):
    """把电量按阶梯拆成三档的电量。

    ⚠️ v1.1.59 修正档位判定口径（依据《四川省电网居民生活电价表》
       2026-07-01 版原文：「月用电量在 260 千瓦时及以下部分 /
       261-460 千瓦时部分 / 460 千瓦时以上部分」）：

       官方表里的档位门槛写的是「**月用电量**」——
       即**月总电量（峰 + 平 + 谷）**，不是只算平段。

       之前这里按**平段电量**判档，是错的。
       验证（宜宾叙州区政府官网账单算例 2025-02）：
           峰 100 + 平 150 + 谷 150 = 400 度 → 账单 214.625 元
           · 官方口径（按总电量判档）重算 = 214.625 元，误差 0
           · 旧代码（按平段判档）重算 = 116.385 元，误差 -45.8%
       → 官方口径成立，必须按总电量判档。

       口径细化：
         · 档位归属由**月总电量**决定（峰+平+谷一起算）；
         · 每一档内部，峰/平/谷各自的电量按该档的**电量占比**分摊，
           分别套用该档的峰/平单价与该档的低谷单价；
         · 低谷电量同样占用高档位容量（官方「月用电量」含谷段）。

    参数：
      flat_kwh    平段（峰/平）电量 kWh
      valley_kwh  低谷电量 kWh
      tiers       累计上限形式：[180, 280, None] 表示 0-180 / 181-280 / 280+
      carry1      一档结转额度（加到一档**增量容量**上）
      carry2      二档结转额度（加到二档**增量容量**上）

    ⚠️ 结转必须加在**增量容量**上，不能加在累计上限上 ——
       否则一档扩容后可能超过二档累计上限，破坏阶梯的单调性，
       导致二档容量被压成 0（实测 bug）。

    返回 (t1, t2, t3, split)：
      · t1/t2/t3  三档电量，单位 kWh，三档之和 = 总电量；
      · split     三档的 (平段电量, 谷段电量) 分配明细。
    """
    flat = max(0.0, float(flat_kwh or 0.0))
    valley = max(0.0, float(valley_kwh or 0.0))
    total = flat + valley
    if total <= 1e-12:
        return 0.0, 0.0, 0.0, [(0.0, 0.0), (0.0, 0.0), (0.0, 0.0)]

    caps = _effective_caps(tiers, carry1, carry2)

    # ① 按**总电量**切三档
    out = [0.0, 0.0, 0.0]
    left = total
    for i, cap in enumerate(caps):
        if cap <= 0:
            continue
        a = min(left, cap)
        out[i] += a
        left -= a
        if left <= 1e-12:
            break

    # ② 每档内部：平段/谷段按该档电量占比分摊
    #    档 i 的电量 = tier_kwh[i]
    #    平段占比 = flat / total，谷段占比 = valley / total
    flat_share = flat / total
    valley_share = valley / total
    split = []
    for i in range(3):
        t = out[i]
        split.append((t * flat_share, t * valley_share))

    return out[0], out[1], out[2], split


def compute_bill(flat_kwh, valley_kwh, cfg=None, ts=None):
    """按配置口径计算电费。

    参数：
      flat_kwh    平段（峰/平）电量 kWh
      valley_kwh  低谷电量 kWh
      cfg         电价配置（None 时读文件）
      ts          计算基准时间戳（用于判断季节/丰枯），默认当前时间

    返回：
      {
        "yuan": 总电费,
        "flat_kwh", "valley_kwh",
        "flat_yuan", "valley_yuan",
        "tier_kwh": [一档, 二档, 三档],
        "tier_rates": [一档单价, 二档单价, 三档单价],
        "eff_rate": 综合单价（元/kWh）,
        "mode": "fixed" | "tiered",
        "region", "region_name",
        "valley_label": 谷时段描述,
        "detail": 逐档明细（供前端展示）
      }
    """
    cfg = cfg or load_config()
    region = cfg.get("region") or DEFAULT_REGION
    prof = effective_profile(region, cfg)
    lt = time.localtime(ts if ts is not None else time.time())
    month = lt.tm_mon

    flat_kwh = max(0.0, float(flat_kwh or 0.0))
    valley_kwh = max(0.0, float(valley_kwh or 0.0))
    total = flat_kwh + valley_kwh

    # ---- 电压等级（v1.1.59）----
    # 官方表给了三列：不满1千伏 / 1-10千伏 / 35-110千伏。
    # 高峰平段价按电压等级不同；**低谷价三档三等级完全相同**。
    # ⚠️ v1.1.61：统一走 resolve_voltage（唯一回落规则），
    #    不再就地解析 —— 以前这里的回落规则与 describe/profile_list
    #    不一致，导致前端切地区后下拉与表格峰价对不上。
    voltage, voltage_name, peak_rates = resolve_voltage(region, cfg, prof)

    # ---- 未启用：沿用单一固定单价 ----
    # ⚠️ 未启用时**不套用地区的阶梯价**，而是用「单一均价」——
    #   因为用户没确认自己属于哪一档、也没确认是不是分时表用户。
    #   这里的 0.6 是历史遗留的默认值，保持不变以免改变既有显示。
    if not cfg.get("enabled"):
        rate = cfg.get("rate_override")
        if rate is None:
            rate = DEFAULT_FIXED_RATE
        rate = float(rate)
        yuan = total * rate
        return {
            "yuan": round(yuan, 4),
            "flat_kwh": round(flat_kwh, 4),
            "valley_kwh": round(valley_kwh, 4),
            "flat_yuan": round(flat_kwh * rate, 4),
            "valley_yuan": round(valley_kwh * rate, 4),
            "tier_kwh": [round(total, 4), 0.0, 0.0],
            "tier_rates": [round(rate, 4), 0.0, 0.0],
            "eff_rate": round(rate, 4),
            "mode": "fixed",
            "region": region,
            "region_name": prof["name"],
            "has_time_of_use": bool(prof.get("has_time_of_use", True)),
            "valley_label": "",
            "detail": [],
        }

    # ---- 用户手工指定单一单价：覆盖一切 ----
    ro = cfg.get("rate_override")
    if ro is not None and float(ro) > 0:
        rate = float(ro)
        yuan = total * rate
        return {
            "yuan": round(yuan, 4),
            "flat_kwh": round(flat_kwh, 4),
            "valley_kwh": round(valley_kwh, 4),
            "flat_yuan": round(flat_kwh * rate, 4),
            "valley_yuan": round(valley_kwh * rate, 4),
            "tier_kwh": [round(total, 4), 0.0, 0.0],
            "tier_rates": [round(rate, 4), 0.0, 0.0],
            "eff_rate": round(rate, 4),
            "mode": "fixed",
            "region": region,
            "region_name": prof["name"],
            "has_time_of_use": bool(prof.get("has_time_of_use", True)),
            "valley_label": "",
            "detail": [],
        }

    # ---- 阶梯 + 分时 ----
    has_tou = prof.get("has_time_of_use", True)
    has_tiers = prof.get("has_tiers", True)
    if not has_tou:
        # 该地区居民不执行峰谷分时（如天津）→ 所有时段同价，
        # 谷段电量按平段价计费，不享受任何优惠。
        valley_kwh = 0.0
        flat_kwh = total

    if not has_tiers:
        # ⚠️ v1.1.59：合表居民 / 固定单价 —— **不执行阶梯电价**，
        #    全部电量按该地区的第一档（单一）电价计费。
        #    合表：0.5464（不满1kV）/ 0.5364（其他等级），不分时不分阶梯。
        rate = peak_rates[0] if peak_rates else DEFAULT_FIXED_RATE
        rate = float(rate)
        yuan = total * rate
        return {
            "yuan": round(yuan, 4),
            "flat_kwh": round(flat_kwh, 4),
            "valley_kwh": round(valley_kwh, 4),
            "flat_yuan": round(flat_kwh * rate, 4),
            "valley_yuan": round(valley_kwh * rate, 4),
            "tier_kwh": [round(total, 4), 0.0, 0.0],
            "tier_rates": [round(rate, 4), 0.0, 0.0],
            "eff_rate": round(rate, 4),
            "mode": "fixed",
            "region": region,
            "region_name": prof["name"],
            "has_time_of_use": bool(has_tou),
            "valley_label": "",
            "detail": [],
            "carry_in": 0.0,
            "voltage": voltage,
            "voltage_name": voltage_name,
        }

    tiers = prof["tiers_summer"] if month in (prof.get("summer_months") or []) \
        else prof["tiers_other"]
    # peak_rates 已在上面按**电压等级**解析过了，这里不要覆盖
    valley_rates = list(prof["valley_rates_wet"] if month in (prof.get("wet_months") or [])
                         else prof["valley_rates_dry"])

    # 阶梯电量结转（自 carry_over_since 起，**未使用完的**一/二档余量逐月结转）
    # ⚠️ v1.1.59 官方注4：未使用完的第一档、第二档电量可逐月滚动结转至
    #    **年内**后续月份对应阶梯档位，**年末清零**。
    #    台账里存的应该是**剩余额度**，不是已用量。
    #
    # 结算方式（国网实际执行口径）：
    #   结转额度**叠加到本月对应档位的可用容量**上，
    #   即 本月一档可用量 = 一档上限 + 上月一档剩余
    #      本月二档可用量 = 二档上限 + 上月二档剩余
    #   本月实际用电按这个「扩容后」的阶梯判档、计价。
    #   （结转额度本身不在本月产生电量，不重复计费。）
    # ⚠️ v1.1.59 修复（致命缺陷）：结转额度必须读**上个月**的台账。
    #    原来读的是当月台账 → 跨月结转永远读到 0，功能形同虚设。
    mk = _month_key(ts)
    carry1, carry2 = carry_in_for(mk, prof)
    carry = carry1 + carry2

    # ⚠️ v1.1.62：「一户多人口」加成与「抄表周期 ×2」**已移除**
    #   （见文件上方说明：前者需主动申报才生效，后者是两个月抄表场景，
    #    自动抄表时代已不适用）。档位门槛只受**跨月结转**影响。

    # 扩容后的阶梯：结转额度叠加到对应档位的**增量容量**上
    # ⚠️ v1.1.59：档位门槛按**月总电量**判定（官方表原文口径）。
    t1, t2, t3, split = _split_kwh_by_tier(
        flat_kwh, valley_kwh, tiers, carry1, carry2)
    tier_kwh = [t1, t2, t3]

    # 逐档计价（每档内的平段/谷段按电量占比分摊到该档单价）
    yuan = 0.0
    flat_yuan = 0.0
    valley_yuan = 0.0
    detail = []
    for i in range(3):
        t_kwh = tier_kwh[i]
        flat_part, valley_part = split[i]
        d_yuan = flat_part * peak_rates[i] + valley_part * valley_rates[i]
        yuan += d_yuan
        flat_yuan += flat_part * peak_rates[i]
        valley_yuan += valley_part * valley_rates[i]
        detail.append({
            "tier": i + 1,
            "kwh": round(t_kwh, 4),
            "flat_kwh": round(flat_part, 4),
            "valley_kwh": round(valley_part, 4),
            "peak_rate": peak_rates[i],
            "valley_rate": valley_rates[i],
            "yuan": round(d_yuan, 4),
        })

    eff = (yuan / total) if total > 0 else 0.0
    vs = int(cfg.get("valley_start", 23)) % 24
    ve = int(cfg.get("valley_end", 7)) % 24

    # ⚠️ v1.1.62：当前落在第几档 + 该档区间。
    #   老板的原话：「我没有输入电量，你怎么知道是按那个阶段进行计费呢？」
    #   → 判档依据是**本机自测的月总电量**（不是用户输入的数），
    #     所以必须让用户在界面上直接看到「本月 X 度 → 第 N 档」。
    #   档位归属按**月总电量**（峰+平+谷）判定，与官方口径一致。
    cur_tier = 0
    for i, k in enumerate(tier_kwh):
        if k > 1e-9:
            cur_tier = i + 1
    tier_limits = None
    try:
        lim, _c = effective_tier_limits(region, cfg, ts)
        tier_limits = lim
    except Exception:
        tier_limits = None

    return {
        "yuan": round(yuan, 4),
        "flat_kwh": round(flat_kwh, 4),
        "valley_kwh": round(valley_kwh, 4),
        "flat_yuan": round(flat_yuan, 4),
        "valley_yuan": round(valley_yuan, 4),
        "tier_kwh": [round(v, 4) for v in tier_kwh],
        "tier_rates": peak_rates,
        # ⚠️ v1.1.62：当前判档结果（供界面显示判档依据）
        "current_tier": cur_tier,
        "tier_limits": tier_limits,
        "eff_rate": round(eff, 4),
        "mode": "tiered",
        "region": region,
        "region_name": prof["name"],
        "has_time_of_use": bool(has_tou),
        "valley_label": ("%02d:00–%02d:00" % (vs, ve)) if has_tou else "",
        "detail": detail,
        "carry_in": round(carry, 4),
        # 电压等级（v1.1.59）
        "voltage": voltage,
        "voltage_name": voltage_name,
    }


def profile_list(cfg=None):
    """给前端用的地区列表（已套用用户的季节参数覆盖）。"""
    cfg = cfg if cfg is not None else load_config()
    out = []
    for key in PROFILES:
        p = effective_profile(key, cfg)
        # ⚠️ v1.1.59：给出该地区在**当前全局配置**下的生效档位，
        #    前端切换地区时直接用这个，
        #    避免前端自己复制一份加成/结转算法（迟早会漂移）。
        try:
            eff_limits, _ = effective_tier_limits(key, cfg)
        except Exception:
            eff_limits = None
        # ⚠️ v1.1.60：推导明细也按地区给一份 ——
        #    前端切换地区时推导行要跟着变，不能残留上一个地区的。
        try:
            eff_deriv = tier_derivation(key, cfg)
        except Exception:
            eff_deriv = {}
        # ⚠️ v1.1.61 关键修复：这个地区**自己**的生效电压等级。
        #    以前 profile_list 不给这个字段 → 前端切地区时
        #    `j.profile = hit` 把 voltage 覆盖成 undefined →
        #    价目表回落到 prof.peak_rates（lv1 默认价），
        #    而电压下拉仍显示用户的 lv2 → 下拉与表格对不上。
        try:
            eff_vid, eff_vname, _eff_peaks = resolve_voltage(key, cfg, p)
        except Exception:
            eff_vid, eff_vname = "", ""
        out.append({
            "id": key,
            "name": p["name"],
            "note": p["note"],
            "unit": p["unit"],
            "peak_rates": p["peak_rates"],
            "valley_rates_wet": p["valley_rates_wet"],
            "valley_rates_dry": p["valley_rates_dry"],
            "tiers_summer": p["tiers_summer"],
            "tiers_other": p["tiers_other"],
            # 该地区当前生效的档位（含抄表周期 ×2 / 一户多人口加成 / 结转）
            "effective_tiers": eff_limits,
            # 该地区当前生效档位的推导明细（前端解释行用）
            "effective_derivation": eff_deriv,
            # ⚠️ v1.1.61：该地区生效的电压等级（前端切地区时同步下拉与峰价）
            "voltage": eff_vid,
            "voltage_name": eff_vname,
            "summer_months": p["summer_months"],
            "wet_months": p["wet_months"],
            "peak_hours": p["peak_hours"],
            "valley_hours": p["valley_hours"],
            "has_time_of_use": p.get("has_time_of_use", True),
            "has_tiers": p.get("has_tiers", True),
            "carry_over_since": p.get("carry_over_since"),
            # 电压等级（v1.1.59）
            "voltage_levels": p.get("voltage_levels") or [],
            "default_voltage": p.get("default_voltage") or "",
        })
    return out


def tier_derivation(region=None, cfg=None, ts=None):
    """算出本月生效档位的**推导明细**（前端解释行用）。

    ⚠️ v1.1.62：推导链只剩两环 —— **基准档位 + 跨月结转**。
       「抄表周期倍数」与「一户多人口加成」两项已移除（见文件上方说明）。

    返回 dict：
      base_tiers   本月对应的**基准**档位
      base_label   基准档位名称（夏季档位 / 其他月档位）
      carry1/2     跨月结转额度（度）
      limits       最终生效的累计上限 [180, 280, None]
    """
    cfg = cfg if cfg is not None else load_config()
    region = region or cfg.get("region") or DEFAULT_REGION
    prof = effective_profile(region, cfg)
    lt = time.localtime(ts if ts is not None else time.time())
    month = lt.tm_mon
    is_summer = month in (prof.get("summer_months") or [])
    base = list(prof["tiers_summer"] if is_summer else prof["tiers_other"])
    base_label = "夏季档位" if is_summer else "其他月档位"

    carry1, carry2 = carry_in_for(_month_key(ts), prof)
    limits, _caps = effective_tier_limits(region, cfg, ts)
    return {
        "base_tiers": base,
        "base_label": base_label,
        "is_summer": is_summer,
        "carry1": round(float(carry1 or 0.0), 2),
        "carry2": round(float(carry2 or 0.0), 2),
        "limits": limits,
    }


def effective_tier_limits(region=None, cfg=None, ts=None):
    """返回**本月实际生效**的阶梯累计上限（前端价目表用）。

    ⚠️ v1.1.59 修复（真实缺陷）：原来 describe() 直接把内置档位
       （如 [180, 280, None]）当成「当前生效档位」吐给前端，
       **没有反映**这三个会改变实际门槛的因素：
         · 抄表周期 2 个月 → 门槛 ×2（川发改价格〔2012〕560号）
         · 一户多人口      → 一档容量 +100/+130/... 度（668号）
         · 跨月结转        → 一/二档容量 + 上月剩余（官方注4）
       结果：用户看到的价目表（0–180 / 181–280）和**实际计费口径**
       （0–360 / 361–560 …）对不上 → 按表估电费会算错。

    返回 (tiers, caps)：
      tiers  累计上限形式 [180, 280, None]（与内置表同构）
      caps   每档**增量容量** [180, 100, inf]
    """
    cfg = cfg if cfg is not None else load_config()
    region = region or cfg.get("region") or DEFAULT_REGION
    prof = effective_profile(region, cfg)
    lt = time.localtime(ts if ts is not None else time.time())
    month = lt.tm_mon
    tiers = list(prof["tiers_summer"] if month in (prof.get("summer_months") or [])
                 else prof["tiers_other"])

    # 结转额度（读上月台账）
    # ⚠️ v1.1.62：抄表周期 ×2 与一户多人口加成已移除，
    #    现在门槛只受**跨月结转**影响。
    carry1, carry2 = carry_in_for(_month_key(ts), prof)
    caps = _effective_caps(tiers, carry1, carry2)

    # 把「增量容量」还原成「累计上限」，供前端显示区间
    limits = []
    prev = 0.0
    for i in range(3):
        c = caps[i]
        if c == float("inf"):
            limits.append(None)
            break
        prev += c
        limits.append(prev)
    while len(limits) < 3:
        limits.append(None)
    return limits, caps


def describe():
    """给前端用的当前配置描述。"""
    cfg = load_config()
    region = cfg.get("region")
    prof = effective_profile(region, cfg)
    lt = time.localtime()
    month = lt.tm_mon
    is_summer = month in (prof.get("summer_months") or [])
    is_wet = month in (prof.get("wet_months") or [])
    # 电压等级（v1.1.59）：peak_rates 按当前等级给出
    # ⚠️ v1.1.61：统一走 resolve_voltage —— 原来这里找不到匹配时
    #    会**保持 cfg 里的原值**（可能是不存在于本地区的 id），
    #    与 compute_bill 的回落规则不一致 → 前端切地区后
    #    下拉显示 lv2、表格却是 lv1 的峰价。
    voltage, voltage_name, cur_peak = resolve_voltage(region, cfg, prof)
    # ⚠️ v1.1.59 修复：active_tiers 必须是**本月实际生效**的门槛
    #    （含跨月结转），否则前端价目表与真实计费口径对不上。
    try:
        active_limits, active_caps = effective_tier_limits(region, cfg)
    except Exception:
        active_limits = (prof["tiers_summer"] if is_summer
                         else prof["tiers_other"])
        active_caps = []
    # ⚠️ v1.1.59 修复（致命）：三档容量是 float("inf")，
    #    而 **Infinity 不是合法 JSON**（json.dumps 默认写成 Infinity，
    #    浏览器 JSON.parse 会直接抛 SyntaxError）→
    #    前端 r.json() 失败 → 整个电价面板显示「读取失败」。
    #    这里统一转成 None（= 不限），与 tiers 的表示法保持一致。
    active_caps = [None if (c is None or c == float("inf")) else c
                    for c in (active_caps or [])]
    # ⚠️ v1.1.60：推导明细（本月生效门槛是怎么来的）
    #    前端据此渲染一行解释，取代原来那段重复罗列季节参数的说明文字。
    try:
        derivation = tier_derivation(region, cfg)
    except Exception:
        derivation = {}
    return {
        "config": cfg,
        "current_month": month,
        # 当前生效的档位与低谷价（前端价目表据此显示，避免把夏季档
        # 和非夏季档混在一张表里 —— 那样用户会按错的档位估电费）
        "active_tiers": active_limits,
        "active_tier_caps": active_caps,
        "active_valley_rates": (prof["valley_rates_wet"] if is_wet
                                else prof["valley_rates_dry"]),
        "is_summer_tier": is_summer,
        "is_wet_season": is_wet,
        # 推导明细：基准档位 → +跨月结转 → 生效门槛
        "tier_derivation": derivation,
        # 用户对当前地区做的季节参数覆盖（前端「季节参数」区块回填用）
        "overrides": (cfg.get("overrides") or {}).get(region) or {},
        "profile": {
            "id": region,
            "name": prof["name"],
            "note": prof["note"],
            "unit": prof["unit"],
            "peak_rates": cur_peak,
            "valley_rates_wet": prof["valley_rates_wet"],
            "valley_rates_dry": prof["valley_rates_dry"],
            "tiers_summer": prof["tiers_summer"],
            "tiers_other": prof["tiers_other"],
            "summer_months": prof["summer_months"],
            "wet_months": prof["wet_months"],
            "peak_hours": prof["peak_hours"],
            "valley_hours": prof["valley_hours"],
            "has_time_of_use": prof.get("has_time_of_use", True),
            "has_tiers": prof.get("has_tiers", True),
            "carry_over_since": prof.get("carry_over_since"),
            # 电压等级（v1.1.59）
            "voltage_levels": prof.get("voltage_levels") or [],
            "voltage": voltage,
            "voltage_name": voltage_name,
        },
        "profiles": profile_list(cfg),
    }
