#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ---------------------------------------------------------------------------
# FnStyle —— 系统监控阈值门限配置（单一权威来源）
#
# 背景（v1.1.79）
#   老板需求：「对软件中系统监控部分相关的阈值门限的配置提取出来进行单独的配置，
#   配置页面以抽屉方式呈现，入口为页面顶部栏中增加配置管理按钮点击进入。」
#
#   改之前，系统监控的阈值门限**硬编码散落在 5 个文件**里：
#     · app/ui/static/js/monitor.js
#         TH_TEMP_WARN=60 / TH_TEMP_CRIT=75
#         TH_DISK_TEMP_WARN=50 / TH_DISK_TEMP_CRIT=58
#         capLevel() 里 95 / 85（容量分级）
#         capRampStops() 里 85 / 95（容量色阶）
#         bar() 里 70 / 90（进度条着色）
#     · app/ui/static/js/compute.js
#         CPU_WARN=85 / CPU_CRIT=95（处理器抽屉阈值虚线）
#         TEMP_CHARTS 阈值 60 / 75（温度抽屉阈值虚线）
#         rampInk() 里 90 / 70 / 35（文字色阶）
#     · app/ui/static/js/overview.js
#         TH = { cpu 85/95, mem 85/95, cap 85/95, temp 80/90 }
#         ⚠️ 这里的温度是 **80/90**，与 monitor.js 的 **60/75** 口径**不一致**！
#     · app/ui/storage_collect.py
#         DISK_SCAN_GROWTH_CRIT = 10（一次巡检内缺陷增长到此数判故障）
#
#   这带来三个问题：
#     ① 用户改不了 —— NAS 风扇一转，CPU 到 62℃ 就变黄，但概览卡按 80℃ 才变黄，
#        同一台机器两个地方给出两种结论。
#     ② 口径漂移 —— overview.js 的温度 80/90 是照抄 fnmonitor 的默认值，
#        而 monitor.js 的 60/75 才是真正在用的家用口径，两者从未对齐。
#     ③ 维护成本 —— 改一个门限要翻 4 个前端文件 + 1 个后端文件。
#
# 本模块的定位
#   **唯一权威来源**：定义每个门限的 key / 默认值 / 合法区间 / 单位 / 说明；
#   提供 load / save / describe 三个接口，JSON 持久化到应用 var 目录。
#
#   前端通过 GET /api/monitor/thresholds 拿到一份「键 → 数值」的映射，
#   覆盖各文件里的硬编码常量（见 app/ui/static/js/thresholds.js）。
#   后端 storage_collect.py 的 DISK_SCAN_GROWTH_CRIT 也从这里读。
#
# 命名与取值口径
#   · 百分比类（cpu / mem / cap）单位是 %，区间 1~100
#   · 温度类单位是 ℃，区间 0~120（CPU 关键温度约 100℃，留余量）
#   · 计数类（scan growth）单位是「个」，区间 1~10000
#   · 进度条色阶（barWarn / barCrit）单位是 %，区间 1~100
#
# 校验规则
#   · warn 必须 < crit —— 否则「告警线比严重线还高」永远不可能触发
#   · 单项越界 → 拒绝保存并返回明确错误（不做静默夹取）
#   · 配置文件损坏 → 回落到默认值（不抛异常，避免拖垮监控服务）
# ---------------------------------------------------------------------------

import json
import os
import time

# ---------------------------------------------------------------------------
# 配置存储
# ---------------------------------------------------------------------------


def _data_dir():
    """配置目录（与 power_tariff._data_dir 同一套解析规则）。"""
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
    return os.path.join(_data_dir(), "monitor_thresholds.json")


# ---------------------------------------------------------------------------
# 门限定义（唯一权威清单）
# ---------------------------------------------------------------------------
# spec 字段：
#   label     界面显示名
#   unit      单位（'' 表示无单位）
#   def       默认值
#   min/max   合法区间（闭区间）
#   group     分组（界面按组分段）
#   desc      说明（界面直接展示，告诉用户这个门限管什么）
#   pair      关联的 crit 键 —— 用于校验 warn < crit

