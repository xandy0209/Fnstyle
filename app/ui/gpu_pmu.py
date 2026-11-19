# -*- coding: utf-8 -*-
"""Intel 核显占用率采样（i915 PMU 引擎计数 + RC6 驻留估算）。

来源与许可
----------
本模块的 PMU 采样器移植自「飞牛监控 fnmonitor」（MIT License），
原作者 Copyright (c) 2026 fnmonitor contributors。
原实现见 fnmonitor 的 server.py（IntelGpuPmu / GpuIdleResidency 类）。
MIT 许可全文见本项目 app/ui/monitor_collect.py 顶部的许可声明。

实现要点
--------
1. **PMU 引擎占用（首选）**
   通过 perf_event_open 打开 /sys/bus/event_source/devices/i915*/events/ 下的
   ``rcs0-busy`` / ``bcs0-busy`` / ``vcs0-busy`` / ``vecs0-busy`` / ``ccs0-busy``
   五个引擎的 busy 计数器，取两次采样间的增量换算成占用率百分比。
   与 intel_gpu_top 同源（同一个内核计数器）。

2. **RC6 驻留估算（降级）**
   PMU 不可用时，读 ``gt/gt0/rc6_residency_ms``（i915）或
   ``device/tile0/gt0/gtidle/idle_residency_ms``（xe）：
   活跃度 = 100 − Δ空闲毫秒 / Δ墙钟毫秒 × 100。
   只能给出**整体**活跃度，不区分引擎。

3. **都不可用** → 返回 util=None，前端显示「未能读取核显占用」。

线程安全
--------
采样器内部用锁保护 fd 集合与上次快照；初始化失败后按 RETRY_S 退避，
不空转、不刷日志。
"""

import errno
import json
import os
import platform
import re
import struct
import threading
import time

# perf_event_open 的 syscall 号（按架构）
_PERF_SYSCALL_NR = {
    "x86_64": 298,
    "aarch64": 241,
    "armv7l": 364,
    "armv6l": 364,
    "i386": 336,
    "i686": 336,
    "ppc64le": 319,
    "s390x": 331,
    "riscv64": 280,
}

# i915 引擎事件名 → 前端展示的类别键
_I915_ENGINE_RE = re.compile(r"^(rcs|bcs|vcs|vecs|ccs)(\d+)-busy$")
_I915_ENGINE_KEY = {
    "rcs": "render",       # 渲染
    "bcs": "blitter",      # 复制
    "vcs": "video",        # 视频
    "vecs": "video_enhance",  # 视频增强
    "ccs": "compute",      # 计算
}

# 频率事件（i915 各版本命名不一，按优先级尝试）
_I915_FREQ_EVENTS = {
    "act": ("actual-frequency", "gt0-actual-frequency"),
    "req": ("requested-frequency", "gt0-requested-frequency"),
}


def read_text(path, limit=8192):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except Exception:
        return ""


def _parse_pmu_config(text):
    """PMU 事件描述（如 'config=0x1000002'）→ config 整数；i915 只用 config= 形式。"""
    m = re.search(r"(?:^|,)\s*config=(0x[0-9a-fA-F]+|\d+)\s*(?:,|$)", (text or "").strip())
    return int(m.group(1), 0) if m else None


def _first_cpu(mask):
    """cpumask 文本（'0' / '0-3' / '0,2'）→ 第一个 CPU 编号。"""
    m = re.match(r"\s*(\d+)", mask or "")
    return int(m.group(1)) if m else 0


def _pci_tag(pci):
    """lspci 的 '00:02.0' → '00_02.0'（PMU 目录名 i915_0000_00_02.0 的后缀）。"""
    return (pci or "").strip().replace(":", "_")


def _drm_cards(sysfs="/sys"):
    """[(card 路径, PCI 地址, 驱动名)]（只读 sysfs 链接，不访问设备）。"""
    out = []
    base = os.path.join(sysfs, "class", "drm")
    try:
        names = sorted(n for n in os.listdir(base) if re.match(r"^card\d+$", n))
    except OSError:
        return out
    for n in names:
        p = os.path.join(base, n)
        try:
            pci = os.path.basename(os.path.realpath(os.path.join(p, "device")))
        except Exception:
            pci = ""
        try:
            drv = os.path.basename(os.readlink(os.path.join(p, "device", "driver")))
        except OSError:
            drv = None
        out.append((p, pci, drv))
    return out


