#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ---------------------------------------------------------------------------
# FnStyle —— 存储监控采集模块
#
# 能力完全对齐开源项目 fnmonitor 的「存储」板块
# （https://github.com/MisiteQ/fnmonitor ，MIT License，
#   Copyright (c) 2026 Misite齊 / iqiuzixuan）：
#
#   1. 存储池容量   —— /volN 挂载点，fsid 去重，过滤伪文件系统与网络挂载
#   2. 物理盘清单   —— lsblk 设备树（型号/品牌/序列号/容量/挂载点/盘位）
#   3. SMART 健康   —— 整体评估 + 5 项 ATA 告警计数 + SAS/SCSI 缺陷表解析
#   4. 硬盘分级     —— ok / warn / crit / unknown，含缺陷增长趋势判定
#   5. 巡检记录     —— 定时全盘巡检，SQLite 存历史，比对上次结果识别恶化
#   6. 磁盘 IO      —— /proc/diskstats 差分：读写速率(B/s) 与 IOPS
#   7. 休眠保护     —— smartctl -n standby / hdparm -C 非唤醒式探测，
#                      休眠盘沿用上次数据，绝不因监控而唤醒硬盘
#   8. 软 RAID      —— /proc/mdstat
#   9. 硬 RAID 卡   —— lspci 识别 + storcli/perccli 读取控制器与物理盘
#  10. 温度/转速/通电时长/制造周期
#
# 设计要点（与 fnmonitor 一致）：
#   - 零第三方依赖，只用 Python 标准库；
#   - SMART 读取较重，后台巡检线程定时跑，采集循环只读缓存；
#   - 休眠盘不唤醒：smartctl -n standby 命中时直接 exit(2)，沿用缓存值；
#   - 任何单项采集失败只降级该项，绝不影响其它板块；
#   - 缺陷增长按「序列号」而非设备名比对（重启后设备名会轮换）。
#
# 致谢与许可：
#   本模块为 FnStyle 独立实现，采集路径与判定口径参考了开源项目 fnmonitor
#   （https://github.com/MisiteQ/fnmonitor ，MIT License，
#     Copyright (c) 2026 Misite齊 / iqiuzixuan）。fnmonitor 的原始作者与
#   许可证声明保留如下：
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
import json
import time
import glob
import shutil
import sqlite3
import threading

try:
    import subprocess
except Exception:  # pragma: no cover
    subprocess = None


# ============================ 基础工具 ============================

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


def run_cmd(cmd, timeout=6):
    """执行外部命令，返回 (stdout, returncode)；失败返回 ("", -1)。"""
    if subprocess is None:
        return "", -1
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=timeout)
        return p.stdout.decode("utf-8", "replace"), p.returncode
    except Exception:
        return "", -1


def _nz(v):
    """None / 空串 / 非数字 → None；否则转 int。"""
    if v is None or v == "":
        return None
    try:
        return int(v)
    except Exception:
        try:
            return int(float(v))
        except Exception:
            return None


def _mbf(float_val):
    """字节 → 可读字符串（1024 进制）。

    ⚠️ 只用于内存 / Swap / 显存等「操作系统按二进制计量」的场景。
    硬盘与存储池容量必须用 _dsize()（十进制），否则与 fnOS 存储页、
    磁盘厂商标称容量对不上（1 TB 盘显示成 0.91 TiB 就是这个原因）。
    """
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
    显示规则（照搬 fnmonitor）：
      ≥100 TB/GB 取整、≥10 取 1 位小数、否则 2 位小数；
      GB 以上才带小数，MB 为整数。
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


# ============================ 1. 挂载点与存储池 ============================

def _unescape_mount_path(s):
    """还原 /proc/mounts 里的八进制转义（\\040 -> 空格），中文等多字节不受影响。"""
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), s)


def read_mounts():
    """解析 /proc/mounts，返回挂载点列表（含设备、文件系统类型、挂载选项）。"""
    mounts = []
    for line in read_text("/proc/mounts", 65536).splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        mounts.append({
            "device": parts[0],
            "mount": _unescape_mount_path(parts[1]),
            "fs": parts[2],
            "opts": parts[3].split(","),
        })
    return mounts


# 伪文件系统（proc/sys/tmpfs/overlay 等，不是磁盘空间）
_PSEUDO_FS = {
    "proc", "sysfs", "devtmpfs", "devpts", "tmpfs", "cgroup", "cgroup2",
    "mqueue", "pstore", "securityfs", "debugfs", "tracefs", "fusectl",
    "configfs", "bpf", "squashfs", "ramfs", "autofs", "binfmt_misc",
    "hugetlbfs", "rpc_pipefs", "nsfs", "overlay", "efivarfs",
    "fuse.gvfsd-fuse", "fuse.portal", "fuse.snapfuse",
    # 网络文件系统：占用的是远端空间，不能计入本机存储
    "nfs", "nfs4", "cifs", "smbfs", "smb3", "9p", "ceph", "glusterfs",
    "fuse.sshfs", "fuse.rclone", "fuse.curlftpfs",
}
# 路径级排除：引导 / 恢复分区、Docker / 容器运行时目录
_SKIP_PATH_EXACT = {"/boot", "/efi", "/boot/efi", "/rescue", "/recovery"}
_SKIP_PATH_PREFIX = ("/boot/", "/efi/", "/sys/", "/proc/", "/dev/", "/run/",
                     "/var/lib/docker", "/var/lib/containerd", "/snap/")
# 小于该容量的文件系统视为引导/恢复小分区，不计入存储统计（256 MB）
_MIN_FS_BYTES = 256 * 1024 * 1024

# 物理盘设备名白名单（避免把任意字符串拼进 smartctl 的 argv）
_DEV_NAME_RE = re.compile(r"^(?:sd[a-z]{1,3}|nvme\d+n\d+|hd[a-z]{1,3}|vd[a-z]{1,3}|xvd[a-z]{1,3})$")
_WHOLE_DISK_RE = re.compile(r"^(?:sd[a-z]{1,3}|nvme\d+n\d+|vd[a-z]{1,3}|xvd[a-z]{1,3})$")


def disk_usage(mountpoint):
    """statvfs 读取挂载点容量。

    ⚠️ 口径（与 fnmonitor / fnOS 存储页一致）：
    - total = f_blocks × f_frsize
    - used  = (f_blocks - f_bfree) × f_frsize —— 含 ext4 保留块，
      与系统 df 的 Used 列、fnOS 存储页、fnmonitor 完全同源；
    - free  = f_bavail × f_frsize —— 普通用户真正可用的空间（保留块不给普通用户）。
    所以 free 与 total - used 并不相等，差值就是保留块。
    fsid 用于同一文件系统的多挂载点去重（fnOS 的 btrfs 子卷与存储池同 fsid）。
    """
    try:
        st = os.statvfs(mountpoint)
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        used = max(0, (st.f_blocks - st.f_bfree) * st.f_frsize)
        return {
            "total": total, "used": used, "free": free,
            "percent": _pct(used, total),
            "fsid": getattr(st, "f_fsid", 0) or 0,
        }
    except Exception:
        return {"total": 0, "used": 0, "free": 0, "percent": 0, "fsid": 0}


# 休眠盘的容量缓存（避免 statvfs 唤醒硬盘）
_DISK_USAGE_CACHE = {}


def collect_disks():
    """返回本机真实磁盘文件系统列表（去重、过滤后）。

    口径与 fnmonitor 一致：
    - 仅统计本地可写块设备文件系统，排除伪文件系统、网络挂载、只读介质、
      引导/恢复分区、<256MB 小分区；
    - fnOS 的应用/系统子卷（挂载路径含 /@，如 /vol1/@appdata）与存储池是
      同一个 btrfs 文件系统，按 fsid 去重——同一文件系统只保留路径最浅的
      一个挂载点，避免同一存储池被重复统计。
    """
    disks = []
    seen = {}
    order = []
    states = disk_power_states()
    for m in read_mounts():
        mp, dev, fs = m["mount"], m["device"], m["fs"]
        if fs in _PSEUDO_FS:
            continue
        if mp in _SKIP_PATH_EXACT or mp.startswith(_SKIP_PATH_PREFIX):
            continue
        # fnOS btrfs 子卷与存储池同文件系统
        if "/@" in mp:
            continue
        if "ro" in (m.get("opts") or []):
            continue
        # 休眠保护：statvfs 会唤醒 STANDBY 的硬盘。盘休眠时沿用上次缓存，
        # 服务刚启动且盘就休眠（尚无缓存）时跳过该挂载点，绝不主动唤醒。
        sleeping = mount_on_standby(mp, states)
        if sleeping:
            u = _DISK_USAGE_CACHE.get(mp)
            if not u or not u.get("total"):
                continue
        else:
            u = disk_usage(mp)
            if not u["total"] or u["total"] < _MIN_FS_BYTES:
                continue
            _DISK_USAGE_CACHE[mp] = dict(u)
        key = ("fsid", u["fsid"]) if u["fsid"] else ("dev", os.path.realpath(dev) or dev)
        entry = {
            "device": dev, "mount": mp, "fs": fs,
            "total": u["total"], "used": u["used"], "free": u["free"],
            "percent": u["percent"], "standby": bool(sleeping),
        }
        if key in seen:
            cur = seen[key]
            if mp.count("/") < cur["mount"].count("/"):
                seen[key] = entry
            continue
        seen[key] = entry
        order.append(key)
    disks = [seen[k] for k in order]
    disks.sort(key=lambda d: -d["total"])
    return disks


def _pool_mountpoints():
    """列出 fnOS 存储池的挂载点（/vol1、/vol2 …）。

    ⚠️ 必须以 /proc/mounts 的**挂载点**为准，不能用 glob 扫 /vol[0-9]* 目录：
    fnOS 的系统盘虽然挂在 /（ext4），但磁盘根目录下同样存在 /vol00 这个
    普通目录 —— glob 会把它当存储池收进来，于是把 62.5 GB 的系统盘算进
    存储池总量（表现为「4 个存储池 / 2.4 TB」），与 fnOS 存储页和 fnmonitor
    的「3 个存储池」对不上。

    另外系统卷编号是 vol00（没有独立挂载点），存储池编号从 vol1 起；
    这里同时排除 vol00，双保险。
    """
    out = []
    for m in read_mounts():
        mp = m["mount"]
        if not re.match(r"^/vol[1-9][0-9]*$", mp):
            continue
        if m["fs"] in _PSEUDO_FS:
            continue
        if "ro" in (m.get("opts") or []):
            continue
        if mp not in out:
            out.append(mp)
    return out


def collect_volumes():
    """fnOS 存储池容量（/volN）。

    与 collect_disks 的区别：这里只取 fnOS 的存储池挂载点 /volN。
    只统计池级别容量，不下钻子目录 —— 与 fnmonitor 行为一致，
    避免每个池的每个一级子目录都做一次 statvfs（纯浪费 IO）。

    口径对齐 fnmonitor / fnOS 存储页：
    - 只统计存储池挂载点，系统盘（vol00 / 挂在 / 的 ext4）不计入；
    - 无存储池时回退为 collect_disks() 的结果（开发机 / 普通 Linux）。
    """
    volumes = []
    seen = set()
    candidates = _pool_mountpoints()
    if not candidates:
        # 回退：没有 /volN 挂载点时，用文件系统清单里容量最大的当"池"
        ds = collect_disks()
        if ds:
            d = ds[0]
            candidates = [d["mount"]]
    for vol in candidates:
        try:
            if vol in seen or not os.path.isdir(vol):
                continue
            seen.add(vol)
            usage = disk_usage(vol)
            if not usage or not usage["total"]:
                continue
            volumes.append({
                "name": os.path.basename(vol),
                "path": vol,
                "total": usage["total"], "used": usage["used"], "free": usage["free"],
                "percent": usage["percent"],
                "totalText": _dsize(usage["total"]),
                "usedText": _dsize(usage["used"]),
                "freeText": _dsize(usage["free"]),
            })
        except Exception:
            continue
    return volumes


