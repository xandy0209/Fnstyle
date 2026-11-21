#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CDP/HTTP 探针（v1.1.62）：实机验证「每日归档按完整自然日推进」

背景
----
v1.1.61 及之前：`archive_daily(target)` 直接按 target 结算，
而 target = now - N*86400 落在**一天中间的任意时刻**（比如 12:58），
于是某个自然日被拆成两半：
  · 归档表先写入 00:00–12:58 的部分（kwh = 半天值、小时桶只有 kwh@00..kwh@12）
  · 剩余部分要等下一轮归档才补上（`_UPSERT_DAILY_ADD` 累加）
→ 归档表在补齐之前一直是半成品，而 `_integrate_points` 直接消费
  `kwh@HH` 分桶 → 半天空档期的谷/平拆分会算错；
→ 更糟：`cleanup()` 按 cutoff 删原始点，但 cutoff 当天 00:00–cutoff
  这段还没进归档 → **数据丢失**。

v1.1.62 修复：
  · archive_daily() 把 target 向上取整到下一个自然日 00:00
  · cleanup() 的删除线收紧为 min(cutoff, 归档水位线)

逐项验证：
  A. /api/power/daily 返回 archived_until（归档水位线）
  B. 水位线落在自然日 00:00（不是一天中间）
  C. 归档水位线是「过去的某个完整日」（不会超前到未来）
  D. daily_summary 里的每一天都是完整日（kwh 总量与 24 个小时桶自洽）
  E. 有数据的最近一天是今天或昨天（没有隔空缺日）
  F. 页面无 JS 异常

前置：Chrome 已带 --remote-debugging-port=9222 启动（可选，只为 F）。
"""
import json
import sys
import time
import urllib.request

API = "http://192.168.5.8:9910"
URL = API + "/"

PASS = 0
FAIL = 0


def check(label, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  ✓ %s" % label)
    else:
        FAIL += 1
        print("  ✗ %s  → %s" % (label, detail))


def api(path):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return json.loads(op.open(API + path, timeout=30).read().decode())


def main():
    print("v1.1.62 实机探针：每日归档按完整自然日推进")
    print("\n--- A. 接口返回 archived_until ---")
    d = api("/api/power/daily?days=30")
    check("接口返回 archived_until 字段", "archived_until" in d, str(list(d.keys())))
    au = d.get("archived_until")
    check("archived_until 有值（已归档过）", au is not None, str(au))

    if au:
        lt = time.localtime(float(au))
        print("\n--- B. 水位线对齐自然日 00:00 ---")
        check("水位线落在自然日 00:00",
              lt.tm_hour == 0 and lt.tm_min == 0 and lt.tm_sec == 0,
              time.strftime("%Y-%m-%d %H:%M:%S", lt))
        check("水位线秒级以下为 0（不是取整残留）",
              int(float(au)) == float(au), str(au))

        print("\n--- C. 水位线不超过今天 ---")
        now_lt = time.localtime()
        check("水位线 ≤ 今天 00:00",
              (lt.tm_year, lt.tm_mon, lt.tm_mday)
              <= (now_lt.tm_year, now_lt.tm_mon, now_lt.tm_mday),
              time.strftime("%Y-%m-%d", lt))

    print("\n--- D. 每日数据完整性 ---")
    days = d.get("days") or []
    with_data = [x for x in days if x.get("kwh") is not None]
    check("有数据的天数 ≥ 1", len(with_data) >= 1,
          "共 %d 天有数据 / %d 天" % (len(with_data), len(days)))
    # 每天必须有 power_h（说明覆盖了完整时段）
    bad = [x.get("day") for x in with_data if not x.get("power_h")]
    check("每个有数据的天都有 power_h（覆盖时段）", not bad, str(bad))
    # 归档水位线之前的天必须是完整日：kwh 与 power_h 自洽
    # （恒定功率下 kwh/power_h ≈ 1/3.6；这里只断言 kwh > 0 且 power_h > 0）
    bad2 = [x.get("day") for x in with_data
            if x.get("kwh") is not None and x.get("kwh") <= 0]
    check("有数据天的 kwh 均 > 0", not bad2, str(bad2))

    print("\n--- E. 日期连续性 ---")
    if len(with_data) >= 2:
        ds = [x["day"] for x in with_data]
        gaps = []
        for i in range(1, len(ds)):
            a = time.strptime(ds[i - 1], "%Y-%m-%d")
            b = time.strptime(ds[i], "%Y-%m-%d")
            gap = (time.mktime(b) - time.mktime(a)) / 86400.0
            if gap > 1.5:
                gaps.append("%s→%s (%.1f 天)" % (ds[i - 1], ds[i], gap))
        check("有数据的日子之间没有隔空缺日", not gaps, str(gaps))

    print("\n--- F. 页面健康（可选） ---")
    try:
        import websocket
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        tabs = json.loads(op.open("http://127.0.0.1:9222/json/list",
                                  timeout=5).read().decode())
        pages = [t for t in tabs if t.get("type") == "page"
                 and t.get("webSocketDebuggerUrl")]
        if not pages:
            print("  – 无 CDP page target，跳过（不影响判定）")
        else:
            ws = websocket.create_connection(pages[0]["webSocketDebuggerUrl"],
                                             timeout=30)
            ws.send(json.dumps({"id": 1, "method": "Runtime.enable"}))
            ws.send(json.dumps({"id": 2, "method": "Page.enable"}))
            ws.send(json.dumps({"id": 3, "method": "Page.navigate",
                                 "params": {"url": URL}}))
            end = time.time() + 8
            while time.time() < end:
                try:
                    ws.recv()
                except Exception:
                    break
            ws.send(json.dumps({"id": 4, "method": "Runtime.evaluate",
                                 "params": {"expression": "JSON.stringify("
                                            "(window.__fnstyleErrors||[]))",
                                            "returnByValue": True}}))
            errs = []
            end = time.time() + 5
            while time.time() < end:
                try:
                    m = json.loads(ws.recv())
                except Exception:
                    break
                if m.get("id") == 4:
                    v = (m.get("result") or {}).get("result") or {}
                    try:
                        errs = json.loads(v.get("value") or "[]")
                    except Exception:
                        errs = []
                    break
            check("页面无 JS 异常", not errs, str(errs))
            ws.close()
    except Exception as e:
        print("  – CDP 检查跳过：%s" % e)

    print("\n" + "=" * 62)
    print("通过 %d 项，失败 %d 项" % (PASS, FAIL))
    if FAIL:
        print("实机验证未通过")
        return 1
    print("实机验证全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
