#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复现「昨天和今天的用电数据为什么没有」。

⚠️ 结论已被 v1.1.83 的取证推翻并更正（2026-11-23 16:20）：
   **断电不是原因，架构水位线漂移才是原因。**

   本脚本（2026-11-23 上午的第一版假设）原本断言「11/22 因断电 → kwh=null」。
   实机取证 + 源码复现证明：
     · 断电当天（11/22）其实**有** 1322 个 power 点，采集完全正常；
     · 真正的原因是 `archive_daily` 的对齐分支让归档水位线
       **每次调用 +1 天**（v1.1.62 引入）⇒ 4 天内漂到 2027-01-29；
     · 水位线漂到未来后，`daily_rows` 的实时尾段起点 = 水位线 > now
       ⇒ 昨天/今天的点被整段跳过 ⇒ kwh=null。

   所以本脚本现在**反过来**证明：
     **不发生水位线漂移时，断电本身根本不会让 11/22 变成 null。**
   水位线漂移的复现见 `repro_watermark_drift.py`；
   真实实机数据的端到端验证见 `verify_real_db_recovery.py`。

用法：python3 verify/repro_power_gap.py
"""
import os
import sqlite3
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'app', 'ui'))

import storage_collect as sc          # noqa: E402
import history_collect as hc           # noqa: E402


def ts(s):
    """'2026-11-19 08:00:00' → epoch"""
    return time.mktime(time.strptime(s, '%Y-%m-%d %H:%M:%S'))


class Checker(object):
    def __init__(self):
        self.n = 0
        self.bad = 0

    def check(self, name, cond, detail=''):
        self.n += 1
        ok = bool(cond)
        if not ok:
            self.bad += 1
        print('  %s %s%s' % ('OK  ' if ok else 'FAIL', name,
                             ('  → ' + detail) if detail else ''))
        return ok


def main():
    print('=== 复现：NAS 断电场景下的用电数据（反证断电不是根因） ===\n')

    tmp = tempfile.mkdtemp(prefix='fnrepro_')
    db = os.path.join(tmp, 'storage.db')

    store = sc.HistoryStore(db)

    # ---- 构造 metrics：11/16 00:00 ~ 11/22 23:35 每 60s 一个 power 点 ----
    # 功率取 120W（稳态），断电前的最后一点在 23:42
    rows = []
    step = 60
    t = ts('2026-11-16 00:00:00')
    end = ts('2026-11-22 23:35:00')
    while t <= end:
        rows.append((t, 'power', 120.0))
        t += step
    print('构造 metrics 点数: %d（11/16 00:00 ~ 11/22 23:35，每 60s）' % len(rows))

    ok = store.write(rows)
    print('写入结果: %s\n' % ok)

    # ---- 模拟「NAS 在 11/23 恢复后继续采集」----
    now_rows = []
    t = ts('2026-11-23 06:00:00')
    end2 = ts('2026-11-23 14:40:00')
    while t <= end2:
        now_rows.append((t, 'power', 120.0))
        t += step
    store.write(now_rows)
    print('追加 11/23 06:00 ~ 14:40 点数: %d\n' % len(now_rows))

    # ---- 走真实归档流程 ----
    print('--- 调用 archive_daily(cutoff)，cutoff = now - 7 天 ---')
    cutoff = time.time() - sc.HISTORY_RETENTION_DAYS * 86400
    n = store.archive_daily(cutoff)
    print('archive_daily 写入 %d 条\n' % n)

    # ---- 用真实 daily_rows + daily_energy 出数 ----
    #   注意 daily_energy 用的是模块级 _store()，这里直接复用 HistoryStore
    hc._STORE = store
    res = hc.daily_energy(0.5224, max_days=7, eff_rate=0.5224)
    days = res.get('days') or []

    print('--- /api/power/daily 近 7 天输出 ---')
    print('  %-12s %-10s %-8s %-10s' % ('day', 'kwh', 'has_data', 'power_h'))
    for d in days:
        print('  %-12s %-10s %-8s %-10s' % (
            d['day'], d['kwh'], d['has_data'], d['power_h']))
    print()

    by_day = {d['day']: d for d in days}

    ck = Checker()

    # 11/19 ~ 11/21 应有完整数据
    for day in ('2026-11-19', '2026-11-20', '2026-11-21'):
        d = by_day.get(day) or {}
        ck.check('%s 有完整 kwh（>1.0）' % day,
                 d.get('kwh') is not None and d['kwh'] > 1.0,
                 'kwh=%s' % d.get('kwh'))

    # 11/22：断电当天，但**数据采集其实完整**（1416 个点）——
    #        断电本身**不会**让它变成 null。
    #        真正让它变 null 的是归档水位线漂到未来（见 repro_watermark_drift.py）。
    #        本脚本的场景里水位线正常（停在 11-17），所以 11/22 反而有完整读数。
    d22 = by_day.get('2026-11-22') or {}
    ck.check('2026-11-22 断电当天仍有 kwh（断电本身不导致 null）',
             d22.get('kwh') is not None and d22['kwh'] > 2.0,
             'kwh=%s has_data=%s' % (d22.get('kwh'), d22.get('has_data')))

    # 今天：只有 06:00 之后的实时尾段 → kwh 很小
    d23 = by_day.get('2026-11-23') or {}
    ck.check('2026-11-23 仅有实时尾段（kwh 明显小于整日）',
             d23.get('kwh') is not None and d23['kwh'] < 2.0,
             'kwh=%s' % d23.get('kwh'))

    # 关键：11/22 归档后是否真的丢了
    conn = sqlite3.connect(db)
    try:
        rows22 = conn.execute(
            "SELECT metric, value FROM daily_summary WHERE day='2026-11-22'"
        ).fetchall()
        nrows22 = conn.execute(
            "SELECT COUNT(*) FROM metrics WHERE ts>=? AND ts<?",
            (ts('2026-11-22 00:00:00'), ts('2026-11-23 00:00:00'))).fetchone()[0]
        wm = conn.execute(
            "SELECT value FROM meta WHERE key='daily_archived_until'").fetchone()
    finally:
        conn.close()

    print()
    print('--- 数据库实况 ---')
    print('  daily_summary 中 2026-11-22 的条目: %s' % (
        rows22 if rows22 else '（无）'))
    print('  metrics 中 11/22 的原始点数: %d' % nrows22)
    print('  归档水位线: %s' % (wm[0] if wm else None))
    if wm:
        print('  水位线对应时间: %s' % time.strftime(
            '%Y-%m-%d %H:%M:%S', time.localtime(float(wm[0]))))

    print()
    print('=== 结论 ===')
    print('  断电当天（11/22）的 kwh = %s —— **有数据**。' % d22.get('kwh'))
    print('  ⇒ 断电本身不会让用电数据变成 null。')
    print('  ⇒ 老板截图里 11/22 的 null，根因是归档水位线漂移到未来')
    print('     （archive_daily 每次调用 +1 天，v1.1.62 引入）。')
    print('  详见 repro_watermark_drift.py（漂移复现）')
    print('        与 verify_real_db_recovery.py（真实实机数据端到端验证）。')
    print()
    print('=== 结果: %d/%d ===' % (ck.n - ck.bad, ck.n))
    return 0 if ck.bad == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