# ============================ 2. 硬盘休眠状态（不唤醒） ============================

_DISK_STATE_CACHE = {"states": {}, "ts": 0.0}
_DISK_STATE_LOCK = threading.RLock()
_DISK_PROBE_LOCK = threading.RLock()
_DISK_PROBE_UNAVAILABLE_TS = 0.0
_DISK_STATE_TTL = 60.0


def _whole_disks():
    """列出所有物理整盘设备名。"""
    names = []
    try:
        for name in os.listdir("/sys/block"):
            if _WHOLE_DISK_RE.match(name):
                names.append(name)
    except Exception:
        pass
    return names


def _probe_disk_power_state(name):
    """非唤醒式探测单盘电源状态。

    smartctl -n standby 优先：盘处于 STANDBY 时 smartctl 直接 exit(2)，
    不下发任何会唤醒硬盘的 SMART 命令；hdparm -C（CHECK POWER MODE，
    只查询状态不改变电源状态）作为兜底。
    返回 "standby" | "active/idle" | "unknown"。
    """
    dev = "/dev/" + name
    if shutil.which("smartctl"):
        out, rc = run_cmd(["smartctl", "-n", "standby", "-i", dev], timeout=4)
        up = (out or "").upper()
        if "STANDBY" in up or "SLEEP" in up:
            return "standby"
        if rc == 0 and ("ACTIVE" in up or "IDLE" in up or "MODEL" in up or "SERIAL" in up):
            return "active/idle"
    if shutil.which("hdparm"):
        out, rc = run_cmd(["hdparm", "-C", dev], timeout=3)
        low = (out or "").lower()
        if rc == 0 and ("standby" in low or "sleeping" in low):
            return "standby"
        if rc == 0 and ("active" in low or "idle" in low):
            return "active/idle"
    return "unknown"


def _disk_state_ttl():
    """缓存有效期与采集间隔对齐：采集线程每轮强制刷新一次。"""
    return max(_DISK_STATE_TTL, 30.0)


def disk_power_states(force=False):
    """返回 {盘名: standby / active/idle / unknown}。"""
    now = time.time()
    ttl = _disk_state_ttl()
    with _DISK_STATE_LOCK:
        if not force and _DISK_STATE_CACHE["ts"] and \
                now - _DISK_STATE_CACHE["ts"] <= ttl:
            return dict(_DISK_STATE_CACHE["states"])
    with _DISK_PROBE_LOCK:
        with _DISK_STATE_LOCK:
            age = time.time() - _DISK_STATE_CACHE["ts"] if _DISK_STATE_CACHE["ts"] else 1e9
            if age <= (1.0 if force else ttl):
                return dict(_DISK_STATE_CACHE["states"])
        return _probe_all_disk_states(now)


def _probe_all_disk_states(now):
    global _DISK_PROBE_UNAVAILABLE_TS
    states = {}
    if now - _DISK_PROBE_UNAVAILABLE_TS > 300:
        for name in _whole_disks():
            states[name] = _probe_disk_power_state(name)
        if states and all(v == "unknown" for v in states.values()) \
                and not shutil.which("smartctl") and not shutil.which("hdparm"):
            _DISK_PROBE_UNAVAILABLE_TS = now
    with _DISK_STATE_LOCK:
        _DISK_STATE_CACHE["states"] = states
        _DISK_STATE_CACHE["ts"] = now
    return dict(states)


_MOUNT_DISK_CACHE = {"mounts": {}, "ts": 0.0}
_MOUNT_DISK_LOCK = threading.RLock()


def _mount_disk_map():
    """返回 {挂载点: [物理盘名]}。

    直挂分区（/dev/sdb1）由设备名直接归并；LVM / device-mapper 等多层结构
    由 lsblk 设备树递归把挂载点归到顶层物理盘（lsblk 只读 sysfs，
    不会唤醒休眠硬盘）。60 秒缓存。
    """
    now = time.time()
    with _MOUNT_DISK_LOCK:
        if _MOUNT_DISK_CACHE["ts"] and now - _MOUNT_DISK_CACHE["ts"] <= 60:
            return {k: list(v) for k, v in _MOUNT_DISK_CACHE["mounts"].items()}
    result = {}

    def _add(mp, disk):
        if mp and disk:
            result.setdefault(mp, set()).add(disk)

    # 1) /proc/mounts 直挂分区
    for m in read_mounts():
        dev = os.path.basename(m["device"] or "")
        mm = re.match(r"^(sd[a-z]+)\d*$", dev) \
            or re.match(r"^(nvme\d+n\d+)(p\d+)?$", dev) \
            or re.match(r"^(vd[a-z]+|xvd[a-z]+)\d*$", dev)
        if mm:
            _add(m["mount"], mm.group(1))
    # 2) lsblk 设备树（覆盖 LVM / dm-* / 多层分区挂载）
    out, rc = run_cmd(["lsblk", "-b", "-J", "-o", "NAME,MOUNTPOINT,TYPE"], timeout=10)
    if rc == 0 and out.strip():
        try:
            data = json.loads(out)

            def _collect(blk, acc):
                if blk.get("mountpoint"):
                    acc.append(blk["mountpoint"])
                for ch in blk.get("children", []) or []:
                    _collect(ch, acc)

            for blk in data.get("blockdevices", []) or []:
                root = blk.get("name") or ""
                if not _WHOLE_DISK_RE.match(root):
                    continue
                mps = []
                _collect(blk, mps)
                for mp in mps:
                    _add(mp, root)
        except Exception:
            pass
    with _MOUNT_DISK_LOCK:
        _MOUNT_DISK_CACHE["mounts"] = {k: sorted(v) for k, v in result.items()}
        _MOUNT_DISK_CACHE["ts"] = now
    return {k: list(v) for k, v in _MOUNT_DISK_CACHE["mounts"].items()}


def mount_on_standby(mountpoint, states=None):
    """该挂载点是否位于已休眠的物理硬盘上。"""
    states = states if states is not None else disk_power_states()
    disks = _mount_disk_map().get(mountpoint)
    return bool(disks) and any(states.get(d) == "standby" for d in disks)


# ============================ 3. SMART 解析 ============================

SMART_COUNTER_KEYS = ("reallocated_sectors", "reallocated_events", "pending_sectors",
                      "uncorrectable", "udma_crc")
SMART_COUNTER_LABELS = {
    "reallocated_sectors": "重映射扇区",
    "reallocated_events": "重映射事件",
    "pending_sectors": "待映射扇区",
    "uncorrectable": "无法纠正错误",
    "udma_crc": "UDMA CRC 错误",
}


def smart_detail(dev):
    """smartctl 读取指定盘健康/型号/转速/通电时长/告警计数。失败返回 {}。

    -n standby：盘处于 STANDBY 时 smartctl 直接 exit(2)、不执行任何命令，
    从而不唤醒休眠硬盘；此处返回 standby 标记供调用方复用上次结果。
    """
    if not shutil.which("smartctl"):
        return {}
    out, rc = run_cmd(["smartctl", "-n", "standby", "-i", "-H", "-A", "/dev/" + dev], timeout=8)
    # 先判 exit(2)：-n standby 命中休眠盘时 stdout 可能完全为空，
    # 若先判空输出就会把「休眠」误判成「读取失败」
    if rc == 2 or "STANDBY" in out.upper() or "SLEEP" in out.upper():
        return {"standby": True}
    if not out.strip():
        return {}
    if rc != 0:
        return {}
    d = _parse_smart_text(out)
    # 5 项告警计数的键必须始终存在（值可为 None 表示「该盘无此属性」）。
    # 少一个键前端就会显示成空白格，而不是明确的「—」。
    for _k in SMART_COUNTER_KEYS:
        d.setdefault(_k, None)
    return d


def _parse_smart_text(out):
    """解析 smartctl 输出文本 → 健康数据字典。

    单独拆出来是为了能脱离硬件做单元测试（不同 SATA/SAS/NVMe 的输出格式
    各不相同，逐条比对比上机试错快得多）。
    """
    d = {"health": "未知"}
    for line in out.splitlines():
        s = line.strip()
        if ":" not in s:
            continue
        k, _, v = s.partition(":")
        k, v = k.strip(), v.strip()
        if k == "SMART overall-health self-assessment test result" or k == "SMART Health Status":
            d["health"] = v
        elif k == "Device Model" or k == "Model Number" or k == "Product":
            d["model2"] = v
        elif k.lower() == "serial number":
            # SAS 写 "Serial number"（小写 n），SATA/NVMe 写 "Serial Number"
            d["serial"] = v
        elif k == "Rotation Rate":
            d["rpm"] = v
        elif k == "Temperature":
            try:
                d["temp_c"] = int(v.split()[0])
            except Exception:
                pass
        elif k == "Current Drive Temperature" or k == "Drive Temperature":
            # SAS/SCSI 写法：Current Drive Temperature:     33 C
            m = re.search(r"(-?\d+)", v)
            if m:
                d["temp_c"] = int(m.group(1))
    # ATA 告警计数（属性 5/196/197/198/199）
    ata_attrs = {
        "5": ("reallocated_sectors", "重映射扇区"),
        "196": ("reallocated_events", "重映射事件"),
        "197": ("pending_sectors", "待重映射扇区"),
        "198": ("uncorrectable", "无法纠正错误"),
        "199": ("udma_crc", "UDMA CRC 错误"),
    }
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 10 and parts[0] in ata_attrs and "Power_On_Hours" not in parts[1]:
            key, _label = ata_attrs[parts[0]]
            d[key] = _nz(parts[9]) or 0
        if len(parts) >= 10 and parts[0] == "9" and "Power_On_Hours" in parts[1]:
            d["power_on_hours"] = parts[9]
        # ATA 温度走属性行 194（Temperature_Celsius），不是 "Temperature:" 那种键值行。
        # 只认 "Temperature:" 的话， SATA 机械盘温度永远读不出来，前端只能显示「—」。
        if len(parts) >= 10 and parts[0] == "194" and "Temperature_Celsius" in parts[1]:
            # RAW_VALUE 可能是 "42 (Min/Max 20/50)" 这种形式，取开头数字
            m = re.match(r"(\d+)", parts[9])
            if m:
                d["temp_c"] = int(m.group(1))
    # NVMe：从 SMART/Health Information 段解析通电时长与温度
    for line in out.splitlines():
        s = line.strip()
        if "Power On Hours" in s:
            m = re.search(r"([\d,]+)\s*hours", s)
            if m:
                d["power_on_hours"] = m.group(1).replace(",", "")
        elif s.startswith("Temperature:"):
            try:
                d["temp_c"] = int(s.split()[1])
            except Exception:
                pass
    # SAS/SCSI：没有 ATA 属性表，健康数据在 grown defect list 与 Error counter log。
    # 同时给出 SAS 原始键与映射键，使前端不改也能判红黄绿：
    #   grown_defects    -> reallocated_sectors（增长缺陷表，语义最接近 ATA 重映射）
    #   scsi_uncorrected -> uncorrectable（read/write/verify 三向不可纠正错误合计）
    scsi = False
    grown = None
    power_on = None
    ecc_fast = ecc_delayed = rereads = corrected = algo = unc = 0
    gigabytes = 0.0
    in_err_log = False
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("Error counter log"):
            in_err_log, scsi = True, True
            continue
        if in_err_log and not s:
            in_err_log = False
            continue
        m = re.match(r"Elements in grown defect list:\s*(\d+)", s)
        if m:
            grown, scsi = int(m.group(1)), True
            continue
        m = re.match(r"Pending defects:\s*(\d+)", s)
        if m:
            d["scsi_pending_defects"] = int(m.group(1))
            scsi = True
            continue
        m = re.match(r"Accumulated power on time,\s*hours:minutes\s+(\d+):(\d+)", s)
        if m:
            power_on, scsi = int(m.group(1)), True
            continue
        m = re.match(r"Manufactured in week\s+(\d+)\s+of year\s+(\d+)", s)
        if m:
            d["manufactured"] = "%s-W%s" % (m.group(2), m.group(1).zfill(2))
            continue
        if in_err_log:
            m = re.match(r"(read|write|verify):\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+([\d.]+)\s+(\d+)", s)
            if m:
                ecc_fast += int(m.group(2))
                ecc_delayed += int(m.group(3))
                rereads += int(m.group(4))
                corrected += int(m.group(5))
                algo += int(m.group(6))
                try:
                    gigabytes += float(m.group(7))
                except Exception:
                    pass
                unc += int(m.group(8))
    if scsi:
        d["scsi"] = True
        if grown is not None:
            d["grown_defects"] = grown
            d.setdefault("reallocated_sectors", grown)
        d["scsi_uncorrected"] = unc
        # 0 也是有意义的读数（盘干净），必须落键：否则「不可纠正」永远显示 None
        d.setdefault("uncorrectable", unc)
        if d.get("scsi_pending_defects") is not None:
            d.setdefault("pending_sectors", d["scsi_pending_defects"])
        if ecc_fast or ecc_delayed or rereads:
            # ECC 计数是 32 位计数器会回绕，只反映读过多少数据、不反映健康度，
            # 因此单独暴露为 scsi_ecc，不映射到告警键
            d["scsi_ecc"] = ecc_fast + ecc_delayed
            d["scsi_rereads"] = rereads
        if corrected or algo or gigabytes:
            d["scsi_corrected"] = corrected
            d["scsi_gb"] = round(gigabytes, 3)
        if power_on is not None:
            d["power_on_hours"] = str(power_on)
    return d


