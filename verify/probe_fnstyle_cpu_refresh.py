#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
探针：核查 FnStyle 概览卡片「处理器」CPU 利用率的**刷新节奏**。

问题
----
老板截图里的卡片（标题「处理器」、右上「平稳」、大数字 7 + 圆环仪表 +
底部 sparkline，副标题「4 核 · 2.26 GHz · 负载 0.52」）——CPU 利用率多久变一次？

三层周期（都要查清）
--------------------
  L1 后端 CPU 采样窗口 : monitor_collect.py 的 CPU_SAMPLE_WINDOW（默认 10 秒）
  L2 后端采集循环       : MonitorCollector.interval（默认 3 秒）
  L3 前端自动刷新       : monitor.js 的 REFRESH_MS（3000 毫秒）
  ⇒ 真正决定「CPU% 数字多久变一次」的是 **L1 ∩ L2**

判定
----
F1 CPU_SAMPLE_WINDOW == 10（秒）
F2 REFRESH_MS == 3000（毫秒），且注释声明与后端采集周期一致
F3 前端 refresh() 定时器确实用 REFRESH_MS
F4 概览卡 CPU 数字直接取 d.cpu.percent（无额外节流/去抖）
F5 sparkline 缓冲 LIVE_MAX == 60
F6 后端 CPU 线程用 self._stop.wait(CPU_SAMPLE_WINDOW) 而非 interval
F7 采集循环间隔与 CPU 采样窗口**不同**（10 vs 3）⇒ CPU 走独立慢通道

