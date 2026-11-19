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


def energy_stats(rate_yuan_per_kwh=0.6, uptime_seconds=None):
    """用电量与电费统计（1:1 对齐 fnmonitor 的能耗统计口径）。

    口径：以历史库里的 power 指标（整机瓦数）按时间分段梯形积分成 kWh。
      - 今日：当天 00:00 起的累计
      - 本月：当月 1 日起的累计
      - 开机以来：按当前功率 × 开机时长估算

    与 fnmonitor 一致：功率历史每 60 秒一条，积分时按每段的实际时长
    折算成 kWh（瓦 × 秒 / 3.6e6）。

    返回：{"today_kwh", "month_kwh", "boot_kwh",
           "today_yuan", "month_yuan", "rate_yuan_per_kwh"}
    """
    rate = float(rate_yuan_per_kwh or 0.6)
    result = {
        "today_kwh": 0.0, "month_kwh": 0.0, "boot_kwh": 0.0,
        "today_yuan": 0.0, "month_yuan": 0.0,
        "rate_yuan_per_kwh": rate,
        "basis": "history",
    }

    now = time.time()
    lt = time.localtime(now)
    today_start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    month_start = time.mktime((lt.tm_year, lt.tm_mon, 1, 0, 0, 0, 0, 0, -1))

    # 取最长保留期的功率历史（7 天足够覆盖本月统计）
    pts = query_history("power", "7d")
    if pts:
        today_kwh = 0.0
        month_kwh = 0.0
        for i in range(1, len(pts)):
            t0, v0 = pts[i - 1]
            t1, v1 = pts[i]
            dt = t1 - t0
            if dt <= 0:
                continue
            # 梯形积分：平均功率 × 时长
            w = (float(v0) + float(v1)) / 2.0
            kwh = w * dt / 3.6e6
            if t0 >= today_start:
                today_kwh += kwh
            if t0 >= month_start:
                month_kwh += kwh
        result["today_kwh"] = round(today_kwh, 4)
        result["month_kwh"] = round(month_kwh, 4)
        result["today_yuan"] = round(today_kwh * rate, 2)
        result["month_yuan"] = round(month_kwh * rate, 2)

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
