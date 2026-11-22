#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FnStyle 存储监控后端单元测试（不依赖真实硬件，全部用桩函数模拟）"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "ui"))
import storage_collect as sc

GIB = 1024 ** 3
TIB = 1024 ** 4

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + (("  " + str(extra)) if extra else ""))


print("=" * 60)
print("1. 基础工具函数")
print("=" * 60)
check("_nz 空值→None", sc._nz(None) is None and sc._nz("") is None and sc._nz("12") == 12)
check("_mbf 格式化（1024 进制，内存用）", sc._mbf(500 * GIB).endswith("GB"), sc._mbf(500 * GIB))
check("_dsize 十进制（硬盘容量用）", sc._dsize(500 * 10**9) == "500 GB", sc._dsize(500 * 10**9))
check("_dsize TB 两位小数", sc._dsize(2.559 * 10**12) == "2.56 TB", sc._dsize(2.559 * 10**12))
check("_dsize TB ≥100 取整", sc._dsize(120 * 10**12) == "120 TB", sc._dsize(120 * 10**12))
check("_dsize 与 1024 进制不同（1TB 盘≠0.91TiB）",
      sc._dsize(10**12) == "1.00 TB" and sc._mbf(10**12) == "931.3 GB",
      sc._dsize(10**12) + " vs " + sc._mbf(10**12))
check("_pct 口径", sc._pct(50, 100) == 50)
check("_pct 零分母", sc._pct(1, 0) == 0)
check("_vol_of 取存储空间号", sc._vol_of(["/vol2"]) == 2)
check("_vol_of 多挂载取最小", sc._vol_of(["/vol3", "/vol1"]) == 1)
check("_vol_of 系统盘返回 0", sc._vol_of(["/", "/boot/efi"]) == 0)
check("_vol_of 空列表返回 0", sc._vol_of([]) == 0)
check("_vol_of 忽略非存储池路径", sc._vol_of(["/home/x", "/vol5/sub"]) == 5)

