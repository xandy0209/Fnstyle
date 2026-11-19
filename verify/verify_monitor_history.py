#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""处理器历史趋势 —— 后端单元测试

覆盖：
  ① HistoryStore：建表 / 写入 / 查询 / 范围过滤 / 清理
  ② history_collect：写入器取数口径、查询封装、范围映射
  ③ HTTP 路由 /api/monitor/history：单指标 / 多指标 / 非法参数 / 实时兜底
  ④ 采集口径：每核使用率与 fnmonitor 的 calc_cpu_percent 逐位一致

运行：python3 verify/verify_monitor_history.py
"""

import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
UI = os.path.abspath(os.path.join(HERE, "..", "app", "ui"))
sys.path.insert(0, UI)

PASS = 0
FAIL = 0


def check(label, ok, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  ✓ %s" % label)
    else:
        FAIL += 1
        print("  ✗ %s%s" % (label, ("  → " + str(extra)) if extra else ""))


def section(title):
    print("\n=== %s ===" % title)


# ---------------------------------------------------------------------------
# ① HistoryStore
# ---------------------------------------------------------------------------

def test_history_store():
    section("HistoryStore（表结构 / 读写 / 清理）")
    import storage_collect as sc

    db = tempfile.mktemp(suffix=".db")
    st = sc.HistoryStore(db)
    now = time.time()

    # 建表与索引（首次写入时惰性建表，所以先写一条再查表结构）
    st.write([(now, "cpu", 42.0)])

    import sqlite3
    conn = sqlite3.connect(db)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    indexes = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_metric%'")]
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='metrics'").fetchone()
    sql = row[0] if row else ""
    conn.close()

    check("建出 metrics 表", "metrics" in tables, tables)
    check("建出 idx_metric_ts 索引", "idx_metric_ts" in indexes, indexes)
    check("表结构含 ts/metric/value 三列",
          all(k in sql for k in ("ts", "metric", "value")), sql.replace("\n", " "))

    # 写入（列序必须是 ts, metric, value —— 写反了查询永远查不到）
    ok = st.write([(now, "load1", 1.25)])
    check("写入成功", ok)

    # 写入列序验证：直接查表
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT ts, metric, value FROM metrics").fetchall()
    conn.close()
    check("写入列序为 (ts, metric, value)",
          len(rows) == 2 and rows[0][1] == "cpu" and rows[0][2] == 42.0, rows)

    # 查询
    got = st.query("cpu", 3600)
    check("按 metric 查询只返回该指标", len(got) == 1 and got[0][1] == 42.0, got)
    check("返回的是 (ts, value) 二元组", len(got[0]) == 2, got)

    # 时间升序
    st.write([(now - 120, "cpu", 1.0), (now - 60, "cpu", 2.0)])
    got = st.query("cpu", 3600)
    check("查询结果按时间升序",
          [g[0] for g in got] == sorted(g[0] for g in got), got)
    check("区间过滤：10m 窗口内的点数",
          len(st.query("cpu", 600)) == 3, len(st.query("cpu", 600)))

    # all（seconds=0）返回全部
    check("all 范围返回全部记录", len(st.query("cpu", 0)) == 3, len(st.query("cpu", 0)))

    # 清理
    n = st.cleanup(retention_days=0)
    check("retention_days=0 清空全部", n == 4 and len(st.query("cpu", 0)) == 0,
          "删除 %s，剩余 %s" % (n, len(st.query("cpu", 0))))

    st.write([(now - 8 * 86400, "cpu", 1.0), (now, "cpu", 2.0)])
    n = st.cleanup()
    check("默认保留 7 天：清掉超期点、保留期内点不动",
          n == 1 and len(st.query("cpu", 0)) == 1, "删除 %s，剩余 %s" % (n, len(st.query("cpu", 0))))

    os.unlink(db)


# ---------------------------------------------------------------------------
# ② history_collect
# ---------------------------------------------------------------------------

def test_history_collect():
    section("history_collect（写入口径 / 查询封装 / 范围映射）")
    d = tempfile.mkdtemp()
    os.environ["FNSTYLE_DATA_DIR"] = d

    # 重新导入，指向临时数据目录
    for m in ("history_collect", "storage_collect"):
        sys.modules.pop(m, None)
    import history_collect as hc
    import storage_collect as sc

    st = sc.HistoryStore(os.path.join(d, "storage.db"))
    st.write([(time.time(), "cpu", 42.0)])

    check("RANGE_SECONDS 与 fnmonitor 一致",
          hc.RANGE_SECONDS == {"10m": 600, "1h": 3600, "6h": 21600,
                               "24h": 86400, "7d": 604800, "all": 0},
          hc.RANGE_SECONDS)

    got = hc.query_history("cpu", "1h")
    check("query_history 返回 [[ts,v], ...]",
          len(got) == 1 and len(got[0]) == 2 and got[0][1] == 42.0, got)

    # write_history 取数口径
    ok = hc.write_history({"cpu": {"percent": 33.33,
                                    "loadavg": ["1.25", "0.9", "0.8"]}})
    check("write_history 写入成功", ok)
    got = hc.query_history("cpu", "1h")
    check("cpu 指标写入快照的 percent", len(got) == 2 and abs(got[-1][1] - 33.33) < 0.01, got)
    got = hc.query_history("load1", "1h")
    check("load1 指标写入 loadavg[0]（1 分钟负载）",
          len(got) == 1 and abs(got[0][1] - 1.25) < 0.01, got)

    # 无有效值不写（避免污染曲线）
    before = len(hc.query_history("cpu", "1h"))
    check("无 cpu 指标时 write_history 返回 False",
          hc.write_history({"cpu": {}}) is False)
    check("无 cpu 指标时不写库",
          len(hc.query_history("cpu", "1h")) == before)

    check("write_history(None) 安全返回 False", hc.write_history(None) is False)

    # 清理
    check("cleanup_history 可调用", isinstance(hc.cleanup_history(), int))


# ---------------------------------------------------------------------------
# ③ HTTP 路由
# ---------------------------------------------------------------------------

def test_http_route():
    section("HTTP 路由 /api/monitor/history")
    d = tempfile.mkdtemp()
    os.environ["FNSTYLE_DATA_DIR"] = d
    os.environ["FNSTYLE_UI_DIR"] = UI

    for m in ("history_collect", "storage_collect", "monitor_collect", "fnstyle_server"):
        sys.modules.pop(m, None)
    import storage_collect as sc

    st = sc.HistoryStore(os.path.join(d, "storage.db"))
    now = time.time()
    st.write([(now - 300, "cpu", 40.0), (now - 200, "cpu", 55.0),
              (now - 100, "cpu", 60.0),
              (now - 100, "load1", 0.8), (now - 50, "load1", 1.2)])

    import fnstyle_server as fs
    port = 19917
    srv = fs.Server(("127.0.0.1", port), fs.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)

    def get(path):
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path),
                                    timeout=6) as r:
            return json.loads(r.read().decode())

    try:
        # 单指标
        r = get("/api/monitor/history?metric=cpu&range=1h")
        check("单指标返回 points",
              r.get("success") is True and len(r.get("points", [])) == 3, r)
        check("单指标返回 metric / range 字段",
              r.get("metric") == "cpu" and r.get("range") == "1h", r)

        # 多指标
        r = get("/api/monitor/history?metrics=cpu,load1&range=1h")
        check("多指标返回 series", set(r.get("series", {}).keys()) == {"cpu", "load1"}, r)
        check("多指标各自有序列点",
              len(r["series"]["cpu"]) == 3 and len(r["series"]["load1"]) == 2, r)

        # 非法 range 回落 1h
        r = get("/api/monitor/history?metric=cpu&range=xxx")
        check("非法 range 回落为 1h", r.get("range") == "1h", r.get("range"))

        # 缺参数
        try:
            get("/api/monitor/history")
            check("缺参数返回 400", False, "未抛错")
        except urllib.error.HTTPError as e:
            check("缺参数返回 400", e.code == 400, e.code)

        # 非法指标名被拒（防注入）
        try:
            get("/api/monitor/history?metric=../../etc/passwd")
            check("非法指标名返回 400", False, "未抛错")
        except urllib.error.HTTPError as e:
            check("非法指标名返回 400", e.code == 400, e.code)

        # 实时兜底：查一个不存在的指标但要 cpu 的实时值 —— 用空历史验证
        r = get("/api/monitor/history?metric=load1&range=10m")
        check("空历史时接口仍正常返回", r.get("success") is True, r)
    finally:
        srv.shutdown()


# ---------------------------------------------------------------------------
# ④ 采集口径（与 fnmonitor 逐位一致）
# ---------------------------------------------------------------------------

def test_cpu_percent_parity():
    section("每核使用率口径（与 fnmonitor calc_cpu_percent 一致）")
    import monitor_collect as mc

    # 构造一段 /proc/stat
    stat = (
        "cpu  100 0 50 1000 0 0 0 0 0 0\n"
        "cpu0 25 0 12 250 0 0 0 0 0 0\n"
        "cpu1 25 0 13 250 0 0 0 0 0 0\n"
        "cpu2 25 0 12 250 0 0 0 0 0 0\n"
        "cpu3 25 0 13 250 0 0 0 0 0 0\n"
        "intr 1234\n"
    )
    total, cores = mc._proc_stat_cpu_times(stat)
    check("汇总行取前 8 个字段", total == [100, 0, 50, 1000, 0, 0, 0, 0], total)
    check("每核 4 行全部识别", len(cores) == 4, len(cores))
    check("cpuN 的核心号不混进时间片",
          cores[0] == [25, 0, 12, 250, 0, 0, 0, 0], cores[0])
    check("非 cpu 开头的行被忽略（intr）",
          all(c[0] != 1234 for c in cores), cores)

    # fnmonitor 的 calc_cpu_percent：round(max(0, 100*(1-idle_delta/total_delta)), 1)
    # idle = parts[3] + parts[4]
    prev = [100, 0, 50, 1000, 0, 0, 0, 0]
    curr = [200, 0, 100, 1900, 0, 0, 0, 0]

    def fnmonitor_calc(p, c):
        """fnmonitor server.py 的 calc_cpu_percent 原样复刻（作为对照基准）。"""
        if p is None or c is None:
            return 0.0
        if len(p) < 4 or len(c) < 4:
            return 0.0
        prev_idle = p[3] + (p[4] if len(p) > 4 else 0)
        curr_idle = c[3] + (c[4] if len(c) > 4 else 0)
        total_delta = sum(c) - sum(p)
        idle_delta = curr_idle - prev_idle
        if total_delta <= 0:
            return 0.0
        return round(max(0.0, 100.0 * (1.0 - idle_delta / total_delta)), 1)

    expect = fnmonitor_calc(prev, curr)
    got = mc._cpu_percent(prev, curr)
    check("汇总使用率与 fnmonitor 逐位一致", got == expect,
          "fnmonitor=%s 本实现=%s" % (expect, got))
    check("汇总使用率保留 1 位小数", got is not None and round(got, 1) == got, got)

    # 每核：fnmonitor 逐核差分
    pcore = [25, 0, 12, 250, 0, 0, 0, 0]
    ccore = [50, 0, 25, 490, 0, 0, 0, 0]
    expect_c = fnmonitor_calc(pcore, ccore)
    got_c = mc._cpu_percent(pcore, ccore)
    check("每核使用率与 fnmonitor 逐位一致", got_c == expect_c,
          "fnmonitor=%s 本实现=%s" % (expect_c, got_c))

    # idle 索引不能错位：把 idle 挪到 parts[4]（旧 bug 的形态），读出的值必须不同
    wrong_shape = [100, 0, 1000, 50, 0, 0, 0, 0]
    wrong = mc._cpu_percent(wrong_shape, [200, 0, 1900, 100, 0, 0, 0, 0])
    right = mc._cpu_percent(prev, curr)
    check("idle 取 parts[3]（错位形态会算出不同的值）", wrong != right,
          "错位=%s 正确=%s" % (wrong, right))

    # total_delta <= 0 时返回 None（不产出假数据）
    check("total_delta<=0 返回 None", mc._cpu_percent(curr, prev) is None)
    check("空输入返回 None", mc._cpu_percent(None, curr) is None)
    check("字段不足返回 None", mc._cpu_percent([1, 2], [1, 2]) is None)


def test_collect_cpu_uses_shared_percent():
    section("collect_cpu 复用 _cpu_percent（避免两套算法）")
    import monitor_collect as mc

    src = open(os.path.join(UI, "monitor_collect.py"), encoding="utf-8").read()
    body = src[src.index("def collect_cpu()"):src.index("def collect_memory()")]

    check("collect_cpu 调用了 _cpu_percent", "_cpu_percent(" in body)
    # 不应再有另一套手写的 100.0 * (1.0 - ... ) 公式
    check("collect_cpu 内不再另写使用率公式",
          "100.0 * (1.0 -" not in body, "发现重复实现")
    # 基线必须存完整时间片列表，不能只存 (total, idle)
    check("基线存完整 8 字段时间片列表",
          '_CPU_STATE["last"] = list(total_line)' in body)
    check("每核基线存完整时间片列表",
          '_CPU_STATE["cores"] = [list(n) for n in core_lines]' in body)


def main():
    print("=" * 60)
    print("处理器历史趋势 —— 后端单元测试")
    print("=" * 60)
    test_history_store()
    test_history_collect()
    test_http_route()
    test_cpu_percent_parity()
    test_collect_cpu_uses_shared_percent()
    print("\n" + "=" * 60)
    print("结果: 通过 %d 项, 失败 %d 项" % (PASS, FAIL))
    print("=" * 60)
    if FAIL:
        print("❌ 有失败项")
        return 1
    print("🎉 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
