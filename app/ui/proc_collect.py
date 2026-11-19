# -*- coding: utf-8 -*-
"""进程 TOP 采样（按区间 CPU / 内存占用排序）。

来源与许可
----------
思路移植自「飞牛监控 fnmonitor」（MIT License），
原作者 Copyright (c) 2026 fnmonitor contributors。
原实现见 fnmonitor 的 server.py（ProcSampler / _proc_stat_snapshot）。
MIT 许可全文见本项目 app/ui/monitor_collect.py 顶部的许可声明。

为什么不用 ps
------------
1. ps 自己会出现在结果里 —— 它的 %CPU = 累计CPU时间 ÷ 进程存活时间，
   而 ps 只活几十毫秒又要扫几千个进程，分母极小，
   于是进程列表永远挂着一条假的几百 % 占用。
2. ps 的 pcpu 是进程整个生命周期的平均值，不是当前占用，长跑进程会严重滞后。
3. 每次刷新都 fork+exec 一次 ps，在低功耗 NAS 上本身就是笔开销。

改为直接读 /proc/[pid]/stat 并与上一轮快照做差：
得到真实的区间占用百分比，不产生任何新进程。

实现取舍
--------
fnmonitor 用常驻后台线程每 10 秒全量扫一遍 /proc。
FnStyle 是被动的轮询式采集器（见 monitor_collect.MonitorCollector），
所以这里改成**按需采样**：
抽屉打开时前端请求 /api/monitor?procs=1 才触发一次采样，
采样结果缓存 15 秒，避免反复打开抽屉时反复扫 /proc。
"""

import os
import threading
import time


def _page_size():
    try:
        return os.sysconf("SC_PAGE_SIZE")
    except Exception:
        return 4096


def _clk_tck():
    """sysconf(_SC_CLK_TCK) —— CPU 时间计量单位（Hz）。"""
    try:
        return os.sysconf("SC_CLK_TCK") or 100
    except Exception:
        return 100


def _uptime_seconds(proc_root="/proc"):
    try:
        with open(os.path.join(proc_root, "uptime"), "r") as f:
            return float(f.read().split()[0])
    except Exception:
        return 0.0


def _proc_stat_snapshot(proc_root="/proc"):
    """遍历 {proc_root}/[pid]/stat，返回 {pid: (cpu_ticks, starttime, rss_bytes, comm)}。

    只读 stat 一个文件：
      - utime+stime 算 CPU 增量
      - starttime 识别 PID 复用（同一 PID 换了进程就不能相减）
      - rss（页数）用于内存排序
      - comm 从括号段取
    进程退出时的竞态用 try/except 跳过。
    """
    snap = {}
    try:
        names = os.listdir(proc_root)
    except Exception:
        return snap
    page = _page_size()
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open(os.path.join(proc_root, name, "stat"), "rb") as fh:
                raw = fh.read().decode("utf-8", "replace")
        except Exception:
            continue
        # comm 可能含空格和括号，取最后一个 ')' 才是字段起点
        lp = raw.rfind(")")
        sp = raw.find("(")
        if lp < 0 or sp < 0 or sp > lp:
            continue
        fields = raw[lp + 2:].split()
        if len(fields) < 22:      # 需要到第 24 列 rss
            continue
        try:
            ticks = int(fields[11]) + int(fields[12])   # utime + stime
            start = int(fields[19])                     # starttime
            rss = int(fields[21]) * page                # rss 页数 → 字节
        except Exception:
            continue
        snap[int(name)] = (ticks, start, rss, raw[sp + 1:lp])
    return snap


def _proc_cmdline(pid):
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            raw = f.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
        return raw
    except Exception:
        return ""


class ProcSampler(object):
    """进程快照采样器（带缓存，按需采样）。

    与 fnmonitor 的常驻线程不同：FnStyle 按需触发，
    采样结果缓存 CACHE_S 秒，避免频繁扫 /proc。
    """

    CACHE_S = 15.0

    def __init__(self, proc_root="/proc"):
        self._proc_root = proc_root
        self._lock = threading.Lock()
        self._snap = {}
        self._ts = 0.0
        self._rows = []
        self._cached_ts = 0.0

    def top(self, limit=12, force=False):
        """返回进程列表 [{pid, comm, args, cpu, rss}]，按 CPU 降序（0% 的按内存排后面）。"""
        now = time.time()
        with self._lock:
            if not force and self._cached_ts and (now - self._cached_ts) < self.CACHE_S:
                return self._rows[:limit]
            snap = _proc_stat_snapshot(self._proc_root)
            if not snap:
                self._cached_ts = now
                return []
            clk = _clk_tck()
            prev, prev_ts = self._snap, self._ts
            dt = now - prev_ts if prev_ts > 0 else 0.0
            uptime = _uptime_seconds(self._proc_root) if (dt < 1.0) else 0.0

            scored = []
            for pid, (ticks, start, rss, comm) in snap.items():
                old = prev.get(pid)
                if old is not None and old[1] == start and dt >= 1.0:
                    pct = (ticks - old[0]) * 100.0 / (clk * dt)     # 区间平均占用
                else:
                    # 首次采样或新进程：退化为生命周期平均值，下一轮转为区间值
                    elapsed = uptime - start / float(clk) if uptime > 0 else 0.0
                    pct = ticks * 100.0 / (clk * elapsed) if elapsed > 0.05 else 0.0
                scored.append((max(0.0, pct), rss, pid, comm))

            # CPU 相同的按内存降序
            scored.sort(key=lambda it: (-it[0], -it[1]))
            self._snap = snap
            self._ts = now

            rows = []
            for pct, rss, pid, comm in scored[:30]:
                rows.append({
                    "pid": pid,
                    "comm": comm,
                    "args": _proc_cmdline(pid),
                    "cpu": round(pct, 1),
                    "rss": rss,
                })
            self._rows = rows
            self._cached_ts = now
            return rows[:limit]


_SAMPLER = None
_SAMPLER_LOCK = threading.Lock()


def sampler():
    global _SAMPLER
    with _SAMPLER_LOCK:
        if _SAMPLER is None:
            _SAMPLER = ProcSampler()
        return _SAMPLER


def top_processes(limit=12, force=False):
    """进程 TOP。数据来自 /proc 快照差分。"""
    try:
        return sampler().top(limit=limit, force=force)
    except Exception:
        return []
