#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ---------------------------------------------------------------------------
# FnStyle —— 处理器历史指标写入与查询（趋势图数据源）
#
# 处理器详情抽屉里的「CPU 使用率」「系统负载（1 分钟）」两张趋势图需要
# 一段时间内的连续采样值。fnmonitor 用 SQLite 的 metrics 表实现：
# 每 60 秒写一条，保留 7 天，前端按范围（10m/1h/6h/24h/7d/all）查询。
#
# 本模块与 fnmonitor 的差异：
#   - 复用既有的 storage.db，不新建数据库文件（同一块盘、同一份清理策略）；
#   - 只写处理器抽屉真正需要的两个指标：cpu 与 load1。
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
    """把一帧监控快照里的处理器指标写进历史库。

    调用方传的是 monitor_collect 的完整快照（dict）。只有 cpu 指标可用时
    才写 —— 没有有效值写进去只会污染曲线。

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