# ============================ 4. 物理盘详情 ============================

def _lsblk_json():
    """lsblk 设备树（失败返回空列表）。"""
    out, rc = run_cmd(
        ["lsblk", "-b", "-J", "-o",
         "NAME,MODEL,VENDOR,SERIAL,SIZE,FSTYPE,MOUNTPOINT,RO,TYPE,ROTA"], timeout=10)
    if rc != 0 or not out.strip():
        return []
    try:
        data = json.loads(out)
    except Exception:
        return []
    return data.get("blockdevices", []) or []


def _vol_of(mount_paths):
    """从挂载点列表推断所属存储空间编号。

    fnOS 的「存储空间 N」就挂在 /volN，所以直接取挂载点里的 /volN 的 N。
    一个盘可能挂多个点（系统盘同时挂 / 与 /vol1），取编号最小的一个，
    保证同一块盘在盘位卡片上的标签稳定。
    没挂到任何存储池（纯系统盘 / 未挂载）返回 0。
    """
    best = 0
    for mp in (mount_paths or []):
        m = re.match(r"^/vol(\d+)(?:/|$)", str(mp))
        if not m:
            continue
        n = int(m.group(1))
        if best == 0 or n < best:
            best = n
    return best


def collect_disks_detail(prev=None, with_states=True, health_prev=None):
    """按物理硬盘返回详情。

    字段：名称/品牌/型号/序列号/容量/已用/使用率/温度/转速/通电时长/
          挂载点/休眠状态/SMART 健康/5 项告警计数/分级/上次变化。
    prev：上一轮详情缓存。已 STANDBY 的硬盘不再下发 smartctl，
    健康/转速/通电时长/温度沿用上次活跃时的值。
    health_prev：上次巡检结果（dev → row），用于判定缺陷增长趋势。
    """
    result = []
    prev_by = {}
    for old in (prev or []):
        if old.get("name"):
            prev_by[old["name"]] = old
    health_prev = health_prev or {}
    # 巡检记录按序列号索引：设备名会随重启轮换，只有序列号能认出「同一块盘」，
    # 按名字比会凭空记下一条「sdg 由正常变为故障」。
    # 统一用 _prev_index：它同时挂 "dev:<名字>" 别名，给读不到序列号的盘兜底。
    health_by_serial = _prev_index(health_prev)

    def _walk_mounts(blk, acc):
        """递归收集该设备树上的所有挂载点（兼容分区/LVM/device-mapper 多层结构）。"""
        if blk.get("mountpoint"):
            acc.append(blk["mountpoint"])
        for ch in blk.get("children", []) or []:
            _walk_mounts(ch, acc)

    blocks = _lsblk_json()
    if not blocks:
        # lsblk 不可用时退回 sysfs 读取（能力降级但不中断）
        return _collect_disks_detail_sysfs(prev_by)

    mounts = {}
    for d in collect_disks():
        mounts[d["mount"]] = d
    states = disk_power_states() if with_states else {}
    for blk in blocks:
        name = blk.get("name") or ""
        btype = (blk.get("type") or "").lower()
        if not name or btype == "loop" or blk.get("ro"):
            continue
        if not _DEV_NAME_RE.match(name):
            continue
        model = (blk.get("model") or "").strip()
        vendor = (blk.get("vendor") or "").strip()
        serial = (blk.get("serial") or "").strip()
        size = _nz(blk.get("size")) or 0
        mount_paths = []
        _walk_mounts(blk, mount_paths)
        used = 0
        total = 0
        for mp in mount_paths:
            m = mounts.get(mp)
            if m:
                used += m["used"]
                total += m["total"]
        percent = round(used / total * 100, 1) if total else 0.0
        state = states.get(name, "unknown")
        old = prev_by.get(name) or {}
        rot = blk.get("rota")
        entry = {
            "name": name,
            "model": model or old.get("model") or "未知型号",
            "vendor": vendor or old.get("vendor", ""),
            "serial": serial or old.get("serial", ""),
            "size": size or old.get("size", 0),
            "sizeText": _dsize(size) if size else (old.get("sizeText") or "—"),
            "used": used,
            "total": total,
            "percent": percent,
            "usedText": _dsize(used) if used else "—",
            "mounts": mount_paths,
            # 所属存储空间编号：挂载点形如 /volN 的取 N（fnOS「存储空间 N」= /volN）。
            # 没挂载到存储池（纯系统盘 / 未挂载）返回 0，前端标「未挂载」。
            "vol": _vol_of(mount_paths),
            "state": state,
            "standby": state == "standby",
            "health": old.get("health", ""),
            "rpm": old.get("rpm", ""),
            "power_on_hours": old.get("power_on_hours", ""),
            "powerOnText": old.get("powerOnText", ""),
            "manufactured": old.get("manufactured", ""),
            "proto": old.get("proto", ""),
            "type": "SSD" if rot == 0 else ("HDD" if rot == 1 else old.get("type", "未知")),
        }
        for _k in SMART_COUNTER_KEYS:
            entry[_k] = old.get(_k)
        # SMART 数据优先取巡检缓存；巡检未覆盖到时按需补读（最多 8 块，
        # 避免首屏空白时对所有盘跑 smartctl）
        if state != "standby":
            sd = _DISK_HEALTH_MEM.get(name)
            if sd is None and with_states:
                sd = smart_detail(name) if len(result) < 8 else {}
                if sd and not sd.get("standby"):
                    _DISK_HEALTH_MEM[name] = sd
            # ⚠️ v1.1.73 修复：sd 可能是 None —— with_states=False 且 SMART 缓存未命中时，
            #   下面 sd.get("standby") 会抛 AttributeError，
            #   而 monitor_collect.collect_disks() 用 `except Exception: return []` 吞掉，
            #   → **监控页存储卡拿不到任何磁盘数据**（老板要的「一块需留意」永远显示不出来）。
            if not sd:
                sd = {}
            if sd.get("standby"):
                entry["state"] = "standby"
                entry["standby"] = True
            else:
                entry["health"] = sd.get("health", entry["health"])
                entry["rpm"] = sd.get("rpm", entry["rpm"])
                entry["power_on_hours"] = sd.get("power_on_hours", entry["power_on_hours"])
                entry["serial"] = sd.get("serial", entry["serial"])
                entry["manufactured"] = sd.get("manufactured", entry["manufactured"])
                if sd.get("scsi"):
                    entry["proto"] = "SAS"
                for _k in SMART_COUNTER_KEYS:
                    if sd.get(_k) is not None:
                        entry[_k] = sd[_k]
                if sd.get("temp_c") is not None and entry.get("temp") is None:
                    entry["temp"] = sd["temp_c"]
        if state == "standby" and not entry["health"]:
            entry["health"] = "休眠"
        # 温度：sysfs hwmon 优先（快），无则用 SMART 读数
        if entry.get("temp") is None:
            entry["temp"] = _disk_sysfs_temp(name)
        # 分级：按巡检口径（crit/warn/ok/unknown），休眠盘不参与判定
        if state == "standby":
            entry["grade"] = "unknown"
            entry["detail"] = "休眠中，未唤醒检测"
        else:
            # 上次巡检记录按序列号匹配（设备名重启后会轮换）
            prev_row = health_by_serial.get(str(entry.get("serial") or name)) or {}
            grade, reasons = grade_disk(
                {"health": entry.get("health", ""),
                 "uncorrectable": entry.get("uncorrectable"),
                 "reallocated_sectors": entry.get("reallocated_sectors"),
                 "pending_sectors": entry.get("pending_sectors")},
                prev_row)
            entry["grade"] = grade
            entry["detail"] = "；".join(reasons)
        entry["gradeLabel"] = GRADE_LABEL.get(entry.get("grade"), "未知")
        result.append(entry)
    return result


def _collect_disks_detail_sysfs(prev_by):
    """lsblk 不可用时的降级实现：直接读 /sys/block。"""
    result = []
    devs = sorted(glob.glob("/sys/block/sd*[a-z]") +
                  glob.glob("/sys/block/nvme[0-9]*n[0-9]*") +
                  glob.glob("/sys/block/vd*[a-z]") +
                  glob.glob("/sys/block/xvd*[a-z]"))
    for blk_path in devs:
        name = os.path.basename(blk_path)
        if not _DEV_NAME_RE.match(name):
            continue
        dev = os.path.join(blk_path, "device")
        rot = read_int(os.path.join(blk_path, "queue", "rotational"))
        model = read_text(os.path.join(dev, "model"), 256).strip()
        if not model:
            model = read_text(os.path.join(dev, "name"), 256).strip()
        sectors = read_int(os.path.join(blk_path, "size"))
        size = (sectors or 0) * 512
        temp = _disk_sysfs_temp(name)
        state = disk_power_states().get(name, "unknown")
        old = prev_by.get(name) or {}
        entry = {
            "name": name,
            "model": model or old.get("model") or "未知型号",
            "vendor": old.get("vendor", ""),
            "serial": old.get("serial", ""),
            "size": size,
            "sizeText": _dsize(size) if size else (old.get("sizeText") or "—"),
            "used": old.get("used", 0),
            "total": old.get("total", 0),
            "percent": old.get("percent", 0.0),
            "usedText": old.get("usedText", "—"),
            "mounts": old.get("mounts", []),
            "vol": _vol_of(old.get("mounts", [])),
            "state": state,
            "standby": state == "standby",
            "health": old.get("health", ""),
            "rpm": old.get("rpm", ""),
            "power_on_hours": old.get("power_on_hours", ""),
            "powerOnText": old.get("powerOnText", ""),
            "manufactured": old.get("manufactured", ""),
            "proto": old.get("proto", ""),
            "temp": temp if temp is not None else old.get("temp"),
            "type": "SSD" if rot == 0 else ("HDD" if rot == 1 else "未知"),
        }
        for _k in SMART_COUNTER_KEYS:
            entry[_k] = old.get(_k)
        if state != "standby":
            sd = smart_detail(name)
            if sd and not sd.get("standby"):
                entry["health"] = sd.get("health", entry["health"])
                entry["rpm"] = sd.get("rpm", entry["rpm"])
                entry["power_on_hours"] = sd.get("power_on_hours", entry["power_on_hours"])
                entry["serial"] = sd.get("serial", entry["serial"])
                for _k in SMART_COUNTER_KEYS:
                    if sd.get(_k) is not None:
                        entry[_k] = sd[_k]
                if sd.get("temp_c") is not None and entry.get("temp") is None:
                    entry["temp"] = sd["temp_c"]
        result.append(entry)
    return result


