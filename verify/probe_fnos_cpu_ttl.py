#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
探针（决定性）：测 fnOS `appcgi.resmon.cpu` 后端缓存的 **TTL**。

方法
----
受控间隔的「双次请求」实验：
    取一次样本 A → 等待 Δ → 取一次样本 B
若 A 与 B 完全相同 ⇒ 服务端在 Δ 内返回同一个快照 ⇒ 存在缓存
逐步增大 Δ，找出「开始出现差异」的 Δ ⇒ 即为缓存 TTL

同时做「并发同刻」实验：
    N 个请求几乎同时发出，若全部返回**逐位相同**的浮点数
    ⇒ 证明服务端返回的是同一份快照（实时计算不可能逐位相同）

判定
----
T1 并发同刻：≥4 个请求返回完全相同的 load1m（10 位小数全等）
T2 Δ 扫描：存在「相同 Δ 区间」与「不同 Δ 区间」的分界
T3 TTL 估计：Δ 从 0 增大，首次出现差异的 Δ ≈ 缓存 TTL
T4 快照一致性：同刻并发时 busy 也完全相同

用法
----
python3 verify/probe_fnos_cpu_ttl.py
"""
import json
import os
import statistics
import subprocess
import sys
import time

SKILL = os.path.expanduser("~/.workbuddy/skills/fnos")
TC = os.path.join(SKILL, "scripts", "trim-cli")
PROFILE = "relay"


def fetch():
    p = subprocess.run([TC, "--profile", PROFILE, "monitor", "cpu"],
                       capture_output=True, text=True, timeout=30)
    try:
        c = json.loads(p.stdout).get("cpu", {})
        return ((c.get("busy") or {}).get("all"),
                (c.get("loadavg") or {}).get("avg1min"))
    except Exception:
        return (None, None)


def concurrent_same_moment(n=6):
    """N 个请求几乎同时发出，看返回是否逐位相同"""
    import concurrent.futures as cf
    def one(_):
        return fetch()
    with cf.ThreadPoolExecutor(n) as ex:
        res = list(ex.map(one, range(n)))
    busy = [r[0] for r in res]
    load = [r[1] for r in res]
    uniq_b = set(busy)
    uniq_l = set(load)
    return busy, load, uniq_b, uniq_l


def main():
    print("=" * 74)
    print("fnOS resmon.cpu 后端缓存 TTL —— 决定性判定")
    print("=" * 74)

    # ---------- T1/T4 并发同刻 ----------
    print()
    print("-" * 74)
    print("T1/T4 并发同刻请求（6 并发）")
    print("-" * 74)
    busy, load, uniq_b, uniq_l = concurrent_same_moment(6)
    print("busy  : %s" % busy)
    print("load1m: %s" % load)
    print("busy 唯一值数 : %d / %d" % (len(uniq_b), len(busy)))
    print("load1m 唯一值数: %d / %d" % (len(uniq_l), len(load)))
    t1 = len(uniq_l) == 1 and len(uniq_b) == 1
    t4 = len(uniq_b) == 1
    print("=> %s" % ("全部逐位相同 ⇒ 服务端返回同一份快照（实时计算不可能）✔"
                     if t1 else "存在差异 ⇒ 非快照复用 ✘"))

    # ---------- T2/T3 Δ 扫描 ----------
    print()
    print("-" * 74)
    print("T2/T3 受控间隔 Δ 扫描（每档 8 组，取「相同」比例）")
    print("-" * 74)
    deltas = [0.0, 0.15, 0.3, 0.45, 0.6, 0.75, 0.9, 1.05, 1.2, 1.4, 1.6, 2.0]
    trials = 8
    scan = []
    for d in deltas:
        same = 0
        diffs = []
        for _ in range(trials):
            a_b, a_l = fetch()
            time.sleep(d)
            b_b, b_l = fetch()
            if a_l is not None and b_l is not None:
                if a_l == b_l and a_b == b_b:
                    same += 1
                else:
                    diffs.append((a_l, b_l))
        ratio = same / float(trials)
        scan.append({"delta": d, "same_ratio": ratio,
                     "diffs": diffs[:2]})
        print("  Δ=%4.2fs  两次相同 %d/%d (%.0f%%)" % (d, same, trials, ratio * 100))

    # TTL 估计：找「相同率首次掉到 <50%」的 Δ
    ttl_est = None
    for row in scan:
        if row["same_ratio"] < 0.5:
            ttl_est = row["delta"]
            break

    print()
    print("-" * 74)
    print("判定")
    print("-" * 74)
    # T2：必须存在分界（低 Δ 高相同率，高 Δ 低相同率）
    low_d = [r for r in scan if r["delta"] <= 0.6]
    high_d = [r for r in scan if r["delta"] >= 1.4]
    lo_avg = statistics.mean([r["same_ratio"] for r in low_d]) if low_d else 0
    hi_avg = statistics.mean([r["same_ratio"] for r in high_d]) if high_d else 0
    t2 = lo_avg > hi_avg
    t3 = ttl_est is not None

    checks = [
        ("T1 并发同刻返回逐位相同（快照复用）", t1,
         "load1m 唯一值 %d 个" % len(uniq_l)),
        ("T4 并发同刻 busy 也完全相同", t4,
         "busy 唯一值 %d 个" % len(uniq_b)),
        ("T2 存在分界：Δ小→同率高，Δ大→同率低", t2,
         "Δ≤0.6s 同率%.0f%% vs Δ≥1.4s 同率%.0f%%" % (lo_avg * 100, hi_avg * 100)),
        ("T3 TTL 可估计（存在差异的最小 Δ）", t3,
         "TTL ≈ %.2f 秒" % ttl_est if ttl_est is not None else "N/A"),
    ]
    allok = True
    for n, ok, d in checks:
        allok = allok and ok
        print("  %s %-48s %s" % ("✅" if ok else "❌", n, d))

    print()
    print("=" * 74)
    print("最终结论")
    print("=" * 74)
    print("后端 appcgi.resmon.cpu : %s" %
          ("缓存 TTL ≈ %.2f 秒（同一秒内请求返回同一快照）" % ttl_est
           if ttl_est else "疑似缓存"))
    print("前端 FnDesk「运行状态」卡片 : 每 3000 ms 轮询一次 resmoGen")
    print("⇒ 端到端：桌面卡片上的 CPU% 最多滞后 ~3 秒 + 后端缓存窗口")
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