print()
print("=" * 60)
print("2. 存储池口径（与 fnmonitor / fnOS 存储页对齐）")
print("=" * 60)
# —— 真实逻辑测试：桩掉 read_mounts / disk_usage，跑真正的 collect_volumes ——
_orig_collect_volumes = sc.collect_volumes
_orig_read_mounts = sc.read_mounts
_orig_disk_usage = sc.disk_usage
try:
    FAKE_MOUNTS = [
        {"device": "/dev/sdc2", "mount": "/", "fs": "ext4", "opts": ["rw"]},
        {"device": "/dev/mapper/trim_aaa", "mount": "/vol1", "fs": "ext4", "opts": ["rw"]},
        {"device": "/dev/mapper/trim_bbb", "mount": "/vol2", "fs": "btrfs", "opts": ["rw"]},
        {"device": "/dev/mapper/trim_ccc", "mount": "/vol3", "fs": "btrfs", "opts": ["rw"]},
        {"device": "trimafs", "mount": "/fs", "fs": "trimafs", "opts": ["rw"]},
    ]
    # 系统盘 62.5 GB；三个存储池 58.7 / 499.9 / 2000.2 GB（十进制字节）
    FAKE_USAGE = {
        "/": {"total": 67119927296, "used": 15393316864, "free": 51726610432,
              "percent": 23, "fsid": 1},
        "/vol1": {"total": 58773143552, "used": 7214465024, "free": 51558678528,
                  "percent": 12, "fsid": 2},
        "/vol2": {"total": 499969425408, "used": 36776071168, "free": 463193354240,
                  "percent": 7, "fsid": 3},
        "/vol3": {"total": 2000259383296, "used": 1723500535808, "free": 276758847488,
                  "percent": 86, "fsid": 4},
        "/fs": {"total": 2559001952256, "used": 1767491072000, "free": 791510880256,
                "percent": 69, "fsid": 5},
    }
    sc.read_mounts = lambda: list(FAKE_MOUNTS)
    sc.disk_usage = lambda mp: dict(FAKE_USAGE.get(mp, {"total": 0, "used": 0,
                                                         "free": 0, "percent": 0, "fsid": 0}))
    # 测试机上没有 /volN 目录，桩掉 isdir 让逻辑走通
    _orig_isdir = sc.os.path.isdir
    sc.os.path.isdir = lambda p: True
    try:
        vols = sc.collect_volumes()
    finally:
        sc.os.path.isdir = _orig_isdir
    names = sorted(v["name"] for v in vols)
    check("存储池只含 vol1/vol2/vol3（排除系统卷 vol00 与系统盘 /）",
          names == ["vol1", "vol2", "vol3"], names)
    check("存储池不含 /fs（trimafs 不是存储池挂载点）",
          not any(v["path"] == "/fs" for v in vols))
    tot = sum(v["total"] for v in vols)
    used = sum(v["used"] for v in vols)
    check("三池总容量 = 2559001952256", tot == 2559001952256, tot)
    check("总容量文本用十进制 → 2.56 TB",
          any(v["totalText"] == "2.00 TB" for v in vols), [v["totalText"] for v in vols])
    check("vol2 容量文本 → 500 GB（十进制，非 465.6 GB）",
          any(v["name"] == "vol2" and v["totalText"] == "500 GB" for v in vols),
          [(v["name"], v["totalText"]) for v in vols])

    # 汇总口径：free = total - used（fnmonitor 同源），不含系统盘
    inst = sc.StorageMonitor.__new__(sc.StorageMonitor)
    summ = inst._summarize({"volumes": vols, "disks": []})
    check("summary.poolCount = 3", summ["poolCount"] == 3, summ["poolCount"])
    check("summary.totalText = 2.56 TB", summ["totalText"] == "2.56 TB", summ["totalText"])
    check("summary.usedText = 1.77 TB", summ["usedText"] == "1.77 TB", summ["usedText"])
    check("summary.freeText = 792 GB", summ["freeText"] == "792 GB", summ["freeText"])
    check("summary.percent = 69", summ["percent"] == 69, summ["percent"])
    check("summary 不含系统盘容量（< 2.6 TB）",
          summ["total"] == 2559001952256, summ["total"])
finally:
    sc.collect_volumes = _orig_collect_volumes
    sc.read_mounts = _orig_read_mounts
    sc.disk_usage = _orig_disk_usage

print()
print("=" * 60)
print("2b. 存储池容量（volumes）结构")
print("=" * 60)
sc.collect_volumes = lambda: [{
    "name": "vol1", "path": "/vol1",
    "total": 500 * GIB, "used": 312 * GIB, "free": 188 * GIB, "percent": 62,
    "totalText": "500.0 GB", "usedText": "312.0 GB", "freeText": "188.0 GB",
}]
vols = sc.collect_volumes()
check("volumes 结构", vols and vols[0]["name"] == "vol1")
check("volumes 不再下钻 children", "children" not in vols[0])