SPEC = {
    # ---- 处理器 ----
    "cpuWarn": {
        "label": "处理器使用率 · 留意", "unit": "%", "def": 85,
        "min": 1, "max": 100, "group": "processor",
        "desc": "处理器占用率达到该值时标黄提示「繁忙」。",
        "pair": "cpuCrit",
    },
    "cpuCrit": {
        "label": "处理器使用率 · 严重", "unit": "%", "def": 95,
        "min": 1, "max": 100, "group": "processor",
        "desc": "处理器占用率达到该值时标红提示「高负载」。",
        "pair": "cpuWarn",
    },

    # ---- 内存 ----
    "memWarn": {
        "label": "内存使用率 · 留意", "unit": "%", "def": 85,
        "min": 1, "max": 100, "group": "memory",
        "desc": "内存占用率达到该值时标黄提示「偏高」。",
        "pair": "memCrit",
    },
    "memCrit": {
        "label": "内存使用率 · 严重", "unit": "%", "def": 95,
        "min": 1, "max": 100, "group": "memory",
        "desc": "内存占用率达到该值时标红提示「严重」。",
        "pair": "memWarn",
    },

    # ---- 存储容量 ----
    "capWarn": {
        "label": "存储容量 · 留意", "unit": "%", "def": 85,
        "min": 1, "max": 100, "group": "storage",
        "desc": "单块硬盘或单个存储池占用率达到该值时提示「偏满」。",
        "pair": "capCrit",
    },
    "capCrit": {
        "label": "存储容量 · 严重", "unit": "%", "def": 95,
        "min": 1, "max": 100, "group": "storage",
        "desc": "占用率达到该值时提示「将满」，建议尽快清理。",
        "pair": "capWarn",
    },

    # ---- 通用温度（CPU / 主板 / 风扇位）----
    "tempWarn": {
        "label": "系统温度 · 留意", "unit": "℃", "def": 60,
        "min": 0, "max": 120, "group": "temperature",
        "desc": "CPU / 主板等传感器温度达到该值时提示过热。",
        "pair": "tempCrit",
    },
    "tempCrit": {
        "label": "系统温度 · 严重", "unit": "℃", "def": 75,
        "min": 0, "max": 120, "group": "temperature",
        "desc": "温度达到该值时判定为高温，建议检查散热。",
        "pair": "tempWarn",
    },

    # ---- 硬盘温度 ----
    "diskTempWarn": {
        "label": "硬盘温度 · 留意", "unit": "℃", "def": 50,
        "min": 0, "max": 120, "group": "temperature",
        "desc": "硬盘温度达到该值时提示「留意」——硬盘的温度余量比 CPU 小得多。",
        "pair": "diskTempCrit",
    },
    "diskTempCrit": {
        "label": "硬盘温度 · 严重", "unit": "℃", "def": 58,
        "min": 0, "max": 120, "group": "temperature",
        "desc": "硬盘温度达到该值时判定为高温，建议改善风道。",
        "pair": "diskTempWarn",
    },

    # ---- 进度条着色 ----
    "barWarn": {
        "label": "进度条 · 留意", "unit": "%", "def": 70,
        "min": 1, "max": 100, "group": "display",
        "desc": "通用进度条填充比例达到该值时由绿转橙。",
        "pair": "barCrit",
    },
    "barCrit": {
        "label": "进度条 · 严重", "unit": "%", "def": 90,
        "min": 1, "max": 100, "group": "display",
        "desc": "通用进度条填充比例达到该值时由橙转红。",
        "pair": "barWarn",
    },

    # ---- 巡检缺陷增长 ----
    "scanGrowthCrit": {
        "label": "巡检缺陷增长 · 判故障", "unit": "个", "def": 10,
        "min": 1, "max": 10000, "group": "storage",
        "desc": "一次巡检间隔内重映射扇区增长达到该个数时，判定硬盘故障。",
        "pair": None,
    },
}

GROUPS = [
    ("processor", "处理器"),
    ("memory", "内存"),
    ("storage", "存储"),
    ("temperature", "温度"),
    ("display", "显示着色"),
]

GROUP_LABEL = dict(GROUPS)


def default_config():
    """默认配置（未配置时的兜底）。"""
    cfg = {}
    for k, spec in SPEC.items():
        cfg[k] = spec["def"]
    cfg["updated"] = 0
    return cfg