def _card_for(pci, sysfs="/sys"):
    """按 PCI 地址找对应的 DRM card；找不到则回落到 i915/xe 驱动的第一张卡。"""
    cards = _drm_cards(sysfs)
    tag = (pci or "").strip()
    for c in cards:
        if tag and c[1].endswith(tag):
            return c
    for c in cards:
        if c[2] in ("i915", "xe"):
            return c
    return None


class _PerfOpener(object):
    """perf_event_open(attr, pid=-1, cpu, group_fd=-1, flags=0) 的 ctypes 封装（只读计数）。"""

    def __init__(self, machine=None):
        self.machine = (machine or platform.machine() or "").lower()
        self.nr = _PERF_SYSCALL_NR.get(self.machine)
        self._libc = None

    def __call__(self, ptype, config, cpu):
        import ctypes
        if not self.nr or platform.system() != "Linux":
            raise OSError(errno.ENOSYS,
                          "perf_event_open 不支持该平台（%s）" % (self.machine or "?"))
        if self._libc is None:
            self._libc = ctypes.CDLL(None, use_errno=True)
            self._libc.syscall.restype = ctypes.c_long
        # struct perf_event_attr（PERF_ATTR_SIZE_VER0 = 64 字节）：
        # type, size, config, sample_period, sample_type, read_format(0 → 读回单个 u64),
        # flags(0 → 立即计数), wakeup, bp_type, config1
        buf = ctypes.create_string_buffer(
            struct.pack("=IIQQQQQIIQ", int(ptype), 64, int(config), 0, 0, 0, 0, 0, 0, 0), 64)
        fd = self._libc.syscall(ctypes.c_long(self.nr), buf, ctypes.c_int(-1),
                                ctypes.c_int(int(cpu)), ctypes.c_int(-1), ctypes.c_ulong(0))
        if fd < 0:
            e = ctypes.get_errno()
            raise OSError(e, os.strerror(e))
        return int(fd)


def _perf_read(fd):
    data = os.read(fd, 8)
    if len(data) != 8:
        raise OSError(5, "short read")
    return struct.unpack("=Q", data)[0]


