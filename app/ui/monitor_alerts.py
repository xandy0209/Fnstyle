#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ---------------------------------------------------------------------------
# FnStyle —— 超阈值告警引擎 + 通知推送（v1.1.84）
#
# 老板需求：
#   「对于超阈值的生成告警消息，提供通过飞牛的通知消息Webhook进行推送的能力，
#     同时提供通知消息的选择配置。」
#
# 拆成三件事
#   ① **告警判定**：服务端按 monitor_thresholds 的门限判定哪些指标超限
#      —— 口径必须与前端一致（前端 overview.js 的 lvCPU / lvMem / lvCap / lvTemp，
#         monitor.js 的 tempLevel / capLevel）。**这是本模块存在的理由**：
#      之前告警只存在于前端渲染层（颜色、文案），服务端完全不知道，
#      所以「超阈值」这个事实**没有任何地方能记录、能推送**。
#   ② **通知选择配置**：用户能选「哪些指标」「哪些级别」参与推送，
#      还能选「哪些通道」（Webhook / 不推）。
#   ③ **推送**：把告警消息 POST 到用户配置的 Webhook 地址。
#
# 为什么不放前端做
#   · 浏览器标签页一关就没了 —— NAS 半夜过热没人知道；
#   · 前端 3 秒轮询一次，判定与推送都在浏览器线程里跑；
#   · 浏览器侧发请求会把用户配置的 Webhook 地址暴露在 DevTools 里，
#     而 Webhook 地址通常带密钥。
#   ⇒ 判定与推送都在**服务端**（monitor_collect 的独立线程）做。
#
# 去抖与静默期（必须做，否则一定刷屏）
#   采集周期 3 秒 ⇒ 一个持续 10 分钟的高温会触发 200 次判定。
#   · **边沿触发**：只在「从不超 → 超」的那一刻发一条；
#   · **静默期**：同一指标同级别在 cooldown 秒内不重复发；
#   · **等级升级**：warn → crit 要**立即**发（不能等静默期），
#     因为「更严重了」是用户最需要知道的变化。
#
# 通道设计
#   目前支持两种投递方式：
#     · `webhook` —— POST 到用户填的地址（飞书 / 钉钉 / 企业微信 / 自建均可用）。
#       飞牛 fnOS 自带的「Webhook 机器人」也是这个方向：
#       fnOS → 外部接收器。FnStyle 是反过来（FnStyle → 接收器），
#       但**接收地址可以填同一个** —— 这是当前最稳妥、依赖最少的路径。
#     · `fnos` —— 尝试投递到 fnOS 自己的通知中心（若系统提供该入口）。
#       ⚠️ 这条路径依赖 fnOS 是否对第三方应用开放通知投递接口，
#          详见 fnos_notify.py 的说明。接口不可用时自动降级为「仅记录」。
#
# 消息格式
#   走通用的 JSON：`{"msgtype":"text","text":{"content":"..."}}`
#   —— 飞书 / 钉钉 / 企业微信三家的机器人 Webhook 都认这个形状。
#   企业微信实际是 `{"msgtype":"text","text":{"content":"..."}}`，
#   钉钉是 `{"msgtype":"text","text":{"content":"..."}}`，一致。
# ---------------------------------------------------------------------------

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

# ---------------------------------------------------------------------------
# 配置存储
# ---------------------------------------------------------------------------


def _data_dir():
    """配置目录（与 monitor_thresholds._data_dir 同一套解析规则）。"""
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
    return os.path.join(_data_dir(), "monitor_alerts.json")


# ---------------------------------------------------------------------------
# 可告警指标清单（唯一权威来源）
# ---------------------------------------------------------------------------
# 每个条目：
#   key      内部标识（也用作配置里的键）
#   label    界面显示名
#   group    分组（界面按组分段）
#   desc     说明
#   unit     单位
#   warn_key / crit_key  对应 monitor_thresholds.SPEC 里的门限键
#   default  默认是否参与推送
#
# ⚠️ 门限键必须是 monitor_thresholds.SPEC 里**真实存在**的键 ——
#    拼错会导致永远不触发（get_value 回落默认值 → 判定永远为否）。
#    verify_notify_config.py 里有断言守着这一点。

