#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ---------------------------------------------------------------------------
# FnStyle —— 处理器 / 内存历史指标写入与查询（趋势图数据源）
#
# 处理器详情抽屉里的「CPU 使用率」「系统负载（1 分钟）」两张趋势图，
# 以及内存详情抽屉里的「内存使用率」趋势图，都需要一段时间内的连续采样值。
# fnmonitor 用 SQLite 的 metrics 表实现：每 60 秒写一条，保留 7 天，
# 前端按范围（10m/1h/6h/24h/7d/all）查询。
#
# 本模块与 fnmonitor 的差异：
#   - 复用既有的 storage.db，不新建数据库文件（同一块盘、同一份清理策略）；
#   - 只写抽屉真正需要的三个指标：cpu、load1、mem。
#
# 致谢与许可：
#   表结构与读写口径参考了开源项目 fnmonitor
#   （https://github.com/MisiteQ/fnmonitor ，MIT License，
#     Copyright (c) 2026 Misite齊 / iqiuzixuan）。
#   MIT 许可全文见 app/ui/monitor_collect.py 顶部的许可声明。
# ---------------------------------------------------------------------------

import os
import time
import threading

try:
    import storage_collect as _sc
except Exception:  # pragma: no cover
    _sc = None


# 各范围对应的秒数（与 fnmonitor 的 _RANGE_SECONDS 一致）
RANGE_SECONDS = {
    "10m": 600,
    "1h": 3600,
    "6h": 21600,
    "24h": 86400,
    "7d": 604800,
    "all": 0,          # 0 = 保留期内全部
}

# 采样间隔（秒）。与 fnmonitor 的 history_interval 一致。
HISTORY_INTERVAL = 60


def _data_dir():
    """数据目录（与 monitor_collect 的 _storage_data_dir 同一套解析规则）。"""
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


def _db_path():
    """历史库路径（复用存储巡检库 storage.db）。"""
    try:
        return os.path.join(_data_dir(), "storage.db")
    except Exception:
        return None


def _store():
    """惰性创建 HistoryStore（storage_collect 不可用时返回 None）。"""
    global _STORE
    if _STORE is None and _sc is not None:
        try:
            _STORE = _sc.HistoryStore(_db_path())
        except Exception:
            _STORE = False
    return _STORE or None


_STORE = None


def write_history(snapshot):
    """把一帧监控快照里的处理器 / 内存 / 温度 / 功耗指标写进历史库。

    调用方传的是 monitor_collect 的完整快照（dict）。只有指标可用时
    才写 —— 没有有效值写进去只会污染曲线。

    写的指标（与 fnmonitor 的 SHEETS 对应）：
      cpu     CPU 使用率（处理器抽屉趋势图）
      load1   系统负载 1 分钟（处理器抽屉趋势图）
      mem     内存使用率（内存抽屉趋势图）
      temp    CPU 温度（温度与散热板块趋势图）
      temp_mb 主板温度（温度与散热板块趋势图）
      power   整机功耗（能耗板块趋势图）

    返回 True 表示本次确实写了。
    """
    store = _store()
    if store is None or not snapshot:
        return False

    cpu = snapshot.get("cpu") or {}
    rows = []
    now = time.time()

    percent = cpu.get("percent")
    if percent is not None:
        try:
            rows.append((now, "cpu", round(float(percent), 2)))
        except Exception:
            pass

    loadavg = cpu.get("loadavg") or []
    if loadavg:
        try:
            rows.append((now, "load1", round(float(loadavg[0]), 2)))
        except Exception:
            pass

    # 内存使用率 —— 内存抽屉的「内存使用率」趋势图数据源
    mem = snapshot.get("mem") or {}
    mem_percent = mem.get("percent")
    if mem_percent is not None:
        try:
            rows.append((now, "mem", round(float(mem_percent), 2)))
        except Exception:
            pass

    # CPU / 主板温度 —— 温度与散热板块的趋势图数据源
    temp = snapshot.get("temp") or {}
    cpu_temp = temp.get("cpuTemp")
    if cpu_temp is not None:
        try:
            rows.append((now, "temp", round(float(cpu_temp), 2)))
        except Exception:
            pass
    mb_temp = temp.get("mbTemp")
    if mb_temp is not None:
        try:
            rows.append((now, "temp_mb", round(float(mb_temp), 2)))
        except Exception:
            pass

    # 整机功耗 —— 能耗板块的趋势图数据源
    power = snapshot.get("power") or {}
    p_total = power.get("total")
    if p_total is not None and power.get("ok"):
        try:
            rows.append((now, "power", round(float(p_total), 2)))
        except Exception:
            pass

    if not rows:
        return False
    return store.write(rows)