class IntelGpuPmu(object):
    """i915 PMU 引擎占用采样器。

    线程安全、惰性初始化、失败后退避重试、进程退出时关闭 fd。
    sysfs / opener / reader / closer / clock 均可注入，便于测试。
    """

    RETRY_S = 300.0        # 初始化失败后的重试间隔（不刷日志、不空转）
    REREAD_RETRY_S = 30.0  # 读数失败（如 GPU 复位）后多久重新初始化

    def __init__(self, sysfs="/sys", opener=None, reader=None, closer=None,
                 clock=None, paranoid_path=None):
        self.sysfs = sysfs
        self.opener = opener or _PerfOpener()
        self.reader = reader or _perf_read
        self.closer = closer or os.close
        self.clock = clock or time.monotonic
        self.paranoid_path = paranoid_path or "/proc/sys/kernel/perf_event_paranoid"
        self._lock = threading.Lock()
        self._fds = {}            # 事件名 → fd
        self._events = {}         # 事件名 → (类别键, 实例号) 或 ('freq', 'act'/'req')
        self._prev = None
        self._last = None
        self._next_init = 0.0
        self._diag = {"initialized": False}

    def _pmu_dir(self, pci):
        base = os.path.join(self.sysfs, "bus", "event_source", "devices")
        try:
            names = sorted(n for n in os.listdir(base)
                           if n == "i915" or n.startswith("i915_"))
        except OSError:
            names = []
        self._diag["pmu_candidates"] = names
        tag = _pci_tag(pci)
        for n in names:
            if tag and n.startswith("i915_") and n.endswith(tag):
                return os.path.join(base, n)
        if "i915" in names:
            return os.path.join(base, "i915")
        return os.path.join(base, names[0]) if names else None

    def _close(self):
        for fd in self._fds.values():
            try:
                self.closer(fd)
            except Exception:
                pass
        self._fds = {}
        self._events = {}

    def _init(self, pci):
        self._close()
        d = {"initialized": True, "pmu_dir": None, "events": {}, "open": {}, "error": None,
             "perf_event_paranoid": (read_text(self.paranoid_path) or "").strip() or None,
             "machine": getattr(self.opener, "machine", None),
             "syscall_nr": getattr(self.opener, "nr", None)}
        self._diag = d
        pdir = self._pmu_dir(pci)
        d["pmu_candidates"] = self._diag.get("pmu_candidates", [])
        if not pdir:
            d["error"] = "未找到 i915 PMU（/sys/bus/event_source/devices/i915*）"
            return
        d["pmu_dir"] = pdir
        try:
            ptype = int((read_text(os.path.join(pdir, "type")) or "").strip())
        except ValueError:
            d["error"] = "PMU type 不可读"
            return
        d["type"] = ptype
        mask = (read_text(os.path.join(pdir, "cpumask")) or "").strip()
        d["cpumask"] = mask or None
        cpu = _first_cpu(mask)
        evdir = os.path.join(pdir, "events")
        try:
            names = sorted(n for n in os.listdir(evdir) if "." not in n)
        except OSError:
            d["error"] = "PMU events 目录不可读"
            return
        wanted = {}
        for n in names:
            m = _I915_ENGINE_RE.match(n)
            if m:
                wanted[n] = (_I915_ENGINE_KEY[m.group(1)], int(m.group(2)))
        for k, cands in _I915_FREQ_EVENTS.items():
            for c in cands:
                if c in names:
                    wanted[c] = ("freq", k)
                    break
        d["events_all"] = len(names)
        for n, meaning in sorted(wanted.items()):
            cfg = _parse_pmu_config(read_text(os.path.join(evdir, n)))
            unit = (read_text(os.path.join(evdir, n + ".unit")) or "").strip() or None
            d["events"][n] = {"config": hex(cfg) if cfg is not None else None, "unit": unit}
            if cfg is None:
                d["open"][n] = {"ok": False, "error": "config 无法解析"}
                continue
            try:
                fd = self.opener(ptype, cfg, cpu)
                self._fds[n] = fd
                self._events[n] = meaning
                d["open"][n] = {"ok": True}
            except OSError as e:
                d["open"][n] = {"ok": False, "errno": e.errno,
                               "strerror": e.strerror or str(e)}
            except Exception as e:   # ctypes 不可用等
                d["open"][n] = {"ok": False, "error": str(e)[:200]}
        if not any(m[0] != "freq" for n, m in self._events.items() if n in self._fds):
            d["error"] = d["error"] or "引擎 busy 事件均未能打开（见 open）"
            self._close()

    def sample(self, pci=None):
        """返回 {'engines', 'util', 'freq_act', 'freq_req'}；首次采样返回 {'warming': True}；
        PMU 不可用返回 None。不阻塞：只做 read()。"""
        with self._lock:
            now = self.clock()
            if not self._fds:
                if now < self._next_init:
                    return None
                self._init(pci)
                if not self._fds:
                    self._next_init = now + self.RETRY_S
                    return None
            try:
                vals = {n: int(self.reader(fd)) for n, fd in self._fds.items()}
            except Exception as e:
                self._diag["error"] = "读取计数器失败：%s" % (str(e)[:200],)
                self._close()
                self._next_init = now + self.REREAD_RETRY_S
                return None
            prev, self._prev = self._prev, (now, vals)
            self._diag["last_raw"] = {"t": round(now, 3), "values": vals}
            if not prev:
                return {"warming": True}
            dt = now - prev[0]
            if dt < 0.2:
                return self._last or {"warming": True}
            engines, freq = {}, {}
            for n, v in vals.items():
                if n not in prev[1]:
                    continue
                dv = v - prev[1][n]
                if dv < 0:                    # 计数器回绕 / GPU 复位：以本次为新基线
                    self._last = {"warming": True}
                    return self._last
                kind, sub = self._events[n]
                if kind == "freq":
                    freq[sub] = dv / dt       # MHz·s 累计 → MHz
                else:
                    pct = max(0.0, min(100.0, dv / (dt * 1e9) * 100.0))
                    # 同类多实例（vcs0/vcs1）取最忙的
                    engines[kind] = max(engines.get(kind, 0.0), pct)
            engines = {k: round(v, 1) for k, v in engines.items()}
            self._last = {"engines": engines,
                          "util": max(engines.values()) if engines else None,
                          "freq_act": round(freq["act"]) if "act" in freq else None,
                          "freq_req": round(freq["req"]) if "req" in freq else None}
            return self._last

    def diag(self):
        with self._lock:
            return json.loads(json.dumps(self._diag))

    def close(self):
        with self._lock:
            self._close()


