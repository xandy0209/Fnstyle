#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 CPU 使用率的采样窗口（v1.1.37）。

背景
----
/proc/stat 的时间片精度是 **1 jiffy = 10ms**（USER_HZ=100）。
采样窗口内每核能分到的 jiffy 数量，直接决定了读数的量化分辨率：

    窗口    每核 jiffy    单 jiffy 占比
    0.25s      ~25          4.0%      ← 读数在 2%~64% 间乱跳
    3s        ~300          0.33%
    10s      ~1000          0.10%     ← 数值稳定

fnmonitor 在 NAS 上的实测配置（/vol1/@appdata/fnmonitor/config.json）
为 `interval = 10`，因此 FnStyle 也改成 10 秒窗口。

本测试用 **NAS 真机采集的 /proc/stat 样本**（verify/fixtures/cpu_stat_samples.json）
驱动真实代码，验证：
  1. 采样窗口常量与线程结构正确
  2. 算法与 fnmonitor 的 calc_cpu_percent() 逐位一致
  3. 10 秒窗口的读数波动明显小于 0.25 秒窗口
  4. 10 秒窗口的每核读数更稳定

运行：python3 verify/verify_cpu_sample_window.py
"""

import json
import os
import random
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "app", "ui"))

import monitor_collect as mc  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "cpu_stat_samples.json")

_results = {"pass": 0, "fail": 0}


def check(name, cond, extra=""):
    if cond:
        _results["pass"] += 1
        print("  ✓ %s %s" % (name, extra))
    else:
        _results["fail"] += 1
        print("  ✗ %s %s" % (name, extra))
    return bool(cond)


def fnmonitor_calc(prev, curr):
    """fnmonitor 的 calc_cpu_percent()（MIT License，ui-src/server.py）。"""
    if prev is None or curr is None:
        return 0.0
    if len(prev) < 4 or len(curr) < 4:
        return 0.0
    p_idle = prev[3] + (prev[4] if len(prev) > 4 else 0)
    c_idle = curr[3] + (curr[4] if len(curr) > 4 else 0)
    td = sum(curr) - sum(prev)
    idd = c_idle - p_idle
    if td <= 0:
        return 0.0
    return round(max(0.0, 100.0 * (1.0 - idd / float(td))), 1)


def jiffy_weight(window_s, hz=100):
    """窗口内单个 jiffy 占的百分比（每核）。"""
    return 100.0 / int(window_s * hz)


# ---------------------------------------------------------------------------
# 1. 采样窗口常量与线程结构
# ---------------------------------------------------------------------------
def test_structure():
    print("\n=== 1. 采样窗口常量与线程结构 ===")
    src = open(os.path.join(ROOT, "app", "ui", "monitor_collect.py"),
              encoding="utf-8").read()

    check("CPU_SAMPLE_WINDOW == 10",
          getattr(mc, "CPU_SAMPLE_WINDOW", None) == 10,
          "(实际 %s)" % getattr(mc, "CPU_SAMPLE_WINDOW", "缺失"))
    check("主循环不再有 0.25 秒双采样",
          "time.sleep(0.25)" not in src,
          "（旧实现每轮连采两次，间隔 0.25 秒）")
    check("存在独立的 _cpu_loop 采样线程",
          "def _cpu_loop" in src)
    check("_cpu_loop 等待一个完整窗口",
          "self._stop.wait(CPU_SAMPLE_WINDOW)" in src)
    check("主循环从 CPU 缓存取数",
          "cpu = self._cpu_snapshot()" in src)
    check("CPU 拓扑有进程级缓存",
          "def _cpu_topology_cached" in src and "_CPU_TOPO_CACHE" in src,
          "（避免每次采集都 fork lscpu）")
    # 排除函数定义行 `def collect_cpu():`
    call_sites = [ln for ln in src.splitlines()
                  if "collect_cpu()" in ln and not ln.strip().startswith("def ")]
    check("collect_cpu() 只在 _cpu_loop 内被调用（2 处）",
          len(call_sites) == 2,
          "(%d 处：%s)" % (len(call_sites),
                           " / ".join(ln.strip() for ln in call_sites)))


# ---------------------------------------------------------------------------
# 2. 量化分辨率
# ---------------------------------------------------------------------------
def test_quantization():
    print("\n=== 2. 采样窗口的量化分辨率 ===")
    check("0.25s 窗口单 jiffy ≈ 4%",
          abs(jiffy_weight(0.25) - 4.0) < 0.01,
          "(%.2f%%)" % jiffy_weight(0.25))
    check("3s 窗口单 jiffy ≈ 0.33%",
          abs(jiffy_weight(3) - 1.0 / 3) < 0.005,
          "(%.3f%%)" % jiffy_weight(3))
    check("10s 窗口单 jiffy ≈ 0.1%",
          abs(jiffy_weight(10) - 0.1) < 0.005,
          "(%.3f%%)" % jiffy_weight(10))
    check("10s 窗口分辨率比 0.25s 好 40 倍",
          jiffy_weight(0.25) / jiffy_weight(10) > 39,
          "(%.0f 倍)" % (jiffy_weight(0.25) / jiffy_weight(10)))


# ---------------------------------------------------------------------------
# 3. 算法与 fnmonitor 逐位一致
# ---------------------------------------------------------------------------
def test_algorithm():
    print("\n=== 3. 算法与 fnmonitor calc_cpu_percent() 逐位一致 ===")
    random.seed(20261119)
    mismatch = 0
    for _ in range(1000):
        prev = [random.randint(0, 10 ** 9) for _ in range(8)]
        curr = [prev[i] + random.randint(0, 5000) for i in range(8)]
        if mc._cpu_percent(prev, curr) != fnmonitor_calc(prev, curr):
            mismatch += 1
    check("1000 组随机样本逐位一致", mismatch == 0,
          "(不一致 %d 组)" % mismatch)

    # 边界：total_delta <= 0 时不应产生百分比
    check("total_delta=0 时返回 None",
          mc._cpu_percent([100] * 8, [100] * 8) is None)
    check("基线为 None 时返回 None",
          mc._cpu_percent(None, [100] * 8) is None)


# ---------------------------------------------------------------------------
# 4. 用 NAS 真机样本驱动真实代码
# ---------------------------------------------------------------------------
def _frame_text(lines):
    return "\n".join(lines) + "\n"


def test_real_samples():
    print("\n=== 4. NAS 真机 /proc/stat 样本驱动真实代码 ===")
    if not os.path.exists(FIXTURE):
        check("样本文件存在", False, "(缺少 %s)" % FIXTURE)
        return
    data = json.load(open(FIXTURE, encoding="utf-8"))
    samples = data["samples"]
    check("样本文件存在", True, "(%d 组)" % len(samples))

    fake_stat = {"v": ""}
    orig_read_text = mc.read_text

    def fake_read_text(path, limit=8192):
        if path == "/proc/stat":
            return fake_stat["v"]
        if path == "/proc/loadavg":
            return "0.50 0.60 0.70\n"
        return orig_read_text(path, limit)

    mc.read_text = fake_read_text
    try:
        w025 = []
        w10 = []
        for s in samples:
            frames = s["frames"]
            mc._CPU_STATE.update({"last": None, "lastTime": None, "cores": None})
            fake_stat["v"] = _frame_text(frames["A"])
            r1 = mc.collect_cpu()
            fake_stat["v"] = _frame_text(frames["B"])
            r2 = mc.collect_cpu()

            if r1.get("available"):
                check("基线帧不产出百分比", False)
            if not r2.get("available"):
                check("差分帧产出百分比", False,
                      "(window=%s)" % s["window"])
                continue
            if not isinstance(r2.get("coresPercent"), list):
                check("产出逐核数组", False)
                continue

            rec = (r2["percent"], r2["coresPercent"])
            (w025 if s["window"] == "0.25s" else w10).append(rec)

        check("所有样本都产出有效读数",
              len(w025) + len(w10) == len(samples),
              "(%d/%d)" % (len(w025) + len(w10), len(samples)))

        # 汇总读数的波动幅度
        if w025 and w10:
            t025 = [x[0] for x in w025]
            t10 = [x[0] for x in w10]
            rng025 = max(t025) - min(t025)
            rng10 = max(t10) - min(t10)
            print("    0.25s 窗口读数: %s  → 极差 %.1f" % (t025, rng025))
            print("    10s   窗口读数: %s  → 极差 %.1f" % (t10, rng10))
            check("10s 窗口波动小于 0.25s 窗口",
                  rng10 < rng025,
                  "(%.1f < %.1f)" % (rng10, rng025))

        # 每核数值的离散度：越低越稳定
        def spread(recs):
            vals = []
            for _, cores in recs:
                vals.extend(cores)
            if not vals:
                return 0.0
            return max(vals) - min(vals)
        if w025 and w10:
            check("10s 窗口逐核读数更集中",
                  spread(w10) <= spread(w025),
                  "(10s %.1f vs 0.25s %.1f)" % (spread(w10), spread(w025)))

        # 关键：0.25s 窗口能出现「整核为 0」或「整核为 100」这种极端跳变
        extreme025 = any(0.0 in cores or 100.0 in cores for _, cores in w025)
        extreme10 = any(0.0 in cores or 100.0 in cores for _, cores in w10)
        check("0.25s 窗口出现 0%/100% 极端跳变", extreme025)
        check("10s 窗口未出现极端跳变", not extreme10,
              "(更符合 fnmonitor 显示形态)")
    finally:
        mc.read_text = orig_read_text


# ---------------------------------------------------------------------------
# 5. CPU 线程端到端
# ---------------------------------------------------------------------------
def test_thread():
    print("\n=== 5. CPU 采样线程端到端 ===")
    fake_stat = {"v": "", "n": 0}
    orig_read_text = mc.read_text

    def make_frame(base_user, base_idle, busy_list, window=1000, cores=4):
        """构造一帧 /proc/stat，语义严格对齐内核的**绝对累加值**。

        每核：user = base_user + busy；idle = base_idle + (window - busy)
        两帧间：user 增量 = busy，idle 增量 = (window - busy)
          → 使用率 = 1 - (window-busy)/window = busy/window
        汇总行：user 累加各核 busy，idle 累加各核 (window-busy)。
        """
        lines = []
        tb = sum(busy_list)
        ti = sum(window - b for b in busy_list)
        lines.append("cpu  " + " ".join(str(x) for x in
                     [base_user + tb, 0, 0, base_idle + ti, 0, 0, 0, 0]))
        for i in range(cores):
            b = busy_list[i] if i < len(busy_list) else 0
            lines.append("cpu%d " % i + " ".join(str(x) for x in
                         [base_user + b, 0, 0, base_idle + (window - b),
                          0, 0, 0, 0]))
        return "\n".join(lines) + "\n"

    BASE_U, BASE_I, WIN = 10 ** 7, 10 ** 8, 1000

    def auto_frame():
        """按读取次数自动推进时间片，模拟真实时间流逝。

        第 n 次读取（基线按**累计**推进，帧内只叠加本窗增量）：
          · base_user = BASE_U + (n-1)*1750   累计 busy = 1750/窗
          · base_idle = BASE_I + (n-1)*2250   累计 idle = 2250/窗
          · busy     = [1000, 500, 250, 0]   本窗各核 busy 增量
        相邻两帧：user 增量 = 1750，idle 增量 = 2250，total 增量 = 4000
          → 使用率 = 1 - 2250/4000 = 43.75%
        （逐核差异化由第 4 组的 NAS 真机样本覆盖）
        """
        n = fake_stat["n"]
        fake_stat["n"] = n + 1
        bu = BASE_U + (n - 1) * 1750
        bi = BASE_I + (n - 1) * 2250
        return make_frame(bu, bi, [1000, 500, 250, 0], window=WIN)

    def fake_read_text(path, limit=8192):
        if path == "/proc/stat":
            return auto_frame()
        if path == "/proc/loadavg":
            return "0.50 0.60 0.70\n"
        return orig_read_text(path, limit)

    mc.read_text = fake_read_text
    old_window = mc.CPU_SAMPLE_WINDOW
    try:
        mc.CPU_SAMPLE_WINDOW = 0.25
        mc._CPU_STATE.update({"last": None, "lastTime": None, "cores": None})

        c = mc.MonitorCollector(interval=3)
        c._stop.clear()
        th = threading.Thread(target=c._cpu_loop, daemon=True)
        th.start()
        time.sleep(0.15)
        s1 = c._cpu_snapshot()
        check("首轮尚无数据（available=False）",
              s1.get("available") is False, str(s1)[:60])

        time.sleep(0.5)
        s2 = c._cpu_snapshot()
        check("第二轮产出数据（available=True）",
              s2.get("available") is True, str(s2)[:60])
        check("产出 percent",
              isinstance(s2.get("percent"), (int, float)),
              "=%s" % s2.get("percent"))
        # 汇总 = 各核 busy 增量之和 / (核数 × 窗口) = 1750 / 4000 = 43.75%
        check("汇总百分比正确（43.8%）",
              s2.get("percent") == 43.8, "=%s" % s2.get("percent"))
        check("产出 4 项逐核数组",
              isinstance(s2.get("coresPercent"), list)
              and len(s2["coresPercent"]) == 4,
              "=%s" % s2.get("coresPercent"))
        check("产出 loadavg（3 项）",
              isinstance(s2.get("loadavg"), list) and len(s2["loadavg"]) == 3)

        # 抖动保护：某轮差分失败时，必须保留上一轮的有效值
        good = c._cpu_snapshot()
        mc._CPU_STATE.update({"last": None, "lastTime": None, "cores": None})
        time.sleep(0.4)
        after = c._cpu_snapshot()
        check("基线丢失时保留上一轮有效值",
              after.get("available") is True
              and after.get("percent") == good.get("percent"),
              "(%s → %s)" % (good.get("percent"), after.get("percent")))

        c._stop.set()
        th.join(timeout=3)
        check("线程能正常退出", not th.is_alive())

        # 缓存解耦
        c2 = mc.MonitorCollector(interval=3)
        check("空缓存时返回不可用占位",
              c2._cpu_snapshot() == {"available": False})
        c2._cpu_cache = {"available": True, "percent": 42.0}
        got = c2._cpu_snapshot()
        check("_cpu_snapshot 返回副本", got.get("percent") == 42.0)
        got["percent"] = 99.0
        check("修改副本不影响内部缓存",
              c2._cpu_cache["percent"] == 42.0)
    finally:
        mc.read_text = orig_read_text
        mc.CPU_SAMPLE_WINDOW = old_window


def main():
    print("=" * 70)
    print("CPU 采样窗口验证（v1.1.37）")
    print("=" * 70)
    test_structure()
    test_quantization()
    test_algorithm()
    test_real_samples()
    test_thread()

    print("\n" + "=" * 70)
    print("通过 %d 项，失败 %d 项" % (_results["pass"], _results["fail"]))
    print("=" * 70)
    return 1 if _results["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