def _disk_sysfs_temp(name):
    """从 /sys/block/<dev>/device/hwmon 读温度（毫摄氏度 → 摄氏度）。"""
    try:
        for pat in ("/sys/block/%s/device/hwmon/hwmon*/temp*_input" % name,
                    "/sys/block/%s/hwmon/hwmon*/temp*_input" % name):
            hits = sorted(glob.glob(pat))
            if hits:
                t = read_int(hits[0])
                if t:
                    return round(t / 1000.0, 1)
    except Exception:
        pass
    return None


# ============================ 5. 磁盘 IO 速率 ============================

_DISK_IO_PREV = {"stats": None, "ts": 0.0, "result": []}
_DISK_IO_LOCK = threading.RLock()


def read_diskstats():
    """读取各物理磁盘累计 IO 计数（/proc/diskstats 优先，失败回退 /sys/block/*/stat）。"""
    txt = read_text("/proc/diskstats", 65536)
    out = {}
    if txt:
        for line in txt.splitlines():
            parts = line.split()
            if len(parts) < 14:
                continue
            name = parts[2]
            if name.startswith("loop") or name.startswith("ram"):
                continue
            try:
                out[name] = {
                    "reads": int(parts[3]),
                    "sectors_read": int(parts[5]),
                    "writes": int(parts[7]),
                    "sectors_written": int(parts[9]),
                    "in_progress": int(parts[11]),
                }
            except Exception:
                pass
        return out
    # 兜底：/sys/block/*/stat
    try:
        for name in sorted(os.listdir("/sys/block")):
            if name.startswith("loop") or name.startswith("ram"):
                continue
            parts = read_text(os.path.join("/sys/block", name, "stat"), 512).split()
            if len(parts) < 8:
                continue
            try:
                out[name] = {
                    "reads": int(parts[0]),
                    "sectors_read": int(parts[2]),
                    "writes": int(parts[4]),
                    "sectors_written": int(parts[6]),
                    "in_progress": int(parts[8]) if len(parts) > 8 else 0,
                }
            except Exception:
                pass
    except Exception:
        pass
    return out


def collect_disk_io(min_interval=2.0):
    """计算各磁盘读写速率（B/s）与 IOPS（与上次采样差分）。

    min_interval：两次采样之间的最小间隔（秒）。太密的采样会让速率值
    剧烈抖动（读一个块就跳一下），没有参考价值。
    """
    with _DISK_IO_LOCK:
        now = time.time()
        prev = _DISK_IO_PREV["stats"]
        prev_ts = _DISK_IO_PREV["ts"]
        if prev is not None and prev_ts and (now - prev_ts) < min_interval:
            # 距上次采样太近：沿用上一次的速率结果，不重新差分
            return list(_DISK_IO_PREV["result"])
        cur = read_diskstats()
        result = []
        for name, c in cur.items():
            r_rate = w_rate = r_iops = w_iops = 0.0
            if prev and name in prev and prev_ts and now > prev_ts:
                dt = now - prev_ts
                r_rate = max(0, c["sectors_read"] - prev[name]["sectors_read"]) * 512.0 / dt
                w_rate = max(0, c["sectors_written"] - prev[name]["sectors_written"]) * 512.0 / dt
                r_iops = max(0, c["reads"] - prev[name]["reads"]) / dt
                w_iops = max(0, c["writes"] - prev[name]["writes"]) / dt
            result.append({
                "name": name,
                "read_rate": round(r_rate, 1), "write_rate": round(w_rate, 1),
                "read_iops": round(r_iops, 1), "write_iops": round(w_iops, 1),
                "readText": _rate_str(r_rate), "writeText": _rate_str(w_rate),
                "in_progress": c["in_progress"],
            })
        result.sort(key=lambda x: -(x["read_rate"] + x["write_rate"]))
        _DISK_IO_PREV["stats"] = cur
        _DISK_IO_PREV["ts"] = now
        _DISK_IO_PREV["result"] = result
    return result


def _rate_str(bps):
    """字节/秒 → 可读字符串。"""
    try:
        v = float(bps)
    except Exception:
        return "—"
    if v <= 0:
        return "0 B/s"
    return _mbf(v) + "/s"


# ============================ 6. RAID ============================

_MD_STATUS_WORDS = ("active", "inactive", "auto-read-only", "failed", "clear")
_MD_LAYOUTS = ("raid0", "raid1", "raid4", "raid5", "raid6", "raid10",
               "linear", "faulty", "container")
# 成员盘：sda1[0] / sdb1[1](F) / sdc[2](S) / sdd1[3](W)
_MD_MEMBER_RE = re.compile(r"^(?P<dev>[A-Za-z0-9_./-]+?)\[(?P<idx>\d+)\]"
                           r"(?P<flags>(?:\([A-Z]+\))*)")


def collect_raid():
    """软 RAID 阵列状态：/proc/mdstat。

    ⚠️ 不能用一条固定正则去套 —— mdstat 首行格式随状态而变：
       md0 : active raid1 sdb1[1] sda1[0]
       md1 : inactive sdb1[1](S)
       md2 : auto-read-only raid10 sdc1[1] ...
       第二行才是 [N/M] 有效/总数。
    因此按行解析：识别「mdN :」开头的首行取状态与成员，
    再用紧随其后的第二行里的 [N/M] 补齐 active/total。
    """
    raids = []
    md = read_text("/proc/mdstat", 8192)
    if not md:
        return raids
    lines = md.splitlines()
    cur = None
    for line in lines:
        s = line.strip()
        if not s:
            continue
        m = re.match(r"^(md\d+)\s*:\s*(.*)$", s)
        if m:
            dev = m.group(1)
            rest = (m.group(2) or "").strip()
            toks = rest.split()
            status = ""
            if toks and toks[0].lower() in _MD_STATUS_WORDS:
                status = toks[0]
                toks = toks[1:]
            layout = ""
            if toks and toks[0].lower() in _MD_LAYOUTS:
                layout = toks[0]
                toks = toks[1:]
            members = []
            for tok in toks:
                mm = _MD_MEMBER_RE.match(tok)
                if mm:
                    members.append(tok)
            cur = {
                "device": dev,
                "state": status,
                "status": status,
                "layout": layout,
                "members": members,
                "active": 0,
                "total": len(members),
                "raidState": "",
            }
            raids.append(cur)
            continue
        if cur is not None:
            # 第二行：976630336 blocks super 1.2 [3/3] [UUU]
            mt = re.search(r"\[(\d+)/(\d+)\]", s)
            if mt:
                cur["active"] = int(mt.group(1))
                cur["total"] = int(mt.group(2))
            ms = re.search(r"\[([U_]+)\]", s)
            if ms:
                cur["raidState"] = ms.group(1)
            # 一行可能同时含 [N/M] 与 [UUU]，上面的 [N/M] 正则已优先匹配
    return raids


# 硬 RAID 卡：storcli / perccli
_STORCLI_CANDIDATES = ("storcli64", "storcli", "perccli64", "perccli",
                       "/opt/MegaRAID/storcli/storcli64",
                       "/opt/MegaRAID/storcli/perccli64")
_STORCLI_PD_STATE = {
    "onln": ("ok", "在线"),
    "jbod": ("ok", "JBOD 直通"),
    "ugood": ("ok", "未配置(好)"),
    "rbld": ("warn", "重建中"),
    "copybk": ("warn", "拷贝中"),
    "cvd": ("warn", "创建中"),
    "cs": ("warn", "一致性检查"),
    "offln": ("fail", "离线"),
    "msng": ("fail", "丢失"),
    "ubad": ("fail", "未配置(坏)"),
    "bad": ("fail", "故障"),
    "fld": ("fail", "故障"),
}
_STORCLI_VD_STATE = {
    "optl": ("on", "正常"),
    "optimal": ("on", "正常"),
    "onln": ("on", "正常"),
    "dgrd": ("off", "降级"),
    "degraded": ("off", "降级"),
    "ofln": ("off", "离线"),
    "offln": ("off", "离线"),
    "rbld": ("", "重建中"),
}


def _find_storcli():
    """按候选顺序查找 storcli/perccli 可执行文件，找不到返回空串。"""
    for name in _STORCLI_CANDIDATES:
        if "/" in name:
            if os.path.isfile(name) and os.access(name, os.X_OK):
                return name
        else:
            try:
                p = shutil.which(name)
            except Exception:
                p = None
            if p:
                return p
    return ""


def _storcli_ci(d, *names):
    """storcli JSON 键容错查找：大小写/空格/点号不敏感。"""
    if not isinstance(d, dict):
        return None
    norm = {}
    for k, v in d.items():
        norm[str(k).lower().replace(".", "").replace(" ", "")] = v
    for n in names:
        v = norm.get(n.lower().replace(".", "").replace(" ", ""))
        if v is not None:
            return v
    return None


def _storcli_num(v):
    """解析 storcli 数值：27 / "27" / "27C (80.60 F)" → int。"""
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        m = re.search(r"-?\d+", v)
        if m:
            return int(m.group())
    return None


def _storcli_txt(v):
    """值转去除首尾空白的字符串（None → 空串）。"""
    if v is None:
        return ""
    if isinstance(v, (int, float)):
        return str(v)
    return str(v).strip()


def _storcli_controllers(out):
    """解析 storcli 控制器列表。"""
    ctrls = {}
    try:
        data = json.loads(out)
    except Exception:
        return ctrls
    key = None
    for cid, c in (data.get("Controllers") or {}).items():
        if not isinstance(c, dict):
            continue
        ctrls[cid] = {
            "id": cid,
            "model": _storcli_txt(_storcli_ci(c, "Model Name", "Model")),
            "serial": _storcli_txt(_storcli_ci(c, "Serial Number", "Serial")),
            "roc_temp": _storcli_num(_storcli_ci(c, "Temperature", "ROC Temperature")),
            "memory": _storcli_txt(_storcli_ci(c, "Memory", "Memory Size")),
            "status": _storcli_txt(_storcli_ci(c, "Status", "Controller Status")),
        }
        key = cid
    return ctrls


def _storcli_vds(out):
    """解析 storcli 虚拟磁盘列表。"""
    vds = {}
    try:
        data = json.loads(out)
    except Exception:
        return vds
    for cid, c in (data.get("Controllers") or {}).items():
        if not isinstance(c, dict):
            continue
        lst = []
        for vid, v in (c.get("Virtual Drives") or c.get("VirtualDrive") or {}).items():
            if not isinstance(v, dict):
                continue
            state = _storcli_txt(_storcli_ci(v, "State", "Status")).lower()
            badge, label = _STORCLI_VD_STATE.get(state, ("", state or "--"))
            lst.append({
                "vd": vid,
                "name": _storcli_txt(_storcli_ci(v, "Name")),
                "type": _storcli_txt(_storcli_ci(v, "RAID Type", "Type")),
                "size": _storcli_txt(_storcli_ci(v, "Size", "Size in GB")),
                "state": state or "--",
                "state_label": label,
                "badge": badge,
            })
        if lst:
            vds[cid] = lst
    return vds