METRICS = [
    {
        "key": "cpu", "label": "处理器使用率", "group": "processor",
        "unit": "%", "warn_key": "cpuWarn", "crit_key": "cpuCrit",
        "default": True,
        "desc": "处理器占用率越过留意 / 严重门限时告警。",
    },
    {
        "key": "mem", "label": "内存使用率", "group": "memory",
        "unit": "%", "warn_key": "memWarn", "crit_key": "memCrit",
        "default": True,
        "desc": "内存占用率越过留意 / 严重门限时告警。",
    },
    {
        "key": "cap", "label": "存储容量", "group": "storage",
        "unit": "%", "warn_key": "capWarn", "crit_key": "capCrit",
        "default": True,
        "desc": "单块硬盘或单个存储池占用率越过门限时告警。",
    },
    {
        "key": "temp", "label": "系统温度", "group": "temperature",
        "unit": "℃", "warn_key": "tempWarn", "crit_key": "tempCrit",
        "default": True,
        "desc": "CPU / 主板等传感器温度越过门限时告警。",
    },
    {
        "key": "diskTemp", "label": "硬盘温度", "group": "temperature",
        "unit": "℃", "warn_key": "diskTempWarn", "crit_key": "diskTempCrit",
        "default": True,
        "desc": "物理硬盘温度越过门限时告警。",
    },
    {
        "key": "diskHealth", "label": "硬盘健康", "group": "storage",
        "unit": "", "warn_key": None, "crit_key": None,
        "default": True,
        # ⚠️ v1.1.87：描述同步扩展到 warn 级 ——
        #    现在「重映射扇区 / 待处理扇区 / UDMA CRC 错误」任一 > 0 也会推送，
        #    不只是「自检未通过 / 不可纠正错误」。
        "desc": "SMART 出现非零计数（重映射扇区 / 待处理扇区 / UDMA CRC 错误等）"
                "或自检未通过、存在不可纠正错误时告警。",
    },
]

METRIC_BY_KEY = {m["key"]: m for m in METRICS}

# 告警级别
LEVELS = ["warn", "crit"]

LEVEL_LABEL = {"warn": "留意", "crit": "严重"}

# 投递通道
CHANNELS = ["webhook", "fnos"]

# 默认静默期（秒）
DEFAULT_COOLDOWN = 300


# ---------------------------------------------------------------------------
# 默认配置
# ---------------------------------------------------------------------------

def default_config():
    """默认配置（未配置时的兜底）。

    ⚠️ 默认**不启用推送**（enabled=False）——
       一上来就往外发消息是打扰，而且默认没配地址。
       但**指标选择**默认全开，这样用户一填地址就能用。
    """
    cfg = {
        "enabled": False,
        "webhook_url": "",
        "webhook_secret": "",
        "channels": ["webhook"],
        "levels": ["warn", "crit"],
        "metrics": {m["key"]: m["default"] for m in METRICS},
        "cooldown": DEFAULT_COOLDOWN,
        "updated": 0,
    }
    return cfg


