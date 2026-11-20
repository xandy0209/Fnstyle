#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.1.53：每日用电聚合与月/年推算的单元测试

用假的历史点验证 daily_energy / avg_power / energy_stats 的口径，
对齐 fnmonitor 的 monthProj / yearProj 公式。

⚠️ v1.1.53：daily_energy 的数据来源从「直接扫 metrics」改成
   「daily_summary 归档表 + 水位线后的实时尾段」（HistoryStore.daily_rows），
   所以本测试不再 mock query_history，而是**直接建一个临时库**、
   写入假点、跑归档，再验证整条链路 —— 这样才能真正测到归档机制。
"""
import os
import sys
import time
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI = os.path.join(BASE, 'app', 'ui')
sys.path.insert(0, UI)

import storage_collect as sc  # noqa: E402
import history_collect as hc  # noqa: E402

PASS = 0
FAIL = 0


def check(label, ok, extra=''):
    global PASS, FAIL
    if ok:
        PASS += 1
        print('  ✓ %s' % label)
    else:
        FAIL += 1
        print('  ✗ %s%s' % (label, ('  → %s' % extra) if extra else ''))


def build_store(points_per_day=48, days=3, watts=30.0):
    """建一个临时历史库，写入 days 天、恒定 watts 的功率点。

    points_per_day=48 → 每 30 分钟一个点（间隔 1800s）。
    返回 (HistoryStore, 写入的点数)。
    """
    tmpdir = tempfile.mkdtemp(prefix='fnst_power_')
    db = os.path.join(tmpdir, 'storage.db')
    store = sc.HistoryStore(db)
    now = time.time()
    lt = time.localtime(now - (days - 1) * 86400)
    start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    step = 86400.0 / points_per_day
    rows = []
    t = start
    while t < now:
        rows.append((t, 'power', watts))
        t += step
    store.write(rows)
    return store, len(rows)


def main():
    print('\n=== 归档机制（archive_daily / daily_rows）===')
    store, npts = build_store()
    check('写入功率点成功', npts > 0, '%d 点' % npts)

    n = store.archive_daily(time.time())
    check('archive_daily 有归档写入', n > 0, '%d 条' % n)

    conn = store._connect()
    try:
        tabs = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        check('建了 daily_summary 表', 'daily_summary' in tabs, str(tabs))
        check('建了 meta 表', 'meta' in tabs, str(tabs))
        wm = conn.execute(
            "SELECT value FROM meta WHERE key='daily_archived_until'").fetchone()
        check('meta 里有 daily_archived_until 水位线', wm is not None)
        days_in_arc = [r[0] for r in conn.execute(
            "SELECT DISTINCT day FROM daily_summary")]
        check('归档表里有多天记录', len(days_in_arc) >= 2, str(days_in_arc))
    finally:
        conn.close()

    dr = store.daily_rows(30, default_dt=1800)
    check('daily_rows 返回字典', isinstance(dr, dict))
    check('daily_rows 带 _meta 水位线', '_meta' in dr)

    print('\n=== 每日用电聚合（daily_energy）===')
    # 把临时库挂到 history_collect
    hc._STORE = store
    d = hc.daily_energy(0.6, 30)
    check('返回 success', d.get('success') is True)
    check('返回 days 列表', isinstance(d.get('days'), list))
    check('days 恒为请求天数（30）', len(d['days']) == 30, '实际 %d' % len(d['days']))

    # 无数据的天：kwh=None（对齐 fnmonitor，不是 0）
    nodata = [x for x in d['days'] if not x.get('has_data')]
    check('存在无数据的天', len(nodata) > 0, '%d 天' % len(nodata))
    if nodata:
        x = nodata[0]
        check('无数据天 kwh=None（不是 0）', x['kwh'] is None, str(x['kwh']))
        check('无数据天 cost=None', x['cost'] is None, str(x['cost']))
        check('无数据天 power_h=None', x['power_h'] is None, str(x['power_h']))
        check('无数据天 avg_w=None', x['avg_w'] is None, str(x['avg_w']))

    # 恒定 30W 跑满一整天 = 30W × 24h / 1000 = 0.72 kWh
    full = [x for x in d['days']
            if x.get('power_h') is not None and x['power_h'] > 23]
    check('存在采集满整日的天', len(full) > 0,
          'power_h: %s' % [x.get('power_h') for x in d['days']])
    if full:
        x = full[0]
        check('整日用电 ≈ 0.72 kWh（30W × 24h）',
              abs(x['kwh'] - 0.72) < 0.05, '实际 %.4f' % x['kwh'])
        check('整日采集小时数 ≈ 24h',
              abs(x['power_h'] - 24) < 1.0, '实际 %.2f' % x['power_h'])
        check('电费 = kWh × 0.6',
              abs(x['cost'] - round(x['kwh'] * 0.6, 2)) < 0.011,
              '实际 %s' % x['cost'])
        check('avg_w ≈ 30W',
              x['avg_w'] is not None and abs(x['avg_w'] - 30.0) < 1.0,
              '实际 %s' % x['avg_w'])

    # 日期升序
    keys = [x['day'] for x in d['days']]
    check('日期按升序排列', keys == sorted(keys))
    check('日期格式 YYYY-MM-DD',
          all(len(k) == 10 and k[4] == '-' and k[7] == '-' for k in keys))

    # max_days 截断
    d2 = hc.daily_energy(0.6, 2)
    check('maxDays 生效（截断到 2 天）', len(d2['days']) == 2,
          '实际 %d' % len(d2['days']))

    print('\n=== 平均功率（avg_power）===')
    ap = hc.avg_power()
    check('返回平均功率', ap is not None)
    if ap:
        check('平均功率 ≈ 30W', abs(ap['w'] - 30.0) < 1.0, '实际 %.2f' % ap['w'])
        check('给出口径说明', bool(ap.get('basis')), str(ap.get('basis')))

    print('\n=== 本月预计 / 全年估算（energy_stats）===')
    s = hc.energy_stats(0.6, 3600)
    check('返回 month_proj_kwh', 'month_proj_kwh' in s)
    check('返回 year_proj_kwh', 'year_proj_kwh' in s)
    check('返回 avg_w', 'avg_w' in s)
    check('返回 avg_basis', 'avg_basis' in s)

    if s.get('avg_w'):
        w = s['avg_w']
        # 全年估算 = 平均功率 × 8.76（8760 小时 / 1000）
        expect_year = round(w * 8.76, 1)
        check('全年估算 = 平均功率 × 8.76',
              abs((s.get('year_proj_kwh') or 0) - expect_year) < 0.6,
              '期望 %.1f 实际 %s' % (expect_year, s.get('year_proj_kwh')))
        check('全年估算为正', (s.get('year_proj_kwh') or 0) > 0)
    else:
        check('avg_w 有值', False, 'avg_w 为空')

    # 本月预计应 ≥ 本月已用
    if s.get('month_proj_kwh') is not None:
        check('本月预计 ≥ 本月已用',
              s['month_proj_kwh'] >= s['month_kwh'],
              '%.4f vs %.4f' % (s['month_proj_kwh'], s['month_kwh']))

        # ⚠️ v1.1.55：单位 bug 回归断言。
        #   修复前 month_proj_kwh ≈ month_kwh（因为秒差却除以毫秒口径的
        #   3.6e9 → 结果小了 1000 倍），导致「本月预计电费」几乎等于
        #   「本月已用电费」，界面上三个电费看起来互相矛盾。
        #   现在必须满足：month_proj_kwh ≥ month_kwh + 平均功率 × 剩余时间。
        import datetime
        now_dt = datetime.datetime.now()
        if now_dt.month == 12:
            end_dt = datetime.datetime(now_dt.year + 1, 1, 1)
        else:
            end_dt = datetime.datetime(now_dt.year, now_dt.month + 1, 1)
        remain_secs = max(0.0, (end_dt - now_dt).total_seconds())
        expect_min = s['month_kwh'] + s['avg_w'] * remain_secs / 3.6e6
        check('本月预计含剩余时间的用电量（单位已修正为秒口径 3.6e6）',
              s['month_proj_kwh'] >= expect_min * 0.95,
              '期望 ≥ %.4f 实际 %.4f（剩余 %.1f 小时）'
              % (expect_min, s['month_proj_kwh'], remain_secs / 3600.0))
        check('本月预计显著大于本月已用（修复前两者几乎相等）',
              s['month_proj_kwh'] > s['month_kwh'] * 1.2,
              '%.4f vs %.4f' % (s['month_proj_kwh'], s['month_kwh']))

        # 三处电费同源：month_proj_yuan 必须由 month_proj_kwh 推出
        if s.get('month_proj_yuan') is not None:
            check('本月预计电费 = 本月预计用电量 × 有效电价',
                  abs(s['month_proj_yuan'] -
                      s['month_proj_kwh'] * s.get('eff_rate_yuan_per_kwh', 0.6)) < 0.02,
                  '%.2f vs %.4f' % (s['month_proj_yuan'],
                                    s['month_proj_kwh'] *
                                    s.get('eff_rate_yuan_per_kwh', 0.6)))
            check('本月预计电费 ≠ 本月已用电费（修复前两者几乎相等）',
                  abs(s['month_proj_yuan'] - s['month_yuan']) > 0.5,
                  '预计 %.2f vs 已用 %.2f' % (s['month_proj_yuan'], s['month_yuan']))
    else:
        check('本月预计有值', False, 'month_proj_kwh 为空（本月已用为 0 时不推算）')

    print('\n=== cleanup 先归档再删（真实稳态）===')
    # 造一个「10 天历史」的库：cleanup(7) 之后，7 天前的点会被删，
    # 但必须已经全部落进 daily_summary —— 这是归档机制存在的意义。
    store2, n2 = build_store(points_per_day=48, days=10, watts=30.0)
    check('10 天数据写入成功', n2 > 0, '%d 点' % n2)
    conn = store2._connect()
    try:
        n_before = conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
    finally:
        conn.close()
    store2.cleanup(7)
    conn = store2._connect()
    try:
        n_after = conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
        arc = dict((r[0], r[1]) for r in conn.execute(
            "SELECT day, value FROM daily_summary WHERE metric='kwh'"))
        wm = conn.execute(
            "SELECT value FROM meta WHERE key='daily_archived_until'").fetchone()
    finally:
        conn.close()
    check('超出保留期的原始点被删除', n_after < n_before,
          '%d → %d' % (n_before, n_after))
    check('被删掉那几天的数据没丢（进了归档表）',
          len(arc) >= 3, str(sorted(arc.keys())))
    check('归档里的整日电量 ≈ 0.72 kWh（30W × 24h）',
          any(abs(v - 0.72) < 0.05 for v in arc.values()), str(arc))
    check('归档里还有非整日的天（半天 ≈ 0.25~0.36 kWh）',
          any(0.1 < v < 0.7 for v in arc.values()), str(arc))
    check('水位线推进到保留期边界', wm is not None, str(wm))

    print('\n=== 空数据兜底 ===')
    empty = sc.HistoryStore(os.path.join(tempfile.mkdtemp(), 'empty.db'))
    hc._STORE = empty
    d3 = hc.daily_energy(0.6, 30)
    check('无历史时 days 仍为 30 天（全 None）',
          len(d3.get('days')) == 30, '实际 %d' % len(d3.get('days', [])))
    check('无历史时每天 kwh 都是 None',
          all(x['kwh'] is None for x in d3.get('days', [])))
    check('无历史时 success 仍为 True', d3.get('success') is True)
    ap2 = hc.avg_power()
    check('无历史时 avg_power 返回 None', ap2 is None)
    s2 = hc.energy_stats(0.6, None)
    check('无历史时各值不报错', s2.get('success') is None and
          s2.get('today_kwh') == 0.0)

    hc._STORE = None

    print('\n' + '=' * 56)
    print('通过 %d 项，失败 %d 项' % (PASS, FAIL))
    print('=' * 56)
    if FAIL:
        print('❌ 每日用电聚合测试未全部通过')
        return 1
    print('🎉 每日用电聚合测试全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
