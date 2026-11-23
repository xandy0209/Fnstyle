#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ---------------------------------------------------------------------------
# FnStyle —— 系统监控数据采集
#
# 采集四大板块（能力对齐 fnmonitor，MIT）：
#   1. 存储      —— 存储池/数据盘容量与占用
#   2. 处理器/内存/显卡 —— CPU 使用率、内存、GPU
#   3. 温度和散热 —— CPU/主板/硬盘温度、风扇转速
#   4. 能耗      —— RAPL 实测功耗，无 RAPL 时按 TDP 模型估算
#
# 设计要点：
#   - 零第三方依赖（只用 Python 标准库），与 fnmonitor 一致；
#   - 后台轮询线程采集 + 内存缓存，HTTP 请求只读缓存（不阻塞页面）；
#   - 任何单项采集失败都只降级该项，绝不影响其它板块；
#   - CPU 使用率必须两次采样差分，故轮询线程是唯一正确的采集位置。
#
# 致谢与许可：
#   本模块为 FnStyle 独立实现，采集路径与功耗口径参考了开源项目 fnmonitor
#   （https://github.com/MisiteQ/fnmonitor ，MIT License，
#     Copyright (c) 2026 Misite齊 / iqiuzixuan）。
#   fnmonitor 的原始作者与许可证声明保留如下：
#
#   ---------------------------------------------------------------------
#   MIT License
#   Copyright (c) 2026 Misite齊
#   Copyright (c) 2026 iqiuzixuan
#   Permission is hereby granted, free of charge, to any person obtaining a
#   copy of this software and associated documentation files (the
#   "Software"), to deal in the Software without restriction, including
#   without limitation the rights to use, copy, modify, merge, publish,
#   distribute, sublicense, and/or sell copies of the Software, and to
#   permit persons to whom the Software is furnished to do so, subject to
#   the following conditions:
#   The above copyright notice and this permission notice shall be included
#   in all copies or substantial portions of the Software.
#   THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
#   OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
#   MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
#   IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
#   CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
#   TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
#   SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
#   ---------------------------------------------------------------------
# ---------------------------------------------------------------------------

import os
import re
import time
import glob
import json
import shutil
import threading

try:
    import subprocess
except Exception:  # pragma: no cover
    subprocess = None


# ⚠️ v1.1.68：部署探针。
#    采集进程是常驻后台线程，替换 FPK 后若没重启进程，跑的仍是旧代码 ——
#    出现过「本地源码已改、实机 API 返回的却是旧结构」的迷惑现象
#    （nicDetail 里有新字段、但缺 tunnel 键，正是新旧代码混合的指纹）。
#    这个常量会随 power 数据一起上报，从 API 一眼就能确认
#    「NAS 上跑的到底是哪一版代码」，省掉逐字段比对。
COLLECT_VERSION = "1.1.84"


# ⚠️ v1.1.84：告警判定周期（秒）。
#   比采集周期（3 秒）慢，比分钟级告警快。
#   温度类指标在临界点会来回抖 —— 判定太快会频繁翻转状态，
#   靠 AlertState 的边沿触发兜住，但没必要白白消耗 CPU。
ALERT_INTERVAL = 15


# ----------------------------- 基础工具 -----------------------------

