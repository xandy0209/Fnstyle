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


def collect_cpu():
    """CPU 使用率：/proc/stat 两次采样差分。

    首次调用（没有基线）时先记基线、返回 available=False，下一轮才有真实值。
    """
    out = {"available": False}
    stat = read_text("/proc/stat", 8192)
    m = re.search(r"^cpu\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)(?:\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+))?",
                  stat, re.M)
    if not m:
        return out
    nums = [int(x) for x in m.groups() if x is not None]
    idle = (nums[3] if len(nums) > 3 else 0) + (nums[4] if len(nums) > 4 else 0)
    total = sum(nums)
    cpu_total = 0
    cpu_idle = 0
    per_core = []
    for line in re.finditer(r"^cpu(\d+)\s+(\d+)\s+(\d+)\s+(\d+)(?:\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+))?",
                             stat, re.M):
        n = [int(x) for x in line.groups() if x is not None]
        core_total = sum(n)
        core_idle = (n[3] if len(n) > 3 else 0) + (n[4] if len(n) > 4 else 0)
        cpu_total += core_total
        cpu_idle += core_idle
        per_core.append((core_total, core_idle))
    if not per_core:
        per_core = [(total, idle)]

    now = time.monotonic()
    prev = _CPU_STATE["last"]
    prev_t = _CPU_STATE["lastTime"]
    prev_cores = _CPU_STATE["cores"]
    _CPU_STATE["last"] = (cpu_total, cpu_idle)
    _CPU_STATE["cores"] = per_core
    _CPU_STATE["lastTime"] = now

    if prev is not None and prev_t is not None:
        dt = now - prev_t
        d_total = cpu_total - prev[0]
        d_idle = cpu_idle - prev[1]
        if d_total > 0 and dt > 0:
            out["percent"] = _pct(d_total - d_idle, d_total)
            out["available"] = True

        # 每核使用率（仅在前后核数一致时计算）
        if prev_cores and len(prev_cores) == len(per_core):
            cores = []
            for i in range(len(per_core)):
                dc = per_core[i][0] - prev_cores[i][0]
                di = per_core[i][1] - prev_cores[i][1]
                if dc > 0:
                    cores.append(_pct(dc - di, dc))
                else:
                    cores.append(0)
            out["coresPercent"] = cores

    # CPU 型号
    info = read_text("/proc/cpuinfo", 8192)
    mm = re.search(r"^model name\s*:\s*(.+)$", info, re.M)
    if mm:
        out["model"] = mm.group(1).strip()
    # 拓扑：物理核 / 逻辑线程 / 插槽（lscpu 优先，/proc/cpuinfo 回退）
    try:
        tcores, tthreads, tsockets = read_cpu_topology()
    except Exception:
        tcores = tthreads = tsockets = 0
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

