#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FnStyle 硬盘巡检闭环测试：scan_once → 落库 → 变化文本 → 历史 → 汇总

这是本轮修复 `_save_health` / `_scan_change_text` 的 dict-vs-list 缺陷后，
必须通过的回归测试 —— 巡检历史是用户最看重的存储能力。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "ui"))
import storage_collect as sc

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + (("  " + str(extra)) if extra else ""))


ATA_OK = """Device Model:     WDC WD40EFZX-68AWUN0
Serial Number:    WD-AAA111
Rotation Rate:    5400 rpm
SMART overall-health self-assessment test result: PASSED

ID# ATTRIBUTE_NAME          FLAG     VALUE WORST THRESH TYPE      UPDATED  WHEN_FAILED RAW_VALUE
  5 Reallocated_Sector_Ct   0x0033   100   100   010    Pre-fail  Always       -       0
  9 Power_On_Hours          0x0032   099   099   000    Old_age   Always       -       8761
194 Temperature_Celsius     0x0022   115   095   000    Old_age   Always       -       34
197 Current_Pending_Sector  0x0012   100   100   000    Old_age   Always       -       0
198 Offline_Uncorrectable   0x0010   100   100   000    Old_age   Offline      -       0
199 UDMA_CRC_Error_Count    0x003e   200   200   000    Old_age   Always       -       0
"""

ATA_WARN = ATA_OK.replace("WD-AAA111", "WD-BBB222").replace(
    "Reallocated_Sector_Ct   0x0033   100   100   010    Pre-fail  Always       -       0",
    "Reallocated_Sector_Ct   0x0033   100   100   010    Pre-fail  Always       -       7")

ATA_CRIT = ATA_OK.replace("WD-AAA111", "WD-CCC333").replace(
    "Offline_Uncorrectable   0x0010   100   100   000    Old_age   Offline      -       0",
    "Offline_Uncorrectable   0x0010   100   100   000    Old_age   Offline      -       4")


def make_patches(dev_map, standby_devs=()):
    """构造 smart_detail / disk_scan_devices 的桩。

    每块盘的序列号必须唯一 —— 序列号是巡检比对的主键，
    两块盘共用序列号会人为制造「找不到上一轮记录」。
    """
    def fake_smart(dev):
        if dev in standby_devs:
            return {"standby": True}
        txt = dev_map.get(dev)
        if not txt:
            return {}
        # 强制唯一序列号：按盘名派生
        import re as _re
        # 设备改名但序列号不变（真实场景：同一块盘重启后换名）
        _ALIAS = {"sdz": "WD-SDB"}
        uniq = _ALIAS.get(dev) or ("WD-" + _re.sub(r"[^A-Za-z0-9]", "", dev).upper())
        txt = _re.sub(r"Serial Number:\s+\S+", "Serial Number:    " + uniq, txt)
        return sc._parse_smart_text(txt)
    devices = [{"dev": d, "size": 4000653453312, "model": "WDC WD40EFZX"}
               for d in dev_map]
    for d in standby_devs:
        devices.append({"dev": d, "size": 4000653453312, "model": "WDC WD40EFZX"})
    return fake_smart, devices


print("=" * 64)
print("巡检闭环测试：scan_once → SQLite → 变化检测 → 历史 → 汇总")
print("=" * 64)

tmpdb = os.path.join(tempfile.mkdtemp(), "storage.db")
scanner = sc.DiskScanner(tmpdb, interval_h=6)

# ---- 第 1 轮：3 块盘全正常 ----
print()
print("— 第 1 轮巡检（3 块盘全部正常）—")
ATA_SDA = ATA_OK
ATA_SDC = ATA_OK.replace("WD-AAA111", "WD-CCC333")
fake_smart, devs = make_patches({"sda": ATA_SDA, "sdb": ATA_OK, "sdc": ATA_SDC})
sc.smart_detail = fake_smart
sc.disk_scan_devices = lambda: devs

r1 = scanner.scan_once()
check("巡检成功", r1.get("ok") is True, r1.get("error"))
check("巡检盘数 = 3", r1.get("total") == 3, r1.get("total"))
check("分级统计全 ok", r1.get("counts", {}).get("ok") == 3, r1.get("counts"))
check("首次巡检不算变化（无上轮基线）", not r1.get("changed"), r1.get("changed"))

health = scanner._read_health()
check("落库 disk_health 有 3 行", len(health) == 3, list(health.keys()))
check("落库含 serial", any(r.get("serial") == "WD-SDA" for r in health.values()),
      [r.get("serial") for r in health.values()])
check("落库含 defects", all("defects" in r for r in health.values()))
check("落库含 grade", all(r.get("grade") == "ok" for r in health.values()),
      [r.get("grade") for r in health.values()])

log1 = scanner.read_scan_log(10)
check("巡检流水有 1 条", len(log1) == 1, len(log1))
check("流水标记未变化", log1[0]["changed"] is False, log1[0])

# ---- 第 2 轮：sdb 出现重映射，sdc 出现不可纠正 ----
print()
print("— 第 2 轮巡检（sdb 重映射 7、sdc 不可纠正 4）—")
fake_smart, devs = make_patches({"sda": ATA_SDA, "sdb": ATA_WARN, "sdc": ATA_CRIT})
sc.smart_detail = fake_smart
sc.disk_scan_devices = lambda: devs