def _storcli_pds(out):
    """解析 storcli 物理盘列表。"""
    pds = {}
    try:
        data = json.loads(out)
    except Exception:
        return pds
    for cid, c in (data.get("Controllers") or {}).items():
        if not isinstance(c, dict):
            continue
        lst = []
        for pid, p in (c.get("Physical Drives") or c.get("PhysicalDrive") or {}).items():
            if not isinstance(p, dict):
                continue
            state = _storcli_txt(_storcli_ci(p, "State")).lower()
            health, label = _STORCLI_PD_STATE.get(state, ("", state or "--"))
            media_errors = _storcli_num(_storcli_ci(p, "Media Error Count")) or 0
            lst.append({
                "loc": _storcli_txt(_storcli_ci(p, "Slot Number", "Location")) or str(pid),
                "model": _storcli_txt(_storcli_ci(p, "Model Name", "Model")),
                "serial": _storcli_txt(_storcli_ci(p, "Serial Number", "Serial")),
                "intf": _storcli_txt(_storcli_ci(p, "Interface")),
                "media": _storcli_txt(_storcli_ci(p, "Media Type", "Medium Type")),
                "size": _storcli_txt(_storcli_ci(p, "Size", "Raw Size")),
                "temp": _storcli_num(_storcli_ci(p, "Temperature")),
                "health": health,
                "health_label": label,
                "media_errors": media_errors,
            })
        if lst:
            pds[cid] = lst
    return pds


def collect_raid_card():
    """阵列卡检测与硬 RAID 物理盘温度/健康采集（LSI/Broadcom storcli 通用）。

    lspci 按优先级识别控制器（RAID > HBA/SAS > 存储控制器 > SATA 控制器，
    纯 SATA 主板不再误报为阵列卡）。MegaRAID（含 Dell PERC 贴牌）经 storcli
    的 JSON 输出解析：控制器型号/ROC 芯片温度、虚拟阵列状态、每块物理盘的
    盘位/型号/温度/健康/介质错误。温度与 SMART 由阵列卡读传感器，不寻道、
    不唤醒 STANDBY 硬盘。
    """
    info = {"type": "none", "label": "", "detail": "", "tool": "",
            "controllers": [], "error": ""}
    out, rc = run_cmd(["lspci"], timeout=10)
    if rc != 0:
        return info
    pri_line = ""
    pri = 99
    for line in out.splitlines():
        low = line.lower()
        lvl = None
        if "megaraid" in low or ("raid" in low and "sata" not in low):
            lvl = 0
        elif "hba" in low or "sas" in low:
            lvl = 1
        elif "storage controller" in low:
            lvl = 2
        elif "sata controller" in low:
            lvl = 3
        if lvl is not None and lvl < pri:
            pri, pri_line = lvl, line
        if pri == 0:
            break
    if not pri_line:
        return info
    label = pri_line.split(" ", 1)[1] if " " in pri_line else pri_line
    info["label"] = label
    if pri == 0:
        info["type"] = "megaraid"
        tool = _find_storcli()
        info["tool"] = tool
        if not tool:
            info["detail"] = ("MegaRAID 阵列卡：未找到 storcli/perccli 工具，无法读取物理盘温度与健康。"
                              "从 Broadcom 官网下载 storcli 解压后，把 storcli64 放入 /usr/local/bin/"
                              " 或 /opt/MegaRAID/storcli/，稍候面板自动识别")
            info["error"] = "storcli not found"
            return info
        cout, crc = run_cmd([tool, "/call", "show", "all", "J"], timeout=30)
        vout, _ = run_cmd([tool, "/call", "vall", "show", "J"], timeout=30)
        pout, prc = run_cmd([tool, "/call", "eall", "sall", "show", "all", "J"], timeout=30)
        ctrls = _storcli_controllers(cout)
        vds = _storcli_vds(vout)
        pds = _storcli_pds(pout)
        if not ctrls and not pds and not vds:
            info["detail"] = ("MegaRAID 阵列卡：storcli 输出解析失败"
                              "（需 root 权限，或 storcli 版本过旧不支持 J JSON 输出）")
            info["error"] = "storcli parse failed"
            return info
        ids = sorted(set(ctrls) | set(vds) | set(pds))
        for cid in ids:
            ci = ctrls.get(cid) or {}
            info["controllers"].append({
                "id": cid,
                "model": ci.get("model") or label,
                "serial": ci.get("serial", ""),
                "roc_temp": ci.get("roc_temp"),
                "memory": ci.get("memory", ""),
                "status": ci.get("status", ""),
                "vd": vds.get(cid) or [],
                "pds": pds.get(cid) or [],
            })
        n = sum(len(c["pds"]) for c in info["controllers"])
        info["detail"] = "MegaRAID 阵列卡（RAID 模式），经 %s 读取 %d 块物理盘温度与健康" % (
            os.path.basename(tool), n)
        return info
    if pri == 1:
        info["type"] = "hba"
        info["detail"] = "HBA 直通卡（IT 模式），物理盘由系统直接识别为 /dev/sdX，SMART 见硬盘面板"
        return info
    # 纯 SATA 主板（AHCI）：不算阵列卡
    info["type"] = "none"
    info["label"] = ""
    info["detail"] = ""
    return info


# ============================ 7. 硬盘分级与巡检 ============================

GRADE_LABEL = {"ok": "正常", "warn": "留意", "crit": "故障", "unknown": "未知"}
DISK_SCAN_GROWTH_CRIT = 10        # 一次巡检内缺陷增长到这个数就判故障
DISK_SCAN_LOG_KEEP_OK = 7 * 86400  # 「未见异常」保留 7 天；有变化的永久保留

# SMART 明细缓存（巡检结果，供采集循环与详情面板复用）
_DISK_HEALTH_MEM = {}
_DISK_HEALTH_LOCK = threading.RLock()


def grade_disk(rec, prev=None):
    """给单盘定级，返回 (grade, reasons)。rec 为 smart_detail() 结果。

    prev 是上一次巡检记录，用于判定缺陷增长。
    """
    if not rec:
        return "unknown", ["未读取到 SMART"]
    health = str(rec.get("health") or "").strip()
    unc = _nz(rec.get("uncorrectable")) or 0
    reall = _nz(rec.get("reallocated_sectors"))
    pend = _nz(rec.get("pending_sectors")) or 0
    if re.search(r"FAIL", health, re.I):
        return "crit", ["SMART 自检未通过"]
    if unc > 0:
        return "crit", ["不可纠正错误 %d 个" % unc]
    delta = None
    if reall is not None and prev is not None:
        old = _nz(prev.get("defects"))
        if old is not None:
            delta = reall - old
    if delta is not None and delta >= DISK_SCAN_GROWTH_CRIT:
        return "crit", ["重映射扇区增长 %+d 个（%d → %d）" % (delta, reall - delta, reall)]
    reasons = []
    if reall:
        reasons.append("重映射扇区 %d 个" % reall)
    if pend:
        reasons.append("待处理扇区 %d 个" % pend)
    if delta and delta > 0:
        reasons.append("较上次 +%d" % delta)
    if reasons:
        return "warn", reasons
    if not health:
        return "unknown", ["SMART 未提供总体评估"]
    return "ok", ["SMART 通过"]


def disk_scan_devices():
    """要巡检的物理盘：lsblk -d 只列整盘，排除分区/loop/只读/可移动介质。"""
    out, rc = run_cmd(["lsblk", "-b", "-J", "-d", "-o", "NAME,TYPE,SIZE,RO,RM,MODEL"], timeout=10)
    devices = []
    if rc == 0 and out.strip():
        try:
            data = json.loads(out)
            for b in data.get("blockdevices", []) or []:
                name = str(b.get("name") or "")
                if str(b.get("type") or "").lower() != "disk":
                    continue
                if str(b.get("ro")) in ("1", "True", "true") or \
                        str(b.get("rm")) in ("1", "True", "true"):
                    continue
                if not _DEV_NAME_RE.match(name):
                    continue
                devices.append({"dev": name, "size": _nz(b.get("size")) or 0,
                                "model": str(b.get("model") or "").strip()})
        except Exception:
            devices = []
    if devices:
        return devices
    # lsblk 不可用时的降级：读 /sys/block
    for blk_path in sorted(glob.glob("/sys/block/sd*[a-z]") +
                           glob.glob("/sys/block/nvme[0-9]*n[0-9]*") +
                           glob.glob("/sys/block/vd*[a-z]")):
        name = os.path.basename(blk_path)
        if not _DEV_NAME_RE.match(name):
            continue
        sectors = read_int(os.path.join(blk_path, "size"))
        model = read_text(os.path.join(blk_path, "device", "model"), 256).strip()
        devices.append({"dev": name, "size": (sectors or 0) * 512, "model": model})
    return devices


def _serial_norm(v):
    """序列号归一：去空白、转大写。不同厂商写法大小写不一，直接比会漏判。"""
    return str(v or "").strip().upper()


def _scan_key(rec):
    """巡检比对键：优先序列号，缺失时回退设备名。

    设备名会随重启轮换，只有序列号能认出「同一块盘」；但不是所有盘都能读出
    序列号（USB 桥接盒、部分 NVMe），这时必须回退到设备名 —— 若直接返回空串，
    多块盘的 key 会全部撞成同一个，历史比对就废了。
    """
    return _serial_norm(rec.get("serial")) or str(rec.get("dev") or "")


def _prev_index(prev):
    """把上次巡检结果整理成 {比对键: 行} 的索引。

    prev 可能是 dict（键=dev，值=行）或 list（行数组），两种都兼容。
    每行额外挂一份 "dev:<名字>" 别名，给「读不到序列号」的场景兜底：
    休眠盘只回一句 standby，拿不到序列号，这时只能按设备名沿用上次的分级，
    否则一休眠就退回 unknown。
    """
    rows = []
    if isinstance(prev, dict):
        for dev, row in prev.items():
            r = dict(row or {})
            r.setdefault("dev", dev)
            rows.append(r)
    elif isinstance(prev, (list, tuple)):
        for row in prev:
            r = dict(row or {})
            rows.append(r)
    out = {}
    for r in rows:
        out[_scan_key(r)] = r
        dev = str(r.get("dev") or "")
        if dev:
            out.setdefault("dev:" + dev, r)
    return out


def _prev_row_for(item, idx):
    """按序列号取上次的巡检记录；取不到再退回设备名。

    退回设备名时要求两边序列号不冲突：名字对上了但序列号不同，说明那块盘已经
    不在这个位置（是另一块盘顶了这个名字），绝不能拿它的计数当基线 ——
    否则会凭空记下一条「sdg 由正常变为故障」。
    """
    idx = idx or {}
    row = idx.get(_scan_key(item))
    if row is not None:
        return row
    dev = str((item or {}).get("dev") or "")
    row = idx.get("dev:" + dev) if dev else None
    if row is None:
        return {}
    a = _serial_norm(item.get("serial"))
    b = _serial_norm(row.get("serial"))
    if a and b and a != b:
        return {}
    return row


def _scan_change_text(items, prev):
    """把本次巡检相对上次的变化压成一句话列表。

    只比等级与三个计数：温度、通电时长这类自然变量天天在动，算「变化」会把
    「未见异常」的记录淹掉。
    """
    out = []
    prev_idx = _prev_index(prev)
    for it in items or []:
        dev = it.get("dev") or ""
        if not dev or it.get("standby"):
            continue
        old = _prev_row_for(it, prev_idx)
        if not old:
            continue
        og, ng = old.get("grade") or "", it.get("grade") or ""
        if og and ng and og != ng:
            out.append("%s 由「%s」变为「%s」" % (dev, GRADE_LABEL.get(og, og), GRADE_LABEL.get(ng, ng)))
            continue
        bits = []
        for key, label_k in (("defects", "重映射扇区"), ("pending_defects", "待映射扇区"),
                             ("uncorr", "不可纠正错误")):
            a, b = _nz(old.get(key)), _nz(it.get(key))
            if a is not None and b is not None and a != b:
                bits.append("%s %s→%s" % (label_k, a, b))
        if bits:
            out.append("%s，%s" % (dev, "，".join(bits)))
    return out