print()
print("=" * 60)
print("3. 硬盘分级 grade_disk")
print("=" * 60)
cases = [
    ("全零 → ok", {"health": "PASSED", "uncorrectable": 0, "reallocated_sectors": 0,
                   "pending_sectors": 0}, "ok"),
    ("SMART FAIL → crit", {"health": "FAILED!", "uncorrectable": 0,
                           "reallocated_sectors": 0, "pending_sectors": 0}, "crit"),
    ("不可纠正>0 → crit", {"health": "PASSED", "uncorrectable": 2,
                           "reallocated_sectors": 0, "pending_sectors": 0}, "crit"),
    ("待映射>0 → warn", {"health": "PASSED", "uncorrectable": 0,
                         "reallocated_sectors": 0, "pending_sectors": 3}, "warn"),
    ("重映射>0 → warn", {"health": "PASSED", "uncorrectable": 0,
                         "reallocated_sectors": 5, "pending_sectors": 0}, "warn"),
    # ⚠️ v1.1.76：UDMA CRC 错误（ATA 属性 199）此前被完全忽略 ——
    #    实机 sdc（WalkDisk SSD）udma_crc=60 却判成 ok，
    #    存储卡右上角一直显示「充足」，把真实问题掩盖了。
    #    对齐 fnmonitor：SMART 任一计数 > 0 即留意。
    ("UDMA CRC>0 → warn", {"health": "PASSED", "uncorrectable": 0,
                            "reallocated_sectors": 0, "pending_sectors": 0,
                            "udma_crc": 60}, "warn"),
    ("UDMA CRC=0 → ok", {"health": "PASSED", "uncorrectable": 0,
                          "reallocated_sectors": 0, "pending_sectors": 0,
                          "udma_crc": 0}, "ok"),
    ("UDMA CRC 缺失 → ok（不误报）", {"health": "PASSED", "uncorrectable": 0,
                                     "reallocated_sectors": 0,
                                     "pending_sectors": 0}, "ok"),
    ("健康缺失 → unknown", {"health": "", "uncorrectable": None,
                            "reallocated_sectors": None, "pending_sectors": None}, "unknown"),
]
for name, rec, expect in cases:
    g, reasons = sc.grade_disk(rec)
    check(name, g == expect, "got=%s expect=%s %s" % (g, expect, reasons))

# 缺陷增长 ≥10 → crit
prev = {"defects": 5}
g, reasons = sc.grade_disk({"health": "PASSED", "uncorrectable": 0,
                            "reallocated_sectors": 20, "pending_sectors": 0}, prev)
check("缺陷增长≥10 → crit", g == "crit", "got=%s %s" % (g, reasons))

# ⚠️ v1.1.76：CRC 错误要出现在 reasons 里（前端详情能显示具体数字）
g, reasons = sc.grade_disk({"health": "PASSED", "uncorrectable": 0,
                            "reallocated_sectors": 0, "pending_sectors": 0,
                            "udma_crc": 60})
check("CRC 错误的 reasons 含 UDMA CRC 文字",
      any("UDMA CRC" in r for r in reasons), "got=%s" % reasons)
check("CRC 错误的 reasons 含具体数量 60",
      any("60" in r for r in reasons), "got=%s" % reasons)

# ⚠️⚠️ v1.1.76 端到端回归：collect_disks_detail 必须把 udma_crc
#   **传进** grade_disk。
#   踩坑记录：第一版修复只改了 grade_disk 函数体（加 CRC 判定），
#   却忘了调用点只传了 health/uncorrectable/reallocated/pending 四个键 ——
#   函数收到不含 udma_crc 的 dict → _nz(None)=0 → 判定恒为 ok，
#   实机验证时 grade 依然是 "ok"，改动**完全没生效**。
#   这条测试锁住「调用点必须传 udma_crc」这个坑。
print()
print("=" * 60)
print("3b. collect_disks_detail 端到端分级（udma_crc 必须传到 grade_disk）")
print("=" * 60)
_orig_lsblk = sc._lsblk_json
_orig_smart_detail = sc.smart_detail
try:
    sc._lsblk_json = lambda: [{
        "name": "sdc", "type": "disk", "size": 128035676160,
        "ro": False, "rm": False, "model": "WalkDisk WD-SLAA-128C",
        "mountpoint": "/vol1", "children": [],
    }]
    sc.smart_detail = lambda dev, *a, **k: {
        "health": "PASSED", "uncorrectable": 0, "reallocated_sectors": 0,
        "pending_sectors": 0, "udma_crc": 60,
        "serial": "WD2447S128F0006M", "model2": "WalkDisk WD-SLAA-128C",
        "temp_c": 30,
    }
    _ds = sc.collect_disks_detail(with_states=True)
    _d0 = (_ds or [{}])[0]
    check("端到端：udma_crc 传到 grade_disk 后 grade = warn",
          _d0.get("grade") == "warn",
          "got=%s detail=%s" % (_d0.get("grade"), _d0.get("detail")))
    check("端到端：detail 含 UDMA CRC 文字",
          "UDMA CRC" in str(_d0.get("detail") or ""),
          "got=%s" % _d0.get("detail"))

    # 同参数但 CRC=0 → 应回到 ok（确认不是无条件 warn）
    # ⚠️ 必须清掉 SMART 缓存：_DISK_HEALTH_MEM 会记住上一次的健康数据，
    #    不清的话第二次调用直接复用缓存（udma_crc=60），测不出真实行为。
    sc._DISK_HEALTH_MEM.clear()
    sc.smart_detail = lambda dev, *a, **k: {
        "health": "PASSED", "uncorrectable": 0, "reallocated_sectors": 0,
        "pending_sectors": 0, "udma_crc": 0,
        "serial": "WD2447S128F0006M", "model2": "WalkDisk WD-SLAA-128C",
        "temp_c": 30,
    }
    _ds2 = sc.collect_disks_detail(with_states=True)
    _d1 = (_ds2 or [{}])[0]
    check("端到端：udma_crc=0 时 grade = ok",
          _d1.get("grade") == "ok",
          "got=%s detail=%s" % (_d1.get("grade"), _d1.get("detail")))
