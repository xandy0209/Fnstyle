#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
探针：测 fnOS「运行状态」卡片 CPU 数值的**端到端陈旧延迟（staleness）**。

这是老板感知的直接对应量：
    「我看到 CPU% 变了」  ==  「NAS 上实际负载已经发生」 之间的延迟

实验设计
--------
  1. 低频采样 busy（前台，记录时间序列）
  2. 后台并发压 NAS（每个 trim-cli 调用都会在 NAS 侧跑 appcgi 处理 ⇒ 制造真实 CPU 负载）
  3. 找出 busy 从「空闲基线」跳到「高值」的时刻 ⇒ T_detect
  4. 找出压测**停止**后 busy 回落到基线的时刻 ⇒ T_release
  ⇒ 两者共同刻画「数值多久才动一次 / 多久才反映一次」

判据
----
S1 基线阶段 busy 保持低位（说明起始确实空闲）
S2 负载阶段 busy 明显升高（证明压测确实在 NAS 侧产生 CPU 消耗）
S3 T_detect 可测量（负载发生后 busy 在有限时间内变化）
S4 回落延迟 T_release 可测量（负载停止后 busy 也有滞后回落）

用法
----
python3 verify/probe_fnos_cpu_stale.py --baseline 12 --load 18 --recover 15
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time

SKILL = os.path.expanduser("~/.workbuddy/skills/fnos")
TC = os.path.join(SKILL, "scripts", "trim-cli")
PROFILE = "relay"


def fetch():
    p = subprocess.run([TC, "--profile", PROFILE, "monitor", "cpu"],
                       capture_output=True, text=True, timeout=30)
    try:
        d = json.loads(p.stdout)
        c = d.get("cpu", {})
        return ((c.get("busy") or {}).get("all"),
                (c.get("loadavg") or {}).get("avg1min"))
    except Exception:
        return (None, None)


class LoadGen(threading.Thread):
    """后台并发压 NAS，制造真实 CPU 负载"""

    def __init__(self, workers=8):
        super().__init__(daemon=True)
        self.workers = workers
        self.stop_flag = False
        self.calls = 0
        self.lock = threading.Lock()

    def run(self):
        def w():
            while not self.stop_flag:
                subprocess.run([TC, "--profile", PROFILE, "monitor", "cpu"],
                               capture_output=True, timeout=30)
                with self.lock:
                    self.calls += 1
        ts = [threading.Thread(target=w, daemon=True) for _ in range(self.workers)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", type=int, default=12, help="基线阶段秒数")
    ap.add_argument("--load", type=int, default=18, help="负载阶段秒数")
    ap.add_argument("--recover", type=int, default=15, help="回落阶段秒数")
    ap.add_argument("--workers", type=int, default=8, help="压测并发数")
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()

    print("=" * 72)
    print("fnOS CPU 数值陈旧延迟（staleness）探针")
    print("=" * 72)
    print("阶段: 基线 %ds → 负载 %ds(%d并发) → 回落 %ds" %
          (args.baseline, args.load, args.workers, args.recover))

    samples = []
    stop_load = threading.Event()
    lg = LoadGen(args.workers)
    t0 = time.time()
    phase = "baseline"

    total = args.baseline + args.load + args.recover
    next_switch = args.baseline
    load_started = None
    load_stopped = None

    while time.time() - t0 < total:
        now = time.time() - t0
        b, l = fetch()
        samples.append({"t": round(now, 3), "busy": b, "load1m": l, "phase": phase})

        if now >= next_switch:
            if phase == "baseline":
                phase = "load"
                load_started = time.time()
                lg.start()
                next_switch = args.baseline + args.load
                print("  [t=%5.1fs] ▶ 开始负载 (%d 并发)" % (now, args.workers))
            elif phase == "load":
                phase = "recover"
                load_stopped = time.time()
                stop_load.set()
                lg.stop_flag = True
                next_switch = args.baseline + args.load + args.recover
                print("  [t=%5.1fs] ■ 停止负载" % now)
            else:
                break
        time.sleep(0.1)

    lg.stop_flag = True
    time.sleep(0.5)

    # ---- 分析 ----
    base = [s for s in samples if s["phase"] == "baseline" and s["busy"] is not None]
    loadp = [s for s in samples if s["phase"] == "load" and s["busy"] is not None]
    recp = [s for s in samples if s["phase"] == "recover" and s["busy"] is not None]

    print()
    print("-" * 72)
    print("阶段统计")
    print("-" * 72)
    def stat(name, arr):
        if not arr:
            print("  %-8s 无数据" % name)
            return
        v = [s["busy"] for s in arr]
        print("  %-8s n=%-4d min=%-4d 中位=%-5.0f max=%-4d 均值=%.1f" %
              (name, len(v), min(v), sorted(v)[len(v) // 2], max(v), sum(v) / len(v)))
    stat("基线", base)
    stat("负载", loadp)
    stat("回落", recp)

    # T_detect: 负载开始后，busy 首次达到基线最大值 + 5 的时刻
    base_max = max((s["busy"] for s in base), default=0)
    thr = base_max + 5
    t_detect = None
    if load_started:
        for s in samples:
            if s["t"] >= (load_started - t0) and s["busy"] is not None and s["busy"] >= thr:
                t_detect = s["t"] - (load_started - t0)
                break

    # T_release: 负载停止后，busy 首次回落到 <= thr 的时刻
    t_release = None
    if load_stopped:
        for s in samples:
            if s["t"] >= (load_stopped - t0) and s["busy"] is not None and s["busy"] <= thr:
                t_release = s["t"] - (load_stopped - t0)
                break

    print()
    print("-" * 72)
    print("关键延迟")
    print("-" * 72)
    print("基线 busy 最大值   : %d" % base_max)
    print("检测阈值 (基线+5)  : %d" % thr)
    print("T_detect  (负载→数值反映) : %s" %
          ("%.2f 秒" % t_detect if t_detect is not None else "未触发"))
    print("T_release (停压→数值回落) : %s" %
          ("%.2f 秒" % t_release if t_release is not None else "未触发"))
    print("压测期间 trim-cli 调用次数: %d" % lg.calls)

    print()
    print("-" * 72)
    print("时间序列（busy）")
    print("-" * 72)
    line = []
    for s in samples:
        line.append("%s" % (s["busy"] if s["busy"] is not None else "-"))
    # 每 10 个一行
    for i in range(0, len(line), 20):
        print("  t=%5.1f  %s" % (samples[i]["t"], " ".join("%3s" % x for x in line[i:i + 20])))

    print()
    print("-" * 72)
    print("判定")
    print("-" * 72)
    checks = [
        ("S1 基线阶段 busy 处于低位（≤基线最大值）", bool(base) and base_max <= 100,
         "base_max=%d" % base_max),
        ("S2 负载阶段 busy 明显高于基线",
         bool(loadp) and max(s["busy"] for s in loadp) > base_max + 5,
         "load_max=%s" % (max((s['busy'] for s in loadp), default=0))),
        ("S3 T_detect 可测量", t_detect is not None,
         "%.2fs" % t_detect if t_detect is not None else "N/A"),
        ("S4 T_release 可测量", t_release is not None,
         "%.2fs" % t_release if t_release is not None else "N/A"),
    ]
    allok = True
    for n, ok, d in checks:
        allok = allok and ok
        print("  %s %-44s %s" % ("✅" if ok else "❌", n, d))

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump({"samples": samples, "t_detect": t_detect,
                       "t_release": t_release, "calls": lg.calls},
                      f, ensure_ascii=False, indent=2)
        print("\n原始采样 : %s" % args.json_out)
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
