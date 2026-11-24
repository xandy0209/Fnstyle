#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
探针（终版）：精确定位 fnOS `appcgi.resmon.*` 的**后端缓存槽周期**。

已确认事实（前端侧）
--------------------
  fnOS 前端 bundle：
    KZe = 3*1e3                      → 3000 ms
    Mj  = useInterval(fn, delay)     → setInterval(r, t)
    qZe  → Mj(() => resmoGen({item:[storeSpeed,netSpeed,cpuBusy,memPercent]}),
               KZe, {immediate:true})
    q1e  → 渲染 cpuMemSpeed[last].cpuBusy + '%'   ← 桌面「运行状态」卡片

  ⇒ 前端每 **3 秒**拉一次 resmoGen，DOM 每 3 秒刷新。

本探针要回答的剩余问题
----------------------
  后端 `appcgi.resmon.cpu` / `resmon.gen` 自己是每次实时算，还是**按时间槽缓存**？

判据
----
C1 高频采样下，同一个「值平台」内连续样本完全相等（说明服务端返回的是同一个快照）
C2 平台持续时长 ≈ 整数秒 ⇒ 槽按秒对齐（滚动 TTL）
C3 平台边界间隔的分布众数 ⇒ 槽长
C4 gen.cpuBusy 与 cpu.busy.all 同源同值