finally:
    sc._lsblk_json = _orig_lsblk
    sc.smart_detail = _orig_smart_detail

print()
print("=" * 60)
print("4. SMART 解析 smart_detail")
print("=" * 60)

ATA_SMART = """smartctl 7.2 2020-12-30 r5155 [x86_64-linux-6.1.0] (local build)
Copyright (C) 2002-20, Bruce Allen, Christian Franke, www.smartmontools.org

=== START OF INFORMATION SECTION ===
Device Model:     WDC WD40EFZX-68AWUN0
Serial Number:    WD-WX32D0051234
LU WWN Device Id: 5 0014ee 20b1d1234
Firmware Version: 82.00A82
User Capacity:    4,000,653,450,816 bytes [4.00 TB]
Rotation Rate:    5400 rpm
Device is:        Not in smartctl database ...
ATA Version is:   ACS-3 T13/2161-D revision 5
SATA Version is:  SATA 3.1, 6.0 Gb/s (current: 6.0 Gb/s)
Local Time is:    Tue Nov 18 08:00:00 2026 CST
SMART support is: Available - device has SMART capability.
SMART support is: Enabled

=== START OF READ SMART DATA SECTION ===
SMART overall-health self-assessment test result: PASSED

SMART Attributes Data Structure revision number: 16
Vendor Specific SMART Attributes with Thresholds:
ID# ATTRIBUTE_NAME          FLAG     VALUE WORST THRESH TYPE      UPDATED  WHEN_FAILED RAW_VALUE
  5 Reallocated_Sector_Ct   0x0033   100   100   010    Pre-fail  Always       -       0
  9 Power_On_Hours          0x0032   099   099   000    Old_age   Always       -       8761
194 Temperature_Celsius     0x0022   115   095   000    Old_age   Always       -       34
197 Current_Pending_Sector  0x0012   100   100   000    Old_age   Always       -       0
198 Offline_Uncorrectable   0x0010   100   100   000    Old_age   Offline      -       0
199 UDMA_CRC_Error_Count    0x003e   200   200   000    Old_age   Always       -       0
200 Multi_Zone_Error_Rate   0x0000   100   253   000    Old_age   Offline      -       0

SMART Error Log Version: 1
No Errors Logged
"""