def load_config():
    """读取阈值配置；文件不存在或损坏时返回默认配置。

    ⚠️ 与 power_tariff 不同：这里**逐项校验**而不是整份信任 ——
       单项越界时只把该项回落默认值，其余照常生效，
       避免一份坏配置把整个监控服务的门限全部打回原形。
    """
    cfg = default_config()
    raw = {}
    try:
        with open(_cfg_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            raw = data
    except Exception:
        raw = {}

    for k, spec in SPEC.items():
        if k not in raw:
            continue
        v = raw.get(k)
        try:
            v = float(v)
        except Exception:
            continue
        if not _in_range(v, spec):
            continue
        # 百分比类取整存储，避免 85.5 这种值在界面上显示成小数
        if spec["unit"] == "%" or spec["unit"] == "℃":
            v = int(round(v))
        else:
            v = int(round(v))
        cfg[k] = v

    # 回落之后可能违反 warn < crit（如用户只改了 warn），统一纠正
    _fix_pair_order(cfg)
    return cfg


def _in_range(v, spec):
    return v >= spec["min"] and v <= spec["max"]


def _fix_pair_order(cfg):
    """保证每个 warn < crit 对里，warn 确实小于 crit。

    ⚠️ 违反时把 **crit 下调到 warn - 1**（而不是把 warn 上调）——
       用户主动调低的 warn 一般是真实意图（他觉得这个值就该报警），
       而 crit 是派生上限，跟着 warn 走更合理。
    """
    for k, spec in SPEC.items():
        pair = spec.get("pair")
        if not pair:
            continue
        # 只在「本项是 crit、对方是 warn」时做下调
        if not k.endswith("Crit"):
            continue
        wkey = pair
        if wkey not in cfg or k not in cfg:
            continue
        if cfg[k] <= cfg[wkey]:
            newv = max(SPEC[k]["min"], cfg[wkey] - 1)
            cfg[k] = newv


def save_config(incoming):
    """写入阈值配置。返回 (ok, message)。

    incoming 形如 {"cpuWarn": 80, "cpuCrit": 90, ...}
    未提交的键保持原值（前端表单可能只提交部分字段）。
    """
    if not isinstance(incoming, dict):
        return False, "配置格式错误"

    old = load_config()
    out = dict(old)

    # 1) 逐项校验并写入
    errors = []
    for k, spec in SPEC.items():
        if k not in incoming:
            continue
        v = incoming.get(k)
        if v in (None, ""):
            continue
        try:
            v = float(v)
        except Exception:
            errors.append("%s 必须是数字" % spec["label"])
            continue
        if not _in_range(v, spec):
            errors.append("%s 必须在 %g-%g %s 之间" % (
                spec["label"], spec["min"], spec["max"], spec["unit"]))
            continue
        out[k] = int(round(v))

    if errors:
        return False, "；".join(errors)

    # 2) 校验 warn < crit（只在**本次提交涉及到的对**上检查）
    for k, spec in SPEC.items():
        pair = spec.get("pair")
        if not pair or not k.endswith("Crit"):
            continue
        if k not in incoming and pair not in incoming:
            continue
        if out[k] <= out[pair]:
            return False, "%s 必须大于 %s" % (
                SPEC[k]["label"], SPEC[pair]["label"])

    out["updated"] = int(time.time())

    p = _cfg_path()
    try:
        d = os.path.dirname(p)
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
        return True, "阈值配置已保存"
    except Exception as e:
        return False, "写入失败：%s" % e


def reset_config():
    """恢复默认阈值。返回 (ok, message)。"""
    return save_config(default_config())


def describe():
    """返回给前端的完整描述（默认清单 + 当前生效值 + 元信息）。"""
    cfg = load_config()
    items = []
    for k, spec in SPEC.items():
        items.append({
            "key": k,
            "label": spec["label"],
            "unit": spec["unit"],
            "default": spec["def"],
            "min": spec["min"],
            "max": spec["max"],
            "group": spec["group"],
            "desc": spec["desc"],
            "pair": spec.get("pair"),
            "value": cfg.get(k, spec["def"]),
        })
    return {
        "success": True,
        "config": cfg,
        "items": items,
        "groups": [{"key": g, "label": lbl} for g, lbl in GROUPS],
        "updated": cfg.get("updated", 0),
    }


def get_value(key, default=None):
    """取单个门限值（供后端模块内部使用，如 storage_collect 的巡检增长门限）。"""
    cfg = load_config()
    if key in cfg:
        return cfg[key]
    if key in SPEC:
        return SPEC[key]["def"]
    return default