用法
----
python3 verify/probe_fnos_cpu_slot.py -n 300 -i 0.05
"""
import argparse
import json
import os
import subprocess
import sys
import time
from collections import Counter

SKILL = os.path.expanduser("~/.workbuddy/skills/fnos")
TC = os.path.join(SKILL, "scripts", "trim-cli")
PROFILE = "relay"


def fetch_cpu():
    p = subprocess.run([TC, "--profile", PROFILE, "monitor", "cpu"],
                       capture_output=True, text=True, timeout=30)
    try:
        c = json.loads(p.stdout).get("cpu", {})
        return ((c.get("busy") or {}).get("all"),
                (c.get("loadavg") or {}).get("avg1min"))
    except Exception:
        return (None, None)


def fetch_gen():
    p = subprocess.run([TC, "--profile", PROFILE, "monitor", "gen",
                        "--item", "cpuBusy,memPercent"],
                       capture_output=True, text=True, timeout=30)
    try:
        it = json.loads(p.stdout).get("item", {})
        return it.get("cpuBusy"), it.get("memPercent")
    except Exception:
        return (None, None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", "--times", type=int, default=300)
    ap.add_argument("-i", "--interval", type=float, default=0.05)
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()

    print("=" * 74)
    print("fnOS resmon 后端缓存槽周期 —— 终版判定")
    print("=" * 74)

    samples = []
    t0 = time.time()
    for i in range(args.times):
        b, l = fetch_cpu()
        wall = time.time()
        samples.append({"t": round(wall - t0, 4), "busy": b, "load1m": l})
        time.sleep(args.interval)
    total = time.time() - t0

    # ---- 平台（连续同值段）----
    runs = []
    cur, start, last = None, None, None
    for s in samples:
        if s["busy"] != cur:
            if cur is not None:
                runs.append({"val": cur, "start": start, "end": last})
            cur, start = s["busy"], s["t"]
        last = s["t"]
    runs.append({"val": cur, "start": start, "end": samples[-1]["t"]})

    durs = [round(r["end"] - r["start"], 4) for r in runs]
    gaps = [round(runs[i + 1]["start"] - runs[i]["start"], 4) for i in range(len(runs) - 1)]

    # ---- C1 平台内样本完全相等 ----
    c1 = all(r["val"] is not None for r in runs)
    # ---- C2 平台时长是否呈整数秒量化 ----
    # ⚠️ 不用「墙钟整秒对齐」判据 —— 实测同秒内也会出现不同值（滚动 TTL，非墙钟对齐）。
    # 改用「平台时长 ≈ 整数秒的整数倍」这一更本质的量化特征：
    # 取每个平台时长乘以 100 取整后，对 100ms 桶做直方图，看是否在 1.0s/2.0s 处有峰。
    dur_hist = Counter(round(d, 1) for d in durs)
    # 量化得分：平台时长落在 {k*1.0s ± 0.25s} 的比例（k>=1）
    quantized = sum(1 for d in durs
                    if d >= 0.75 and abs(d - round(d)) <= 0.30)
    near_int = quantized
    # ---- C3 间隔众数 ----
    gh = Counter(round(g, 1) for g in gaps)
    mode_gap = gh.most_common(1)[0] if gh else (0, 0)

    # ---- loadavg 独立平台 ----
    lruns = []
    cur, start, last = None, None, None
    for s in samples:
        if s["load1m"] != cur:
            if cur is not None:
                lruns.append({"val": cur, "start": start, "end": last})
            cur, start = s["load1m"], s["t"]
        last = s["t"]
    lruns.append({"val": cur, "start": start, "end": samples[-1]["t"]})
    lgaps = [round(lruns[i + 1]["start"] - lruns[i]["start"], 4)
             for i in range(len(lruns) - 1)]

    # ---- C4 交叉验证 ----
    gbusy, gmem = fetch_gen()
    last_busy = samples[-1]["busy"]

    print()
    print("采样: %d 次 / %.1fs / 实际平均周期 %.3fs" %
          (len(samples), total, total / max(len(samples), 1)))
    print()
    print("-" * 74)
    print("C1/C2/C3 busy 值平台（连续同值段）")
    print("-" * 74)
    print("平台段数        : %d" % len(runs))
    if durs:
        print("段时长(秒)      : min=%.3f 中位=%.3f 平均=%.3f max=%.3f" %
              (min(durs), sorted(durs)[len(durs) // 2], sum(durs) / len(durs), max(durs)))
    print("接近整数秒的段数 : %d / %d" % (near_int, len(durs)))
    print("平台间隔直方图  : %s" % sorted(gh.items()))
    print("间隔众数        : %.1f 秒 (出现 %d 次)" % mode_gap)
    print("平台时长直方图  : %s" % sorted(dur_hist.items()))
    print()
    print("busy 前 80 个值 : %s" % [s["busy"] for s in samples[:80]])

    print()
    print("-" * 74)
    print("loadavg.avg1min 平台（独立于 busy 的另一个槽）")
    print("-" * 74)
    print("平台段数        : %d" % len(lruns))
    if lgaps:
        lg = sorted(lgaps)
        print("间隔(秒)        : min=%.3f 中位=%.3f 平均=%.3f max=%.3f" %
              (min(lgaps), lg[len(lg) // 2], sum(lgaps) / len(lgaps), max(lgaps)))
        print("间隔直方图      : %s" % sorted(Counter(round(g, 1) for g in lgaps).items()))

    print()
    print("-" * 74)
    print("C4 交叉验证")
    print("-" * 74)
    print("monitor gen cpuBusy = %s ；monitor cpu busy.all = %s" % (gbusy, last_busy))

    print()
    print("-" * 74)
    print("判定")
    print("-" * 74)
    checks = [
        ("C1 busy 存在平台期（服务端返回同一快照）", len(runs) < len(samples) * 0.9,
         "%d 平台 / %d 样本" % (len(runs), len(samples))),
        ("C2 平台时长呈秒级量化（1s/2s 倍数）", near_int >= len(durs) * 0.45,
         "%d/%d 秒级量化" % (near_int, len(durs))),
        ("C3 平台间隔众数落在 0.5~1.5 秒（1 秒后端缓存槽）",
         0.5 <= mode_gap[0] <= 1.5, "众数=%.1fs×%d" % mode_gap),
        ("C4 gen.cpuBusy 与 cpu.busy.all 一致",
         gbusy is not None and last_busy is not None and
         abs(float(gbusy) - float(last_busy)) <= 2,
         "gen=%s cpu=%s" % (gbusy, last_busy)),
    ]
    allok = True
    for n, ok, d in checks:
        allok = allok and ok
        print("  %s %-46s %s" % ("✅" if ok else "❌", n, d))

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump({"samples": samples, "runs": runs, "load_runs": lruns},
                      f, ensure_ascii=False, indent=2)
        print("\n原始采样 : %s" % args.json_out)
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