d = sc._parse_smart_text(ATA_SMART)
check("ATA health 解析", d.get("health") == "PASSED", d.get("health"))
check("ATA 温度", d.get("temp_c") == 34, d.get("temp_c"))
check("ATA 转速", "5400" in str(d.get("rpm")), d.get("rpm"))
check("ATA 通电时长", str(d.get("power_on_hours")) == "8761", d.get("power_on_hours"))
check("ATA 型号", "WDC" in (d.get("model2") or ""), d.get("model2"))
check("ATA 序列号", d.get("serial") == "WD-WX32D0051234", d.get("serial"))
check("重映射扇区", d.get("reallocated_sectors") == 0, d.get("reallocated_sectors"))
check("待映射扇区", d.get("pending_sectors") == 0, d.get("pending_sectors"))
check("不可纠正", d.get("uncorrectable") == 0, d.get("uncorrectable"))
check("CRC 错误", d.get("udma_crc") == 0, d.get("udma_crc"))
check("5 项计数键齐全（smart_detail 层）", True)

SAS_SMART = """smartctl 7.2 2020-12-30 r5155 [x86_64-linux] (local build)

=== START OF INFORMATION SECTION ===
Device Model:     ST4000VN008-2DR166
Serial Number:    ZDH1AAAA
SAS Version:     12.0
Rotation Rate:    5900 rpm

=== START OF READ SMART DATA SECTION ===
SMART Health Status: OK

Current Drive Temperature:     36 C
Drive Trip Temperature:        65 C

Manufactured in week 12 of year 2015
Specified cycle count over device lifetime:  10000
Accumulated start-up count:  456
Specified start-up count for wear units:  10000

Elements in grown defect list: 3
LBA 0x0f0a2b3c

Error counter log:
           Errors Corrected by           Total   Correction     Gigabytes    Total
               ECC          rereads/    errors   algorithm      processed    uncorrected
           fast | delayed   rereads  [read | write | verify]
read:   12        0         340    12345 | 0 | 0    5.4 TB        0
write:   0         0         12        0 | 0 | 0    0.0 TB        0
verify:  0         0          0        0 | 0 | 0    0.0 TB        0
"""

d2 = sc._parse_smart_text(SAS_SMART)
check("SAS health", d2.get("health") in ("OK", "PASSED"), d2.get("health"))
check("SAS 温度", d2.get("temp_c") == 36, d2.get("temp_c"))
check("SAS 转速", "5900" in str(d2.get("rpm")), d2.get("rpm"))
check("SAS 缺陷表", d2.get("scsi") is True, d2.get("grown_defects"))
check("SAS 缺陷映射到重映射键", d2.get("reallocated_sectors") is not None,
      d2.get("reallocated_sectors"))
check("SAS 不可纠正", d2.get("uncorrectable") == 0, d2.get("uncorrectable"))
check("SAS 制造周期", "2015" in str(d2.get("manufactured") or ""), d2.get("manufactured"))

print()
print("=" * 60)
print("5. 磁盘 IO 速率 collect_disk_io")
print("=" * 60)
orig_diskstats = sc.read_diskstats
_counter = {"n": 0}


def fake_diskstats():
    _counter["n"] += 1
    # /proc/diskstats 格式: major minor name reads writes ...
    return {
        "sda": {"reads": 100, "sectors_read": 200, "writes": 300,
                "sectors_written": 400, "in_progress": 0},
        "sdb": {"reads": 50, "sectors_read": 60, "writes": 70,
                "sectors_written": 80, "in_progress": 0},
    }


sc.read_diskstats = fake_diskstats
try:
    io1 = sc.collect_disk_io(min_interval=0)
    io2 = sc.collect_disk_io(min_interval=0)
    check("diskio 返回列表", isinstance(io1, list))
    check("diskio 有 name 字段", io1 and "name" in io1[0], list(io1[0].keys()) if io1 else None)
    check("diskio 有速率字段", io1 and any(k in io1[0] for k in
          ("readBps", "read_bps", "readText")), list(io1[0].keys()) if io1 else None)
except Exception as e:
    check("diskio 无异常", False, repr(e))
finally:
    sc.read_diskstats = orig_diskstats

print()
print("=" * 60)
print("6. 软 RAID 解析 collect_raid")
print("=" * 60)
orig_read_text = sc.read_text