def _hwmon_sensors():
    """遍历 /sys/class/hwmon 读取温度与风扇。"""
    temps = []
    fans = []
    for d in sorted(glob.glob("/sys/class/hwmon/hwmon[0-9]*")):
        name = read_text(os.path.join(d, "name"), 128).strip() or os.path.basename(d)
        for tp in sorted(glob.glob(os.path.join(d, "temp[0-9]*_input"))):
            base = tp[:-len("_input")]
            label = read_text(base + "_label", 128).strip()
            val = read_int(tp)
            if val is None or val <= 0:
                continue
            idx = re.search(r"temp(\d+)_input", tp)
            temps.append({
                "name": label or ("%s %s" % (name, idx.group(1) if idx else "")),
                "chip": name,
                "value": round(val / 1000.0, 1),
                "max": _hwmon_scale(read_int(base + "_max")),
                "crit": _hwmon_scale(read_int(base + "_crit")),
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
    return temps, fans


def _hwmon_scale(v):
    if v is None or v <= 0:
        return None
    return round(v / 1000.0, 1)


def collect_temp_fan():
    """温度与散热：thermal_zone（CPU）→ hwmon（主板/硬盘/风扇）。"""
    temps = []
    fans = []
    source = []

    # --- CPU 温度优先走 thermal_zone ---
    cpu_temp = None
    for zone in sorted(glob.glob("/sys/class/thermal/thermal_zone[0-9]*")):
        ztype = read_text(os.path.join(zone, "type"), 128).strip()
        raw = read_int(os.path.join(zone, "temp"))
        if raw is None or raw <= 0:
            continue
        val = round(raw / 1000.0, 1)
        label = ztype or os.path.basename(zone)
        temps.append({"name": label, "chip": "thermal", "value": val,
                      "max": None, "crit": None})
        if cpu_temp is None and re.search(r"x86_pkg_temp|cpu|soc|package", label, re.I):
            cpu_temp = val
    if temps:
        source.append("thermal")

    # --- hwmon 补充（主板/网卡/硬盘），并补风扇 ---
    hw_temps, hw_fans = _hwmon_sensors()
    if hw_temps:
        source.append("hwmon")
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

    # 硬盘温度：复用存储板块的物理磁盘清单（避免同一批 sysfs 读两遍）
    disks = collect_disks()

    if cpu_temp is None:
        for t in temps:
            if re.search(r"coretemp|k10temp|cpu|package", t["name"], re.I):
                cpu_temp = t["value"]
                break

    return {
        "available": bool(temps or fans),
        "temps": temps,
        "fans": fans,
        "disks": disks,
        "cpuTemp": cpu_temp,
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


def collect_power(cpu_percent, disks_active):
    """整机功耗。

    优先级：RAPL 实测（package + dram + 主板基线 + 硬盘）→ 无 RAPL 时按 TDP 模型估算。
    口径与 fnmonitor 一致：package 已含 core/uncore，不能重复相加。
    """
    out = {"ok": False, "source": "unknown"}
    # 硬盘功耗（SSD 2.5W / HDD 8W）
    disks_w = 0.0
    disk_count = 0
    for blk in glob.glob("/sys/block/sd*[a-z]") + glob.glob("/sys/block/nvme[0-9]*n[0-9]*"):
        rot = read_int(os.path.join(blk, "queue", "rotational"))
        disks_w += 2.5 if rot == 0 else 8.0
        disk_count += 1

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
                base_w = 8.0
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
        nics = 0
        for nic in glob.glob("/sys/class/net/*"):
            base = os.path.basename(nic)
            if base == "lo" or base.startswith(("docker", "br-", "veth", "virbr", "bond", "tun")):
                continue
            nics += 1
        disk_w = (disks_w / disk_count) if disk_count else 8.0
        base = tdp * (cpu_p / 100.0 * 0.7 + 0.3)
        total = base + disk_count * disk_w + nics * 5.0
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
        })
    except Exception:
        out = {"ok": False, "source": "unknown"}
    return out


# ===================== 轮询线程 + 缓存 =====================

class MonitorCollector(object):
    """后台轮询采集器：每 interval 秒刷新一次缓存，HTTP 只读缓存。"""

    def __init__(self, interval=3):
        self.interval = interval
        self._lock = threading.Lock()
        self._cache = {}
        self._thread = None
        self._stop = threading.Event()

    def snapshot(self):
        with self._lock:
            return dict(self._cache)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        # 启动硬盘巡检线程（定时全盘扫 SMART，独立于 3 秒采集循环）
        _storage_monitor()

    def stop(self):
        self._stop.set()
        m = _storage_monitor()
        if m:
            try:
                m.stop()
            except Exception:
                pass

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._collect_once()
            except Exception:
                pass
            self._stop.wait(self.interval)

    def _collect_once(self):
        # CPU 使用率需要两次采样差分。
        # 关键：collect_cpu() 内部维护基线，**第一次调用**只记基线不产出百分比。
        # 所以这里必须连续调两次（间隔 ~0.25s）才能拿到本轮的实时使用率。
        cpu = collect_cpu()
        time.sleep(0.25)
        cpu2 = collect_cpu()
        if cpu2.get("available") or not cpu.get("available"):
            cpu = cpu2

        mem = collect_memory()
        gpu = collect_gpu()
        temp = collect_temp_fan()
        storage = collect_storage()

        disks_active = len(storage.get("disks") or [])
        power = collect_power(cpu.get("percent", 0), disks_active)

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
            "system": collect_system(),
            "procs": procs,
            "memory": mem_slots_data,
            "uptime": _uptime_text(),
            "ts": int(time.time()),
            "interval": self.interval,
        }
        with self._lock:
            self._cache = data
        return data


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
