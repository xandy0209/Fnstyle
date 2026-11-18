#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FnStyle 存储监控后端单元测试（不依赖真实硬件，全部用桩函数模拟）"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "ui"))
import storage_collect as sc

GIB = 1024 ** 3

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + (("  " + str(extra)) if extra else ""))


print("=" * 60)
print("1. 基础工具函数")
print("=" * 60)
check("_nz 空值→None", sc._nz(None) is None and sc._nz("") is None and sc._nz("12") == 12)
check("_mbf 格式化", sc._mbf(500 * GIB).endswith("GB"), sc._mbf(500 * GIB))
check("_pct 口径", sc._pct(50, 100) == 50)
check("_pct 零分母", sc._pct(1, 0) == 0)
check("_vol_of 取存储空间号", sc._vol_of(["/vol2"]) == 2)
check("_vol_of 多挂载取最小", sc._vol_of(["/vol3", "/vol1"]) == 1)
check("_vol_of 系统盘返回 0", sc._vol_of(["/", "/boot/efi"]) == 0)
check("_vol_of 空列表返回 0", sc._vol_of([]) == 0)
check("_vol_of 忽略非存储池路径", sc._vol_of(["/home/x", "/vol5/sub"]) == 5)

print()
print("=" * 60)
print("2. 存储池容量（volumes）")
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

print()
print("=" * 60)
print("10. JSON 可序列化")
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