def query_history(metric, range_="1h"):
    """查询某指标的历史序列，返回 [[ts, value], ...]（时间升序）。

    range_ 取值：10m / 1h / 6h / 24h / 7d / all。
    """
    store = _store()
    if store is None:
        return []
    seconds = RANGE_SECONDS.get(range_, 3600)
    try:
        rows = store.query(metric, seconds)
    except Exception:
        return []
    return [[float(t), round(float(v), 2) if isinstance(v, float) else v]
            for t, v in rows]


def _day_key(ts):
    """时间戳 → 本地日期字符串 YYYY-MM-DD。"""
    lt = time.localtime(ts)
    return "%04d-%02d-%02d" % (lt.tm_year, lt.tm_mon, lt.tm_mday)


# ---------------------------------------------------------------------------
# 谷段 / 平段电量拆分
#
# FnStyle 每 60 秒采一次整机功率，天然就能把电量按「谷段 / 平段」拆开 ——
# 这正是国网账单的做法（四川口径：谷段 23:00-次日 7:00 享低谷优惠价，
# 阶梯加价按平段电量计算）。
#
# ⚠️ v1.1.57：这里的积分口径必须与 energy_stats / daily_energy 完全一致，
#    否则「总电量」和「拆分后的电量」对不上，界面上的电费就自相矛盾。
#    统一口径：e = 瞬时功率(W) × 该采样代表的间隔秒数 / 3600000，
#    间隔取相邻采样的实际差，断档（> 3 倍中位间隔）只按一个典型间隔计。
# ---------------------------------------------------------------------------

