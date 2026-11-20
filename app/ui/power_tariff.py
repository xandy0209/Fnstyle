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
        "note": "一户一表，不满 1kV。高峰/平段 7:00-23:00，"
                "低谷 23:00-次日 7:00。阶梯加价按平段电量计算。",
        # 峰/平段阶梯单价（三档）
        "peak_rates": [0.5224, 0.6224, 0.8224],
        # 低谷优惠单价（丰水期 / 枯平期 各三档）
        "valley_rates_wet": [0.1750, 0.2750, 0.4750],
        "valley_rates_dry": [0.2535, 0.3535, 0.5535],
        # 阶梯电量上限（kWh，按月）：夏季 0-260/261-460/460+；其他月 0-180/181-280/280+
        "tiers_summer": [260.0, 460.0, None],
        "tiers_other": [180.0, 280.0, None],
        "summer_months": [7, 8, 9],
        # 丰水期（6-10月）执行更低的低谷价
        "wet_months": [6, 7, 8, 9, 10],
        "peak_hours": [[7, 23]],
        "valley_hours": [[23, 7]],
        # 执行居民低谷优惠电价（分时）
        "has_time_of_use": True,
        # 执行阶梯电价
        "has_tiers": True,
        # 2027年1月起一/二档电量逐月结转
        "carry_over_since": "2027-01",
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
    """读取某月的阶梯台账。"""
    try:
        with open(_ledger_path(month_key), "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return {"month": month_key, "tier1_used": 0.0, "tier2_used": 0.0, "updated": 0}


def save_ledger(month_key, tier1_used, tier2_used):
    """写入某月的阶梯台账（记录截至当前的累计用量）。"""
    d = {
        "month": month_key,
        "tier1_used": round(float(tier1_used or 0.0), 4),
        "tier2_used": round(float(tier2_used or 0.0), 4),
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


def _split_kwh_by_tier(flat_kwh, valley_kwh, tiers):
    """把平段/谷段电量按阶梯拆成三档的电量。

    四川口径（川发改价格〔2026〕255号 + 国网四川电力执行细则）：
        「低谷电量计入阶梯总电量，阶梯加价按平段电量计算」
      → **档位归属只由平段电量决定**；
      → 谷段电量虽然计入阶梯总电量，但**不占用**高档位容量，
        它的加价按低谷优惠价走（远低于阶梯加价）。

    举例（11月，档位 0-180 / 181-280 / 280+）：
        平段 100 kWh + 谷段 200 kWh
          → 档位按平段 100 判定 → 一档
          → 谷段 200 kWh 也全按一档的低谷价计费（不加阶梯加价）
        平段 200 kWh + 谷段 200 kWh
          → 档位按平段 200 判定 → 一档 180 + 二档 20
          → 谷段 200 kWh 不推高档，仍按一档低谷价

    tiers 为**累计上限**形式：[180, 280, None] 表示 0-180 / 181-280 / 280+。

    返回 (t1, t2, t3) 三档电量，单位 kWh。
    """
    flat_left = max(0.0, float(flat_kwh or 0.0))
    valley_left = max(0.0, float(valley_kwh or 0.0))
    caps = _tier_caps(tiers)

    # 先按平段电量确定各档归属（阶梯加价按平段电量计算）
    out = [0.0, 0.0, 0.0]
    for i, cap in enumerate(caps):
        if cap <= 0:
            continue
        a = min(flat_left, cap)
        out[i] += a
        flat_left -= a
        if flat_left <= 1e-12:
            break

    # 谷段电量「计入阶梯总电量」：按平段确定的档位比例摊入各档，
    # 保证三档电量之和 = 总电量（国网账单的总电量口径）
    total_flat = max(0.0, float(flat_kwh or 0.0))
    if valley_left > 0 and total_flat > 0:
        flat_sum = sum(out)
        if flat_sum > 0:
            alloc = 0.0
            for i in range(3):
                share = (out[i] / flat_sum) * valley_left
                out[i] += share
                alloc += share
            # 舍入兜底：把差额补到第一档，保证总和守恒
            diff = valley_left - alloc
            if abs(diff) > 1e-12:
                out[0] += diff
    elif valley_left > 0:
        # 全是谷段电量：档位按第一档算（平段为 0，加价按平段 0 判定）
        out[0] += valley_left
    return out[0], out[1], out[2]


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
    if not has_tou:
        # 该地区居民不执行峰谷分时（如天津）→ 所有时段同价，
        # 谷段电量按平段价计费，不享受任何优惠。
        valley_kwh = 0.0
        flat_kwh = total

    tiers = prof["tiers_summer"] if month in (prof.get("summer_months") or []) \
        else prof["tiers_other"]
    peak_rates = list(prof["peak_rates"])
    valley_rates = list(prof["valley_rates_wet"] if month in (prof.get("wet_months") or [])
                         else prof["valley_rates_dry"])

    # 阶梯电量结转（自 carry_over_since 起，第一/二档余量逐月结转）
    carry = 0.0
    mk = _month_key(ts)
    since = prof.get("carry_over_since")
    if since and mk >= since:
        led = load_ledger(mk)
        carry = float(led.get("tier1_used") or 0.0) + float(led.get("tier2_used") or 0.0)

    tier_kwh = _split_kwh_by_tier(flat_kwh + carry, valley_kwh, tiers)

    # 逐档计价：
    #   每档内的平段电量按该档的峰/平段单价（阶梯价）计费；
    #   谷段电量按低谷优惠单价计费（**不加阶梯加价**）。
    yuan = 0.0
    flat_yuan = 0.0
    valley_yuan = 0.0
    detail = []
    # 每档里分到的平段电量（按 flat_left 递减切分，与 _split 逻辑一致）
    flat_alloc = []
    fl = max(0.0, float(flat_kwh or 0.0))
    for i, cap in enumerate(_tier_caps(tiers)):
        a = min(fl, cap) if cap > 0 else 0.0
        flat_alloc.append(a)
        fl -= a
    flat_used = sum(flat_alloc)

    for i in range(3):
        t_kwh = tier_kwh[i]
        flat_part = flat_alloc[i] if i < len(flat_alloc) else 0.0
        # 该档内谷段电量 = 该档总量 - 该档平段
        valley_part = max(0.0, t_kwh - flat_part)
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
    _ = flat_used

    eff = (yuan / total) if total > 0 else 0.0
    vs = int(cfg.get("valley_start", 23)) % 24
    ve = int(cfg.get("valley_end", 7)) % 24
    return {
        "yuan": round(yuan, 4),
        "flat_kwh": round(flat_kwh, 4),
        "valley_kwh": round(valley_kwh, 4),
        "flat_yuan": round(flat_yuan, 4),
        "valley_yuan": round(valley_yuan, 4),
        "tier_kwh": [round(v, 4) for v in tier_kwh],
        "tier_rates": peak_rates,
        "eff_rate": round(eff, 4),
        "mode": "tiered",
        "region": region,
        "region_name": prof["name"],
        "has_time_of_use": bool(has_tou),
        "valley_label": ("%02d:00–%02d:00" % (vs, ve)) if has_tou else "",
        "detail": detail,
        "carry_in": round(carry, 4),
    }


def profile_list(cfg=None):
    """给前端用的地区列表（已套用用户的季节参数覆盖）。"""
    out = []
    for key in PROFILES:
        p = effective_profile(key, cfg)
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
            "summer_months": p["summer_months"],
            "wet_months": p["wet_months"],
            "peak_hours": p["peak_hours"],
            "valley_hours": p["valley_hours"],
            "has_time_of_use": p.get("has_time_of_use", True),
            "has_tiers": p.get("has_tiers", True),
            "carry_over_since": p.get("carry_over_since"),
        })
    return out


def describe():
    """给前端用的当前配置描述。"""
    cfg = load_config()
    region = cfg.get("region")
    prof = effective_profile(region, cfg)
    lt = time.localtime()
    month = lt.tm_mon
    is_summer = month in (prof.get("summer_months") or [])
    is_wet = month in (prof.get("wet_months") or [])
    return {
        "config": cfg,
        "current_month": month,
        # 当前生效的档位与低谷价（前端价目表据此显示，避免把夏季档
        # 和非夏季档混在一张表里 —— 那样用户会按错的档位估电费）
        "active_tiers": prof["tiers_summer"] if is_summer else prof["tiers_other"],
        "active_valley_rates": (prof["valley_rates_wet"] if is_wet
                                else prof["valley_rates_dry"]),
        "is_summer_tier": is_summer,
        "is_wet_season": is_wet,
        # 用户对当前地区做的季节参数覆盖（前端「季节参数」区块回填用）
        "overrides": (cfg.get("overrides") or {}).get(region) or {},
        "profile": {
            "id": region,
            "name": prof["name"],
            "note": prof["note"],
            "unit": prof["unit"],
            "peak_rates": prof["peak_rates"],
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
        },
        "profiles": profile_list(cfg),
    }
