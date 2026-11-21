#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ---------------------------------------------------------------------------
# FnStyle —— 电价模型（单一可配置单价）
#
# 历史背景（v1.1.59 ~ v1.1.62）
#   原来这里实现的是国内居民电费的完整模型：
#     · 阶梯电价（四川 0-180 / 181-280 / 280+，夏季档更宽）
#     · 分时电价（峰平段 7:00-23:00 / 低谷段 23:00-次日 7:00，丰枯水期不同价）
#     · 跨月结转（四川 2027-01 起一/二档未用完电量逐月结转）
#     · 多地区档案（四川一户一表 / 四川合表 / 天津 / 固定单价）
#     · 电压等级（不满1千伏 / 1-10千伏 / 35-110千伏）
#     · 季节参数覆盖（丰水期月份 / 夏季月份 / 各档电价与档位上限）
#
# v1.1.63 起（老板决定）：**全部简化**
#   「电价的计费方式调整为：不使用阶梯电价计费方式，根据成都最低档对应的
#     电价进行计算（并提供可配置能力），删除阶梯计费和档位信息等，
#     也不区分区域，就是一个电费单价的配置，并增加对应的说明即可。」
#
#   → 模型退化为：**总电量 × 单一单价**
#   → 默认单价 = 成都（四川）一户一表居民不满 1 千伏**第一档高峰平段**
#     0.5224 元/kWh —— 这正是「成都最低档对应的电价」。
#   → 不分地区、不分时段、不分档位、不分电压等级；
#     用户可按自己家的实际电价手动改这一个数。
#
# 为什么简化
#   ① 阶梯/分时/结转这套模型要吃**准确的分时电量**与**跨月台账**，
#      而 FnStyle 用的是 NAS 自身功耗推算，误差本来就大；
#      按真实账单口径精算一坨不确定的数，反而制造「很准」的错觉。
#   ② 界面复杂度与收益不匹配：地区/电压/谷段/季节/档位五组控件，
#      用户填完还是看不懂电费怎么来的。
#   ③ 老板要的是「一个能算账的单价」，不是「一个电价政策模拟器」。
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
# 默认单价
# ---------------------------------------------------------------------------

# 成都（四川）一户一表居民不满 1 千伏**第一档高峰平段**电价。
# 这是国网四川居民电价的**最低档** —— 用最低档做统一估算，
# 不会把估算电费抬得过高；用户可按自己家的实际电价改。
DEFAULT_RATE = 0.5224

# 单价合法区间（元/kWh）。上限放宽到 10，覆盖合表/高电压等级等偏高的情况。
_RATE_MIN = 0.0
_RATE_MAX = 10.0

# 旧版本遗留的、现已废弃的配置键。
# ⚠️ v1.1.63：读配置时**主动丢弃**这些键 —— 否则 /api/power/tariff 里的
#    "config" 会原样透出它们，看着像功能还在。
#    · rate_override / proj_rate：单价已合并成一个 rate
#    · region / voltage / valley_start / valley_end：不再分地区/电压/时段
#    · overrides：季节参数整块废弃
#    · household_people / meter_months：v1.1.62 已删的两项
#    · enabled：不再有「启用阶梯电价」的开关
_DEPRECATED_KEYS = (
    "rate_override", "proj_rate", "region", "voltage",
    "valley_start", "valley_end", "overrides",
    "household_people", "meter_months", "enabled",
)


def default_config():
    """默认配置（未配置时的兜底）。"""
    return {
        # 电费单价（元/kWh）—— 唯一需要用户关心的电价配置
        "rate": DEFAULT_RATE,
        "updated": 0,
    }