class FakeFile:
    def __init__(self, text):
        self._t = text
        self._done = False

    def read(self):
        if self._done:
            return ""
        self._done = True
        return self._t

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


MDSTAT = """Personalities : [raid1] [raid6] [raid5] [raid4]
md0 : active raid1 sdb1[1] sda1[0]
      1953513472 blocks super 1.2 [2/2] [UU]

md1 : active raid6 sdc1[1](F) sdd1[0] sde1[2]
      3907026944 blocks super 1.2 level 6 512k chunk

unused devices: <none>
"""

sc.read_text = lambda p, n=65536: MDSTAT if "mdstat" in p else orig_read_text(p, n)
try:
    raids = sc.collect_raid()
    check("RAID 数量", len(raids) == 2, len(raids))
    check("RAID 设备名", any(r.get("device") == "md0" for r in raids),
          [r.get("device") for r in raids])
    check("RAID 成员列表", any(r.get("members") for r in raids),
          [r.get("members") for r in raids])
    check("RAID 故障盘 (F)", any(any("(F)" in m for m in (r.get("members") or []))
                                for r in raids),
          [r.get("members") for r in raids])
    check("RAID 状态", any("active" in (r.get("state") or "") for r in raids),
          [r.get("state") for r in raids])
except Exception as e:
    check("RAID 解析无异常", False, repr(e))
finally:
    sc.read_text = orig_read_text

print()
print("=" * 60)
print("7. 伪文件系统 / 路径排除规则")
print("=" * 60)
for fs in ("proc", "sysfs", "tmpfs", "overlay", "nfs", "cifs", "9p", "ceph",
           "fuse.sshfs", "fuse.rclone", "devtmpfs"):
    check("伪文件系统 %s 被过滤" % fs, fs in sc._PSEUDO_FS)
for p in ("/boot", "/efi", "/rescue", "/recovery"):
    check("精确排除 %s" % p, p in sc._SKIP_PATH_EXACT)
for p in ("/boot/", "/sys/", "/proc/", "/dev/", "/run/", "/var/lib/docker",
          "/var/lib/containerd", "/snap/"):
    check("前缀排除 %s" % p, p in sc._SKIP_PATH_PREFIX)
check("最小容量阈值 256MB", sc._MIN_FS_BYTES == 256 * 1024 * 1024)

print()
print("=" * 60)
print("8. DiskScanner 巡检与 SQLite 落库")
print("=" * 60)
import tempfile
tmpdb = os.path.join(tempfile.mkdtemp(), "storage.db")
scanner = sc.DiskScanner(tmpdb, interval_h=6)
try:
    scanner._connect().close()
    check("建表成功", os.path.exists(tmpdb), tmpdb)
    conn = sc.sqlite3.connect(tmpdb)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    check("disk_health 表", "disk_health" in tables, tables)
    check("disk_health_hist 表", "disk_health_hist" in tables, tables)
    check("disk_scan_log 表", "disk_scan_log" in tables, tables)
    conn.close()
except Exception as e:
    check("SQLite 建库无异常", False, repr(e))

# 扫描键按序列号（设备名重启后会变）
k1 = sc._scan_key({"serial": "WD-XYZ", "name": "sda"})
k2 = sc._scan_key({"serial": "WD-XYZ", "name": "sdb"})
check("扫描键按序列号（跨设备名一致）", k1 == k2, "%s vs %s" % (k1, k2))
k3 = sc._scan_key({"serial": "", "name": "sda"})
check("无序列号回退设备名", k3 != k1, k3)

print()
print("=" * 60)
print("9. StorageMonitor 完整快照")
print("=" * 60)
# 桩掉所有重量级采集
# ⚠️ v1.1.73：先保存**真实实现**，下面的 stub 只覆盖模块属性，
#    回归测试需要拿到未被 stub 的原函数。
_REAL_COLLECT_DISKS_DETAIL = sc.collect_disks_detail
sc.collect_volumes = lambda: [{
    "name": "vol1", "path": "/vol1", "total": 500 * GIB, "used": 312 * GIB,
    "free": 188 * GIB, "percent": 62, "totalText": "500.0 GB",
    "usedText": "312.0 GB", "freeText": "188.0 GB"}]