def _integrate_points(pts, default_dt=60.0, since_ts=None):
    """把功率采样点积分成电量（kWh），返回 (总电量, 分段电量列表)。

    分段方式：优先用 daily_summary 里已归档的 **小时分桶**（kwh@HH），
    因为它永久保留 400 天，而 metrics 原始点只留 7 天。
    归档水位线之后的实时尾段用原始点现算。

    since_ts：只统计该时间戳之后的电量（None = 不限）。
    用于「本月用电量」这类按月口径。

    返回 {"total": kWh, "flat": kWh, "valley": kWh, "by_hour": {HH: kWh}}
    """
    store = _store()
    out = {"total": 0.0, "flat": 0.0, "valley": 0.0, "by_hour": {}}
    if store is None:
        return out

    since_day = None
    if since_ts is not None:
        since_day = _day_key(since_ts)

    # ---- 1) 已归档部分：直接读 daily_summary 的 kwh@HH 分桶 ----
    try:
        conn = store._connect()
        try:
            if since_day:
                rows = conn.execute(
                    "SELECT day, metric, value FROM daily_summary "
                    "WHERE metric LIKE 'kwh@%' AND day >= ?", (since_day,)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT day, metric, value FROM daily_summary "
                    "WHERE metric LIKE 'kwh@%'").fetchall()
        finally:
            conn.close()
    except Exception:
        rows = []

    for _day, metric, value in rows:
        try:
            hh = int(metric[4:])
        except Exception:
            continue
        if not (0 <= hh <= 23):
            continue
        out["by_hour"][hh] = out["by_hour"].get(hh, 0.0) + float(value or 0.0)

    # ---- 2) 水位线之后的实时尾段：用原始点算 ----
    tail_by_hour = {}
    try:
        conn = store._connect()
        try:
            wm, has = store._daily_watermark(conn)
            tail_start = wm if has else store._day_start(time.time())
            if since_ts is not None and tail_start < since_ts:
                tail_start = since_ts
            pts_now = conn.execute(
                "SELECT ts, value FROM metrics WHERE metric='power' AND ts>=? "
                "ORDER BY ts", (tail_start,)).fetchall()
        finally:
            conn.close()
    except Exception:
        pts_now = []

    if pts_now:
        # 取前一个点作为积分起点（与 _daily_aggregate 同款口径）
        try:
            conn = store._connect()
            try:
                pr = conn.execute(
                    "SELECT ts, value FROM metrics WHERE metric='power' AND ts<? "
                    "ORDER BY ts DESC LIMIT 1", (tail_start,)).fetchone()
            finally:
                conn.close()
        except Exception:
            pr = None
        seq = ([pr] if pr else []) + list(pts_now)
        ts_list = [float(r[0]) for r in seq]
        dts = sorted(ts_list[i + 1] - ts_list[i] for i in range(len(ts_list) - 1)
                     if ts_list[i + 1] > ts_list[i])
        med = dts[len(dts) // 2] if dts else float(default_dt)
        cap = 3.0 * max(med, float(default_dt or 0))
        last = None
        for t, v in seq:
            t = float(t)
            gap = (t - last) if last is not None else None
            dt = gap if (gap is not None and 0 < gap <= cap) else med
            last = t
            e = (float(v) or 0.0) * dt / 3600000.0
            hh = time.localtime(t).tm_hour
            tail_by_hour[hh] = tail_by_hour.get(hh, 0.0) + e
            out["by_hour"][hh] = out["by_hour"].get(hh, 0.0) + e

    # ---- 3) 按谷时段拆成平段 / 谷段 ----
    try:
        import power_tariff as pt
        cfg = pt.load_config()
        vs = int(cfg.get("valley_start", 23)) % 24
        ve = int(cfg.get("valley_end", 7)) % 24
    except Exception:
        vs, ve = 23, 7

    # ⚠️ v1.1.61 修复（真实缺陷）：原来这里是「从 vs 逐小时数到 ve」
    #   的循环，**vs == ve 时会数满 24 小时** → 谷段覆盖全天，
    #   全部电量按低谷价计费。
    #   用户在面板上把「低谷时段 起」和「止」设成同一个点时，
    #   语义上应该是**空区间**（谷段时长为 0），而不是全天。
    #   现在与 power_tariff._in_hours 的语义对齐：
    #     · vs == ve → 空区间（除非 vs == 0，那才是真正的 0-24）
    #     · vs > ve  → 跨零点（23→7 这类）
    #     · vs < ve  → 普通区间
    valley_hours = set()
    if vs == ve:
        if vs == 0:
            valley_hours = set(range(24))
    elif vs < ve:
        valley_hours = set(range(vs, ve))
    else:
        valley_hours = set(list(range(vs, 24)) + list(range(0, ve)))

    for hh, v in out["by_hour"].items():
        if hh in valley_hours:
            out["valley"] += v
        else:
            out["flat"] += v
    out["total"] = out["flat"] + out["valley"]
    return out


def split_energy_by_period(cfg=None, months=1):
    """把**计费周期**已用电量拆成谷段 / 平段。

    参数：
      months  计费周期月数（1 = 本月；2 = 两个月抄一次的双月周期）。
              ⚠️ 抄表周期为两个月时，电价表按双月结算 → 档位门槛 ×2
                 （川发改价格〔2012〕560号五），用电量口径也必须取**双月**，
                 否则门槛翻倍而电量不翻倍 → 严重低估电费。

    返回 {"total", "flat", "valley", "by_hour"}（kWh）。
    """
    now = time.time()
    lt = time.localtime(now)
    try:
        m = max(1, int(months or 1))
    except Exception:
        m = 1
    if m <= 1:
        y, mo = lt.tm_year, lt.tm_mon
    else:
        # 回退 m-1 个月（跨年自动处理）
        total_m = lt.tm_year * 12 + (lt.tm_mon - 1) - (m - 1)
        y, mo = total_m // 12, total_m % 12 + 1
    month_start = time.mktime((y, mo, 1, 0, 0, 0, 0, 0, -1))
    return _integrate_points(None, since_ts=month_start)


def tariff_bill(flat_kwh, valley_kwh, cfg=None):
    """按当前电价配置计算电费（阶梯 + 分时）。"""
    try:
        import power_tariff as pt
    except Exception:
        return None
    try:
        return pt.compute_bill(flat_kwh, valley_kwh, cfg)
    except Exception:
        return None


def update_tier_ledger(flat_kwh, valley_kwh):
    """把本月**未使用完的阶梯电量**写进台账（供下月结转用）。

    ⚠️ v1.1.59 重写：原来这里**另写了一份**分档逻辑，
       与 power_tariff.compute_bill 的口径**不一致**
       （没算结转额度、没算「一户多人口」加成），
       导致台账数据自相矛盾，且同月内重复调用会自我污染。
       现在统一委托给 power_tariff.update_tier_ledger，
       保证「算出来的账」与「记下来的账」严格一致。
    """
    try:
        import power_tariff as pt
    except Exception:
        return False
    try:
        return bool(pt.update_tier_ledger(flat_kwh, valley_kwh))
    except Exception:
        return False


def daily_energy(rate_yuan_per_kwh=0.6, max_days=30, fill_days=0, eff_rate=None):
    """按天聚合用电量（1:1 对齐 fnmonitor 的 /api/daily 口径）。

    ⚠️ v1.1.53：数据来源改为「daily_summary 归档表 + 水位线后的实时尾段」
       （HistoryStore.daily_rows），而不是直接扫 metrics 原始点。
       原因：metrics 只保留 7 天，扫原始点最多只能拿到装应用之后的数据；
       daily_summary 永久保留（400 天），才能在装上后立刻显示完整的历史天数。

    积分口径（对齐 fnmonitor 的 _daily_aggregate）：
      每个采样代表它之前的一个间隔 → e = 瞬时功率(W) × dt(s) / 3600000
      dt 取相邻采样的实际间隔；断档（> 3 倍中位间隔）只按一个典型间隔计，
      避免机器休眠后把那段时间算成巨大的耗电量。

    ⚠️ v1.1.57：阶梯电价是**按月**清算的，不能逐日套用。
       所以每天的 cost 仍按「单一单价」折算（与柱状图语义一致），
       但允许外部传入 eff_rate（本月实际综合均价），
       让开启阶梯后各天的 cost 与本月总账口径保持自洽。

    返回：{
      "days": [{"day": "2026-11-15", "kwh": 0.12, "cost": 0.07,
                "power_h": 23.5, "avg_w": 28.1, "has_data": True}, ...],
      "rate_yuan_per_kwh": 0.6,
      "success": True,
    }
    days 按日期升序，**恒为 max_days 天**；无数据的天 kwh=None、has_data=False
    （对齐 fnmonitor：缺失是 null 不是 0）。
    """
    rate = float(rate_yuan_per_kwh or 0.6)
    if eff_rate is not None:
        try:
            rate = float(eff_rate)
        except Exception:
            pass
    store = _store()
    if store is None:
        return {"days": [], "rate_yuan_per_kwh": rate, "success": True}

    try:
        # 至少取 max(days, 31) 天：今日/本月合计与柱状图共用一份数据
        need = max(int(max_days or 30), 31)
        data = store.daily_rows(need, default_dt=HISTORY_INTERVAL) or {}
    except Exception:
        data = {}

    # 生成完整 N 天序列（对齐 fnmonitor 的 api_daily）
    now = time.time()
    lt = time.localtime(now)
    start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    n = max(1, int(max_days or 30))
    out = []
    for i in range(n - 1, -1, -1):
        day = time.strftime("%Y-%m-%d", time.localtime(start - i * 86400 + 3600))
        d = data.get(day) or {}
        has_power = "power_s" in d
        kwh = d.get("kwh") if has_power else None
        power_s = d.get("power_s") if has_power else None
        out.append({
            "day": day,
            "has_data": bool(d),
            "kwh": round(kwh, 4) if kwh is not None else None,
            "cost": round(kwh * rate, 2) if kwh is not None else None,
            "power_h": round(power_s / 3600.0, 2) if power_s is not None else None,
            "avg_w": (round(kwh * 3600000.0 / power_s, 1)
                      if kwh is not None and power_s else None),
        })
    return {"days": out, "rate_yuan_per_kwh": rate, "success": True,
            "archived_until": (data.get("_meta") or {}).get("archived_until")}


def avg_power():
    """平均功率（1:1 对齐 fnmonitor 的 avgW）。

    fnmonitor 的口径：
      1. 优先用每日汇总里有数据的天的平均功率（按 kwh / power_h 反推）
      2. 否则用功率历史的算术平均
      3. 都没有则返回 None

    返回 {"w": 瓦数, "basis": "口径说明", "n": 天数} 或 None
    """
    daily = daily_energy()
    ds = [d for d in daily.get("days", [])
          if d.get("kwh") is not None and (d.get("power_h") or 0) > 0]
    if ds:
        total_kwh = sum(d["kwh"] for d in ds)
        total_h = sum(d["power_h"] for d in ds)
        if total_h >= 1:
            return {
                "w": total_kwh * 1000.0 / total_h,
                "basis": "近 %d 天平均" % len(ds),
                "n": len(ds),
            }
    pts = query_history("power", "all")
    if len(pts) > 5:
        avg = sum(float(p[1] or 0) for p in pts) / len(pts)
        return {"w": avg, "basis": "功率历史平均", "n": len(pts)}
    return None


def energy_stats(rate_yuan_per_kwh=0.6, uptime_seconds=None):
    """用电量与电费统计（1:1 对齐 fnmonitor 的能耗统计口径）。

    口径：以历史库里的 power 指标（整机瓦数）按时间分段梯形积分成 kWh。
      - 今日：当天 00:00 起的累计
      - 本月：当月 1 日起的累计
      - 开机以来：按当前功率 × 开机时长估算
      - 本月预计：本月已用 + 平均功率 × 本月剩余天数
      - 全年估算：平均功率 × 8.76（8760 小时 / 1000）

    与 fnmonitor 一致：功率历史每 60 秒一条，积分时按每段的实际时长
    折算成 kWh（瓦 × 秒 / 3.6e6）。

    返回：{"today_kwh", "month_kwh", "boot_kwh",
           "today_yuan", "month_yuan", "rate_yuan_per_kwh",
           "month_proj_kwh", "year_proj_kwh", "avg_w", "avg_basis"}
    """
    rate = float(rate_yuan_per_kwh or 0.6)
    result = {
        "today_kwh": 0.0, "month_kwh": 0.0, "boot_kwh": 0.0,
        "today_yuan": 0.0, "month_yuan": 0.0,
        "rate_yuan_per_kwh": rate,
        "basis": "history",
        "month_proj_kwh": None, "year_proj_kwh": None,
        "month_proj_yuan": None, "year_proj_yuan": None,
        "avg_w": None, "avg_basis": "",
    }

    # ⚠️ v1.1.57：电价模型。
    #   启用了阶梯+分时后，month_yuan 不再是「月电量 × 单一单价」，
    #   而要按谷/平拆开 + 阶梯判定来算，与国网账单口径一致。
    #   未启用时保持原样（单一固定单价），确保向后兼容。
    tariff = None
    split = None
    bill_months = 1
    try:
        import power_tariff as pt
        tcfg = pt.load_config()
        if tcfg.get("enabled"):
            # ⚠️ v1.1.59：抄表周期为两个月时，档位门槛 ×2，
            #   用电窗口也必须同步取**双月**（口径自洽），否则低估电费。
            tprof = pt.effective_profile(tcfg.get("region"), tcfg)
            bill_months = pt._meter_months_of(tcfg, tprof)
            split = split_energy_by_period(tcfg, months=bill_months)
            tariff = pt.compute_bill(split.get("flat", 0.0), split.get("valley", 0.0), tcfg)
    except Exception:
        tariff = None
        split = None
        bill_months = 1

    now = time.time()
    lt = time.localtime(now)
    today_start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    month_start = time.mktime((lt.tm_year, lt.tm_mon, 1, 0, 0, 0, 0, 0, -1))

    # 取最长保留期的功率历史（7 天足够覆盖本月统计）
    # ⚠️ v1.1.53：积分口径对齐 fnmonitor —— 每个采样代表它之前的一个间隔，
    #   e = 瞬时功率 × dt / 3600000；断档（> 3 倍中位间隔）只按一个典型间隔计。
    #   原先是梯形积分 ((v0+v1)/2 × dt / 3.6e6)，与 fnmonitor 口径不一致。
    pts = query_history("power", "7d")
    if pts:
        today_kwh = 0.0
        month_kwh = 0.0
        ts_list = [p[0] for p in pts]
        dts = sorted(ts_list[i + 1] - ts_list[i] for i in range(len(ts_list) - 1)
                     if ts_list[i + 1] > ts_list[i])
        med = dts[len(dts) // 2] if dts else float(HISTORY_INTERVAL)
        cap = 3.0 * max(med, float(HISTORY_INTERVAL))
        last = None
        for t0, v0 in pts:
            gap = (t0 - last) if last is not None else None
            dt = gap if (gap is not None and 0 < gap <= cap) else med
            last = t0
            kwh = (float(v0) or 0.0) * dt / 3600000.0
            if t0 >= today_start:
                today_kwh += kwh
            if t0 >= month_start:
                month_kwh += kwh
        result["today_kwh"] = round(today_kwh, 4)
        result["month_kwh"] = round(month_kwh, 4)
        result["today_yuan"] = round(today_kwh * rate, 2)
        result["month_yuan"] = round(month_kwh * rate, 2)

    # ---- 阶梯+分时：覆盖上面的固定单价电费 ----
    if tariff is not None and split is not None:
        result["month_kwh"] = round(float(split.get("total") or 0.0), 4)
        result["month_yuan"] = round(float(tariff.get("yuan") or 0.0), 2)
        result["tariff_mode"] = tariff.get("mode")
        result["tariff_region"] = tariff.get("region")
        result["tariff_region_name"] = tariff.get("region_name")
        result["valley_kwh"] = round(float(split.get("valley") or 0.0), 4)
        result["flat_kwh"] = round(float(split.get("flat") or 0.0), 4)
        result["valley_yuan"] = round(float(tariff.get("valley_yuan") or 0.0), 4)
        result["flat_yuan"] = round(float(tariff.get("flat_yuan") or 0.0), 4)
        result["tier_kwh"] = tariff.get("tier_kwh")
        result["tier_rates"] = tariff.get("tier_rates")
        result["tariff_detail"] = tariff.get("detail")
        result["valley_label"] = tariff.get("valley_label")
        result["tariff_carry_in"] = tariff.get("carry_in")
        # 计费周期月数（v1.1.59）：2 = 两个月抄一次（档位门槛 ×2）
        result["bill_months"] = int(bill_months or 1)
        result["tariff_voltage"] = tariff.get("voltage")
        result["tariff_voltage_name"] = tariff.get("voltage_name")
        result["tariff_multi_person_extra"] = tariff.get("multi_person_extra")
        # 本月用电量写进阶梯台账（供下月结转）
        try:
            update_tier_ledger(split.get("flat", 0.0), split.get("valley", 0.0))
        except Exception:
            pass

    # 开机累计：按当前功率 × 开机时长估算
    if uptime_seconds and uptime_seconds > 0:
        store = _store()
        cur_w = 0.0
        if store is not None:
            try:
                rows = store.query("power", 600)
                if rows:
                    cur_w = float(rows[-1][1]) or 0.0
            except Exception:
                cur_w = 0.0
        result["boot_kwh"] = round(cur_w * uptime_seconds / 3.6e6, 4)

    # ---- 本月预计 / 全年估算（对齐 fnmonitor 的 monthProj / yearProj）----
    try:
        ap = avg_power()
    except Exception:
        ap = None
    if ap:
        w = float(ap.get("w") or 0)
        result["avg_w"] = round(w, 1)
        result["avg_basis"] = ap.get("basis") or ""
        # 本月预计：本月已用 + 平均功率 × 本月剩余时间
        #
        # ⚠️ v1.1.55 修正单位 bug：
        #   fnmonitor 的 monthProj 写的是 `mk + a.w * (end - d) / 3.6e9`，
        #   这里的 (end - d) 是 **毫秒** 差（JS Date 相减得 ms），
        #   所以 3.6e9 这个除数是配着「毫秒」用的。
        #   本实现把剩余时长换成了 **秒**（remain_secs），
        #   却沿用了 3.6e9 → 结果小了 1000 倍，
        #   导致 month_proj_kwh ≈ month_kwh（0.5388 vs 0.5317），
        #   于是「本月预计电费」几乎等于「本月已用电费」，
        #   界面上三个电费看起来互相矛盾。
        #   现在秒口径用 3.6e6（瓦·秒 → kWh）。
        if result["month_kwh"] > 0:
            import datetime
            now_dt = datetime.datetime.now()
            # 剩余时长：从现在到**下月 1 日 00:00**（与 fnmonitor 的
            # `new Date(y, m + 1, 1)` 完全一致，精确到秒）。
            if now_dt.month == 12:
                end_dt = datetime.datetime(now_dt.year + 1, 1, 1)
            else:
                end_dt = datetime.datetime(now_dt.year, now_dt.month + 1, 1)
            remain_secs = max(0.0, (end_dt - now_dt).total_seconds())
            result["month_proj_kwh"] = round(
                result["month_kwh"] + w * remain_secs / 3.6e6, 4)
            result["month_proj_remain_days"] = round(remain_secs / 86400.0, 2)
        # 全年估算：平均功率 × 8.76（8760 小时 / 1000）
        result["year_proj_kwh"] = round(w * 8.76, 1)
        # 折算成电费（fnmonitor 的 eff = 本月电费 / 本月用电，没数据时用费率）
        if result["month_kwh"] > 0 and result["month_yuan"] > 0:
            eff = result["month_yuan"] / result["month_kwh"]
        else:
            eff = rate
        result["eff_rate_yuan_per_kwh"] = round(eff, 4)
        if result["month_proj_kwh"] is not None:
            result["month_proj_yuan"] = round(result["month_proj_kwh"] * eff, 2)
        if result["year_proj_kwh"] is not None:
            result["year_proj_yuan"] = round(result["year_proj_kwh"] * eff, 2)

    # ---- 阶梯模式：补上电费构成明细，供前端展示 ----
    if tariff is not None:
        result.setdefault("tariff_mode", tariff.get("mode"))
        result.setdefault("tariff_region", tariff.get("region"))
        result.setdefault("tariff_region_name", tariff.get("region_name"))
        result.setdefault("valley_kwh", round(float(split.get("valley") or 0.0), 4))
        result.setdefault("flat_kwh", round(float(split.get("flat") or 0.0), 4))
        result.setdefault("valley_yuan", round(float(tariff.get("valley_yuan") or 0.0), 4))
        result.setdefault("flat_yuan", round(float(tariff.get("flat_yuan") or 0.0), 4))
        result.setdefault("tier_kwh", tariff.get("tier_kwh"))
        result.setdefault("tier_rates", tariff.get("tier_rates"))
        result.setdefault("tariff_detail", tariff.get("detail"))
        result.setdefault("valley_label", tariff.get("valley_label"))
        result.setdefault("tariff_carry_in", tariff.get("carry_in"))

    return result


def cleanup_history(retention_days=None):
    """清理超出保留期的历史点（默认 7 天）。"""
    store = _store()
    if store is None:
        return 0
    try:
        return store.cleanup(retention_days)
    except Exception:
        return 0


class HistoryWriter(object):
    """后台历史写入器：每 interval 秒把最新快照写一条进历史库。

    与 MonitorCollector 分开成独立线程，原因：
      - 历史采样周期（60 秒）与实时采集周期（3 秒）不同；
      - 写库是磁盘 IO，不该拖慢实时采集循环。
    """

    def __init__(self, interval=HISTORY_INTERVAL, get_snapshot=None):
        self.interval = max(15, int(interval or HISTORY_INTERVAL))
        self._get_snapshot = get_snapshot
        self._thread = None
        self._stop = threading.Event()
        self._last_ts = 0.0
        self._last_cleanup = 0.0

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                       name="fnstyle-history-writer")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        # 启动后先等一个周期再写第一条 —— 采集线程刚起来时 CPU 使用率
        # 还没有基线（第一次差分采样拿不到百分比），写了也是空值。
        while not self._stop.wait(self.interval):
            try:
                self._write_once()
            except Exception:
                pass

    def _write_once(self):
        now = time.time()
        if now - self._last_ts < self.interval:
            return False
        snap = None
        if self._get_snapshot:
            try:
                snap = self._get_snapshot()
            except Exception:
                snap = None
        if not snap:
            return False
        ok = write_history(snap)
        if ok:
            self._last_ts = now
        # 清理按天做一次即可，不必每次都扫表
        if now - self._last_cleanup > 3600:
            self._last_cleanup = now
            cleanup_history()
        return ok
