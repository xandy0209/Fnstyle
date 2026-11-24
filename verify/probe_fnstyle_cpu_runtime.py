#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
探针（实跑）：在**真实运行环境**里跑 FnStyle 的 CPU 采集器，测它实际的
CPU 百分比产出节奏 —— 验证静态结论「10 秒一帧」。

做法
----
直接 import app/ui/monitor_collect.py，启动它的 `MonitorCollector`，
然后按前端的节奏（3 秒一次）反复读 `snapshot()["cpu"]["percent"]`，
统计：
  · 数值变化时刻序列 ⇒ 变化周期
  · 同值平台长度
  · 首帧可用耗时（基线 + 等一个窗口 + 差分 ⇒ 应 ≈ 1×窗口 = 10 秒）

与静态探针 `probe_fnstyle_cpu_refresh.py` 互为印证：
  静态探针查「代码里写的常量」，本探针查「跑起来是什么行为」。

用法
----
python3 verify/probe_fnstyle_cpu_runtime.py [--seconds 45]
"""
import argparse
import math
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI = os.path.join(ROOT, "app", "ui")
sys.path.insert(0, UI)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=45)
    ap.add_argument("--poll", type=float, default=0.5,
                    help="读取间隔（秒）；前端是 3 秒")
    args = ap.parse_args()

    print("=" * 74)
    print("FnStyle CPU 采集器实跑 —— 产出节奏验证")
    print("=" * 74)

    try:
        import monitor_collect as mc
    except Exception as e:
        print("❌ 无法导入 monitor_collect: %s" % e)
        return 2

    print("CPU_SAMPLE_WINDOW = %s 秒" % mc.CPU_SAMPLE_WINDOW)
    print("采集循环 interval = %s 秒" % mc.MonitorCollector.__init__.__defaults__)

    # ⚠️ macOS **没有 /proc/stat**（Linux 专有）⇒ 必须注入合成数据，
    #    否则 collect_cpu() 永远返回 available=False，测不出节奏。
    #    合成规则：每 tick 累加 100 个 jiffy，其中 idle 60 / user 30 / system 10
    #    ⇒ 稳定 40% 使用率（差分算出来应当恒为 40.0）
    # ⚠️⚠️ **必须是函数、每次调用现生成** —— 若在模块级求值成常量字符串，
    #    两次采样的时间片完全相同 ⇒ total_delta=0 ⇒ 永远 available=False。
    _tick = {"n": 0}

    def _fake_stat():
        """合成 /proc/stat：每次调用推进一个 tick。
        ⚠️ 使用率**故意逐 tick 变化**（在 30~50% 之间摆动）——
           若恒定，差分结果也恒定，就测不出「数值多久变一次」。
           ★ 关键：两次采样（基线 + 差分）之间时间片必须不同，
             否则 total_delta=0 ⇒ available=False。"""
        _tick["n"] += 1
        n = _tick["n"]
        # 每 tick 总时间片 +100；idle 的**增量**按**绝对时间**做正弦摆动
        # ⚠️ 关键：不能按 tick 序号摆动 —— 两次采样（基线 + 差分）相隔 10 秒，
        #    期间 tick 序号可能落在摆动周期的同一相位上 ⇒ 使用率恒定。
        #    按绝对时间（time.monotonic）摆动，保证任何 10 秒间隔两端相位不同。
        phase = time.monotonic() / 3.0
        idle_delta = 60 + int(10 * math.sin(phase))   # 50~70 连续摆动
        idle = 60 * n + (idle_delta - 60)
        busy = 100 - idle_delta
        u = int(busy * 0.7)
        sy = busy - u
        base_u = 30 * n
        base_s = 10 * n
        return ("cpu  %d %d 0 %d 0 0 0 0\n" % (base_u + u, base_s + sy, idle) +
                "cpu0 %d %d 0 %d 0 0 0 0\n" % (base_u + u, base_s + sy, idle) +
                "intr 1 2 3\n")

    _orig_read_text = mc.read_text

    def _patched_read_text(path, limit=8192):
        if path == "/proc/stat":
            return _fake_stat()
        return _orig_read_text(path, limit)

    mc.read_text = _patched_read_text
    print("已注入合成 /proc/stat（稳定 40%% 使用率）")

    col = mc.MonitorCollector(interval=3)
    col.start()
    t0 = time.time()
    print()
    print("启动采集器，读取 %d 秒（每 %.1fs 读一次）…" % (args.seconds, args.poll))

    samples = []
    first_valid_at = None
    while time.time() - t0 < args.seconds:
        snap = col.snapshot()
        cpu = snap.get("cpu") or {}
        pct = cpu.get("percent")
        avail = cpu.get("available")
        now = time.time() - t0
        if pct is not None and first_valid_at is None:
            first_valid_at = now
        samples.append((round(now, 2), pct, avail))
        time.sleep(args.poll)

    print()
    print("-" * 74)
    print("采样序列 (t, cpu.percent, available)")
    print("-" * 74)
    for t, pct, avail in samples:
        mark = "  <-- 首帧有效" if (pct is not None and abs(t - (first_valid_at or -1)) < 0.6) else ""
        print("  t=%6.2fs  percent=%-8s available=%-6s%s" % (t, pct, avail, mark))

    # ---- 统计 ----
    valid = [(t, p) for (t, p, a) in samples if p is not None]
    print()
    print("-" * 74)
    print("统计")
    print("-" * 74)
    print("首帧有效耗时 : %s 秒" %
          ("%.2f" % first_valid_at if first_valid_at is not None else "未出现"))
    print("有效样本数   : %d / %d" % (len(valid), len(samples)))

    if len(valid) >= 2:
        # ★ 按**值的平台段**统计，而不是「相邻读取点的间隔」——
        #   轮询间隔（0.5s）与「新值多久生效一次」是两件事。
        segs = []
        cur = valid[0][1]
        seg_start = valid[0][0]
        for t, pv in valid[1:]:
            if pv != cur:
                segs.append((cur, seg_start, t))
                cur = pv
                seg_start = t
        segs.append((cur, seg_start, valid[-1][0]))
        seg_durs = [round(e - s0, 2) for (_v, s0, e) in segs]
        print("数值平台段数 : %d" % len(segs))
        print("平台持续(秒) : %s" % seg_durs[:20])
        if len(segs) > 1:
            print("平台切换点   : %s" %
                  ", ".join("t=%.1f(%s→%s)" % (e, segs[i][0], segs[i + 1][0])
                            for i, (_v, _s, e) in enumerate(segs[:-1])))
            # 平台持续时长 ≈ 10 秒的整数倍？（首尾段可能不完整，剔除）
            mids = seg_durs[1:-1] if len(seg_durs) > 2 else seg_durs
            if mids:
                ds = sorted(mids)
                print("完整平台(秒) : min=%.2f 中位=%.2f 平均=%.2f max=%.2f" %
                      (min(mids), ds[len(ds) // 2],
                       sum(mids) / len(mids), max(mids)))
        changes = [segs[i + 1][2] - segs[i][2] for i in range(len(segs) - 1)]
        if changes:
            print("新值生效间隔 : %s 秒" %
                  ", ".join("%.1f" % c for c in changes[:12]))
        print("CPU 取值     : %s" % [p for _t, p in valid][:24])

    print()
    print("-" * 74)
    print("判定")
    print("-" * 74)
    checks = [
        # 首帧：第 1 次采样记基线 → 等 1 个窗口 → 第 2 次采样做差分 ⇒ ≈ 1×窗口
        ("首帧有效耗时 ≈ 1×CPU_SAMPLE_WINDOW（基线+一次等待）",
         first_valid_at is not None and
         abs(first_valid_at - mc.CPU_SAMPLE_WINDOW) <= 4,
         "%.1fs vs 期望 %ds" % (first_valid_at or -1, mc.CPU_SAMPLE_WINDOW)),
        ("CPU 采样窗口 = 10 秒", mc.CPU_SAMPLE_WINDOW == 10,
         "%s 秒" % mc.CPU_SAMPLE_WINDOW),
    ]
    if len(segs) >= 2:
        # 每个完整平台应持续 ≈ 10 秒（= 一个采样窗口）
        mids = [round(e - s0, 2) for (_v, s0, e) in segs[1:-1]] \
            if len(segs) > 2 else []
        ok10 = bool(mids) and all(abs(m - 10.0) <= 3.0 for m in mids)
        checks.append(("完整平台持续 ≈ 10 秒（一个采样窗口）", ok10,
                       "平台 %s" % (mids[:6] if mids else "样本不足")))
        checks.append(("平台数 %d ⇒ 窗口内新值产出 %d 次" % (len(segs), len(segs) - 1),
                       len(segs) >= 2, "%d 段" % len(segs)))
    allok = True
    for n, ok, d in checks:
        allok = allok and bool(ok)
        print("  %s %-46s %s" % ("✅" if ok else "❌", n, d))

    print()
    print("=" * 74)
    print("结论")
    print("=" * 74)
    print("  后端 CPU 采集线程每 %s 秒产出一帧使用率；" % mc.CPU_SAMPLE_WINDOW)
    print("  前端每 3 秒读一次缓存 ⇒ CPU%% 数字每 %s 秒才可能变一次。" % mc.CPU_SAMPLE_WINDOW)
    print("  首帧（服务刚起）需「基线 + 等一个窗口 + 差分」≈ %s 秒。" % mc.CPU_SAMPLE_WINDOW)
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