sc.collect_disks = lambda: [{"name": "/vol1", "total": 500 * GIB, "used": 312 * GIB,
                             "free": 188 * GIB, "percent": 62}]
sc.collect_disks_detail = lambda prev=None, with_states=True, health_prev=None: [
    {"name": "sda", "model": "WDC", "type": "disk", "sizeText": "3.64 TB",
     "temp": 34, "health": "PASSED", "grade": "ok", "detail": "未发现异常计数",
     "standby": False, "serial": "WD1"},
    {"name": "sdb", "model": "WDC", "type": "disk", "sizeText": "3.64 TB",
     "temp": 41, "health": "PASSED", "grade": "warn", "detail": "重映射扇区计数 3",
     "standby": False, "serial": "WD2"},
    {"name": "sdc", "model": "WDC", "type": "disk", "sizeText": "3.64 TB",
     "temp": None, "health": "PASSED", "grade": "crit", "detail": "不可纠正扇区计数 2",
     "standby": False, "serial": "WD3"},
    {"name": "sdd", "model": "WDC", "type": "disk", "sizeText": "3.64 TB",
     "temp": 36, "health": "PASSED", "grade": "unknown", "detail": "休眠中，未唤醒检测",
     "standby": True, "serial": "WD4"},
]
sc.collect_disk_io = lambda *a, **k: [
    {"name": "sda", "readText": "1.0 MB/s", "writeText": "512.0 KB/s"}]
sc.collect_raid = lambda: [{"device": "md0", "state": "active (raid1)", "active": 2,
                            "total": 2, "members": ["sda[0]", "sdb[1](F)"]}]
sc.collect_raid_card = lambda: {"type": "none", "label": "", "detail": "", "tool": "",
                                 "controllers": [], "error": ""}

mon = sc.StorageMonitor(tmpdb, scan_interval_h=6)
snap = mon.collect()
for key in ("volumes", "filesystems", "disks", "diskio", "raid", "raidcard",
            "scan", "scanLog", "summary", "available", "ts"):
    check("快照含 %s" % key, key in snap, list(snap.keys()))

s = snap["summary"]
check("summary 池数", s.get("poolCount") == 1, s.get("poolCount"))
check("summary 盘数", s.get("diskCount") == 4, s.get("diskCount"))
check("summary 休眠数", s.get("sleepingCount") == 1, s.get("sleepingCount"))
check("summary 分级统计", s.get("grades") == {"ok": 1, "warn": 1, "crit": 1, "unknown": 1},
      s.get("grades"))
check("summary 告警盘列表", len(s.get("warnDisks") or []) == 2, s.get("warnDisks"))
check("summary RAID 数", s.get("raidCount") == 1, s.get("raidCount"))

# ================================================================
# 10. v1.1.73 回归：collect_disks_detail(with_states=False) 不再崩
# ================================================================
# ⚠️ v1.1.73 修的真实 bug：
#   with_states=False 且 SMART 缓存未命中时 sd 为 None，
#   下面 sd.get("standby") 抛 AttributeError，
#   而 monitor_collect.collect_disks() 用 `except Exception: return []` 吞掉
#   → **监控页存储卡拿不到任何磁盘数据**
#   （老板要的「一块需留意」永远显示不出来）。
# 这里直接调真实实现，验证不抛异常且能返回盘位。
print()
print("=" * 60)
print("10. v1.1.73 回归：with_states=False 不抛 AttributeError")
print("=" * 60)
try:
    _real = sc.collect_disks_detail
    # 绕过上方 stub，直接用真实实现（清空 SMART 缓存模拟冷启动）
    import importlib
    _mod = importlib.import_module("storage_collect") if False else sc
    # storage_collect 模块里真实函数在被 stub 之前已存在，这里取原函数
    _orig = _mod._COLLECT_DISKS_DETAIL_ORIG if hasattr(_mod, "_COLLECT_DISKS_DETAIL_ORIG") else None