def load_config():
    """读取电价配置；文件不存在或损坏时返回默认配置。

    ⚠️ v1.1.63：读取时丢弃所有已废弃键（见 _DEPRECATED_KEYS），
       保证「读出来就是干净的」。
    """
    cfg = default_config()
    p = _cfg_path()
    raw = {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            raw = data
            cfg.update(data)
    except Exception:
        pass
    # ⚠️ 顺序很重要：必须在 pop 掉废弃键**之前**取旧版单价的值。
    #   v1.1.61 及更早的配置文件里只有 rate_override（没有 rate）；
    #   v1.1.62 的配置文件里只有 proj_rate（没有 rate）。
    #   老用户升版后若直接丢弃 → 单价悄悄变回 0.5224，等于「帮用户改了电价」。
    #   取值优先级：rate > rate_override > proj_rate。
    legacy_rate = raw.get("rate_override")
    if legacy_rate in (None, ""):
        legacy_rate = raw.get("proj_rate")
    for k in _DEPRECATED_KEYS:
        cfg.pop(k, None)
    # 单价：优先取配置文件里的 rate；旧版只有 rate_override 时**继承**过来，
    # 避免老用户升版后单价变回默认值。
    # ⚠️ 必须看 raw（原始字典）而不是 cfg —— cfg 已被 default_config()
    #    填上了默认值 0.5224，拿 cfg 判断「有没有 rate」永远为真。
    if "rate" in raw and raw.get("rate") not in (None, ""):
        rate = raw.get("rate")
    elif legacy_rate not in (None, ""):
        rate = legacy_rate
    else:
        rate = DEFAULT_RATE
    if rate in (None, ""):
        rate = DEFAULT_RATE
    try:
        rate = float(rate)
    except Exception:
        rate = DEFAULT_RATE
    if rate < _RATE_MIN or rate > _RATE_MAX:
        rate = DEFAULT_RATE
    cfg["rate"] = round(rate, 6)
    return cfg


def save_config(cfg):
    """写入电价配置。返回 (ok, message)。"""
    if not isinstance(cfg, dict):
        return False, "配置格式错误"
    try:
        old_cfg = load_config()
    except Exception:
        old_cfg = {}

    # 单价：前端没提交时继承旧配置（避免被清零）。
    raw = cfg.get("rate")
    if raw in (None, ""):
        raw = old_cfg.get("rate")
    if raw in (None, ""):
        raw = DEFAULT_RATE
    try:
        rate = float(raw)
    except Exception:
        return False, "电费单价必须是数字"
    if rate < _RATE_MIN or rate > _RATE_MAX:
        return False, "电费单价必须在 %g-%g 之间" % (_RATE_MIN, _RATE_MAX)

    out = {
        "rate": round(rate, 6),
        "updated": int(time.time()),
    }
    p = _cfg_path()
    try:
        d = os.path.dirname(p)
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
        return True, "电价配置已保存"
    except Exception as e:
        return False, "写入失败：%s" % e


# ---------------------------------------------------------------------------
# 计费
# ---------------------------------------------------------------------------

def compute_bill(flat_kwh, valley_kwh, cfg=None, ts=None):
    """按单一单价计算电费。

    参数：
      flat_kwh    兼容参数：平段电量 kWh（v1.1.63 起不再区分时段）
      valley_kwh  兼容参数：低谷电量 kWh（同上）
      cfg         电价配置（None 时读文件）
      ts          兼容参数：计算基准时间戳（现已无用）

    返回：
      {
        "yuan": 总电费,
        "total_kwh": 总电量,
        "rate": 生效单价,
        "eff_rate": 综合单价（= rate，单一时恒等）,
        "mode": "fixed",
        "detail": [],
      }
    """
    cfg = cfg if cfg is not None else load_config()
    flat_kwh = max(0.0, float(flat_kwh or 0.0))
    valley_kwh = max(0.0, float(valley_kwh or 0.0))
    total = flat_kwh + valley_kwh

    try:
        rate = float(cfg.get("rate"))
    except Exception:
        rate = DEFAULT_RATE
    if rate < _RATE_MIN or rate > _RATE_MAX:
        rate = DEFAULT_RATE

    yuan = total * rate
    return {
        "yuan": round(yuan, 4),
        "total_kwh": round(total, 4),
        # 兼容字段：前端旧代码可能仍读 flat_kwh / valley_kwh
        "flat_kwh": round(flat_kwh, 4),
        "valley_kwh": round(valley_kwh, 4),
        "flat_yuan": round(flat_kwh * rate, 4),
        "valley_yuan": round(valley_kwh * rate, 4),
        "rate": round(rate, 4),
        "eff_rate": round(rate, 4),
        "mode": "fixed",
        "detail": [],
    }


# ---------------------------------------------------------------------------
# 口径说明（前端直接展示，避免各处各写一遍）
# ---------------------------------------------------------------------------

RATE_NOTE = (
    "电费按「用电量 × 电费单价」估算，不区分阶梯档位、不区分峰谷时段、"
    "不区分地区。默认单价 %s 元/kWh，取成都（四川）一户一表居民不满 1 千伏"
    "第一档高峰平段电价（成都居民电价的最低档），可按你家的实际电价自行调整。"
    % (DEFAULT_RATE,)
)

# ⚠️ v1.1.64：**电价本身**的说明（与 RATE_NOTE 区分开）。
#   RATE_NOTE 讲的是「电费怎么算出来」（电量 × 单价），用于电价设置面板；
#   这里讲的是「这个电价是什么、为什么是这个数、能不能改」，
#   用于板块标题栏的电价标签 —— 老板明确要求两者不要混。
RATE_DESC = (
    "默认取成都（四川）一户一表居民不满 1 千伏第一档高峰平段电价"
    "（成都居民电价的最低档），不区分阶梯档位与峰谷时段；"
    "可在能耗详情的电价设置里按你家的实际电价自行调整。"
)

# ⚠️ v1.1.64：标题栏标签里直接显示的**短句**（完整说明见 RATE_DESC，悬停可见）。
#   不能靠 RATE_DESC.split(/[。；]/)[0] 取 —— 第一句就是 46 字长句
#   （「默认取成都（四川）一户一表居民不满 1 千伏第一档高峰平段电价…」），
#   实测铺开后标题栏胶囊会涨到 708px、占满标题栏 63%，把「详情 ›」挤到边上。
RATE_DESC_SHORT = "默认取成都居民最低档电价，可自行调整"


def describe():
    """给前端用的当前配置描述。"""
    cfg = load_config()
    try:
        rate = float(cfg.get("rate"))
    except Exception:
        rate = DEFAULT_RATE
    return {
        "config": cfg,
        "rate": round(rate, 6),
        "default_rate": DEFAULT_RATE,
        "unit": "元/kWh",
        "note": RATE_NOTE,
    }