def load_config():
    """读取通知配置；文件不存在或损坏时返回默认配置。

    ⚠️ 与 monitor_thresholds 一样**逐项校验**：一个坏字段不能让整份配置失效。
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

    if "enabled" in raw:
        cfg["enabled"] = bool(raw.get("enabled"))

    for k in ("webhook_url", "webhook_secret"):
        v = raw.get(k)
        if isinstance(v, str):
            cfg[k] = v.strip()

    # 通道：只保留白名单内的，且至少留一个
    ch = raw.get("channels")
    if isinstance(ch, list):
        picked = [c for c in ch if c in CHANNELS]
        if picked:
            cfg["channels"] = picked

    # 级别：同上
    lv = raw.get("levels")
    if isinstance(lv, list):
        picked = [l for l in lv if l in LEVELS]
        if picked:
            cfg["levels"] = picked

    # 指标选择：逐个键取布尔值
    ms = raw.get("metrics")
    if isinstance(ms, dict):
        for k in METRIC_BY_KEY:
            if k in ms:
                cfg["metrics"][k] = bool(ms.get(k))

    # 静默期：0~86400 秒
    cd = raw.get("cooldown")
    try:
        cd = int(cd)
        if 0 <= cd <= 86400:
            cfg["cooldown"] = cd
    except Exception:
        pass

    return cfg


def save_config(incoming):
    """写入通知配置。返回 (ok, message)。

    ⚠️ 只提交要改的键（前端可能只提交部分字段）——
       未提交的键保持原值。
    """
    if not isinstance(incoming, dict):
        return False, "配置格式错误"

    old = load_config()
    out = dict(old)

    if "enabled" in incoming:
        out["enabled"] = bool(incoming.get("enabled"))

    if "webhook_url" in incoming:
        v = incoming.get("webhook_url")
        if v is None:
            v = ""
        if not isinstance(v, str):
            return False, "Webhook 地址必须是字符串"
        v = v.strip()
        # 允许留空（= 关闭 webhook 通道），但填了就必须是 http/https
        if v:
            if not (v.startswith("http://") or v.startswith("https://")):
                return False, "Webhook 地址必须以 http:// 或 https:// 开头"
            if len(v) > 2000:
                return False, "Webhook 地址过长"
        out["webhook_url"] = v

    if "webhook_secret" in incoming:
        v = incoming.get("webhook_secret")
        if v is None:
            v = ""
        if not isinstance(v, str):
            return False, "Webhook 密钥必须是字符串"
        out["webhook_secret"] = v.strip()

    if "channels" in incoming:
        v = incoming.get("channels")
        if not isinstance(v, list):
            return False, "通知通道必须是列表"
        picked = [c for c in v if c in CHANNELS]
        out["channels"] = picked

    if "levels" in incoming:
        v = incoming.get("levels")
        if not isinstance(v, list):
            return False, "告警级别必须是列表"
        picked = [l for l in v if l in LEVELS]
        out["levels"] = picked

    if "metrics" in incoming:
        v = incoming.get("metrics")
        if not isinstance(v, dict):
            return False, "通知指标必须是对象"
        ms = dict(out["metrics"])
        for k in v:
            if k in METRIC_BY_KEY:
                ms[k] = bool(v.get(k))
        out["metrics"] = ms

    if "cooldown" in incoming:
        try:
            cd = int(incoming.get("cooldown"))
        except Exception:
            return False, "静默期必须是整数秒"
        if not (0 <= cd <= 86400):
            return False, "静默期必须在 0-86400 秒之间"
        out["cooldown"] = cd

    # ⚠️ 一致性校验：启用了推送却没有地址 → 明确报错，别让用户以为生效了
    if out["enabled"]:
        if not out["channels"]:
            return False, "请至少选择一个通知通道"
        if "webhook" in out["channels"] and not out["webhook_url"]:
            return False, "启用 Webhook 通道时必须填写 Webhook 地址"
        if not out["levels"]:
            return False, "请至少选择一个告警级别"
        if not any(out["metrics"].values()):
            return False, "请至少选择一个告警指标"

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
        return True, "通知配置已保存"
    except Exception as e:
        return False, "写入失败：%s" % e


def reset_config():
    """恢复默认通知配置。返回 (ok, message)。"""
    return save_config(default_config())


# ---------------------------------------------------------------------------
# 判定：从采集快照算出当前处于告警状态的指标
# ---------------------------------------------------------------------------

def _num(v):
    """把可能是 None / '-' / 字符串的值转成可比较的 float，失败返回 None。

    ⚠️ 绝不能用 `if not v` 判断「有没有数据」——
       0 是合法值（CPU 占用 0%），`not 0` 为 True → 会把正常当成缺数据。
    """
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def evaluate(snapshot):
    """按门限判定快照里的指标，返回告警列表。

    返回形如：
      [
        {"metric":"cpu", "label":"处理器使用率", "level":"warn",
         "value":87.0, "unit":"%", "threshold":85,
         "text":"处理器使用率 87.0%，已达留意门限 85%"},
        ...
      ]

    ⚠️ 只在**有数据**时判定。缺数据的指标一律跳过 ———
       把「没采到」当成「正常」是错的（会静默漏报）。
    """
    if not isinstance(snapshot, dict):
        return []

    try:
        import monitor_thresholds as mt
    except Exception:
        return []

    alerts = []
    th = mt.load_config()

    def thv(key, fb):
        if not key:
            return None
        try:
            v = mt.get_value(key, fb)
        except Exception:
            v = fb
        return _num(v)

    # ---- 处理器 ----
    cpu = (snapshot.get("cpu") or {}).get("percent")
    cpu = _num(cpu)
    if cpu is not None:
        w = thv("cpuWarn", 85)
        c = thv("cpuCrit", 95)
        lv, thr = _level_of(cpu, w, c)
        if lv:
            alerts.append(_mk("cpu", cpu, lv, thr, "%"))

    # ---- 内存 ----
    mem = (snapshot.get("mem") or {}).get("percent")
    mem = _num(mem)
    if mem is not None:
        w = thv("memWarn", 85)
        c = thv("memCrit", 95)
        lv, thr = _level_of(mem, w, c)
        if lv:
            alerts.append(_mk("mem", mem, lv, thr, "%"))

    # ---- 存储容量（取最大的一盘 / 一池，不取平均）----
    cap, cap_name = _worst_capacity(snapshot)
    if cap is not None:
        w = thv("capWarn", 85)
        c = thv("capCrit", 95)
        lv, thr = _level_of(cap, w, c)
        if lv:
            a = _mk("cap", cap, lv, thr, "%")
            if cap_name:
                a["text"] = "存储容量 %s 占用 %.1f%%，已达%s门限 %g%%" % (
                    cap_name, cap, LEVEL_LABEL[lv], thr)
            alerts.append(a)

    # ---- 系统温度（取所有测点里最高的那个）----
    tmax, tname = _max_temp(snapshot)
    if tmax is not None:
        w = thv("tempWarn", 60)
        c = thv("tempCrit", 75)
        lv, thr = _level_of(tmax, w, c)
        if lv:
            a = _mk("temp", tmax, lv, thr, "℃")
            if tname:
                a["text"] = "系统温度 %s %.1f℃，已达%s门限 %g℃" % (
                    tname, tmax, LEVEL_LABEL[lv], thr)
            alerts.append(a)

    # ---- 硬盘温度（逐盘取最高）----
    dtmax, dtname = _max_disk_temp(snapshot)
    if dtmax is not None:
        w = thv("diskTempWarn", 50)
        c = thv("diskTempCrit", 58)
        lv, thr = _level_of(dtmax, w, c)
        if lv:
            a = _mk("diskTemp", dtmax, lv, thr, "℃")
            if dtname:
                a["text"] = "硬盘温度 %s %.1f℃，已达%s门限 %g℃" % (
                    dtname, dtmax, LEVEL_LABEL[lv], thr)
            alerts.append(a)

    # ---- 硬盘健康（SMART 判定，**逐盘**，含 warn 级计数）----
    # ⚠️ v1.1.87：从「只报 crit」扩展到「warn 级也报」，
    #    且改为返回列表（原来只返回第一块有问题的盘）。
    alerts.extend(_disk_health_alerts(snapshot))

    return alerts


def _level_of(value, warn, crit):
    """按门限判定级别。返回 (level, threshold) 或 (None, None)。

    ⚠️ 口径与前端严格一致（overview.js 的 lvCPU 等）：
       `>= crit` 判 crit，`>= warn` 判 warn。
    """
    if crit is not None and value >= crit:
        return "crit", crit
    if warn is not None and value >= warn:
        return "warn", warn
    return None, None


def _mk(mkey, value, level, threshold, unit):
    m = METRIC_BY_KEY.get(mkey, {})
    return {
        "metric": mkey,
        "label": m.get("label", mkey),
        "level": level,
        "value": round(float(value), 2),
        "unit": unit,
        "threshold": threshold,
        "text": "%s %.1f%s，已达%s门限 %g%s" % (
            m.get("label", mkey), float(value), unit,
            LEVEL_LABEL[level], threshold, unit),
    }


def _worst_capacity(snapshot):
    """取「最高」的容量占用。返回 (percent, 对象名)。

    ⚠️ 取最大值不取平均 —— 平均会把「一块盘快满了」稀释掉。
       与 overview.js 的口径一致（逐项判定再汇总）。
    """
    storage = snapshot.get("storage") or {}
    best = None
    best_name = ""

    def consider(pct, name):
        nonlocal best, best_name
        p = _num(pct)
        if p is None:
            return
        if best is None or p > best:
            best = p
            best_name = name or ""

    for d in (storage.get("disks") or []):
        consider(d.get("percent"), d.get("name"))
    for v in (storage.get("volumes") or []):
        consider(v.get("percent"), v.get("name"))
    return best, best_name


def _max_temp(snapshot):
    """取所有温度测点里的最高值。返回 (value, 测点名)。"""
    temp = snapshot.get("temp") or {}
    best = None
    best_name = ""
    for t in (temp.get("temps") or []):
        p = _num(t.get("value"))
        if p is None:
            continue
        if best is None or p > best:
            best = p
            best_name = t.get("name") or t.get("type") or ""
    mb = _num(temp.get("mbTemp"))
    if mb is not None and (best is None or mb > best):
        best = mb
        best_name = "主板"
    return best, best_name


def _max_disk_temp(snapshot):
    """取物理硬盘的最高温度。返回 (value, 盘名)。"""
    temp = snapshot.get("temp") or {}
    best = None
    best_name = ""
    for d in (temp.get("disks") or []):
        p = _num(d.get("value"))
        if p is None:
            p = _num(d.get("temp"))
        if p is None:
            continue
        if best is None or p > best:
            best = p
            best_name = d.get("name") or d.get("dev") or ""
    return best, best_name


def _smart_issues(d):
    """从单盘条目里挑出「非零 SMART 计数」，返回 [(标签, 计数), ...]。

    ⚠️ 口径与 storage_collect.grade_disk 一致：
       · uncorrectable（不可纠正错误）→ crit
       · reallocated（重映射扇区）/ pending（待处理扇区）/ udma_crc（UDMA CRC 错误）
         → 任一 > 0 即 warn
    ⚠️ 数值必须用 `_num` 判空 —— 0 是合法值（健康的盘全是 0），
       `not v` 会把 0 当成「没数据」。
    """
    out = []
    for key, label in (
            ("uncorrectable", "不可纠正错误"),
            ("reallocated_sectors", "重映射扇区"),
            ("pending_sectors", "待处理扇区"),
            ("udma_crc", "UDMA CRC 错误"),
    ):
        n = _num(d.get(key))
        if n is not None and n > 0:
            out.append((label, int(n)))
    return out


def _disk_health_alerts(snapshot):
    """硬盘健康告警：**逐盘**判定，返回告警列表。

    ⚠️ v1.1.87：原来只判 crit（SMART FAIL / 不可纠正错误），
       warn 级的 SMART 问题（重映射扇区 / 待处理扇区 / UDMA CRC 错误）
       **全部被丢掉** —— 代码里写着「那属于巡检日志范畴，不作为推送告警（太吵）」。
       老板明确要求这类也要推：
       > 「通知中的硬盘监控通知需要对硬盘的一些不重要的警告信息也需要推送通知，
       >   例如目前已有的『SMART 告警：UDMA CRC 错误 60』，类似这种都需要推送通知。」
       ⇒ 现在 warn 级也产出告警。

    ⚠️ 级别口径（与 storage_collect.grade_disk 对齐）：
       · crit —— SMART 自检未通过 / 不可纠正错误 > 0
       · warn —— 重映射扇区 / 待处理扇区 / UDMA CRC 错误 任一 > 0
       · 其余 —— 不告警

    ⚠️ **逐盘判定，不只报第一个盘** —— 三块盘里两块有问题时，
       只报一块会漏掉另一块。
    """
    storage = snapshot.get("storage") or {}
    out = []
    for d in (storage.get("disks") or []):
        name = d.get("name") or d.get("dev") or "硬盘"
        health = (d.get("health") or "")
        grade = (d.get("grade") or "").lower()

        issues = _smart_issues(d)
        detail = d.get("detail") or ""

        # ---- crit：SMART 自检未通过 ----
        if re_fail(health):
            # ⚠️ detail 传空串 —— health 已在 extra 里出现过，
            #    再当 detail 附加就会变成「（FAILED）（该盘全部：FAILED）」。
            out.append(_mk_disk(name, "crit", "SMART 自检未通过",
                                 "", subkey="%s·SMART" % name,
                                 extra="（%s）" % health if health else ""))
            continue

        # ---- crit：不可纠正错误 ----
        unc = [c for lbl, c in issues if lbl == "不可纠正错误"]
        if unc:
            out.append(_mk_disk(
                name, "crit",
                "不可纠正错误 %d 个" % unc[0],
                "；".join("%s %d 个" % (lbl, c) for lbl, c in issues),
                subkey="%s·不可纠正错误" % name))
            continue

        # ---- warn：其余非零计数 ----
        # ⚠️ 逐项列出，不要只报第一个 —— 「UDMA CRC 错误 60」与
        #    「重映射扇区 3」是两条不同的信息，合并成一条会让人误以为只有一个问题。
        for lbl, c in issues:
            out.append(_mk_disk(
                name, "warn", "%s %d 个" % (lbl, c),
                "；".join("%s %d 个" % (l2, c2) for l2, c2 in issues),
                subkey="%s·%s" % (name, lbl)))

        # ⚠️ grade 兜底：后端已判 crit（但快照里没带具体计数）时也要报出来。
        #    例如重映射扇区**快速增长**（delta ≥ 门限）—— 那条信息只在
        #    detail 文本里，没有独立计数字段。
        #    ⚠️ 这段**不能**放在「issues 为空就 continue」之后 ——
        #    快速增长场景恰好是 issues 为空的（计数没超 0，只是涨得快），
        #    提前 continue 会让这类 crit **永远漏报**。
        #    判据是「这块盘还没产出过 crit 告警」，按**盘名**比较
        #    （不能拿 subkey 比 —— subkey 带问题项后不等于盘名）。
        if grade == "crit" and not any(a["level"] == "crit" and a.get("disk") == name
                                       for a in out):
            out.append(_mk_disk(name, "crit",
                                 detail or "健康检查异常",
                                 "",   # ⚠️ detail 已作为主信息，别再重复一遍
                                 subkey="%s·健康检查" % name))
    return out


def _mk_disk(name, level, reason, detail="", subkey=None, extra=""):
    """构造一条硬盘健康告警。

    ⚠️ `subkey` = 去抖用的次级键 —— 硬盘健康是**逐盘逐项**判定的，
       「sda 的 UDMA CRC」与「sda 的重映射扇区」是两条独立告警，
       必须各自去抖 ⇒ subkey 带问题项（盘名·问题项）。
       若只用盘名，同一块盘的第二个问题会被误判成「同级别重复」而**静默丢掉**。

    ⚠️ `detail` 是「该盘全部问题」的汇总，只在**与主信息不同时**才附加 ——
       否则会出现「UDMA CRC 错误 60 个（UDMA CRC 错误 60 个）」这种重复表述。
    """
    m = METRIC_BY_KEY.get("diskHealth", {})
    label = m.get("label", "硬盘健康")
    parts = ["硬盘 %s：%s" % (name, reason)]
    if extra:
        parts.append(extra)
    if detail and detail != reason and detail not in reason:
        parts.append("（该盘全部：%s）" % detail)
    tail = "，请检查。" if level == "warn" else "，请立即检查。"
    return {
        "metric": "diskHealth",
        "subkey": subkey if subkey is not None else name,
        "disk": name,
        "label": label,
        "level": level,
        "value": None,
        "unit": "",
        "threshold": None,
        "text": "".join(parts) + tail,
        "detail": detail,
    }


def re_fail(health):
    """SMART health 字符串里含 FAIL 关键字 → 判故障。"""
    if not health:
        return False
    return "FAIL" in str(health).upper()


# ---------------------------------------------------------------------------
# 消息构造
# ---------------------------------------------------------------------------

def build_message(alerts, hostname="", ts=None):
    """构造推送消息体。

    格式：`{"msgtype":"text","text":{"content":"..."}}`
    —— 飞书 / 钉钉 / 企业微信的机器人 Webhook 都认这个形状。

    ⚠️ 只在**同一条消息里**包含本次要发的告警（可能多条），
       不要一条告警发一条消息 —— 深夜连续触发会把人吵醒。
    """
    if not alerts:
        return None
    if ts is None:
        ts = time.time()
    try:
        tstr = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    except Exception:
        tstr = str(ts)

    lines = []
    if hostname:
        lines.append("【%s】系统监控告警" % hostname)
    else:
        lines.append("系统监控告警")
    lines.append("时间：%s" % tstr)
    lines.append("")
    for a in alerts:
        lines.append("· [%s] %s" % (LEVEL_LABEL.get(a.get("level"), "告警"),
                                    a.get("text") or a.get("label")))
    content = "\n".join(lines)
    return {
        "msgtype": "text",
        "text": {"content": content},
        "content": content,
    }


# ---------------------------------------------------------------------------
# 推送
# ---------------------------------------------------------------------------

# 代理：fnOS 应用跑在 NAS 本机，直连外网时**不要**走本机代理
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def send_webhook(url, payload, secret="", timeout=10):
    """把消息 POST 到 Webhook 地址。返回 (ok, message)。

    ⚠️ 一定要**绕过系统代理**：应用跑在 NAS 上，若继承了代理环境变量，
       飞书/钉钉的域名走代理会被拒或超时。
    """
    if not url:
        return False, "未配置 Webhook 地址"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if secret:
        # 企业微信 / 钉钉的加签方式：secret 作为 query 参数追加
        sep = "&" if "?" in url else "?"
        url = "%s%ssecret=%s" % (url, sep, urllib.parse.quote(secret, safe=""))
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            code = getattr(resp, "status", 0) or resp.getcode()
            txt = resp.read().decode("utf-8", "replace")[:400]
            if 200 <= code < 300:
                return True, "推送成功（HTTP %s）" % code
            return False, "推送失败（HTTP %s）%s" % (code, txt[:120])
    except urllib.error.HTTPError as e:
        return False, "推送失败（HTTP %s）" % e.code
    except Exception as e:
        return False, "推送失败：%s" % e


def send_fnos(payload):
    """尝试投递到 fnOS 自己的通知中心。

    ⚠️ 这条路径依赖 fnOS 是否对第三方应用开放通知投递接口。
       若系统不提供，函数返回 (False, 原因)，调用方会**降级**为「仅记录」，
       不会因此崩溃或反复重试。

       实测（2026-11-23）：
         · fnOS RPC 网关上 notify.* / notification.* 全部 403（不在 allowlist）
         · CGI /cgi-bin/cgi-bin/trim/notify 返回 `invalid token`（路由存在但要鉴权）
         · 前端 JS 未搜到明文通知 API 路径
       ⇒ 当前按「不可用」处理，但保留接口以便后续打通。
    """
    try:
        import fnos_notify
    except Exception:
        return False, "fnOS 通知模块不可用"
    try:
        return fnos_notify.send(payload)
    except Exception as e:
        return False, "fnOS 通知投递失败：%s" % e


def dispatch(cfg, alerts):
    """按配置的通道推送告警。返回 (ok, message, 逐通道结果)。"""
    if not alerts:
        return True, "没有需要推送的告警", []
    if not cfg.get("enabled"):
        return False, "通知推送未启用", []
    if not cfg.get("channels"):
        return False, "未选择通知通道", []

    results = []
    payload = build_message(alerts)
    ok_any = False
    errs = []

    for ch in cfg.get("channels") or []:
        if ch == "webhook":
            ok, msg = send_webhook(cfg.get("webhook_url") or "",
                                   payload, cfg.get("webhook_secret") or "")
        elif ch == "fnos":
            ok, msg = send_fnos(payload)
        else:
            ok, msg = False, "未知通道：%s" % ch
        results.append({"channel": ch, "ok": ok, "message": msg})
        if ok:
            ok_any = True
        else:
            errs.append("%s: %s" % (ch, msg))

    if ok_any:
        return True, "推送成功", results
    return False, "；".join(errs) or "推送失败", results


# ---------------------------------------------------------------------------
# 去抖状态
# ---------------------------------------------------------------------------

class AlertState(object):
    """记录每个告警的上一次级别与推送时间，用于边沿触发与静默期。

    ⚠️ 状态**不持久化**：服务重启后状态清空，
       意味着重启后可能会重发一次当前正在发生的告警。
       这是有意的 —— 静默期本来就不该跨重启生效（重启往往正是问题发生的时候）。

    ⚠️ v1.1.87：去抖键从「metric」改成「(metric, subkey)」两级。
       原因：硬盘健康现在是**逐盘逐项**判定（sda 的 UDMA CRC 与 sdb 的
       重映射扇区是两条独立告警）。若仍按 metric 去重，
       第二条会被误判成「同级别重复」而**静默丢掉** ⇒ 漏报。
       subkey 由告警的 `subkey` 字段给出（硬盘是「盘名·问题项」），缺省则为空串。
    """

    def __init__(self):
        self._lock = threading.Lock()
        # key: (metric, subkey, level) -> 上次推送时间戳
        self._sent = {}
        # key: (metric, subkey) -> 上一次的级别（'' 表示正常）
        self._level = {}

    def should_send(self, metric, level, now, cooldown, subkey=""):
        """判断这次告警是否该推送。

        规则：
          ① **边沿触发**：上一次是正常（无记录或等级为空）→ 发；
          ② **等级升级**：上次是 warn、这次是 crit → 立即发（不受静默期限制）；
          ③ **同级别重复**：距上次推送不足 cooldown 秒 → 不发；
          ④ 其余情况 → 不发。
        """
        subkey = subkey or ""
        with self._lock:
            prev = self._level.get((metric, subkey), "")
            last = self._sent.get((metric, subkey, level), 0)
            if not prev:
                send = True
            elif prev != level and level == "crit":
                send = True
            elif level == "crit" and prev == "crit":
                send = (now - last) >= cooldown
            else:
                send = (now - last) >= cooldown
            if send:
                self._sent[(metric, subkey, level)] = now
            self._level[(metric, subkey)] = level
            return send

    def update_normal(self, metric, subkey=""):
        """指标回到正常 —— 清掉它的告警记录，下次超限会重新触发。"""
        with self._lock:
            key = (metric, subkey or "")
            self._level[key] = ""
            for k in list(self._sent.keys()):
                if k[0] == metric and k[1] == (subkey or ""):
                    self._sent.pop(k, None)

    def mark_level(self, metric, level, subkey=""):
        """只更新当前级别，不推送。

        ⚠️ 用于「推送未启用」的场景：照样记住当前处于告警状态，
           这样用户事后启用推送时不会立刻收到一串早已发生的旧告警。
        """
        with self._lock:
            self._level[(metric, subkey or "")] = level or ""

    def active_metrics(self):
        """当前处于告警状态的告警键列表（给采集循环清理用）。"""
        with self._lock:
            return ["%s|%s" % (k[0], k[1]) for k, v in self._level.items() if v]

    def snapshot(self):
        with self._lock:
            return {
                "levels": {"%s|%s" % (k[0], k[1]): v for k, v in self._level.items()},
                "sent": {"%s|%s|%s" % k: v for k, v in self._sent.items()},
            }


# ---------------------------------------------------------------------------
# 对外描述
# ---------------------------------------------------------------------------

def describe():
    """给前端用的完整描述（配置 + 可选项清单）。"""
    cfg = load_config()
    return {
        "success": True,
        "config": cfg,
        "metrics": [
            {
                "key": m["key"],
                "label": m["label"],
                "group": m["group"],
                "unit": m["unit"],
                "desc": m["desc"],
                "default": m["default"],
                "enabled": bool(cfg["metrics"].get(m["key"], m["default"])),
            }
            for m in METRICS
        ],
        "levels": [
            {"key": "warn", "label": LEVEL_LABEL["warn"]},
            {"key": "crit", "label": LEVEL_LABEL["crit"]},
        ],
        "channels": [
            {"key": "webhook", "label": "Webhook 推送"},
            {"key": "fnos", "label": "飞牛通知中心"},
        ],
        "groups": [
            {"key": "processor", "label": "处理器"},
            {"key": "memory", "label": "内存"},
            {"key": "storage", "label": "存储"},
            {"key": "temperature", "label": "温度"},
        ],
        "updated": cfg.get("updated", 0),
    }