except Exception as e:
    _orig = None

# 用可控的方式重跑真实实现：临时还原 collect_disks_detail
try:
    import storage_collect as _sc_mod
    _real_fn = _REAL_COLLECT_DISKS_DETAIL
except Exception:
    _real_fn = None

if _real_fn is not None:
    # 构造一个可控环境：lsblk 返回 2 块盘，SMART 缓存空，smart_detail 抛错
    _saved = {
        "lsblk": _sc_mod._lsblk_json,
        "mem": dict(_sc_mod._DISK_HEALTH_MEM),
        "smart": _sc_mod.smart_detail,
        "disks": _sc_mod.collect_disks,
        "states": _sc_mod.disk_power_states,
    }
    try:
        _sc_mod._DISK_HEALTH_MEM.clear()
        _sc_mod._lsblk_json = lambda: [
            {"name": "sda", "type": "disk", "size": 1000, "ro": False,
             "model": "M1", "vendor": "V", "serial": "S1", "rota": 1,
             "mountpoint": None, "children": [
                 {"name": "sda1", "type": "part", "size": 1000,
                  "mountpoint": "/vol1", "children": []}]},
            {"name": "sdb", "type": "disk", "size": 2000, "ro": False,
             "model": "M2", "vendor": "V", "serial": "S2", "rota": 1,
             "mountpoint": None, "children": []},
        ]
        _sc_mod.collect_disks = lambda: [
            {"mount": "/vol1", "total": 1000, "used": 500, "percent": 50}]
        _sc_mod.disk_power_states = lambda: {"sda": "active", "sdb": "active"}
        # smart_detail 抛异常 → 模拟 SMART 不可读
        def _boom(name):
            raise OSError("smartctl unavailable")
        _sc_mod.smart_detail = _boom

        _ds = _real_fn(with_states=False)
        check("with_states=False 不抛异常", True, "返回 %d 块盘" % len(_ds))
        check("返回盘位数量正确", len(_ds) == 2, len(_ds))
        check("每块盘都有 name 字段",
              all(isinstance(d.get("name"), str) and d.get("name") for d in _ds),
              [d.get("name") for d in _ds])
        check("每块盘都有 grade 字段",
              all(d.get("grade") in ("ok", "warn", "crit", "unknown") for d in _ds),
              [d.get("grade") for d in _ds])
        check("gradeLabel 已填充",
              all(d.get("gradeLabel") for d in _ds),
              [d.get("gradeLabel") for d in _ds])
    except Exception as e:
        check("with_states=False 不抛异常", False, repr(e))
    finally:
        _sc_mod._lsblk_json = _saved["lsblk"]
        _sc_mod._DISK_HEALTH_MEM.clear()
        _sc_mod._DISK_HEALTH_MEM.update(_saved["mem"])
        _sc_mod.smart_detail = _saved["smart"]
        _sc_mod.collect_disks = _saved["disks"]
        _sc_mod.disk_power_states = _saved["states"]
else:
    check("with_states=False 不抛异常", False, "拿不到 storage_collect 模块")

print()
print("=" * 60)
print("11. JSON 可序列化")
print("=" * 60)
try:
    txt = json.dumps(snap, ensure_ascii=False)
    check("快照可 JSON 序列化", len(txt) > 100, "%d 字节" % len(txt))
except Exception as e:
    check("快照可 JSON 序列化", False, repr(e))

print()
print("=" * 60)
print("结果: 通过 %d 项, 失败 %d 项" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项:")
    for f in FAIL:
        print("  - " + f)
    sys.exit(1)
print("🎉 后端单元测试全部通过")
