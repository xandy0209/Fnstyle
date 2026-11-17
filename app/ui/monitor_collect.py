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


def _pct(part, total):
    if not total:
        return 0
    try:
        return max(0, min(100, int(round(100.0 * float(part) / float(total)))))
    except Exception:
        return 0


# ============================ 1. 存储 ============================

def _disk_usage(path):
    try:
        st = os.statvfs(path)
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        used = total - st.f_bfree
        return {"total": total, "used": used, "free": free,
                "percent": _pct(used, total)}
    except Exception:
        return None


def collect_storage():
    """采集存储池与数据盘容量。

    来源：/volN（fnOS 存储池挂载点）、/proc/mdstat（软 RAID 状态）。
    降级：/vol* 不存在时回退到 /proc/mounts 里挂在 /vol* 的设备；
          都没有则返回空列表，不抛错。
    """
    volumes = []
    seen = set()
    try:
        candidates = sorted(glob.glob("/vol[0-9]*"))
    except Exception:
        candidates = []

    # 回退源：从 /proc/mounts 里找挂载点形如 /volN 的文件系统
    if not candidates:
        try:
            mounts = read_text("/proc/mounts", 65536)
            for line in mounts.splitlines():
                parts = line.split()
                if len(parts) >= 2 and re.match(r"^/vol[0-9]+$", parts[1]):
                    candidates.append(parts[1])
        except Exception:
            pass

    for vol in candidates:
        try:
            if vol in seen or not os.path.isdir(vol):
                continue
            seen.add(vol)
            # 存储池根目录本身不含用户数据（数据在 @appshare / 用户目录），
            # 但 fnOS 的存储池即整个卷，故整体统计即可。
            usage = _disk_usage(vol)
            if not usage:
                continue
            # 统计各一级子目录占用（appshare / 用户目录等），用于下钻展示
            children = []
            try:
                for name in sorted(os.listdir(vol)):
                    sub = os.path.join(vol, name)
                    if not os.path.isdir(sub) or name.startswith("."):
                        continue
                    su = _disk_usage(sub)
                    if su and su["total"] > 0:
                        children.append({"name": name, "total": su["total"],
                                         "used": su["used"], "free": su["free"],
                                         "percent": su["percent"]})
            except Exception:
                pass
            volumes.append({
                "name": os.path.basename(vol),
                "path": vol,
                "total": usage["total"],
                "used": usage["used"],
                "free": usage["free"],
                "percent": usage["percent"],
                "totalText": _mbf(usage["total"]),
                "usedText": _mbf(usage["used"]),
                "freeText": _mbf(usage["free"]),
                "children": children,
            })
        except Exception:
            continue

    # 物理磁盘清单（型号 / 类型 / 温度）
    disks = collect_disks()

    # 软 RAID 状态（/proc/mdstat）
    raid = []
    md = read_text("/proc/mdstat", 4096)
    if md:
        for m in re.finditer(r"^(md\d+)\s*:\s*(\S+)\s+(\S+)\s+\[(\d+)/(\d+)\]",
                             md, re.M):
            raid.append({
                "name": m.group(1),
                "state": m.group(2),
                "active": int(m.group(4)),
                "total": int(m.group(5)),
            })

    return {"volumes": volumes, "raid": raid, "disks": disks,
            "available": bool(volumes)}


def collect_disks():
    """物理磁盘清单：型号、类型（SSD/HDD）、温度、容量。

    型号来源 /sys/block/<dev>/device/model（virtio/USB 桥接设备可能没有）。
    温度来源 /sys/block/<dev>/device/hwmon/hwmon*/temp1_input。
    """
    disks = []
    devs = sorted(glob.glob("/sys/block/sd*[a-z]") +
                  glob.glob("/sys/block/nvme[0-9]*n[0-9]*") +
                  glob.glob("/sys/block/vd*[a-z]"))
    for blk in devs:
        base = os.path.basename(blk)
        dev = os.path.join(blk, "device")
        rot = read_int(os.path.join(blk, "queue", "rotational"))
        model = read_text(os.path.join(dev, "model"), 256).strip()
        if not model:
            model = read_text(os.path.join(dev, "name"), 256).strip()
        temp = None
        hw = glob.glob(os.path.join(dev, "hwmon", "hwmon*", "temp*_input"))
        if hw:
            t = read_int(hw[0])
            if t:
                temp = round(t / 1000.0, 1)
        size = None
        try:
            sectors = read_int(os.path.join(blk, "size"))
            if sectors:
                size = sectors * 512
        except Exception:
            pass
        disks.append({
            "name": base,
            "model": model or base,
            "temp": temp,
            "size": size,
            "sizeText": _mbf(size) if size else "—",
            "type": "SSD" if rot == 0 else ("HDD" if rot == 1 else "未知"),
        })
    return disks


# ==================== 2. 处理器 / 内存 / 显卡 ====================

_CPU_STATE = {"last": None, "lastTime": None, "cores": None}


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
    out["cores"] = len(re.findall(r"^processor\s*:", info, re.M)) or len(per_core)
    out["loadavg"] = read_text("/proc/loadavg", 256).strip().split()[:3]
    # 频率：优先 cpufreq 的实时频率，回退 /proc/cpuinfo 的 MHz
    khz = read_int("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq")
    if khz:
        out["freqText"] = "%.2f GHz" % (khz / 1000000.0)
    else:
        fm = re.search(r"^cpu MHz\s*:\s*([\d.]+)", info, re.M)
        if fm:
            out["freqText"] = "%.2f GHz" % (float(fm.group(1)) / 1000.0)
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
        "cached": vals.get("Cached", 0),
        "swapTotal": vals.get("SwapTotal", 0),
        "swapUsed": max(0, vals.get("SwapTotal", 0) - vals.get("SwapFree", 0)),
    })
    return out


def collect_gpu():
    """显卡信息。

    优先 nvidia-smi（NVIDIA 独显），其次 /sys/class/drm（Intel/AMD 核显）。
    降级：都没有则返回 available=False，前端显示「未检测到显卡」。
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
            gpus.append({
                "vendor": "NVIDIA", "name": parts[0],
                "temp": _to_int(parts[1]), "util": _to_int(parts[2]),
                "memUsed": _to_int(parts[3]), "memTotal": _to_int(parts[4]),
                "power": _to_float(parts[5]), "utilAvailable": True,
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
            busy = read_int(os.path.join(dev, "gpu_busy_percent"))
            name = _gpu_name(dev, drv, os.path.basename(card))
            temp = None
            hw = glob.glob(os.path.join(dev, "hwmon", "hwmon*", "temp1_input"))
            if hw:
                t = read_int(hw[0])
                if t:
                    temp = round(t / 1000.0, 1)
            mem_used = read_int(os.path.join(dev, "mem_info_vram_used"))
            mem_total = read_int(os.path.join(dev, "mem_info_vram_total"))
            gpus.append({
                "vendor": vendor, "name": name, "temp": temp,
                "util": busy, "memUsed": mem_used, "memTotal": mem_total,
                "power": None, "utilAvailable": busy is not None,
            })
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

    def stop(self):
        self._stop.set()

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

        data = {
            "cpu": cpu, "mem": mem, "gpu": gpu,
            "temp": temp, "storage": storage, "power": power,
            "uptime": _uptime_text(),
            "ts": int(time.time()),
            "interval": self.interval,
        }
        with self._lock:
            self._cache = data
        return data


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