r2 = scanner.scan_once()
check("巡检成功", r2.get("ok") is True, r2.get("error"))
check("检出 2 块盘变化", len(r2.get("changed") or []) == 2, r2.get("changed"))
check("分级统计 ok=1 warn=1 crit=1", r2.get("counts") == {"ok": 1, "warn": 1, "crit": 1, "unknown": 0},
      r2.get("counts"))

health2 = scanner._read_health()
by_serial = {r.get("serial"): r for r in health2.values()}
check("sdb 分级 = warn", by_serial.get("WD-SDB", {}).get("grade") == "warn",
      by_serial.get("WD-SDB", {}).get("grade"))
check("sdb defects = 7", by_serial.get("WD-SDB", {}).get("defects") == 7,
      by_serial.get("WD-SDB", {}).get("defects"))
check("sdc 分级 = crit", by_serial.get("WD-SDC", {}).get("grade") == "crit",
      by_serial.get("WD-SDC", {}).get("grade"))
check("sdc uncorr = 4", by_serial.get("WD-SDC", {}).get("uncorr") == 4,
      by_serial.get("WD-SDC", {}).get("uncorr"))
check("sda 仍为 ok", by_serial.get("WD-SDA", {}).get("grade") == "ok",
      by_serial.get("WD-SDA", {}).get("grade"))

log2 = scanner.read_scan_log(10)
check("巡检流水有 2 条", len(log2) == 2, len(log2))
check("第 2 条标记有变化", log2[0]["changed"] is True, log2[0])
check("流水文本提到 sdb", "sdb" in (log2[0]["summary"] or ""), log2[0]["summary"])
check("流水文本提到 sdc", "sdc" in (log2[0]["summary"] or ""), log2[0]["summary"])

hist = scanner.read_health_hist("sdb", 60)
check("sdb 有历史记录", len(hist) >= 1, len(hist))

# ---- 第 3 轮：sdb 缺陷从 7 涨到 25 → 应判 crit（增长 ≥10）----
print()
print("— 第 3 轮巡检（sdb 重映射 7→25，增长 18）—")
ATA_GROW = ATA_WARN.replace("WD-BBB222", "WD-BBB222").replace(
    "Reallocated_Sector_Ct   0x0033   100   100   010    Pre-fail  Always       -       7",
    "Reallocated_Sector_Ct   0x0033   100   100   010    Pre-fail  Always       -       25")
fake_smart, devs = make_patches({"sda": ATA_SDA, "sdb": ATA_GROW, "sdc": ATA_CRIT})
sc.smart_detail = fake_smart
sc.disk_scan_devices = lambda: devs

r3 = scanner.scan_once()
health3 = scanner._read_health()
by_serial3 = {r.get("serial"): r for r in health3.values()}
check("sdb 缺陷增长 → 升级为 crit",
      by_serial3.get("WD-SDB", {}).get("grade") == "crit",
      by_serial3.get("WD-SDB", {}).get("grade"))
check("详情文本含增长说明", "增长" in (by_serial3.get("WD-SDB", {}).get("detail") or ""),
      by_serial3.get("WD-SDB", {}).get("detail"))

# ---- 第 4 轮：休眠盘不得被唤醒，且沿用上次分级 ----
print()
print("— 第 4 轮巡检（sda 进入休眠）—")
fake_smart, devs = make_patches({"sdb": ATA_GROW, "sdc": ATA_CRIT}, standby_devs=("sda",))
sc.smart_detail = fake_smart
sc.disk_scan_devices = lambda: devs

r4 = scanner.scan_once()
check("巡检成功", r4.get("ok") is True, r4.get("error"))
check("休眠盘被跳过", "sda" in (r4.get("skipped_standby") or []), r4.get("skipped_standby"))
health4 = scanner._read_health()
sda_row = health4.get("sda") or {}
check("休眠盘仍留在库中", bool(sda_row), list(health4.keys()))
check("休眠盘沿用上次分级 ok", sda_row.get("grade") == "ok", sda_row.get("grade"))
check("休眠盘标记 standby", bool(sda_row.get("standby")), sda_row.get("standby"))

# ---- 设备名轮换：同一序列号换了名字，不应记成新盘 ----
print()
print("— 第 5 轮巡检（sdb → sdz，序列号不变）—")
fake_smart, devs = make_patches({"sdz": ATA_GROW, "sdc": ATA_CRIT}, standby_devs=("sda",))
sc.smart_detail = fake_smart
sc.disk_scan_devices = lambda: devs

r5 = scanner.scan_once()
health5 = scanner._read_health()
check("序列号索引：仍是 3 块盘（未多出一块）", len(health5) == 3, list(health5.keys()))
log5 = scanner.read_scan_log(10)
check("改名不算异常变化",
      not any("sdz" in (l.get("summary") or "") and "变为" in (l.get("summary") or "")
              for l in log5),
      [l.get("summary") for l in log5[:2]])

print()
print("=" * 64)
print("结果: 通过 %d 项, 失败 %d 项" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项:")
    for f in FAIL:
        print("  - " + f)
    sys.exit(1)
print("🎉 巡检闭环测试全部通过")