def read_text(path, limit=8192):
    """安全读文本文件；失败返回空串（不抛异常）。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except Exception:
        return ""


def read_int(path):
    """安全读整数；失败返回 None。"""
    t = read_text(path, 64).strip()
    if not t:
        return None
    try:
        return int(float(t))
    except Exception:
        return None


def _net_iface_names():
    """返回内核登记在册的网络接口名集合（来自 /proc/net/dev）。

    ⚠️ v1.1.68 新增。这是判断「一个 /sys/class/net/* 条目是不是真接口」的
    **唯一可靠依据**：
      · /proc/net/dev 由内核直接导出，**只含真正的网络接口**；
      · /sys/class/net 下除此之外还有**内核符号链接**
        （bonding_masters → bonding 模块全局变量），不是接口；
      · 用「读 ifindex/address/speed/operstate 猜」全都不可靠
        —— 实测 bonding_masters 在 NAS 上 address/speed/operstate 全空，
        却仍能通过那种判据。

    格式：每行「  <接口名>: <收包字节> <收包包数> ...」，
    冒号前是接口名；前两行是表头。
    读不到时返回空集合（此时调用方会退化为不过滤，宁可多列也不漏报）。
    """
    out = set()
    try:
        with open("/proc/net/dev", "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except Exception:
        return out
    for line in lines[2:]:
        name = line.split(":", 1)[0].strip()
        if name:
            out.add(name)
    return out


def run_cmd(cmd, timeout=3):
    """执行外部命令并返回 stdout；失败返回空串。"""
    if subprocess is None:
        return ""
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=timeout)
        return p.stdout.decode("utf-8", "replace")
    except Exception:
        return ""


def _mbf(float_val):
    """字节 → 可读字符串。"""
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    v = float(float_val)
    i = 0
    while v >= 1024 and i < len(units) - 1:
        v /= 1024.0
        i += 1
    return "%.1f %s" % (v, units[i])


def _dsize(float_val):
    """字节 → 硬盘/存储池容量字符串（十进制，与 fnmonitor F.dsize 一致）。

    口径：T=1e12, G=1e9, M=1e6 —— 与磁盘厂商标称容量、fnOS 存储页一致。
    """
    try:
        b = float(float_val)
    except Exception:
        return "—"
    if b <= 0:
        return "0 MB"
    T, G, M = 1e12, 1e9, 1e6
    if b >= T:
        v = b / T
        return ("%d TB" % round(v)) if v >= 100 else \
               (("%.1f TB" % v) if v >= 10 else ("%.2f TB" % v))
    if b >= G:
        v = b / G
        return ("%d GB" % round(v)) if v >= 100 else ("%.1f GB" % v)
    return "%d MB" % round(b / M)


def _pct(part, total):
    if not total:
        return 0
    try:
        return max(0, min(100, int(round(100.0 * float(part) / float(total)))))
    except Exception:
        return 0


# ============================ 1. 存储 ============================
# 存储能力已完整迁移到独立模块 storage_collect.py（能力对齐 fnmonitor 存储板块：
# 存储池容量 / 物理盘详情 / SMART 健康与告警计数 / 硬盘分级 / 巡检历史 /
# 磁盘 IO 速率 / 休眠保护 / 软 RAID / 硬 RAID 卡）。
# 这里保留薄封装，避免大段代码重复。


def _storage_module():
    """惰性导入 storage_collect（导入失败时降级为 None）。"""
    global _STORAGE_MOD
    if _STORAGE_MOD is None:
        try:
            import storage_collect as _sc
            _STORAGE_MOD = _sc
        except Exception:
            _STORAGE_MOD = False
    return _STORAGE_MOD or None


_STORAGE_MOD = None


def collect_storage():
    """采集存储池与数据盘容量（薄封装，真实实现见 storage_collect.py）。

    返回结构：
      volumes    存储池容量（/volN）
      filesystems 文件系统清单（fsid 去重、过滤伪文件系统与网络挂载）
      disks      物理盘详情（含 SMART、分级、IO）
      diskio     各盘读写速率与 IOPS
      raid       软 RAID（/proc/mdstat）
      raidcard   硬 RAID 卡（lspci + storcli）
      scan       巡检状态
      scanLog    巡检流水
      summary    汇总统计
    """
    sc = _storage_module()
    if sc is None:
        # 降级：模块加载失败时用最基础的 statvfs 统计，至少不空屏
        return _collect_storage_fallback()
    try:
        return _STORAGE_MONITOR.collect()
    except Exception:
        return _collect_storage_fallback()


def _collect_storage_fallback():
    """storage_collect 不可用时的兜底：只统计 /volN 容量。

    ⚠️ 口径与主路径一致：排除系统卷 vol00（它只是磁盘根目录下的普通目录，
    不是存储池挂载点），容量文本用十进制。
    """
    volumes = []
    try:
        for vol in sorted(glob.glob("/vol[0-9]*")):
            if os.path.basename(vol) == "vol00":
                continue
            if not os.path.isdir(vol):
                continue
            usage = _disk_usage(vol)
            if not usage:
                continue
            volumes.append({
                "name": os.path.basename(vol), "path": vol,
                "total": usage["total"], "used": usage["used"], "free": usage["free"],
                "percent": usage["percent"],
                "totalText": _dsize(usage["total"]),
                "usedText": _dsize(usage["used"]),
                "freeText": _dsize(usage["free"]),
            })
    except Exception:
        pass
    _sum_total = sum(v["total"] for v in volumes)
    _sum_used = sum(v["used"] for v in volumes)
    _sum_free = max(0, _sum_total - _sum_used)
    return {"volumes": volumes, "filesystems": [], "disks": [], "diskio": [],
            "raid": [], "raidcard": {"type": "none", "label": "", "detail": "",
                                     "tool": "", "controllers": [], "error": ""},
            "scan": {"last_ts": 0.0, "running": False, "summary": {}, "error": ""},
            "scanLog": [],
            "summary": {"poolCount": len(volumes), "diskCount": 0, "sleepingCount": 0,
                        "total": _sum_total,
                        "used": _sum_used,
                        "free": _sum_free,
                        "percent": (int(round(100.0 * _sum_used / _sum_total))
                                    if _sum_total else 0),
                        "totalText": _dsize(_sum_total) if _sum_total else "—",
                        "usedText": _dsize(_sum_used) if _sum_used else "—",
                        "freeText": _dsize(_sum_free) if _sum_free else "—",
                        "grades": {"ok": 0, "warn": 0, "crit": 0, "unknown": 0},
                        "warnDisks": [], "raidCount": 0, "raidCardType": "none"},
            "available": bool(volumes)}


def _disk_usage(path):
    """statvfs 读取挂载点容量。

    ⚠️ 口径（与 storage_collect / fnmonitor / fnOS 存储页一致）：
    - used = (f_blocks - f_bfree) × f_frsize（含 ext4 保留块，同系统 df）
    - free = f_bavail × f_frsize（普通用户真正可用）
    """
    try:
        st = os.statvfs(path)
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        used = max(0, (st.f_blocks - st.f_bfree) * st.f_frsize)
        return {"total": total, "used": used, "free": free,
                "percent": _pct(used, total)}
    except Exception:
        return None


# 存储监控聚合器（单例）
_STORAGE_MONITOR = None


def _storage_monitor():
    """惰性创建存储监控聚合器（首次调用时建库、起巡检线程）。"""
    global _STORAGE_MONITOR
    if _STORAGE_MONITOR is None:
        sc = _storage_module()
        if sc is None:
            return None
        try:
            db_path = os.path.join(_storage_data_dir(), "storage.db")
        except Exception:
            db_path = None
        try:
            _STORAGE_MONITOR = sc.StorageMonitor(db_path, scan_interval_h=6)
            _STORAGE_MONITOR.start()
        except Exception:
            _STORAGE_MONITOR = None
    return _STORAGE_MONITOR


def _storage_data_dir():
    """存储监控数据目录（巡检历史库）。"""
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


def storage_scan_now():
    """手动触发一次硬盘巡检。"""
    m = _storage_monitor()
    if not m:
        return {"ok": False, "error": "存储监控未启用"}
    return m.scan_now()


def storage_disk_history(dev, limit=60, serial=None):
    """单盘巡检历史（可传 serial 以跨设备名轮换续查）。"""
    m = _storage_monitor()
    if not m:
        return []
    try:
        return m.disk_history(dev, limit, serial=serial)
    except Exception:
        return []


def collect_disks():
    """物理磁盘清单（薄封装，真实实现见 storage_collect.py）。"""
    sc = _storage_module()
    if sc is None:
        return []
    try:
        return sc.collect_disks_detail(with_states=False)
    except Exception:
        return []


# ==================== 2. 处理器 / 内存 / 显卡 ====================

_CPU_STATE = {"last": None, "lastTime": None, "cores": None}

# CPU 使用率的采样窗口（秒）。
# /proc/stat 时间片精度 = 1 jiffy = 10ms（USER_HZ=100）。
# 窗口内每核能分到的 jiffy 数量决定量化分辨率：
#   0.25s → ~25 jiffy/核 → 单 jiffy 占 4%   → 读数在 2%~64% 间乱跳
#   3s    → ~300 jiffy/核 → 单 jiffy 占 0.33%
#   10s   → ~1000 jiffy/核 → 单 jiffy 占 0.1% → 数值稳定
# fnmonitor 在 NAS 上的实测配置也是 interval=10，两边口径才能对得上。
CPU_SAMPLE_WINDOW = 10


def read_cpu_topology():
    """读取 CPU 拓扑，返回 (物理核心数, 逻辑线程数, 插槽数)。

    移植自 fnmonitor 的同名函数（MIT License）：
      优先 lscpu（字段稳定，x86/ARM 通用）：
        逻辑线程 = CPU(s)
        物理核心 = Socket(s) × Core(s) per socket
        插槽数   = Socket(s)
      缺失时回退 /proc/cpuinfo：
        逻辑线程 = processor 条目数
        物理核心 = (physical id, core id) 去重计数

    注意：/proc/cpuinfo 的 processor 条目数是**逻辑线程数**，
    超线程下 ≠ 物理核数 —— 直接当物理核会让「4 核 8 线程」显示成 8 核 8 线程。
    """
    cores = threads = sockets = 0

    def _first_int(s):
        m = re.search(r"\d+", s or "")
        return int(m.group(0)) if m else None

    out, rc = run_cmd(["lscpu"], timeout=10)
    if rc == 0 and out:
        kv = {}
        for line in out.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                kv[k.strip()] = v.strip()
        cpus = _first_int(kv.get("CPU(s)"))
        tpc = _first_int(kv.get("Thread(s) per core"))
        cps = _first_int(kv.get("Core(s) per socket"))
        skt = _first_int(kv.get("Socket(s)"))
        if cpus:
            threads = cpus
        if skt:
            sockets = skt
        if cps and skt:
            cores = cps * skt
        elif cpus and tpc:
            cores = cpus // tpc

    info = read_text("/proc/cpuinfo", 65536)
    if not threads:
        threads = len(re.findall(r"^processor\s*:", info, re.M))
    if not sockets:
        ids = set(re.findall(r"^physical id\s*:\s*(\d+)", info, re.M))
        sockets = len(ids) or 1
    if not cores:
        pairs = set(re.findall(r"^physical id\s*:\s*(\d+).*?^core id\s*:\s*(\d+)",
                               info, re.M | re.S))
        if pairs:
            cores = len(pairs)
        else:
            cores = threads or 1
    return cores, threads or cores, sockets


# 拓扑缓存：lscpu 是外部进程调用（几十毫秒），而 CPU 拓扑在运行期不变。
_CPU_TOPO_CACHE = {"value": None}


def _cpu_topology_cached():
    """取 CPU 拓扑（物理核 / 逻辑线程 / 插槽），结果进程级缓存。

    lscpu 每次调用要 fork 一个进程，而拓扑在运行期不变 ——
    缓存后每轮采集省掉一次 fork。缓存失败（返回全 0）也会被缓存，
    避免 lscpu 不可用时反复重试拖慢采集。
    """
    if _CPU_TOPO_CACHE["value"] is None:
        try:
            _CPU_TOPO_CACHE["value"] = read_cpu_topology()
        except Exception:
            _CPU_TOPO_CACHE["value"] = (0, 0, 0)
    return _CPU_TOPO_CACHE["value"]


def _proc_stat_cpu_times(stat):
    """解析 /proc/stat 的 CPU 时间片，返回 (汇总行, 每核行)。

    口径与 fnmonitor 的 read_cpu_times() 一致：
      · 取**前 8 个字段** user/nice/system/idle/iowait/irq/softirq/steal
      · steal 计入总量（虚拟机被宿主机占用的时间），guest/guest_nice
        已含在 user 里，不重复计入
      · cpuN 严格按数字后缀匹配，避免误收其他以 cpu 开头的行
    """
    total = None
    cores = []
    for line in stat.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "cpu":
            try:
                total = [int(x) for x in parts[1:9]]
            except ValueError:
                total = None
        elif re.match(r"^cpu\d+$", parts[0]):
            try:
                cores.append([int(x) for x in parts[1:9]])
            except ValueError:
                pass
    return total, cores


def _cpu_percent(prev, curr):
    """两次采样差分算使用率（%），与 fnmonitor 的 calc_cpu_percent() 一致。

    ⚠️ 旧实现有个隐蔽 bug：用正则 groups() 取值时，把 cpuN 的**核心号**
    也当成了时间片，导致 idle 索引整体错位一格（读到 system 而不是 idle），
    每核使用率被系统性低估。现在改为按列表下标取，口径与 fnmonitor 相同。

    口径要点（与 fnmonitor 逐位一致）：
      - idle = parts[3](idle) + parts[4](iowait)
      - total = 全部 8 个字段之和（含 steal）
      - 返回值 round(..., 1)，保留 1 位小数
    """
    if not prev or not curr:
        return None
    if len(prev) < 4 or len(curr) < 4:
        return None
    prev_idle = prev[3] + (prev[4] if len(prev) > 4 else 0)
    curr_idle = curr[3] + (curr[4] if len(curr) > 4 else 0)
    total_delta = sum(curr) - sum(prev)
    idle_delta = curr_idle - prev_idle
    if total_delta <= 0:
        return None
    return round(max(0.0, 100.0 * (1.0 - idle_delta / float(total_delta))), 1)


def collect_cpu():
    """CPU 使用率：/proc/stat 两次采样差分。

    首次调用（没有基线）时先记基线、返回 available=False，下一轮才有真实值。
    """
    out = {"available": False}
    stat = read_text("/proc/stat", 8192)
    total_line, core_lines = _proc_stat_cpu_times(stat)
    if not total_line:
        return out
    # 汇总行缺失时用各核之和兜底（fnmonitor 同样以各核为准）
    if not total_line:
        total_line = [sum(c[i] for c in core_lines) if core_lines else 0]

    cpu_total = sum(total_line)
    cpu_idle = (total_line[3] if len(total_line) > 3 else 0) + \
               (total_line[4] if len(total_line) > 4 else 0)
    per_core = []
    for n in core_lines:
        per_core.append((sum(n),
                         (n[3] if len(n) > 3 else 0) + (n[4] if len(n) > 4 else 0)))
    if not per_core:
        per_core = [(cpu_total, cpu_idle)]

    now = time.monotonic()
    prev = _CPU_STATE["last"]
    prev_t = _CPU_STATE["lastTime"]
    prev_cores = _CPU_STATE["cores"]

    # ⚠️ 基线必须存**完整的 8 字段时间片列表**，不能存 (total, idle) 元组 ——
    #    _cpu_percent() 要按 fnmonitor 的口径分别取 parts[3]/parts[4] 算 idle，
    #    只存汇总值就算不出「idle + iowait」的真实差额。
    _CPU_STATE["last"] = list(total_line)
    _CPU_STATE["cores"] = [list(n) for n in core_lines]
    _CPU_STATE["lastTime"] = now

    if prev is not None and prev_t is not None:
        dt = now - prev_t
        if dt > 0:
            # 汇总口径必须走 _cpu_percent（与 fnmonitor 逐位一致），
            # 不要在这里另写一套 —— 两套算法必然会出现 1 位小数的差异。
            pct = _cpu_percent(prev, list(total_line))
            if pct is not None:
                out["percent"] = pct
                out["available"] = True

        # 每核使用率（仅在前后核数一致时计算）
        if prev_cores and len(prev_cores) == len(core_lines):
            cores = []
            for i in range(len(core_lines)):
                pc = _cpu_percent(prev_cores[i], list(core_lines[i]))
                cores.append(0.0 if pc is None else pc)
            out["coresPercent"] = cores

    # CPU 型号
    info = read_text("/proc/cpuinfo", 8192)
    mm = re.search(r"^model name\s*:\s*(.+)$", info, re.M)
    if mm:
        out["model"] = mm.group(1).strip()
    # 拓扑：物理核 / 逻辑线程 / 插槽（lscpu 优先，/proc/cpuinfo 回退）
    # ⚠️ lscpu 是外部进程调用（几十毫秒），而拓扑在运行期不变 ——
    #    这里做进程级缓存，避免每次采集都 fork 一次。
    tcores, tthreads, tsockets = _cpu_topology_cached()
    out["cores"] = tcores or (len(re.findall(r"^processor\s*:", info, re.M)) or len(per_core))
    out["threads"] = tthreads or out["cores"]
    out["sockets"] = tsockets or 1
    out["loadavg"] = read_text("/proc/loadavg", 256).strip().split()[:3]
    # 频率：优先 cpufreq 的实时频率，回退 /proc/cpuinfo 的 MHz
    khz = read_int("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq")
    freq_mhz = None
    if khz:
        freq_mhz = round(khz / 1000.0, 1)
        out["freqText"] = "%.2f GHz" % (khz / 1000000.0)
    else:
        fm = re.search(r"^cpu MHz\s*:\s*([\d.]+)", info, re.M)
        if fm:
            freq_mhz = float(fm.group(1))
            out["freqText"] = "%.2f GHz" % (float(fm.group(1)) / 1000.0)
    out["freqMhz"] = freq_mhz
    return out


def collect_memory():
    """/proc/meminfo 内存使用量。"""
    out = {"available": False}
    info = read_text("/proc/meminfo", 8192)
    if not info:
        return out
    vals = {}
    for m in re.finditer(r"^(\w+):\s+(\d+)\s*kB", info, re.M):
        vals[m.group(1)] = int(m.group(2)) * 1024
    if "MemTotal" not in vals:
        return out
    total = vals["MemTotal"]
    avail = vals.get("MemAvailable", vals.get("MemFree", 0))
    used = total - avail
    out.update({
        "available": True,
        "total": total,
        "used": used,
        "availableBytes": avail,
        "percent": _pct(used, total),
        "totalText": _mbf(total),
        "usedText": _mbf(used),
        "availText": _mbf(avail),
        # 内存构成三件套（对应 fnmonitor 的 memParts）：
        #   app   = 实际占用（total - available）
        #   cache = Cached（Linux 用空闲内存加速读盘）
        #   buf   = Buffers
        #   free  = 剩余
        "cached": vals.get("Cached", 0),
        "buffers": vals.get("Buffers", 0),
        "swapTotal": vals.get("SwapTotal", 0),
        "swapUsed": max(0, vals.get("SwapTotal", 0) - vals.get("SwapFree", 0)),
    })
    return out


def collect_gpu():
    """显卡信息。

    优先 nvidia-smi（NVIDIA 独显），其次 /sys/class/drm（Intel/AMD 核显）。
    降级：都没有则返回 available=False，前端显示「未检测到显卡」。

    核显占用率的读取分三档（与 fnmonitor 口径一致）：
      1. **pmu** —— i915 PMU 引擎 busy 计数器（与 intel_gpu_top 同源），
         能分出渲染/视频/视频增强/复制/计算五个引擎；
      2. **rc6** —— RC6 驻留时间估算整体活跃度，不区分引擎；
      3. **driver** —— 驱动自身开放的 gpu_busy_percent；
      都拿不到 → util=None，前端显示「未能读取」。
    """
    gpus = []
    # --- NVIDIA ---
    if shutil.which("nvidia-smi"):
        raw = run_cmd(["nvidia-smi",
                       "--query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total,power.draw",
                       "--format=csv,noheader,nounits"], timeout=4)
        for line in raw.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 6:
                continue
            mem_total = _to_int(parts[4])
            gpus.append({
                "vendor": "NVIDIA", "name": parts[0], "type": "nvidia",
                "temp": _to_int(parts[1]), "util": _to_int(parts[2]),
                "memUsed": _to_int(parts[3]), "memTotal": mem_total,
                "memPct": round(_to_int(parts[3]) * 100.0 / mem_total, 1) if mem_total else None,
                "sharedMemory": False,
                "power": _to_float(parts[5]), "utilAvailable": True,
                "utilSource": "driver", "engines": None, "warming": False,
                "freqMhz": None, "freqReqMhz": None,
            })
    # --- DRM 核显 ---
    if not gpus:
        for card in sorted(glob.glob("/sys/class/drm/card[0-9]*")):
            # 排除非 GPU 的 DRM 节点（renderD*、card0-DP-1 这类输出接口）
            if not re.match(r"^card[0-9]+$", os.path.basename(card)):
                continue
            dev = os.path.join(card, "device")
            uevent = read_text(os.path.join(dev, "uevent"), 1024)
            m = re.search(r"DRIVER=(\w+)", uevent)
            drv = m.group(1) if m else ""
            vendor = {"i915": "Intel", "xe": "Intel", "amdgpu": "AMD",
                      "nouveau": "NVIDIA", "nvidia": "NVIDIA"}.get(drv, "GPU")
            name = _gpu_name(dev, drv, os.path.basename(card))
            temp = None
            hw = glob.glob(os.path.join(dev, "hwmon", "hwmon*", "temp1_input"))
            if hw:
                t = read_int(hw[0])
                if t:
                    temp = round(t / 1000.0, 1)
            mem_used = read_int(os.path.join(dev, "mem_info_vram_used"))
            mem_total = read_int(os.path.join(dev, "mem_info_vram_total"))

            g = {
                "vendor": vendor, "name": name,
                "type": {"Intel": "igpu", "AMD": "amd", "NVIDIA": "nvidia"}.get(vendor, "igpu"),
                "temp": temp,
                "util": None, "utilAvailable": False,
                "utilSource": None, "engines": None, "warming": False,
                "memUsed": mem_used, "memTotal": mem_total,
                "memPct": round(mem_used * 100.0 / mem_total, 1) if (mem_used and mem_total) else None,
                # 核显与系统共享内存，没有独立显存
                "sharedMemory": not mem_total,
                "power": None,
                "freqMhz": None, "freqReqMhz": None,
            }

            # --- Intel 核显：PMU 引擎占用 / RC6 估算 ---
            if drv in ("i915", "xe"):
                pci = os.path.basename(os.path.realpath(dev))
                try:
                    import gpu_pmu
                    iu = gpu_pmu.intel_gpu_usage(pci)
                    g["util"] = iu.get("util")
                    g["utilSource"] = iu.get("utilSource")
                    g["engines"] = iu.get("engines")
                    g["warming"] = bool(iu.get("warming"))
                    g["freqMhz"] = iu.get("freqMhz")
                    g["freqReqMhz"] = iu.get("freqReqMhz")
                    g["utilAvailable"] = g["util"] is not None
                except Exception:
                    pass

            # --- AMD / 其它：驱动自身的 gpu_busy_percent ---
            if g["util"] is None:
                busy = read_int(os.path.join(dev, "gpu_busy_percent"))
                if busy is not None:
                    g["util"] = busy
                    g["utilAvailable"] = True
                    g["utilSource"] = "driver"

            gpus.append(g)
    return {"gpus": gpus, "available": bool(gpus)}


def _gpu_name(dev, driver, fallback):
    """尽量给显卡一个可读型号。

    1) lspci 若存在，能直接拿到「VGA compatible controller: Intel ...」；
    2) 否则用 PCI vendor/device ID 拼一个可辨识的名字。
    """
    out = run_cmd(["lspci"], timeout=3)
    for line in out.splitlines():
        low = line.lower()
        if not any(k in low for k in ("vga", "3d controller", "display controller",
                                      "graphics controller")):
            continue
        if any(k in low for k in ("intel", "amd", "radeon", "nvidia",
                                  "geforce", "quadro")):
            return re.sub(r"^\S+\s+", "", line).strip()
    # 回退：用 PCI vendor/device ID
    vid = read_text(os.path.join(dev, "vendor"), 64).strip().replace("0x", "")
    did = read_text(os.path.join(dev, "device"), 64).strip().replace("0x", "")
    if vid and did:
        v = vendor = {"8086": "Intel", "1002": "AMD", "10de": "NVIDIA",
                      "1a03": "ASPEED", "102b": "Matrox"}.get(vid.lower(), "GPU")
        model = "%s GPU (%s:%s)" % (v, vid.lower(), did.lower())
        if driver:
            model += " / %s" % driver
        return model
    return driver.upper() if driver else fallback


def _to_int(s):
    try:
        return int(float(str(s).strip()))
    except Exception:
        return None


def _to_float(s):
    try:
        return round(float(str(s).strip()), 2)
    except Exception:
        return None


# ==================== 3. 温度和散热 ====================

def _drivetemp_dev_map():
    """drivetemp 的 hwmon 实例名不带盘名（label 缺失时前端只会看到
    「drivetemp 1」「drivetemp 2」，分不清是哪块盘）。
    反查 /sys/block/*/device/hwmon 建立 (hwmon 名 → sd 设备名) 映射。
    实机（v1.1.49 探针核实）：
      /sys/block/sda/device/hwmon/hwmon2  →  hwmon2 = sda
      /sys/block/sdb/device/hwmon/hwmon3  →  hwmon3 = sdb
      /sys/block/sdc/device/hwmon/hwmon4  →  hwmon4 = sdc
    映射成功后前端把 sd 名换算成「存储空间 volN」显示。
    """
    m = {}
    for sd in glob.glob("/sys/block/sd[a-z]"):
        for hw in glob.glob(os.path.join(sd, "device", "hwmon", "hwmon*")):
            m[os.path.basename(hw)] = os.path.basename(sd)
    return m


def _hwmon_sensors():
    """遍历 /sys/class/hwmon 读取温度、电压与风扇。

    返回 (temps, fans, volts)。
    温度条目带 type 分类（cpu/pch/board/acpi/gpu/other）与 max/crit 阈值，
    供前端按 fnmonitor 的口径着色与分组。
    ⚠️ v1.1.49：drivetemp 实例映射回 sd 设备名（如 sda）并强制 type='disk'，
    前端据此把它换算成「存储空间 volN」。_sensor_type('sda') 不会命中
    disk 正则（sda 不含 nvme/ssd/hdd/disk/drive 字样），所以必须显式指定。
    """
    temps = []
    fans = []
    volts = []
    drv_map = _drivetemp_dev_map()
    for d in sorted(glob.glob("/sys/class/hwmon/hwmon[0-9]*")):
        name = read_text(os.path.join(d, "name"), 128).strip() or os.path.basename(d)
        for tp in sorted(glob.glob(os.path.join(d, "temp[0-9]*_input"))):
            base = tp[:-len("_input")]
            label = read_text(base + "_label", 128).strip()
            val = read_int(tp)
            if val is None or val <= 0:
                continue
            idx = re.search(r"temp(\d+)_input", tp)
            tname = label or ("%s %s" % (name, idx.group(1) if idx else ""))
            ttype = _sensor_type(tname)
            if name == "drivetemp":
                dev = drv_map.get(os.path.basename(d))
                if dev:
                    tname = dev
                ttype = "disk"
            temps.append({
                "name": tname,
                "chip": name,
                "value": round(val / 1000.0, 1),
                "max": _hwmon_scale(read_int(base + "_max")),
                "crit": _hwmon_scale(read_int(base + "_crit")),
                "type": ttype,
            })
        for fp in sorted(glob.glob(os.path.join(d, "fan[0-9]*_input"))):
            rpm = read_int(fp)
            if rpm is None or rpm <= 0:
                continue
            idx = re.search(r"fan(\d+)_input", fp)
            fans.append({
                "name": "%s %s" % (name, idx.group(1) if idx else ""),
                "rpm": rpm,
            })
        for vp in sorted(glob.glob(os.path.join(d, "in[0-9]*_input"))):
            base = vp[:-len("_input")]
            label = read_text(base + "_label", 128).strip()
            val = read_int(vp)
            if val is None or val <= 0:
                continue
            idx = re.search(r"in(\d+)_input", vp)
            volts.append({
                "name": label or ("%s %s" % (name, idx.group(1) if idx else "")),
                "value": round(val / 1000.0, 2),
            })
    return temps, fans, volts


def _sensor_type(name):
    """按传感器名字猜测类型（对齐 fnmonitor 的 TN 分类）。

    fnmonitor 的分类：cpu / pch / board / acpi / disk / gpu
    前端据此决定阈值口径与标签样式。
    """
    n = (name or "").lower()
    if re.search(r"gpu|core|package|x86_pkg|cpu", n):
        return "cpu" if re.search(r"cpu|package|core|x86_pkg", n) else "gpu"
    if re.search(r"pch|chipset|南桥", n):
        return "pch"
    if re.search(r"board|mobo|主板", n):
        return "board"
    if re.search(r"acpi|thermal", n):
        return "acpi"
    if re.search(r"nvme|ssd|hdd|disk|drives?", n):
        return "disk"
    return "other"


def _hwmon_scale(v):
    if v is None or v <= 0:
        return None
    return round(v / 1000.0, 1)


def collect_temp_fan():
    """温度与散热：thermal_zone（CPU）→ hwmon（主板/硬盘/风扇/电压）。

    返回结构对齐 fnmonitor 的 sensors 口径：
      temps   温度测点（含 type 分类与 max/crit 阈值）
      fans    风扇转速
      volts   电压读数
      cpuTemp CPU 封装温度
      mbTemp  主板温度
      disks   物理磁盘清单（复用存储板块，避免重复读 sysfs）
    """
    temps = []
    fans = []
    volts = []
    source = []

    # --- CPU 温度优先走 thermal_zone ---
    cpu_temp = None
    mb_temp = None
    for zone in sorted(glob.glob("/sys/class/thermal/thermal_zone[0-9]*")):
        ztype = read_text(os.path.join(zone, "type"), 128).strip()
        raw = read_int(os.path.join(zone, "temp"))
        if raw is None or raw <= 0:
            continue
        val = round(raw / 1000.0, 1)
        label = ztype or os.path.basename(zone)
        temps.append({"name": label, "chip": "thermal", "value": val,
                      "max": None, "crit": None,
                      "type": _sensor_type(label)})
        if cpu_temp is None and re.search(r"x86_pkg_temp|cpu|soc|package", label, re.I):
            cpu_temp = val
        elif mb_temp is None and re.search(r"acpitz|mobo|board|mainboard", label, re.I):
            mb_temp = val
    if temps:
        source.append("thermal")

    # --- hwmon 补充（主板/网卡/硬盘），并补风扇与电压 ---
    hw_temps, hw_fans, hw_volts = _hwmon_sensors()
    if hw_temps:
        source.append("hwmon")
    if hw_volts:
        source.append("volt")
    # 去重：同名同值只留一条
    seen = set()
    for t in temps + hw_temps:
        key = (t["name"], t["value"])
        if key in seen:
            continue
        seen.add(key)
        temps.append(t)
    for f in hw_fans:
        fans.append(f)
    for v in hw_volts:
        volts.append(v)

    # 硬盘温度：复用存储板块的物理磁盘清单（避免同一批 sysfs 读两遍）
    disks = collect_disks()

    if cpu_temp is None:
        for t in temps:
            if re.search(r"coretemp|k10temp|cpu|package", t["name"], re.I):
                cpu_temp = t["value"]
                break
    if mb_temp is None:
        for t in temps:
            if t.get("type") in ("board", "pch", "acpi") and t["name"] != "acpitz":
                mb_temp = t["value"]
                break

    return {
        "available": bool(temps or fans),
        "temps": temps,
        "fans": fans,
        "volts": volts,
        "disks": disks,
        "cpuTemp": cpu_temp,
        "mbTemp": mb_temp,
        "source": "+".join(source) if source else "none",
    }


# ============================ 4. 能耗 ============================

_RAPL = {"last": None, "lastTime": None}


def _rapl_read(domain):
    """读 RAPL 域能量（微焦耳）。"""
    base = "/sys/class/powercap"
    p = os.path.join(base, domain)
    e = read_int(os.path.join(p, "energy_uj"))
    if e is None:
        return None
    mx = read_int(os.path.join(p, "max_energy_range_uj")) or 262143300000
    return {"energy": e, "max": mx}


def _rapl_sample():
    out = {}
    for key, dom in (("package", "intel-rapl:0"),
                     ("core", "intel-rapl:0:0"),
                     ("uncore", "intel-rapl:0:1"),
                     ("dram", "intel-rapl:0:2")):
        v = _rapl_read(dom)
        if v:
            out[key] = v
    return out


def _net_dev_counters():
    """读 /proc/net/dev，返回 {接口名: (rx_bytes, tx_bytes)}。

    与 fnmonitor 的 read_net_dev() 同口径：
      · 字段 0 = 收包字节，字段 8 = 发包字节；
      · 解析失败的行直接跳过，不影响其它接口。
    """
    out = {}
    try:
        with open("/proc/net/dev", "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except Exception:
        return out
    for line in lines:
        if ":" not in line:
            continue
        name, _, rest = line.partition(":")
        name = name.strip()
        fields = rest.split()
        if len(fields) < 16:
            continue
        try:
            out[name] = (int(fields[0]), int(fields[8]))
        except (ValueError, IndexError):
            continue
    return out


# 虚拟网卡前缀 —— 这些接口的流量不计入「整机网络」统计。
# ⚠️ 与 v1.1.68 的物理网口判定**刻意用不同的口径**：
#    物理网口数只认有 PCI device 的接口（用户问「我有几个网口」）；
#    而整机流量要把**所有真实对外通信**都算进来 —— 包括
#    tailscale0（VPN 隧道，用户确实在用它传数据）。
#    但 docker0 / br-* / veth* 是**容器内部的虚拟转发**，
#    它们转发的是同一份流量，计入会重复统计 → 必须排除。
VIRT_IFACE_PREFIXES = ("docker", "br-", "veth", "virbr", "tun", "bonding_masters")


def _counted_ifaces(counters):
    """从 {接口: (rx,tx)} 里挑出「整机流量」应计入的接口。

    排除 lo（本地回环）与容器虚拟桥（会重复统计）。
    """
    out = {}
    for name, v in counters.items():
        if name == "lo":
            continue
        if name.startswith(VIRT_IFACE_PREFIXES):
            continue
        out[name] = v
    return out


_NET_STATE = {"prev": None, "prevTs": None}


def _net_state_path():
    """今日流量基准的持久化文件（跨进程重启保留当日基准）。"""
    return os.path.join(_storage_data_dir(), "net_daily.json")


def _load_net_state():
    try:
        with open(_net_state_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_net_state(st):
    try:
        p = _net_state_path()
        d = os.path.dirname(p)
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f)
        os.replace(tmp, p)
        return True
    except Exception:
        return False


def _today_key():
    """今日的日期键（本地时区）。跨零点自然变化 → 今日流量自动归零重算。"""
    return time.strftime("%Y-%m-%d")


def collect_network():
    """整机网络流量：实时上下行速率 + 今日累计。

    口径（对齐 fnmonitor 的 net 模块）：
      · **实时速率** = 两次采样之间各接口累计字节的差分；
      · **今日累计** = 当前累计字节 − 当日 0 点时的基准字节，
        基准持久化在 net_daily.json，跨进程重启不丢；
      · **接口范围** = 排除 lo 与容器虚拟桥（docker*/br-*/veth* 等），
        但**包含** tailscale0 等 VPN 隧道口（用户确实在用它传数据）。

    ⚠️ 注意与 v1.1.68 的「物理网口数」口径**刻意不同**：
       物理网口数回答「我有几个网口」（只认 PCI 设备），
       流量统计回答「实际传了多少数据」（要含隧道口）。
    """
    counters = _net_dev_counters()
    counted = _counted_ifaces(counters)
    total_rx = sum(v[0] for v in counted.values())
    total_tx = sum(v[1] for v in counted.values())

    now = time.time()
    prev = _NET_STATE.get("prev")
    prev_ts = _NET_STATE.get("prevTs")
    rx_rate = tx_rate = 0.0
    if prev is not None and prev_ts:
        dt = now - prev_ts
        if dt > 0:
            rx_rate = max(0, total_rx - prev.get("totalRx", 0)) / dt
            tx_rate = max(0, total_tx - prev.get("totalTx", 0)) / dt
    _NET_STATE["prev"] = {"totalRx": total_rx, "totalTx": total_tx,
                          "ifaces": counted}
    _NET_STATE["prevTs"] = now

    # ---- 今日累计：基准 + 日期键 ----
    day = _today_key()
    st = _load_net_state()
    if st.get("day") != day:
        # 新的一天（或首次运行）→ 以当前累计为新基准，今日流量从 0 起算
        st = {"day": day, "baseRx": total_rx, "baseTx": total_tx}
        _save_net_state(st)
    base_rx = int(st.get("baseRx") or 0)
    base_tx = int(st.get("baseTx") or 0)
    today_rx = max(0, total_rx - base_rx)
    today_tx = max(0, total_tx - base_tx)

    # ---- 开机累计（/proc/net/dev 计数器开机归零 → 当前值即开机以来）----
    boot_rx = total_rx
    boot_tx = total_tx

    ifaces = []
    for name, (rx, tx) in sorted(counted.items()):
        prev_if = (prev or {}).get("ifaces", {}).get(name)
        r_rate = t_rate = 0.0
        if prev_if and prev_ts:
            dt = now - prev_ts
            if dt > 0:
                r_rate = max(0, rx - prev_if[0]) / dt
                t_rate = max(0, tx - prev_if[1]) / dt
        ifaces.append({
            "name": name, "rxBytes": rx, "txBytes": tx,
            "rxRate": round(r_rate, 1), "txRate": round(t_rate, 1),
        })
    ifaces.sort(key=lambda x: -(x["rxRate"] + x["txRate"]))

    return {
        "available": bool(counted),
        "day": day,
        "rxRate": round(rx_rate, 1),
        "txRate": round(tx_rate, 1),
        "totalRx": total_rx,
        "totalTx": total_tx,
        "todayRx": today_rx,
        "todayTx": today_tx,
        "bootRx": boot_rx,
        "bootTx": boot_tx,
        "ifaceCount": len(counted),
        "ifaces": ifaces,
    }


def collect_power(cpu_percent, disks_active):
    """整机功耗。

    优先级：RAPL 实测（package + dram + 主板基线 + 硬盘）→ 无 RAPL 时按 TDP 模型估算。
    口径与 fnmonitor 一致：package 已含 core/uncore，不能重复相加。
    """
    out = {"ok": False, "source": "unknown"}
    # 硬盘功耗（SSD 2.5W / HDD 8W）
    disks_w = 0.0
    disk_count = 0
    disk_typical = 8.0
    for blk in glob.glob("/sys/block/sd*[a-z]") + glob.glob("/sys/block/nvme[0-9]*n[0-9]*"):
        rot = read_int(os.path.join(blk, "queue", "rotational"))
        w = 2.5 if rot == 0 else 8.0
        disks_w += w
        disk_count += 1
        disk_typical = w

    # 网卡固定功耗（估算用）
    #
    # ⚠️ v1.1.67：原来这里数的是 /sys/class/net 下的**网络接口**，
    #    被显示成「网卡 2 口」，但**接口数 ≠ 物理网口数**：
    #    · bond0（ bonding 聚合口）算 1 个，但它背后可能是 2 个物理口
    #    · 虚拟网桥 / 容器口也算进来
    #    → 老板看到「2 口」会问「我 NAS 真有两个网口？」
    #
    #    修法：**物理网口**只认有 PCI/平台设备链接的接口
    #    （/sys/class/net/<if>/device 存在 → 真实网卡）；
    #    虚拟口（docker0/br-*/veth*/virbr*/tun*/lo）排除；
    #    bonding 聚合口则按其成员 slave 的**物理口**计。
    #    两个数都上报：nics（物理口，用于功耗估算）+ nics_iface（接口数，仅供排查）。
    nics = 0            # 物理网口数（用于功耗估算）
    nics_iface = 0      # 非虚拟网络接口数（排查用）
    nic_detail = []     # 每个接口的明细
    _phys_devs = set()  # 已计入的物理设备路径（去重）
    # ⚠️ v1.1.68：/sys/class/net 下不只有网络接口，还有**内核符号链接**
    #    （如 bonding_masters —— 指向 bonding 模块的全局变量）。
    #    glob 会把它们一起扫到，用「读某个属性文件」来猜是不是接口
    #    （ifindex / address / speed / operstate）**全都不可靠**：
    #    实测 bonding_masters 在 NAS 上 address/speed/operstate 皆空，
    #    却仍能通过判据 —— 说明它至少有一个属性能被读到非空值。
    #
    #    正解：用 **/proc/net/dev**（内核权威的网络接口清单）来判定。
    #    这份表由内核直接导出，**只含真正的网络接口**，一行一个，
    #    格式是「  <接口名>: <收包字节> ...」，冒号前就是接口名。
    #    不在表里的 → 根本不是接口 → 直接跳过。
    _real_ifaces = _net_iface_names()
    for nic in sorted(glob.glob("/sys/class/net/*")):
        base = os.path.basename(nic)
        if base == "lo" or base.startswith(("docker", "br-", "veth", "virbr", "tun")):
            continue
        # 不是内核登记在册的接口 → 跳过（bonding_masters 等内核符号链接）
        # ⚠️ 读不到 /proc/net/dev 时（_real_ifaces 为空）**不过滤** ——
        #    否则会把所有接口都排掉，物理口直接变 0，比原来的 bug 更糟。
        #    宁可多列一行，也不要报错数。
        if _real_ifaces and base not in _real_ifaces:
            continue
        _ifindex = read_text(os.path.join(nic, "ifindex"), 32).strip()
        _addr = read_text(os.path.join(nic, "address"), 64).strip()
        _speed = read_text(os.path.join(nic, "speed"), 64).strip()
        _state = read_text(os.path.join(nic, "operstate"), 64).strip()
        nics_iface += 1
        # 设备链接 → 物理网卡；无链接 → 虚拟口
        dev_link = os.path.realpath(os.path.join(nic, "device"))
        is_phys = bool(dev_link) and os.path.exists(os.path.join(nic, "device"))
        slaves = []
        # bonding 聚合口：成员 slave 在 /sys/class/net/<master>/bonding/slaves
        slave_dir = os.path.join(nic, "bonding", "slaves")
        if os.path.isdir(slave_dir):
            try:
                slaves = sorted(os.listdir(slave_dir))
            except OSError:
                slaves = []
        nic_detail.append({
            "name": base,
            # 三态：物理口 / 聚合口（bond，成员另列）/ 虚拟口
            "kind": ("bond" if slaves else ("phys" if is_phys else "virt")),
            "physical": bool(is_phys),
            "slaves": slaves,
            "mac": _addr,
            "speed": _speed,
            "state": _state,
            # ⚠️ v1.1.68：带上 ifindex —— 这是接口的**内核索引号**，
            #    有值即证明它是内核登记在册的真接口。
            #    （诊断用：一旦某个条目的 ifindex 为空，就说明它不是接口。）
            "ifindex": _ifindex,
            "driver": os.path.basename(os.path.realpath(
                os.path.join(nic, "device", "driver"))) if is_phys else "",
        })
        # ⚠️ v1.1.67：Tailscale / WireGuard 这类**隧道虚拟网卡**有 speed/state，
        #    但 speed = -1、mac 为空、且没有 PCI device。
        #    它们不占物理网口，但要在清单里标出来（用户会问「这个算不算口」）。
        if not is_phys and not slaves:
            nd = nic_detail[-1]
            nd["tunnel"] = bool(nd["speed"] == "-1" or not nd["mac"])
        # 物理口计数：**按物理设备去重**
        #   · 有 device 链接的接口 → 记 1 个物理口
        #   · bonding 聚合口本身**不额外计数** —— 它的 slave 本身就是带
        #     device 的物理接口（eth0/eth1），会被下面那条规则记上。
        #     （若 slave 没有独立 device，才按 slave 数补记。）
        if is_phys and dev_link not in _phys_devs:
            _phys_devs.add(dev_link)
            nics += 1
        elif slaves and not is_phys:
            # 聚合口自身无 device（少见）→ 按 slave 数补
            for sl in slaves:
                sl_link = os.path.realpath(os.path.join("/sys/class/net", sl, "device"))
                if sl_link and sl_link not in _phys_devs:
                    _phys_devs.add(sl_link)
                    nics += 1
    nic_fixed = 5.0
    base_fixed = 8.0

    # --- RAPL 实测 ---
    sample = _rapl_sample()
    if sample:
        now = time.monotonic()
        prev = _RAPL["last"]
        prev_t = _RAPL["lastTime"]
        _RAPL["last"] = sample
        _RAPL["lastTime"] = now
        if prev is not None and prev_t is not None:
            dt = max(0.5, now - prev_t)
            watts = {}
            for k in ("package", "core", "uncore", "dram"):
                if k in sample and k in prev:
                    d = sample[k]["energy"] - prev[k]["energy"]
                    if d < 0:
                        d += sample[k]["max"]
                    watts[k] = round(d / dt / 1e6, 2)
            if watts.get("package") is not None:
                base_w = base_fixed
                total = watts["package"] + watts.get("dram", 0.0) + base_w + disks_w
                out.update({
                    "ok": True, "source": "rapl",
                    "total": round(total, 1),
                    "package": watts.get("package"),
                    "core": watts.get("core"),
                    "uncore": watts.get("uncore"),
                    "dram": watts.get("dram"),
                    "base": base_w,
                    "disksW": round(disks_w, 1),
                    "disksActive": disk_count,
                    "nics": nics,
                    # ⚠️ v1.1.67：nics 现在是**物理网口**数（不是网络接口数）。
                    #    下面两个字段用于前端展示与排查。
                    "nicsIface": nics_iface,
                    "nicDetail": nic_detail,
                    # ⚠️ v1.1.68：部署探针（见 COLLECT_VERSION 注释）
                    "collectVersion": COLLECT_VERSION,
                    "estimation_params": {
                        "tdp_w": 65.0, "disk_typical_w": disk_typical,
                        "nic_fixed_w": nic_fixed, "base_w": base_fixed,
                    },
                })
                return out

    # --- TDP 估算回退 ---
    try:
        tdp = 65.0
        cpuinfo = read_text("/proc/cpuinfo", 8192)
        m = re.search(r"^model name\s*:\s*(.+)$", cpuinfo, re.M)
        if m:
            name = m.group(1).lower()
            # 常见桌面/服务器 U 的大致 TDP 档位
            for kw, w in (("ryzen 9", 120), ("ryzen 7", 65), ("ryzen 5", 65),
                          ("i9", 125), ("i7", 65), ("i5", 65),
                          ("epyc", 240), ("xeon", 200)):
                if kw in name:
                    tdp = float(w)
                    break
        cpu_p = float(cpu_percent or 0)
        disk_w = (disks_w / disk_count) if disk_count else 8.0
        base = tdp * (cpu_p / 100.0 * 0.7 + 0.3)
        total = base + disk_count * disk_w + nics * nic_fixed
        out.update({
            "ok": True, "source": "estimated",
            "total": round(total, 1),
            "package": round(base, 1),
            "core": None, "uncore": None, "dram": None,
            "base": round(base, 1),
            "disksW": round(disks_w, 1),
            "disksActive": disk_count,
            "tdp": tdp,
            "nics": nics,
            "nicsIface": nics_iface,
            "nicDetail": nic_detail,
            # ⚠️ v1.1.68：部署探针（见 COLLECT_VERSION 注释）
            "collectVersion": COLLECT_VERSION,
            "estimation_params": {
                "tdp_w": tdp, "disk_typical_w": disk_w,
                "nic_fixed_w": nic_fixed, "base_w": base_fixed,
            },
        })
    except Exception:
        out = {"ok": False, "source": "unknown"}
    return out


# ===================== 轮询线程 + 缓存 =====================

class MonitorCollector(object):
    """后台轮询采集器：每 interval 秒刷新一次缓存，HTTP 只读缓存。

    ⚠️ CPU 使用率与其它指标的采样窗口**故意不同**：
      - 其它指标（内存/温度/硬盘/GPU）：interval 秒（默认 3 秒）刷新一次
      - CPU 使用率与每核占用率：CPU_SAMPLE_WINDOW 秒（默认 10 秒）一次

    为什么 CPU 要单独用 10 秒窗口：
      /proc/stat 的时间片精度是 **1 jiffy = 10ms**（USER_HZ=100）。
      采样窗口内每核能分到的 jiffy 数量决定了量化分辨率：
        0.25s 窗口 → 每核仅 ~25 jiffy → 单 jiffy 占 4% → 读数在 2%~64% 间乱跳
        3s   窗口 → 每核约 300 jiffy → 单 jiffy 占 0.33%
        10s  窗口 → 每核约 1000 jiffy → 单 jiffy 占 0.1% → 数值稳定
      fnmonitor 在 NAS 上的实际配置（/vol1/@appdata/fnmonitor/config.json）
      也是 `interval = 10`，两边口径才能对得上。

      实测（同一台 NAS、同一份 /proc/stat、fnmonitor 原始算法离线复算）：
        0.25s 窗口读数序列：35.8 / 64.0 / 31.7 / 2.0 / 27.9 / 4.7   ← 极差 62 个百分点
        10s   窗口读数序列：34.4 / 43.7 / 20.7 / 17.8 / 14.9 / 4.8   ← 极差 39 个百分点
    """

    def __init__(self, interval=3):
        self.interval = interval
        self._lock = threading.Lock()
        self._cache = {}
        self._thread = None
        self._stop = threading.Event()
        self._history = None
        # CPU 专用线程与它自己的锁（与主缓存分开，避免 3 秒循环读半截数据）
        self._cpu_thread = None
        self._cpu_lock = threading.Lock()
        self._cpu_cache = {}
        # ⚠️ v1.1.84：告警线程（超阈值判定 + 通知推送）
        #   独立成线程而不是塞进采集循环：
        #   · 采集循环 3 秒一次，是「显示」用的，不能被推送拖慢；
        #   · 推送是网络 IO，失败时要等超时（最多 10 秒），
        #     塞进采集循环会让整个监控页面都卡住；
        #   · 告警判定周期 15 秒 —— 比采集慢，但比分钟级告警及时。
        self._alert_thread = None
        self._alert_state = None
        self._alert_last = None      # 最近一次推送结果（给前端看）
        self._alert_lock = threading.Lock()

    def snapshot(self):
        with self._lock:
            return dict(self._cache)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        # CPU 独立采样线程（10 秒窗口），与 3 秒主循环并行
        self._cpu_thread = threading.Thread(target=self._cpu_loop, daemon=True)
        self._cpu_thread.start()
        # 启动硬盘巡检线程（定时全盘扫 SMART，独立于 3 秒采集循环）
        _storage_monitor()
        # 启动历史写入线程（每 60 秒一条，供处理器抽屉的趋势图使用）
        self._start_history()
        # ⚠️ v1.1.84：启动告警线程（超阈值判定 + 通知推送）
        self._start_alerts()

    # ---------------- 告警线程（v1.1.84） ----------------

    def _start_alerts(self):
        """启动告警判定与推送线程。

        独立成线程：采集循环 3 秒一次是给页面显示用的，
        推送是网络 IO（失败要等超时），两者混在一起会拖慢监控页面。
        """
        if self._alert_thread is not None:
            return
        try:
            import monitor_alerts as ma
        except Exception:
            return
        try:
            self._alert_state = ma.AlertState()
            self._alert_thread = threading.Thread(target=self._alert_loop,
                                                  daemon=True)
            self._alert_thread.start()
        except Exception:
            self._alert_thread = None

    def _alert_loop(self):
        """告警循环：每 ALERT_INTERVAL 秒判定一次，超阈值就推送。

        判定周期 15 秒的理由：
          · 采集周期是 3 秒 —— 每 3 秒判一次太频繁，
            温度在临界点附近会来回抖，判定结果频繁翻转；
          · 15 秒足够快（用户不会察觉「晚了 15 秒」），
            又足够慢（不会因为抖动而反复触发）。
        · 真正的「刷屏」防护在 AlertState 里（边沿触发 + 静默期 + 等级升级立即发）。
        """
        try:
            import monitor_alerts as ma
        except Exception:
            return

        interval = ALERT_INTERVAL
        while not self._stop.is_set():
            try:
                self._alert_once(ma)
            except Exception:
                pass
            self._stop.wait(interval)

    def _alert_once(self, ma):
        """执行一次告警判定与推送。"""
        cfg = ma.load_config()
        snap = self.snapshot()
        alerts = ma.evaluate(snap)
        if self._alert_state is None:
            self._alert_state = ma.AlertState()

        # 只保留「配置里勾选了的指标」与「配置里勾选了的级别」
        metrics_cfg = cfg.get("metrics") or {}
        levels_cfg = cfg.get("levels") or []
        picked = []
        for a in alerts:
            if not metrics_cfg.get(a.get("metric"), False):
                continue
            if levels_cfg and a.get("level") not in levels_cfg:
                continue
            picked.append(a)

        # 已回到正常的指标要清掉告警状态，否则下次超限不会重新触发
        active = set(a.get("metric") for a in picked)
        for key in list(self._alert_state.active_metrics()):
            if key not in active:
                self._alert_state.update_normal(key)

        if not picked or not cfg.get("enabled"):
            # 未启用推送时**仍然更新状态** ——
            # 这样用户之后启用推送，不会因为「早就超限了」而立刻收到一串旧告警。
            for a in picked:
                self._alert_state.mark_level(a.get("metric"), a.get("level"))
            with self._alert_lock:
                self._alert_last = {
                    "at": int(time.time()),
                    "alerts": len(picked),
                    "pushed": False,
                    "message": "通知推送未启用" if picked else "",
                }
            return

        now = time.time()
        cooldown = int(cfg.get("cooldown") or 0)
        to_send = []
        for a in picked:
            if self._alert_state.should_send(a.get("metric"), a.get("level"),
                                             now, cooldown):
                to_send.append(a)

        if not to_send:
            with self._alert_lock:
                self._alert_last = {
                    "at": int(time.time()),
                    "alerts": len(picked),
                    "pushed": False,
                    "message": "处于静默期，未重复推送",
                }
            return

        ok, msg, results = ma.dispatch(cfg, to_send)
        with self._alert_lock:
            self._alert_last = {
                "at": int(time.time()),
                "alerts": len(picked),
                "pushed": bool(ok),
                "message": msg,
                "results": results,
                "sent": [
                    {"metric": a.get("metric"), "level": a.get("level"),
                     "text": a.get("text")}
                    for a in to_send
                ],
            }

    def alert_status(self):
        """最近一次告警推送状态（给前端 /api/monitor/alerts/status 用）。"""
        with self._alert_lock:
            last = dict(self._alert_last) if self._alert_last else None
        return {
            "success": True,
            "last": last,
            "state": self._alert_state.snapshot() if self._alert_state else {},
        }

    def _start_history(self):
        """启动历史写入线程（趋势图数据源）。

        独立成线程而不是塞进采集循环：历史采样周期是 60 秒（与实时采集的
        3 秒不同），且写库是磁盘 IO，不该拖慢实时采集。
        """
        if self._history is not None:
            return
        try:
            import history_collect
        except Exception:
            return
        try:
            self._history = history_collect.HistoryWriter(
                get_snapshot=self.snapshot)
            self._history.start()
        except Exception:
            self._history = None

    def stop(self):
        self._stop.set()
        if self._history is not None:
            try:
                self._history.stop()
            except Exception:
                pass
        m = _storage_monitor()
        if m:
            try:
                m.stop()
            except Exception:
                pass
        # ⚠️ v1.1.84：告警线程靠 _stop 事件退出，
        #   不需要额外 join（它是 daemon 线程，进程退出即结束）。

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._collect_once()
            except Exception:
                pass
            self._stop.wait(self.interval)

    def _collect_once(self):
        # CPU 使用率**不在这里采集** —— 它需要 10 秒的采样窗口，
        # 由独立的 _cpu_loop 线程负责（见类注释里的量化分辨率说明）。
        # 这里只取它最新一次的缓存结果。
        cpu = self._cpu_snapshot()

        mem = collect_memory()
        gpu = collect_gpu()
        temp = collect_temp_fan()
        storage = collect_storage()

        disks_active = len(storage.get("disks") or [])
        power = collect_power(cpu.get("percent", 0), disks_active)

        # 网络流量（上下行速率 + 今日累计）
        # ⚠️ v1.1.69：单独 try —— 网络采集失败不能拖垮整帧快照。
        try:
            network = collect_network()
        except Exception:
            network = {"available": False}

        # 进程 TOP：按需采样（ProcSampler 内部缓存 15 秒），
        # 不必每 3 秒都全量扫 /proc —— 抽屉打开时才用得上。
        try:
            import proc_collect
            procs = proc_collect.top_processes(limit=12)
        except Exception:
            procs = []

        # 内存插槽（dmidecode）：模块内部缓存 10 分钟，不会拖慢采集循环。
        try:
            import mem_slots
            mem_slots_data = {
                "items": mem_slots.slots(),
                "dual": mem_slots.dual_channel(),
            }
        except Exception:
            mem_slots_data = {"items": [], "dual": False}

        data = {
            "cpu": cpu, "mem": mem, "gpu": gpu,
            "temp": temp, "storage": storage, "power": power,
            "network": network,
            "system": collect_system(),
            "procs": procs,
            "memory": mem_slots_data,
            "uptime": _uptime_text(),
            "uptimeSeconds": _uptime_seconds(),
            "ts": int(time.time()),
            "interval": self.interval,
        }
        with self._lock:
            self._cache = data
        return data

    # ---------------- CPU 专用采样线程（10 秒窗口） ----------------

    def _cpu_snapshot(self):
        """取 CPU 线程最近一次的结果（可能还没有 → 返回不可用占位）。"""
        with self._cpu_lock:
            if self._cpu_cache:
                return dict(self._cpu_cache)
        return {"available": False}

    def _cpu_loop(self):
        """CPU 采样线程：每 CPU_SAMPLE_WINDOW 秒产出一次使用率。

        采集方式与 fnmonitor 一致 —— **两次采样差分**，采样点间隔就是窗口长度：
          第 1 轮：只记基线（collect_cpu 返回 available=False）
          第 2 轮：与基线差分 → 得到这 10 秒窗口的使用率
        所以第一轮有效读数要等 2×10 = 20 秒，之后每 10 秒刷新一次。

        ⚠️ 只有**拿到有效百分比**才更新缓存：
          若某轮差分失败（total_delta<=0，窗口内没有任何 jiffy 流逝），
          必须保留上一轮的有效值 —— 否则一次抖动就会把显示打成「不可用」，
          表现为处理器卡片整块数据消失。
        """
        while not self._stop.is_set():
            try:
                collect_cpu()          # 第 1 次：记基线（无百分比）
                # 等一个完整窗口，再取第 2 次做差分
                self._stop.wait(CPU_SAMPLE_WINDOW)
                if self._stop.is_set():
                    break
                cpu = collect_cpu()     # 第 2 次：与基线差分
                if not cpu.get("available"):
                    continue            # 本轮无有效读数 → 保留上一轮
                with self._cpu_lock:
                    self._cpu_cache = cpu
            except Exception:
                pass


def collect_system():
    """主机与硬件标识（供存储板块的 NAS 机箱顶栏使用）。

    取 /etc/hostname、/sys/devices/virtual/dmi/id/*（DMI），
    DMI 读不到就退到 /proc/cpuinfo 的 model 与主板 device-tree 的 model。

    ⚠️ DMI 的 product_name 经常是占位串（如 "To Be Filled By O.E.M."、
    "Default string"、"System Product Name"），那不是真实型号，
    直接显示在机箱顶栏会误导用户 —— 这里识别并丢弃。
    """
    out = {"hostname": "", "productName": "", "boardName": "", "available": False}
    hn = read_text("/etc/hostname", 128).strip().split("\n")[0].strip()
    if hn:
        out["hostname"] = hn
    dmi = "/sys/devices/virtual/dmi/id"
    out["productName"] = read_text(os.path.join(dmi, "product_name"), 128).strip()
    out["boardName"] = read_text(os.path.join(dmi, "board_name"), 128).strip()
    if not out["productName"]:
        out["productName"] = read_text(os.path.join(dmi, "product_version"), 128).strip()
    if not out["productName"]:
        # ARM 平台（如飞牛 ARM 机型）没有 DMI，退到 device-tree
        out["productName"] = read_text("/proc/device-tree/model", 128).strip()
        # device-tree 的 model 常带 \0 结尾
        out["productName"] = out["productName"].replace("\x00", "").strip()
    if not out["productName"] and not out["boardName"]:
        m = re.search(r"^model name\s*:\s*(.+)$", read_text("/proc/cpuinfo", 8192), re.M)
        if m:
            out["productName"] = m.group(1).strip()
    # 丢弃 DMI 占位串：它们不是真实型号，显示出来只会误导
    for k in ("productName", "boardName"):
        if _is_dmi_placeholder(out.get(k)):
            out[k] = ""
    # productName 丢了再尝试用板名兜底
    if not out["productName"] and out["boardName"]:
        out["productName"] = out["boardName"]
    out["available"] = bool(out["hostname"] or out["productName"] or out["boardName"])
    return out


# DMI 占位串：这些是主板厂商没填型号时的默认值，不是真实信息
_DMI_PLACEHOLDERS = (
    "to be filled by o.e.m.",
    "default string",
    "system product name",
    "system version",
    "not specified",
    "unknown",
    "none",
    "n/a",
    "to be filled",
    "oem",
    "o.e.m.",
)


def _is_dmi_placeholder(s):
    """判断一个 DMI 字段是否是厂商占位串（不是真实型号）。"""
    t = str(s or "").strip().lower()
    if not t:
        return True
    for p in _DMI_PLACEHOLDERS:
        if t == p or t.startswith(p + " ") or t.startswith(p + " by"):
            return True
    return False


def _uptime_text():
    """系统运行时长（fnOS 走 /proc/uptime）。"""
    t = read_text("/proc/uptime", 64).strip().split()
    if not t:
        return "—"
    try:
        sec = int(float(t[0]))
    except Exception:
        return "—"
    d = sec // 86400
    h = (sec % 86400) // 3600
    m = (sec % 3600) // 60
    if d:
        return "%d 天 %d 小时" % (d, h)
    if h:
        return "%d 小时 %d 分" % (h, m)
    return "%d 分" % m


def _uptime_seconds():
    """系统运行时长（秒）—— 能耗统计用。"""
    t = read_text("/proc/uptime", 64).strip().split()
    if not t:
        return 0
    try:
        return int(float(t[0]))
    except Exception:
        return 0
