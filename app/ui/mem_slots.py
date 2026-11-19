#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""内存插槽信息采集（dmidecode -t memory）。

移植自「飞牛监控 fnmonitor」（MIT License，
  Copyright (c) 2026 fnmonitor contributors）的内存插槽采集逻辑。
MIT 许可全文见 app/ui/monitor_collect.py 顶部的许可声明。

用途：
  · 内存详情抽屉的「内存插槽」段落 —— 展示每条插槽的位置/容量/频率/厂商/型号
  · 内存卡片 DIMM 图形旁边的标签文字（fnmonitor 的 dimmLabel）

为什么单独成文件：
  dmidecode 需要 root 权限且单次执行约 50~200ms，不能每 3 秒跑一次。
  这里做 10 分钟缓存 + 失败退避（30 分钟），避免拖累采集循环。
"""

import os
import re
import shutil
import subprocess
import threading
import time

CACHE_S = 600.0        # 正常缓存 10 分钟
FAIL_RETRY_S = 1800.0  # 失败后 30 分钟再试

_LOCK = threading.Lock()
_CACHE = None          # 缓存的 items 列表
_CACHE_TS = 0.0
_FAILED = False


def _dmidecode_bin():
    """定位 dmidecode；找不到返回 None。"""
    return shutil.which("dmidecode")


def _parse(out):
    """解析 dmidecode -t memory 的文本输出。

    每个 "Memory Device" 段落里关心的字段：
      Size / Type / Speed / Manufacturer / Part Number / Locator
    Size 为 "No Module Installed" 表示空插槽。
    """
    items = []
    cur = None

    def flush():
        if cur and (cur.get("size") or cur.get("present")):
            items.append(cur)

    for line in (out or "").splitlines():
        s = line.strip()
        if s.startswith("Memory Device"):
            flush()
            cur = {"present": False, "size": "", "type": "", "speed": "",
                   "manufacturer": "", "part": "", "locator": ""}
            continue
        if cur is None:
            continue
        if s.startswith("Memory Array Mapped") or s.startswith("Memory Device Mapped"):
            continue
        if ":" not in s:
            continue
        k, _, v = s.partition(":")
        k, v = k.strip(), v.strip()
        if k == "Size":
            if v == "No Module Installed":
                cur["present"] = False
            else:
                cur["present"] = True
                cur["size"] = v
        elif k == "Type" and v != "Unknown":
            cur["type"] = v
        elif k == "Speed" and v and v != "Unknown":
            cur["speed"] = v
        elif k == "Manufacturer" and v and v != "Unknown":
            cur["manufacturer"] = v
        elif k == "Part Number" and v and v not in ("Unknown", "Not Specified"):
            cur["part"] = v
        elif k == "Locator" and v and v != "Not Specified":
            cur["locator"] = v
    flush()
    return items


def _collect():
    """执行一次 dmidecode 并解析。失败返回 []。"""
    exe = _dmidecode_bin()
    if not exe:
        return []
    try:
        r = subprocess.run([exe, "-t", "memory"], stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=10)
    except Exception:
        return []
    if r.returncode != 0:
        return []
    try:
        return _parse(r.stdout.decode("utf-8", "replace"))
    except Exception:
        return []


def slots(force=False):
    """返回内存插槽列表（带缓存）。

    返回的每项：
      {present, size, type, speed, manufacturer, part, locator}
    """
    global _CACHE, _CACHE_TS, _FAILED
    now = time.time()
    with _LOCK:
        span = FAIL_RETRY_S if _FAILED else CACHE_S
        if (not force and _CACHE is not None
                and _CACHE_TS > 0 and (now - _CACHE_TS) < span):
            return _CACHE
        items = _collect()
        _CACHE = items
        _CACHE_TS = now
        _FAILED = not items
        return items


def slots_present():
    """只返回装有内存条的插槽（fnmonitor 的 dimmLabel 用）。"""
    return [it for it in slots() if it.get("present")]


def label(total_bytes=0):
    """DIMM 图形旁的标签文字（fnmonitor 的 dimmLabel）。

    有插槽信息时：「DDR4-3200 · 8 GB · SAMSUNG M378A1K43 · × 2」
    读不到时：  「8 GB 物理内存 · 未读取到插槽信息」
    """
    it = slots_present()
    if not it:
        if total_bytes:
            gb = total_bytes / (1024.0 * 1024.0 * 1024.0)
            g = str(int(round(gb))) if gb >= 10 else ("%.1f" % gb)
            return "%s GB 物理内存 · 未读取到插槽信息" % g
        return "未读取到插槽信息"
    a = it[0]
    parts = []
    if a.get("type") or a.get("speed"):
        parts.append("-".join(x for x in (a.get("type"), a.get("speed")) if x))
    if a.get("size"):
        parts.append(a["size"])
    sub = " ".join(x for x in (a.get("manufacturer"), a.get("part")) if x)
    if sub:
        parts.append(sub)
    if len(it) > 1:
        parts.append("× %d" % len(it))
    return " · ".join(parts).upper()


def dual_channel():
    """是否多通道（fnmonitor 的 dual 判定：插槽位 >= 2 且条数 >= 2）。"""
    it = slots_present()
    if len(it) < 2:
        return False
    locs = set(x.get("locator", "") for x in it)
    return len(locs) >= 2 and len(it) >= 2


if __name__ == "__main__":
    import json
    print(json.dumps({"items": slots(force=True),
                      "label": label(8 * 1024 ** 3),
                      "dual": dual_channel()},
                     ensure_ascii=False, indent=2))