class DiskScanner(threading.Thread):
    """硬盘巡检线程：定时全盘扫 SMART → 分级入库 → 盘位卡片只读缓存。

    独立成线程（而不是在采集里懒触发）：告警必须定时生效，
    没人打开页面也要照常巡检。
    """

    def __init__(self, db_path=None, interval_h=6, scan_interval_h=None):
        threading.Thread.__init__(self, name="fnstyle-disk-scanner", daemon=True)
        self.db_path = db_path
        # interval_h 是正式参数名；scan_interval_h 作为别名保留，
        # 免得调用方按 StorageMonitor 的命名习惯传参时静默失效
        if scan_interval_h is not None:
            interval_h = scan_interval_h
        self.interval_h = max(1, int(interval_h or 6))
        self._stop = threading.Event()
        self.state = {"running": False, "last_ts": 0.0, "summary": {}, "error": ""}

    def stop(self):
        self._stop.set()

    def run(self):
        # 启动后稍等再巡检，避免与首次采集抢 smartctl
        while not self._stop.wait(30):
            try:
                self.scan_once()
            except Exception:
                pass
            self._stop.wait(self.interval_h * 3600)

    def scan_once(self):
        """执行一次全盘巡检并入库。异常不外抛（线程里靠它活命）。"""
        if self.state.get("running"):
            return {"ok": False, "error": "已有巡检正在进行"}
        self.state["running"] = True
        try:
            ts = time.time()
            prev = self._read_health()
            items, skipped, errors = [], [], []
            for d in disk_scan_devices():
                dev = d["dev"]
                try:
                    rec = smart_detail(dev) or {}
                except Exception as e:
                    errors.append("%s 读取异常：%s" % (dev, e))
                    continue
                if rec and not rec.get("standby"):
                    with _DISK_HEALTH_LOCK:
                        _DISK_HEALTH_MEM[dev] = rec
                # ⚠️ prev 是 dict（键=dev），统一走 _prev_index/_prev_row_for
                prev_idx = _prev_index(prev)
                old = _prev_row_for({"dev": dev, "serial": rec.get("serial")}, prev_idx) or {}
                if rec.get("standby"):
                    # 休眠盘不唤醒：沿用上次分级与计数，只标记状态
                    if old:
                        item = dict(old)
                        item.update({"dev": dev, "standby": True})
                        items.append(item)
                    else:
                        items.append({"dev": dev, "standby": True, "grade": "unknown",
                                      "scan_ts": ts, "detail": "休眠中，尚未读取过"})
                    skipped.append(dev)
                    continue
                grade, reasons = grade_disk(rec, old if old else None)
                items.append({
                    "dev": dev,
                    "serial": rec.get("serial") or "",
                    "model": rec.get("model2") or d.get("model") or "",
                    "proto": "SAS" if rec.get("scsi") else "",
                    "health": str(rec.get("health") or ""),
                    "grade": grade,
                    "defects": _nz(rec.get("reallocated_sectors")),
                    "pending_defects": _nz(rec.get("pending_sectors")),
                    "uncorr": _nz(rec.get("uncorrectable")),
                    "temp_c": _nz(rec.get("temp_c")),
                    "power_on_hours": _nz(rec.get("power_on_hours")),
                    "rpm": str(rec.get("rpm") or ""),
                    "manufactured": str(rec.get("manufactured") or ""),
                    "standby": False,
                    "scan_ts": ts,
                    "detail": "；".join(reasons),
                })
            changed = self._save_health(items, prev)
            diff = _scan_change_text(items, prev)
            self._add_scan_log(ts, len(items), bool(diff),
                               "；".join(diff) if diff else
                               "未见异常（%d 块盘分级与计数均无变化）" % len(items))
            summary = {
                "ok": True, "ts": ts, "total": len(items), "changed": changed,
                "skipped_standby": skipped, "errors": errors,
                "log": ({"changed": True, "text": "；".join(diff)} if diff else {"changed": False}),
                "counts": {g: sum(1 for i in items if i.get("grade") == g)
                           for g in ("ok", "warn", "crit", "unknown")},
            }
            self.state.update({"last_ts": ts, "summary": summary, "error": ""})
            return summary
        except Exception as e:
            self.state["error"] = str(e)
            return {"ok": False, "error": str(e)}
        finally:
            self.state["running"] = False

    # ---- SQLite 存储层 ----

    def _connect(self):
        import sqlite3 as _s
        conn = _s.connect(self.db_path, timeout=10)
        conn.execute("""CREATE TABLE IF NOT EXISTS disk_health (
                       dev TEXT PRIMARY KEY, serial TEXT, model TEXT, proto TEXT,
                       health TEXT, grade TEXT, defects INTEGER, pending_defects INTEGER,
                       uncorr INTEGER, temp_c INTEGER, power_on_hours INTEGER, rpm TEXT,
                       manufactured TEXT, standby INTEGER, scan_ts REAL, changed_ts REAL,
                       detail TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS disk_health_hist (
                       id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, dev TEXT, serial TEXT,
                       grade TEXT, defects INTEGER, pending_defects INTEGER, uncorr INTEGER)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_dhh ON disk_health_hist(dev, ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_dhh_serial ON disk_health_hist(serial, ts)")
        # 老库迁移：CREATE TABLE IF NOT EXISTS 不会给已存在的表补列，
        # disk_health_hist 后加的 serial 列必须手工 ALTER，否则老库插入会报错。
        try:
            cols = [r[1] for r in conn.execute(
                "PRAGMA table_info(disk_health_hist)").fetchall()]
            if cols and "serial" not in cols:
                conn.execute("ALTER TABLE disk_health_hist ADD COLUMN serial TEXT")
                conn.commit()
        except Exception:
            pass
        conn.execute("""CREATE TABLE IF NOT EXISTS disk_scan_log (
                       id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, total INTEGER,
                       changed INTEGER, summary TEXT)""")
        conn.commit()
        return conn

    def _read_health(self):
        try:
            conn = self._connect()
            try:
                conn.row_factory = sqlite3.Row
                rows = {}
                for r in conn.execute("SELECT * FROM disk_health").fetchall():
                    d = dict(r)
                    if d.get("dev"):
                        rows[d["dev"]] = d
                return rows
            finally:
                conn.close()
        except Exception:
            return {}

    def _save_health(self, items, prev):
        """写入本次巡检结果；仅当等级或计数变化时补一条历史。"""
        changed = []
        # ⚠️ prev 是 dict（键=dev，值=行），统一走 _prev_index
        prev_idx = _prev_index(prev)
        for it in items or []:
            old = prev_idx.get(_scan_key(it)) or _prev_row_for(it, prev_idx)
            if not old:
                continue
            sig = ("grade", "defects", "pending_defects", "uncorr")
            if any(old.get(k) != it.get(k) for k in sig):
                changed.append(it["dev"])
        try:
            conn = self._connect()
            try:
                conn.executemany(
                    """INSERT INTO disk_health
                       (dev, serial, model, proto, health, grade, defects, pending_defects, uncorr,
                        temp_c, power_on_hours, rpm, manufactured, standby, scan_ts, changed_ts, detail)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(dev) DO UPDATE SET
                         serial=excluded.serial, model=excluded.model, proto=excluded.proto,
                         health=excluded.health, grade=excluded.grade, defects=excluded.defects,
                         pending_defects=excluded.pending_defects, uncorr=excluded.uncorr,
                         temp_c=excluded.temp_c, power_on_hours=excluded.power_on_hours,
                         rpm=excluded.rpm, manufactured=excluded.manufactured,
                         standby=excluded.standby, scan_ts=excluded.scan_ts,
                         changed_ts=CASE WHEN excluded.grade IS NOT disk_health.grade
                                          OR excluded.defects IS NOT disk_health.defects
                                          OR excluded.pending_defects IS NOT disk_health.pending_defects
                                          OR excluded.uncorr IS NOT disk_health.uncorr
                                     THEN excluded.changed_ts
                                     ELSE disk_health.changed_ts END,
                         detail=excluded.detail""",
                    [(it.get("dev"), it.get("serial"), it.get("model"), it.get("proto"),
                      it.get("health"), it.get("grade"), it.get("defects"), it.get("pending_defects"),
                      it.get("uncorr"), it.get("temp_c"), it.get("power_on_hours"), it.get("rpm"),
                      it.get("manufactured"), 1 if it.get("standby") else 0, it.get("scan_ts"),
                      it.get("scan_ts"), it.get("detail")) for it in items])
                hist = [(it.get("scan_ts"), it.get("dev"), it.get("serial"),
                         it.get("grade"), it.get("defects"),
                         it.get("pending_defects"), it.get("uncorr"))
                        for it in items if it["dev"] in changed and not it.get("standby")]
                if hist:
                    conn.executemany(
                        "INSERT INTO disk_health_hist(ts, dev, serial, grade, defects,"
                        " pending_defects, uncorr) VALUES (?,?,?,?,?,?,?)", hist)
                # 历史只留最近 400 天，够看一年趋势又不会无限增长
                conn.execute("DELETE FROM disk_health_hist WHERE ts < ?",
                             (time.time() - 400 * 86400,))
                # ⚠️ disk_health 以 dev 为主键，但设备名会随重启轮换：
                #    同一块盘从 sdb 变成 sdz 时，若只按新名字插入，库里会同时留下
                #    sdb 与 sdz 两行 —— 同一块盘被记成两块，统计与趋势全乱。
                #    这里按序列号清掉「同一块盘在别的设备名下的旧行」。
                for it in items:
                    serial = str(it.get("serial") or "").strip()
                    if not serial:
                        continue
                    conn.execute("DELETE FROM disk_health WHERE serial=? AND dev<>?",
                                 (serial, it.get("dev")))
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass
        return changed

    def _add_scan_log(self, ts, total, changed, summary):
        try:
            conn = self._connect()
            try:
                with conn:
                    conn.execute("INSERT INTO disk_scan_log(ts, total, changed, summary)"
                                 " VALUES (?,?,?,?)",
                                 (float(ts), int(total or 0), 1 if changed else 0,
                                  str(summary or "")[:2000]))
                    # 「未见异常」只留 7 天，异常永久保留
                    conn.execute("DELETE FROM disk_scan_log WHERE changed=0 AND ts<?",
                                 (time.time() - DISK_SCAN_LOG_KEEP_OK,))
            finally:
                conn.close()
            return True
        except Exception:
            return False

    def read_scan_log(self, limit=200):
        """巡检流水（新→旧）。"""
        try:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "SELECT ts, total, changed, summary FROM disk_scan_log"
                    " ORDER BY ts DESC LIMIT ?", (max(1, min(1000, int(limit or 200))),))
                return [{"ts": r[0], "total": r[1], "changed": bool(r[2]), "summary": r[3] or ""}
                        for r in cur.fetchall()]
            finally:
                conn.close()
        except Exception:
            return []

    def read_health_hist(self, dev, limit=60, serial=None):
        """单盘巡检历史（新→旧），用于显示「较上次 +N」。

        ⚠️ 设备名会随重启轮换，同一块盘改名后按 dev 查就断了链。
        传入 serial 时按序列号查（历史表已存 serial），查不到再退回 dev。
        """
        try:
            conn = self._connect()
            try:
                conn.row_factory = sqlite3.Row
                rows = []
                serial = str(serial or "").strip()
                if serial:
                    cur = conn.execute(
                        "SELECT * FROM disk_health_hist WHERE serial=? ORDER BY ts DESC LIMIT ?",
                        (serial, int(limit)))
                    rows = [dict(r) for r in cur.fetchall()]
                if not rows:
                    cur = conn.execute(
                        "SELECT * FROM disk_health_hist WHERE dev=? ORDER BY ts DESC LIMIT ?",
                        (dev, int(limit)))
                    rows = [dict(r) for r in cur.fetchall()]
                return rows
            finally:
                conn.close()
        except Exception:
            return []

    def read_health(self):
        """全部硬盘的最新巡检结果（dev → dict）。"""
        try:
            conn = self._connect()
            try:
                conn.row_factory = sqlite3.Row
                rows = {}
                for r in conn.execute("SELECT * FROM disk_health").fetchall():
                    d = dict(r)
                    if d.get("dev"):
                        rows[d["dev"]] = d
                return rows
            finally:
                conn.close()
        except Exception:
            return {}


# ============================ 8. 对外汇总 ============================

class StorageMonitor:
    """存储监控聚合器：对外提供一份完整的存储快照。

    采集线程每轮调用 collect()，结果写进内存缓存；
    HTTP 请求只读缓存，绝不触发 smartctl 等重量级操作。
    """

    def __init__(self, db_path=None, scan_interval_h=6):
        self.db_path = db_path
        self._cache = {}
        self._lock = threading.RLock()
        self._prev_disks = []
        self.scanner = None
        if db_path:
            self.scanner = DiskScanner(db_path, scan_interval_h)

    def start(self):
        """启动巡检线程（首次巡检在 30 秒后，避免与首屏采集抢 smartctl）。"""
        if self.scanner and not self.scanner.is_alive():
            try:
                self.scanner.start()
            except Exception:
                pass

    def stop(self):
        if self.scanner:
            try:
                self.scanner.stop()
            except Exception:
                pass

    def scan_now(self):
        """手动触发一次巡检（返回摘要）。"""
        if not self.scanner:
            return {"ok": False, "error": "巡检未启用"}
        return self.scanner.scan_once()

    def collect(self):
        """采集一帧完整存储快照并写入缓存。"""
        try:
            data = self._collect_uncached()
        except Exception:
            data = {}
        with self._lock:
            self._cache = data
        return data

    def snapshot(self):
        """读缓存（不触发采集）。"""
        with self._lock:
            return dict(self._cache)

    def _collect_uncached(self):
        out = {}
        # 1) 存储池容量
        try:
            out["volumes"] = collect_volumes()
        except Exception:
            out["volumes"] = []
        # 2) 文件系统清单（去重后）
        try:
            out["filesystems"] = collect_disks()
        except Exception:
            out["filesystems"] = []
        # 3) 物理盘详情（lsblk + SMART）
        try:
            health_prev = {}
            if self.scanner:
                try:
                    health_prev = self.scanner.read_health()
                except Exception:
                    health_prev = {}
            disks = collect_disks_detail(self._prev_disks, with_states=True,
                                         health_prev=health_prev)
        except Exception:
            disks = []
        self._prev_disks = disks
        out["disks"] = disks
        # 4) 磁盘 IO 速率
        try:
            out["diskio"] = collect_disk_io()
        except Exception:
            out["diskio"] = []
        # 5) 软 RAID
        try:
            out["raid"] = collect_raid()
        except Exception:
            out["raid"] = []
        # 6) 硬 RAID 卡
        try:
            out["raidcard"] = collect_raid_card()
        except Exception:
            out["raidcard"] = {"type": "none", "label": "", "detail": "", "tool": "",
                               "controllers": [], "error": ""}
        # 7) 巡检状态与流水
        scan = {"last_ts": 0.0, "running": False, "summary": {}, "error": ""}
        log = []
        if self.scanner:
            scan = dict(self.scanner.state)
            try:
                log = self.scanner.read_scan_log(50)
            except Exception:
                log = []
        out["scan"] = scan
        out["scanLog"] = log
        # 8) 汇总统计
        out["summary"] = self._summarize(out)
        out["available"] = bool(out["volumes"] or out["disks"])
        out["ts"] = time.time()
        return out

    def _summarize(self, data):
        """存储总览：池数、盘数、告警数、分级统计。"""
        disks = data.get("disks") or []
        volumes = data.get("volumes") or []
        counts = {"ok": 0, "warn": 0, "crit": 0, "unknown": 0}
        warn_disks = []
        for d in disks:
            g = d.get("grade") or "unknown"
            counts[g] = counts.get(g, 0) + 1
            if g in ("warn", "crit"):
                warn_disks.append({
                    "name": d.get("name"), "grade": g,
                    "label": GRADE_LABEL.get(g, g),
                    "detail": d.get("detail", ""),
                })
        total = sum(v.get("total", 0) for v in volumes)
        used = sum(v.get("used", 0) for v in volumes)
        # 汇总的"可用空间"用 total - used（与 fnmonitor 的
        # `dsizeStr(o.disk_size - o.disk_used)` 同一口径），
        # 而不是逐池 free 相加 —— 后者是 f_bavail（不含保留块），
        # 与"总容量 - 已用"对不上，两处显示会差出几个 GB。
        free = max(0, total - used)
        return {
            "poolCount": len(volumes),
            "diskCount": len(disks),
            "sleepingCount": sum(1 for d in disks if d.get("standby")),
            "total": total, "used": used, "free": free,
            "percent": _pct(used, total),
            "totalText": _dsize(total) if total else "—",
            "usedText": _dsize(used) if used else "—",
            "freeText": _dsize(free) if free else "—",
            "grades": counts,
            "warnDisks": warn_disks,
            "raidCount": len(data.get("raid") or []),
            "raidCardType": (data.get("raidcard") or {}).get("type", "none"),
        }

    def disk_history(self, dev, limit=60, serial=None):
        """单盘巡检历史。传入 serial 可跨设备名轮换续查。"""
        if not self.scanner:
            return []
        try:
            return self.scanner.read_health_hist(dev, limit, serial=serial)
        except Exception:
            return []


# ============================ 9. 温度采集（供温度板块复用） ============================

def smartctl_temp(dev):
    """通过 smartctl 读取硬盘温度（sysfs 无 hwmon 时的兜底）。

    -n standby：盘休眠时直接退出（exit 2），绝不为读温度把盘唤醒。
    """
    if not shutil.which("smartctl"):
        return None
    out, rc = run_cmd(["smartctl", "-n", "standby", "-A", "/dev/" + dev], timeout=8)
    if rc == 2:
        return None
    if rc != 0 or not out:
        return None
    for line in out.splitlines():
        if "Temperature_Celsius" in line:
            # 标准列序：ID# NAME FLAG VALUE WORST THRESH TYPE UPDATED WHEN_FAILED RAW_VALUE
            # RAW_VALUE 位于第 10 列（parts[9]），可能是 "42 (Min/Max 20/50)" 这种形式
            parts = line.split()
            if len(parts) >= 10:
                m = re.match(r"(\d+)", parts[9])
                if m:
                    return int(m.group(1))
        st = line.strip()
        if st.startswith("Temperature:"):
            parts = st.split()
            try:
                return int(parts[1])  # NVMe 格式
            except Exception:
                pass
        if st.startswith("Current Drive Temperature:") or st.startswith("Drive Temperature:"):
            # SAS/SCSI 格式：Current Drive Temperature:     33 C
            m = re.search(r"(-?\d+)\s*C", st)
            if m:
                return int(m.group(1))
    return None

# ==================== 历史指标存储（趋势图数据源） ====================
#
# 趋势图需要「一段时间内的连续采样值」。fnmonitor 用的是 SQLite 的
# metrics 表（ts / metric / value 三列），每 60 秒写一条，保留 7 天。
#
# 本实现与 fnmonitor 的差异：
#   - 不新建数据库文件，而是复用既有的 storage.db（同一块盘、同一份
#     备份/清理策略，不额外占空间）；
#   - 只写处理器抽屉真正需要的两个指标：cpu（使用率 %）与 load1（1 分钟负载）。
#     内存趋势图不在本次需求内，不写 mem，避免无用的库体积增长。
#
# 表结构与 fnmonitor 保持一致（迁移自 fnmonitor，MIT License）：
#   metrics(ts REAL NOT NULL, metric TEXT NOT NULL, value REAL NOT NULL)
#   idx_metric_ts ON metrics(metric, ts)

# 各范围对应的秒数（与 fnmonitor 的 _RANGE_SECONDS 一致）
RANGE_SECONDS = {
    "10m": 600,
    "1h": 3600,
    "6h": 21600,
    "24h": 86400,
    "7d": 604800,
    "all": 0,          # 0 = 保留期内全部
}

# 历史保留天数（与 fnmonitor 的 retention_days 一致）
HISTORY_RETENTION_DAYS = 7

# 采样间隔（秒）。与 fnmonitor 的 history_interval 一致。
HISTORY_INTERVAL = 60


class HistoryStore(object):
    """处理器历史指标存储：写入 + 查询。

    表结构与读写口径对齐 fnmonitor 的 HistoryStore（MIT License）：
      - 写入按 (ts, metric, value) 三列，顺序不能错 —— 历史上 fnmonitor
        曾把 (metric, ts, value) 写反，导致查询 WHERE metric=? AND ts>=?
        永远查不到，趋势图每次都从零开始。
      - 查询走 idx_metric_ts 索引（metric, ts），取最新 limit 行再反转回升序。
    """

    def __init__(self, db_path=None):
        self.db_path = db_path

    # ---------- 连接 ----------

    def _connect(self, write=False):
        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
        if write:
            # WAL 下 synchronous=NORMAL：断电最多丢最后一个事务（监控数据可接受），
            # 显著减少 fsync，对机械硬盘更友好。
            try:
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA journal_size_limit=67108864")
            except Exception:
                pass
        conn.execute("""CREATE TABLE IF NOT EXISTS metrics (
                       ts REAL NOT NULL, metric TEXT NOT NULL, value REAL NOT NULL)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_metric_ts ON metrics(metric, ts)")
        # ⚠️ v1.1.53：每日汇总归档表（对齐 fnmonitor 的 daily_summary）。
        #   metrics 是「原始采样点」，保留期只有 7 天，删了就永久没了；
        #   daily_summary 存「按天汇总」，永久保留（DAILY_KEEP_DAYS=400 天）。
        #   两者配合才能让「近 30 天用电」在应用刚装上时也拿得到历史。
        conn.execute("""CREATE TABLE IF NOT EXISTS daily_summary (
                       day TEXT NOT NULL, metric TEXT NOT NULL, value REAL NOT NULL,
                       PRIMARY KEY(day, metric))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS meta (
                       key TEXT PRIMARY KEY, value TEXT)""")
        conn.commit()
        return conn

    # ---------- 写入 ----------

    def write(self, rows):
        """写入一批指标。rows 为 [(ts, metric, value), ...]。

        单条失败不影响其它条 —— 监控数据丢一条无所谓，不能因此让采集线程挂掉。
        """
        if not rows or not self.db_path:
            return False
        try:
            conn = self._connect(write=True)
            try:
                conn.executemany(
                    "INSERT INTO metrics(ts, metric, value) VALUES(?,?,?)", rows)
                conn.commit()
            finally:
                conn.close()
            return True
        except Exception:
            return False

    # ---------- 查询 ----------

    def query(self, metric, seconds, limit=7200):
        """查询某指标在指定时间窗内的序列点，返回 [(ts, value), ...]（时间升序）。

        seconds=0 表示查询全部保留期内的历史（「全部」范围）。
        """
        if not self.db_path:
            return []
        try:
            conn = self._connect()
            try:
                if not seconds:
                    limit = 43200   # 30 天 × 每分钟 1 条
                cutoff = (time.time() - seconds) if seconds else 0
                # 取最新 limit 行再反转回升序，避免超大数据量拖慢查询
                cur = conn.execute(
                    "SELECT ts, value FROM metrics WHERE metric=? AND ts>=? "
                    "ORDER BY ts DESC LIMIT ?", (metric, cutoff, limit))
                return cur.fetchall()[::-1]
            finally:
                conn.close()
        except Exception:
            return []

    # ---------- 每日汇总归档（对齐 fnmonitor 的 daily_summary 机制）----------

    DAILY_KEEP_DAYS = 400      # 每日汇总保留天数
    DAILY_CHUNK = 7 * 86400    # 每次归档推进的秒数（7 天一段）
    _DAILY_MAX_KEYS = ("cpu_max",)
    _UPSERT_DAILY_ADD = ("INSERT INTO daily_summary(day, metric, value) VALUES(?,?,?) "
                         "ON CONFLICT(day, metric) DO UPDATE SET value=value+excluded.value")
    _UPSERT_DAILY_MAX = ("INSERT INTO daily_summary(day, metric, value) VALUES(?,?,?) "
                         "ON CONFLICT(day, metric) DO UPDATE SET value=max(value, excluded.value)")

    @staticmethod
    def _day_start(ts):
        """时间戳 → 当天 00:00 的时间戳（本地时区）。"""
        lt = time.localtime(ts)
        return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))

    def _daily_watermark(self, conn):
        """读归档水位线：已归档到哪个时间戳了。

        返回 (水位线, 是否已初始化)。
        首次（从未归档过）时从「保留期起点当天 0 点」开始，库里实际只有
        保留期内的数据，从更早的地方开始归档也扫不出东西。
        """
        r = conn.execute("SELECT value FROM meta WHERE key='daily_archived_until'").fetchone()
        if r:
            try:
                return float(r[0]), True
            except Exception:
                pass
        return self._day_start(time.time() - self.DAILY_KEEP_DAYS * 86400), False

    def _daily_aggregate(self, conn, start, end, default_dt=60):
        """汇总 metrics 中 start <= ts < end 的行 → {(day, key): value}。只读。

        功率 → kWh 的口径（对齐 fnmonitor）：每个采样代表它之前的一个间隔，
        所以 e = 瞬时功率 × 该间隔秒数 / 3600000；间隔取实际相邻采样差，
        断档（> 3 倍中位间隔）只按一个典型间隔计，避免机器休眠一天后
        把那段时间算成巨大的耗电量。
        """
        acc = {}

        def add(day, k, v):
            acc[(day, k)] = acc.get((day, k), 0.0) + v

        def dk(ts):
            lt = time.localtime(ts)
            return "%04d-%02d-%02d" % (lt.tm_year, lt.tm_mon, lt.tm_mday)

        pr = conn.execute(
            "SELECT ts, value FROM metrics WHERE metric=? AND ts<? ORDER BY ts DESC LIMIT 1",
            ("power", start)).fetchone()
        pts = conn.execute(
            "SELECT ts, value FROM metrics WHERE metric=? AND ts>=? AND ts<? ORDER BY ts",
            ("power", start, end)).fetchall()
        if pts:
            ts_list = ([pr[0]] if pr else []) + [t for t, _ in pts]
            dts = sorted(ts_list[i + 1] - ts_list[i] for i in range(len(ts_list) - 1)
                         if ts_list[i + 1] > ts_list[i])
            med = dts[len(dts) // 2] if dts else float(default_dt)
            cap = 3.0 * max(med, float(default_dt or 0))
            last = pr[0] if pr else None
            for t, v in pts:
                gap = (t - last) if last is not None else None
                dt = gap if (gap is not None and 0 < gap <= cap) else med
                last = t
                day = dk(t)
                e = (v or 0.0) * dt / 3600000.0
                add(day, "kwh", e)
                add(day, "kwh@%02d" % time.localtime(t).tm_hour, e)
                add(day, "power_s", dt)
        return acc

    def archive_daily(self, target, default_dt=60):
        """把 [水位线, target) 的 metrics 汇总进 daily_summary（7 天一段独立事务）。

        ⚠️ v1.1.62：归档必须按**完整自然日**结算 —— target **向上取整到
        下一个自然日 00:00**。

        为什么：daily_summary 的语义是「按天汇总」，`kwh` 是日总量、`kwh@HH` 是
        小时分桶。归档区间若切在某天的中间（比如 cutoff = now-7d 落在 12:58），
        那一天会被拆成两半：
          · 归档表里先写入 00:00–12:58 的部分（`kwh` = 半天值、只有 kwh@00..kwh@12）
          · 剩余部分要等下一轮归档才补上（`_UPSERT_DAILY_ADD` 累加）
        → 归档表在补齐之前一直是**半成品**（某天 kwh 只有半天、小时桶缺一半），
          而 `_integrate_points` 直接消费 `kwh@HH` 分桶 → 半天空档期的
          谷/平拆分会短暂算错。

        向上取整到自然日边界后：每个自然日要么**完整归档**（含全部 24 个
        小时桶），要么完全不出现，不会再有半天状态。
        边界日会晚最多 24 小时入档（那一天的实时数据仍由水位线后的尾段
        提供，不会丢）；`cleanup()` 的删除线也随之收紧到水位线，
        保证「凡是删掉的都已经进 daily_summary」。

        注：向上取整可能让 target 超过"当前已有的数据"，
        `_daily_aggregate` 只统计 ts < end 的**现存**点，
        未来时段没有点 → 天然安全，不会凭空造数据。
        """
        if not self.db_path:
            return 0
        # 向上取整到下一个自然日 00:00（只归档完整日）
        _raw_target = float(target)
        target = self._day_start(_raw_target)
        if target <= _raw_target:
            target += 86400
        n = 0
        try:
            conn = self._connect(write=True)
            try:
                wm, _ = self._daily_watermark(conn)
                # ⚠️ v1.1.62：归档必须按**完整自然日**结算。
                #    target 向上取整到下一个自然日 00:00 —— 这样每次推进的边界
                #    都落在自然日边界上，daily_summary 里每个自然日要么完整归档
                #    （含全部 24 个 kwh@HH 小时桶），要么完全不出现，
                #    不会再出现「某天只有半天、小时桶缺一半」的半成品状态。
                #
                #    ⚠️ 老库的水位线可能落在任意时刻（v1.1.61 及之前的 bug）。
                #    这里**不能**把水位线单独归整 —— 那样 [00:00, 老wm) 这段
                #    数据会被永久跳过、永远丢失。正确做法：把 target 推到
                #    「晚于老水位线的下一个自然日边界」，把老水位线到该边界
                #    这一段照常归档掉（老库遗留的一次性代价），
                #    之后所有推进都落在自然日边界上。
                if wm >= target:
                    # 老水位线已越过对齐点 → target 推到下一个自然日边界，
                    # 把 [老wm, 下一个边界) 这段补归档掉，之后水位线就对齐了。
                    nxt = self._day_start(wm)
                    if nxt <= wm:
                        nxt += 86400
                    target = nxt
                while wm < target:
                    end = min(float(target), wm + self.DAILY_CHUNK)
                    with conn:
                        agg = self._daily_aggregate(conn, wm, end, default_dt)
                        for (day, k), v in agg.items():
                            conn.execute(self._UPSERT_DAILY_MAX if k in self._DAILY_MAX_KEYS
                                         else self._UPSERT_DAILY_ADD, (day, k, v))
                            n += 1
                        conn.execute("INSERT INTO meta(key, value) VALUES('daily_archived_until', ?) "
                                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                     (repr(end),))
                    wm = end
                keep = time.strftime("%Y-%m-%d",
                                     time.localtime(time.time() - self.DAILY_KEEP_DAYS * 86400))
                with conn:
                    conn.execute("DELETE FROM daily_summary WHERE day < ?", (keep,))
            finally:
                conn.close()
        except Exception:
            return 0
        return n

    def daily_rows(self, days, default_dt=60):
        """最近 days 天（含今天）的每日汇总：已归档部分 + 水位线之后的实时尾段。

        返回 {day: {key: value}}。
        """
        if not self.db_path:
            return {}
        now = time.time()
        first = time.strftime("%Y-%m-%d",
                               time.localtime(self._day_start(now) - (int(days) - 1) * 86400 + 3600))
        out = {}
        try:
            conn = self._connect()
            try:
                for day, k, v in conn.execute(
                        "SELECT day, metric, value FROM daily_summary WHERE day >= ?", (first,)):
                    out.setdefault(day, {})[k] = v
                wm, has = self._daily_watermark(conn)
                # 从未归档过（刚装上）时只实时汇总今天，避免扫全库
                tail_start = wm if has else max(wm, self._day_start(now))
                for (day, k), v in self._daily_aggregate(conn, tail_start, now + 1,
                                                          default_dt).items():
                    if day < first:
                        continue
                    d = out.setdefault(day, {})
                    if k in self._DAILY_MAX_KEYS:
                        d[k] = max(d.get(k, v), v)
                    else:
                        d[k] = d.get(k, 0.0) + v
            finally:
                conn.close()
        except Exception:
            return {}
        out["_meta"] = {"archived_until": wm if has else None}
        return out

    # ---------- 清理 ----------

    def cleanup(self, retention_days=None):
        """删除超出保留期的历史点（默认 7 天）。

        ⚠️ retention_days=0 表示「立即清掉全部」，必须显式判 None ——
        用 `retention_days or DEFAULT` 会把 0 当成假值，误用默认 7 天。

        ⚠️ v1.1.53：**先归档再删**（对齐 fnmonitor 的 cleanup 顺序）。
           metrics 原始点只保留 7 天，删掉就永久没了；必须先把它们按天
           汇总进 daily_summary（永久保留），否则「近 30 天用电」永远只有
           装应用那几天的数据。

        ⚠️ 归档目标是 **cutoff（保留期边界）**，不是 now-600。
           删的是 ts < cutoff 的行，所以必须把 [水位线, cutoff) 全归档完；
           若只归档到 now-600，保留期边界前的那批点会被直接删掉、永久丢失。
           （fnmonitor 用 max(tgt, cutoff) 取较晚者，靠每小时滚动归档弥补，
             但那要求归档频率 ≥ 删除频率；这里每小时清理一次，
             归档到 cutoff 才真正不丢数据。）
        """
        if not self.db_path:
            return 0
        days = HISTORY_RETENTION_DAYS if retention_days is None else retention_days
        try:
            cutoff = time.time() - days * 86400
            # ⚠️ 先归档再删：archive_daily 自己开连接，必须在删除事务之外调用，
            #    否则同一个库上并发写会撞 SQLite 锁。
            try:
                self.archive_daily(cutoff)
            except Exception:
                pass
            # ⚠️ v1.1.62：删除边界必须**不晚于**归档水位线。
            #    archive_daily() 现在只按**完整自然日**归档（v1.1.62 起），
            #    水位线停在自然日 00:00；而 cutoff = now - N*86400 落在
            #    一天中间（12:58 这种）→ 若直接按 cutoff 删，cutoff 当天
            #    00:00–12:58 那批点会因为「还没归档到」而被永久删掉。
            #    → 实际删除线取 min(cutoff, 归档水位线)，保证
            #      「凡是删掉的，都已经进 daily_summary 了」。
            try:
                conn = self._connect()
                try:
                    wm, has = self._daily_watermark(conn)
                finally:
                    conn.close()
            except Exception:
                wm, has = None, False
            del_before = cutoff
            if has and wm is not None and wm < del_before:
                del_before = wm
            conn = self._connect(write=True)
            try:
                cur = conn.execute("DELETE FROM metrics WHERE ts < ?", (del_before,))
                conn.commit()
                return cur.rowcount or 0
            finally:
                conn.close()
        except Exception:
            return 0