用法
----
python3 verify/probe_fnstyle_cpu_refresh.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI = os.path.join(ROOT, "app", "ui")


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def main():
    mc = read(os.path.join(UI, "monitor_collect.py"))
    mon = read(os.path.join(UI, "static", "js", "monitor.js"))
    ov = read(os.path.join(UI, "static", "js", "overview.js"))

    print("=" * 74)
    print("FnStyle 概览卡片「处理器」CPU 刷新节奏核查")
    print("=" * 74)

    # ---- L1 ----
    m = re.search(r"^CPU_SAMPLE_WINDOW\s*=\s*(\d+)", mc, re.M)
    cpu_win = int(m.group(1)) if m else None

    m = re.search(r"def __init__\(self, interval=(\d+)\)", mc)
    interval = int(m.group(1)) if m else None

    # ---- L2 前端 ----
    m = re.search(r"var REFRESH_MS\s*=\s*(\d+)", mon)
    refresh_ms = int(m.group(1)) if m else None

    m = re.search(r"var LIVE_MAX\s*=\s*(\d+)", ov)
    live_max = int(m.group(1)) if m else None

    print()
    print("-" * 74)
    print("三层周期")
    print("-" * 74)
    print("L1 后端 CPU 采样窗口 CPU_SAMPLE_WINDOW : %s 秒" % cpu_win)
    print("L2 后端采集循环 interval                 : %s 秒" % interval)
    print("L3 前端自动刷新 REFRESH_MS              : %s 毫秒" % refresh_ms)
    print("   sparkline 缓冲 LIVE_MAX               : %s 点" % live_max)
    if cpu_win and interval and refresh_ms:
        print("   ⇒ sparkline 时间跨度 ≈ %s 秒" %
              round(live_max * refresh_ms / 1000.0))

    # ---- 关键代码点 ----
    print()
    print("-" * 74)
    print("关键代码点")
    print("-" * 74)
    # ⚠️ 别用 `_cpu_thread...wait(...)` 跨段匹配 —— 中间隔着 `collect_cpu()`
    #    及其括号，会被 `(?:...)` 之类的模式干扰。直接定位 `_cpu_loop` 函数体。
    cpu_loop_body = ""
    mi = mc.find("def _cpu_loop(self)")
    if mi >= 0:
        cpu_loop_body = mc[mi:mi + 1600]
    cpu_thread_uses_window = "self._stop.wait(CPU_SAMPLE_WINDOW)" in cpu_loop_body
    # 两次采样差分 ⇒ 首个有效读数需 2×窗口
    cpu_diff_two_samples = bool(re.search(
        r"第 1 次：记基线", cpu_loop_body)) or (
        cpu_loop_body.count("collect_cpu()") >= 2)
    main_loop_uses_interval = bool(re.search(
        r"def _loop\([\s\S]{0,1500}?self\._stop\.wait\(self\.interval\)", mc))
    timer_uses_refresh = bool(re.search(
        r"timer\s*=\s*setInterval\([\s\S]{0,300}?\}\s*,\s*REFRESH_MS\s*\)", mon))
    cpu_pct_direct = bool(re.search(
        r"var\s+cp\s*=\s*cpu\.percent", ov))
    cpu_push = bool(re.search(r"pushLive\('cpu',\s*cp\)", ov))

    print("CPU 线程用 CPU_SAMPLE_WINDOW 等待 : %s" % cpu_thread_uses_window)
    print("CPU 用两次采样差分（首帧 2×窗口）: %s" % cpu_diff_two_samples)
    print("主采集循环用 self.interval 等待   : %s" % main_loop_uses_interval)
    print("前端 setInterval 用 REFRESH_MS     : %s" % timer_uses_refresh)
    print("概览卡 CPU 数字直取 cpu.percent   : %s" % cpu_pct_direct)
    print("概览卡 CPU 入 sparkline           : %s" % cpu_push)

    # ---- 判定 ----
    print()
    print("-" * 74)
    print("判定")
    print("-" * 74)
    checks = [
        ("F1 CPU_SAMPLE_WINDOW = 10 秒", cpu_win == 10, "%s 秒" % cpu_win),
        ("F2 REFRESH_MS = 3000 毫秒", refresh_ms == 3000, "%s ms" % refresh_ms),
        ("F3 前端定时器确实用 REFRESH_MS", timer_uses_refresh, ""),
        ("F4 概览卡 CPU 直取 cpu.percent（无额外节流）",
         cpu_pct_direct and cpu_push, ""),
        ("F5 sparkline 缓冲 LIVE_MAX = 60", live_max == 60, "%s 点" % live_max),
        ("F6 CPU 线程独立于主采集循环（用 10s 窗口）",
         cpu_thread_uses_window and main_loop_uses_interval, ""),
        ("F6b CPU 用两次采样差分 ⇒ 首帧需「基线+等一窗+差分」≈ 1×窗口",
         cpu_diff_two_samples, "首帧 ≈ %s 秒" % cpu_win),
        ("F6c 采集器为 CPU 单独建线程与锁",
         "_cpu_thread" in mc and "_cpu_lock" in mc, ""),
        ("F7 CPU 走独立慢通道（10s ≠ 3s 主循环）",
         cpu_win == 10 and interval == 3,
         "CPU=%ss / 主循环=%ss" % (cpu_win, interval)),
    ]
    allok = True
    for n, ok, d in checks:
        allok = allok and bool(ok)
        print("  %s %-46s %s" % ("✅" if ok else "❌", n, d))

    print()
    print("=" * 74)
    print("结论")
    print("=" * 74)
    print("  卡片位置   : app/ui/static/js/overview.js（概览区「处理器」卡片）")
    print("  数字来源   : /api/monitor 的 d.cpu.percent")
    print()
    print("  ★ CPU 利用率数字多久变一次？")
    print("    后端每 %s 秒产出一帧 CPU 使用率（CPU_SAMPLE_WINDOW）；" % cpu_win)
    print("    前端每 %s 秒拉一次 /api/monitor。" % (refresh_ms / 1000.0))
    print("    ⇒ CPU 数值真正变化的周期 = max(%s, %s) = %s 秒"
          % (cpu_win, refresh_ms / 1000.0, max(cpu_win, refresh_ms / 1000.0)))
    print("    ⇒ 数字理论上最多滞后 %s 秒才更新。" % max(cpu_win, refresh_ms / 1000.0))
    print()
    print("  ⚠️ 为什么 CPU 要单独用 10 秒窗口（代码注释原文）：")
    print("     /proc/stat 时间片精度 1 jiffy = 10ms。窗口越短量化误差越大：")
    print("     0.25s 窗口 → 读数极差 62 个百分点；10s 窗口 → 极差 39 个百分点。")
    print("     fnmonitor 在 NAS 上的配置也是 interval = 10，两边口径才对得上。")
    print()
    print("  ★ 底部 sparkline 的点不是每 3 秒一个新 CPU 值 ——")
    print("    它每 3 秒 pushLive 一次，CPU 值在 10 秒内是**同一个值**重复推入。")
    print("    所以 sparkline 呈阶梯状，60 点 ≈ %s 秒 ≈ %.1f 分钟。" %
          (round(live_max * refresh_ms / 1000.0),
           live_max * refresh_ms / 60000.0))
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