class GpuIdleResidency(object):
    """RC6（i915）/ gtidle（xe）驻留时间 → 活跃度估算。

    活跃度 = 100 − Δ空闲 ms / Δ墙钟 ms × 100
    """

    PATHS = ("gt/gt0/rc6_residency_ms", "power/rc6_residency_ms",
             "device/tile0/gt0/gtidle/idle_residency_ms")

    def __init__(self, sysfs="/sys", clock=None):
        self.sysfs = sysfs
        self.clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._prev = None
        self.path = None

    def _find(self, pci):
        c = _card_for(pci, self.sysfs)
        if not c:
            return None
        for rel in self.PATHS:
            p = os.path.join(c[0], rel)
            if os.path.exists(p):
                return p
        return None

    def sample(self, pci=None):
        with self._lock:
            if not self.path:
                self.path = self._find(pci)
                if not self.path:
                    return None
            t = read_text(self.path).strip()
            v = int(t) if t.isdigit() else None
            now = self.clock()
            if v is None:
                self.path = None
                self._prev = None
                return None
            prev, self._prev = self._prev, (now, v)
            if not prev or now - prev[0] < 0.2:
                return {"warming": True}
            dv = v - prev[1]
            if dv < 0:                     # 32 位计数回绕：重新取基线
                return {"warming": True}
            idle = dv / ((now - prev[0]) * 1000.0) * 100.0
            return {"util": round(max(0.0, min(100.0, 100.0 - idle)), 1)}


def intel_gpu_freq(pci, sysfs="/sys"):
    """核显当前频率 MHz（sysfs；优先实际频率 act，其次 cur）。"""
    c = _card_for(pci, sysfs)
    if not c:
        return None
    for rel in ("gt_act_freq_mhz", "gt/gt0/rps_act_freq_mhz",
                "device/tile0/gt0/freq0/act_freq",
                "gt_cur_freq_mhz", "gt/gt0/rps_cur_freq_mhz",
                "device/tile0/gt0/freq0/cur_freq"):
        try:
            with open(os.path.join(c[0], rel)) as f:
                v = int((f.read() or "0").strip())
        except Exception:
            v = 0
        if v:
            return v
    return None


# ---- 全局单例（与 fnmonitor 同思路：一个采样器服务所有请求） ----
_INTEL_SAMPLERS = {}
_INTEL_SAMPLERS_LOCK = threading.Lock()


def _intel_samplers(pci):
    with _INTEL_SAMPLERS_LOCK:
        s = _INTEL_SAMPLERS.get(pci)
        if s is None:
            s = _INTEL_SAMPLERS[pci] = (IntelGpuPmu(), GpuIdleResidency())
        return s


def close_intel_samplers():
    """进程退出时关闭所有 perf fd。"""
    with _INTEL_SAMPLERS_LOCK:
        items = list(_INTEL_SAMPLERS.values())
        _INTEL_SAMPLERS.clear()
    for pmu, _idle in items:
        try:
            pmu.close()
        except Exception:
            pass


def intel_gpu_usage(pci, pmu=None, idle=None, sysfs="/sys"):
    """Intel 核显占用：PMU 引擎占用优先，否则 RC6 活跃度估算，都不可用返回 util=None。"""
    if pmu is None or idle is None:
        pmu, idle = _intel_samplers(pci)
    out = {"util": None, "utilSource": None, "engines": None,
           "freqMhz": None, "freqReqMhz": None, "warming": False}
    r = pmu.sample(pci)
    if r is not None:
        out["utilSource"] = "pmu"
        if r.get("warming"):
            out["warming"] = True
        else:
            out["engines"] = r.get("engines") or None
            out["util"] = r.get("util")
            out["freqMhz"] = r.get("freq_act")
            out["freqReqMhz"] = r.get("freq_req")
    else:
        q = idle.sample(pci)
        if q is not None:
            out["utilSource"] = "rc6"
            out["warming"] = bool(q.get("warming"))
            out["util"] = q.get("util")
    if out["freqMhz"] is None:
        out["freqMhz"] = intel_gpu_freq(pci, sysfs)
    return out


def intel_gpu_diag(pci=None, sysfs="/sys", pmu=None, idle=None):
    """核显读数诊断（PMU / RC6 / 驱动 / 内核），不含任何机密信息。"""
    if pmu is None or idle is None:
        pmu, idle = _intel_samplers(pci)
    try:
        rel = os.uname().release
    except OSError:
        rel = None
    try:
        sources = sorted(os.listdir(os.path.join(sysfs, "bus", "event_source", "devices")))
    except OSError:
        sources = []
    return {
        "kernel": rel,
        "pci": pci,
        "event_sources": sources[:64],
        "perf_event_paranoid": (read_text("/proc/sys/kernel/perf_event_paranoid") or "").strip() or None,
        "drm_cards": [{"card": os.path.basename(c[0]), "pci": c[1], "driver": c[2]}
                      for c in _drm_cards(sysfs)],
        "pmu": pmu.diag(),
        "rc6_path": idle.path,
        "freq_mhz_sysfs": intel_gpu_freq(pci, sysfs),
    }
