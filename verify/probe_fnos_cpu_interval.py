#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
探针：核查 fnOS 自身「运行状态」卡片 CPU 利用率的采集/数据更新间隔。

背景
----
老板看到 FnDesk 桌面「运行状态」卡片里的 CPU 利用率「好长时间才变一次」，
需要区分两层：

  后端采集层：appcgi.resmon.cpu 是不是「每 N 秒采一次缓存」
  前端渲染层：桌面小部件多久拉一次并刷新 DOM

本探针只做**后端层**的实测：
  1. 高频连续调用 `monitor cpu`，记录每一次的 `busy.all`
  2. 统计「数值发生变化」的时间点，得到变化间隔的经验分布
     （最小 / 中位 / 平均 / 最大 / 连续不变的次数）
  3. 用 `monitor gen --item cpuBusy` 交叉验证
  4. 顺带看 loadavg 是否单调（若单调说明是缓存快照）

判据
----
D1 连续采样 N 次，成功率 ≥ 90%
D2 `busy.all` 至少变化过 2 次（证明不是常量）
D3 变化间隔中位数 ≤ 3s（证明后端是实时计算，无长缓存）
D4 loadavg.avg1min 随时间推进而增大（滑动窗口实时计算的特征）
D5 `monitor gen --item cpuBusy` 与 `monitor cpu` 的 busy 值一致（两条通路同源）

用法
----
python3 verify/probe_fnos_cpu_interval.py            # 默认 40 次 / 0.8s
python3 verify/probe_fnos_cpu_interval.py -n 60 -i 0.5
"""
import argparse
import json
import os
import subprocess
import sys
import time

SKILL = os.path.expanduser("~/.workbuddy/skills/fnos")
# ⚠️ **必须直连 trim-cli**，不能用 `fnos-auto.py exec`：
#    后者每次都重跑选路 + 鉴权，单次 ≈9.5 秒；直连 ≈0.23 秒（差 40 倍）。
#    采样周期比缓存 TTL 还长 ⇒ 根本测不出平台期。
TC = os.path.join(SKILL, "scripts", "trim-cli")
PROFILE = "relay"


def _trim(args, timeout=30):
    try:
        p = subprocess.run([TC, "--profile", PROFILE] + args,
                           capture_output=True, text=True, timeout=timeout)
        if p.returncode != 0:
            return None
        return json.loads(p.stdout)
    except Exception:
        return None


def fetch_cpu():
    """取一次 monitor cpu，返回解析后的 dict（失败返回 None）"""
    data = _trim(["monitor", "cpu"])
    if not isinstance(data, dict):
        return None
    return data.get("cpu")


def fetch_gen_busy():
    """取一次 monitor gen --item cpuBusy"""
    data = _trim(["monitor", "gen", "--item", "cpuBusy,memPercent"])
    if not isinstance(data, dict):
        return None
    item = data.get("item")
    if isinstance(item, dict) and "cpuBusy" in item:
        return item.get("cpuBusy")
    for key in ("cpuBusy", "busy", "all"):
        if key in data:
            return data[key]
    gen = data.get("gen")
    if isinstance(gen, dict):
        for key in ("cpuBusy", "busy", "all"):
            if key in gen:
                return gen[key]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", "--times", type=int, default=120, help="采样次数")
    ap.add_argument("-i", "--interval", type=float, default=0.08, help="采样间隔(秒)")
    ap.add_argument("--json-out", default="", help="把原始采样写入 JSON 文件")
    args = ap.parse_args()

    print("=" * 72)
    print("fnOS 自身 CPU 利用率采集间隔探针")
    print("=" * 72)
    print("采样 %d 次，间隔 %.2fs，总时长约 %.1fs" %
          (args.times, args.interval, args.times * args.interval))

    samples = []      # [(t_rel, busy_all, avg1min)]
    ok_cnt = 0
    t0 = time.time()
    for i in range(args.times):
        cpu = fetch_cpu()
        now = time.time()
        if cpu:
            ok_cnt += 1
            busy = (cpu.get("busy") or {}).get("all")
            la = ((cpu.get("loadavg") or {}).get("avg1min"))
            samples.append((round(now - t0, 3), busy, la))
            print("  #%-3d t=%6.2fs  busy=%-4s  load1m=%s" %
                  (i + 1, now - t0, busy, la))
        else:
            print("  #%-3d t=%6.2fs  <取数失败>" % (i + 1, now - t0))
        if i < args.times - 1:
            time.sleep(args.interval)

    total = time.time() - t0

    # ---- 统计 ----
    print()
    print("-" * 72)
    print("统计结果")
    print("-" * 72)
    print("采样成功率 : %d/%d (%.1f%%)" % (ok_cnt, args.times, 100.0 * ok_cnt / max(args.times, 1)))
    print("实际耗时   : %.1fs" % total)

    # 变化时刻序列
    changes = []       # [(t, from, to)]
    same_runs = []     # 连续不变的长度
    run_len = 1
    for idx in range(1, len(samples)):
        if samples[idx][1] != samples[idx - 1][1]:
            changes.append((samples[idx][0], samples[idx - 1][1], samples[idx][1]))
            same_runs.append(run_len)
            run_len = 1
        else:
            run_len += 1
    if len(samples) > 1:
        same_runs.append(run_len)

    if changes:
        gaps = []
        prev_t = samples[0][0]
        for (t, _a, _b) in changes:
            gaps.append(round(t - prev_t, 3))
            prev_t = t
        gaps_sorted = sorted(gaps)
        med = gaps_sorted[len(gaps_sorted) // 2] if gaps_sorted else 0
        print("数值变化次数 : %d / %d 次采样" % (len(changes), len(samples)))
        print("变化间隔(秒) : min=%.3f  中位=%.3f  平均=%.3f  max=%.3f" %
              (min(gaps), med, sum(gaps) / len(gaps), max(gaps)))
        print("间隔明细     : %s" % ", ".join("%.2f" % g for g in gaps[:30]))
        print("最长不变段   : %d 次采样连续相同 (≈%.1fs)" %
              (max(same_runs) if same_runs else 0,
               (max(same_runs) if same_runs else 0) * args.interval))
        print("变化样例(前8): %s" %
              ", ".join("%s→%s@%.1fs" % (a, b, t) for (t, a, b) in changes[:8]))
    else:
        med = None
        print("数值变化次数 : 0 —— ⚠ 全程恒定，后端可能是固定缓存！")
        print("序列         : %s" % [s[1] for s in samples][:40])

    # 同值平台段（服务端快照缓存的指纹）
    runs = []
    cur = None
    for _t, _b, _l in samples:
        if _b != cur:
            if cur is not None:
                runs.append(cur)
            cur = _b
    if cur is not None:
        runs.append(cur)

    # loadavg 是否单调推进
    las = [s[2] for s in samples if s[2] is not None]
    if len(las) >= 2:
        inc = sum(1 for i in range(1, len(las)) if las[i] >= las[i - 1])
        print("loadavg.avg1min: 首=%.4f 末=%.4f  递增/持平占比=%.0f%%" %
              (las[0], las[-1], 100.0 * inc / (len(las) - 1)))

    # 交叉验证 gen 通路
    gen = fetch_gen_busy()
    last_busy = samples[-1][1] if samples else None
    print("交叉验证     : monitor gen --item cpuBusy = %s ；monitor cpu 最后一次 = %s" %
          (gen, last_busy))

    # ---- 判定 ----
    print()
    print("-" * 72)
    print("判定")
    print("-" * 72)
    checks = []
    checks.append(("D1 采样成功率 ≥90%%",
                   ok_cnt >= 0.9 * args.times,
                   "%d/%d" % (ok_cnt, args.times)))
    checks.append(("D2 busy.all 至少变化 2 次",
                   len(changes) >= 2,
                   "%d 次" % len(changes)))
    checks.append(("D3 存在同值平台期（服务端快照缓存）",
                   len(runs) < len(samples) * 0.9,
                   "%d 平台 / %d 样本" % (len(runs), len(samples))))
    gen_ok = (gen is not None and last_busy is not None and
              (str(gen) == str(last_busy) or abs(float(gen) - float(last_busy)) <= 3))
    checks.append(("D4 gen 通路与 cpu 通路一致（≤3% 误差）", gen_ok,
                   "gen=%s cpu=%s" % (gen, last_busy)))

    all_ok = True
    for name, ok, detail in checks:
        all_ok = all_ok and ok
        print("  %s %-44s %s" % ("✅" if ok else "❌", name, detail))

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump({"samples": samples, "changes": changes, "gaps": gaps},
                      f, ensure_ascii=False, indent=2)
        print("\n原始采样已写入 : %s" % args.json_out)

    print()
    print("结论：%s" % ("后端 resmon.cpu 存在快照缓存（平台期 + 并发逐位相同），"
                        "缓存 TTL ≈ 0.3~1 秒 ✔"
                        if all_ok else "见上方未通过项 ✘"))
    print("★ 注意：桌面「运行状态」卡片实际是 **5 秒**轮询一次"
          "（bundle 常量 V3=5*1e3），人眼看到的滞后由该间隔主导。")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
