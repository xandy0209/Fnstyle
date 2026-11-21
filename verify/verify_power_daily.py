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


def build_legacy_store(points_per_day=48, days=12, watts=30.0):
    """构造一个**真实的 v1.1.61 老库**（升级前的典型状态），返回 (store, cutoff)。

    复现三个 v1.1.61 的真实特征：
      1. 归档水位线 daily_archived_until 落在**一天中间**（= cutoff 时刻），
         不是自然日 00:00 —— 这是 v1.1.61 的归档 bug（按任意时刻切区间）。
      2. daily_summary 里水位线那一天是**残缺半天**（只归档了 00:00–cutoff，
         小时桶不满 24 个）。
      3. 原始点已按 `ts < cutoff` 删掉 —— cutoff 之前的点**永久没了**，
         剩下的 [cutoff, ...) 还在库里。

    v1.1.62 的自然日对齐 + `_UPSERT_DAILY_ADD` 累加应当能把这条残缺日
    **自动补齐**为完整日（这正是本次修复要保住的升级路径）。
    """
    tmpdir = tempfile.mkdtemp(prefix='fnst_legacy_')
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

    # cutoff = now - 7 天（v1.1.61 的保留期删除线），落在一天中间。
    # 万一 now 恰好在日界附近，就把 cutoff 挪到当天 13:00，保证是「半天」。
    cutoff = now - 7 * 86400
    lt_cut = time.localtime(cutoff)
    if lt_cut.tm_hour < 1:
        cutoff = store._day_start(cutoff) + 13 * 3600

    # 模拟 v1.1.61 的 archive_daily(cutoff)：归档 [day_start(now-400d), cutoff)
    conn = store._connect(write=True)
    try:
        wm0 = store._day_start(now - 400 * 86400)
        agg = store._daily_aggregate(conn, wm0, cutoff, 60)
        for (day, k), v in agg.items():
            conn.execute(store._UPSERT_DAILY_MAX if k in store._DAILY_MAX_KEYS
                         else store._UPSERT_DAILY_ADD, (day, k, v))
        conn.execute("INSERT INTO meta(key, value) VALUES('daily_archived_until', ?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (repr(cutoff),))
        conn.commit()
        # 模拟 v1.1.61 的 cleanup：删 ts < cutoff
        conn.execute("DELETE FROM metrics WHERE ts < ?", (cutoff,))
        conn.commit()
    finally:
        conn.close()
    return store, cutoff


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
            # ⚠️ v1.1.62：预计电费的单价改用 **proj_rate_yuan_per_kwh**
            #   （老板决定：不按阶梯算、不区分地区，默认成都最低档
            #     0.5224，用户可手动调整），
            #   不再用 eff_rate_yuan_per_kwh（本月实际均价）倒推。
            pr = s.get('proj_rate_yuan_per_kwh', 0.5224)
            check('本月预计电费 = 本月预计用电量 × 预计电费单价',
                  abs(s['month_proj_yuan'] -
                      s['month_proj_kwh'] * pr) < 0.02,
                  '%.2f vs %.4f' % (s['month_proj_yuan'],
                                    s['month_proj_kwh'] * pr))
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
    # ⚠️ v1.1.58 修正断言：归档只收**完整日**（跨零点才结算一天），
    #   所以归档表里不应该出现半天数据 —— 之前这条断言写成
    #   「应该存在非整日的天」，与实现语义相反，一直是失败的。
    #   这里改成正向断言：归档里的每一条都应是整日电量。
    check('归档里没有半天数据（归档只结算完整日）',
          all(abs(v - 0.72) < 0.05 for v in arc.values()), str(arc))
    check('水位线推进到保留期边界', wm is not None, str(wm))

    # ⚠️ v1.1.62：归档必须按**完整自然日**推进（新增断言，防回归）
    #   1) 水位线必须落在自然日 00:00（不是一天中间的任意时刻）
    #   2) 归档表里每个自然日要么有全部 24 个 kwh@HH 小时桶，要么完全没有
    #   3) 连续多轮 cleanup 后不出现「同一天被累加超过整日」的超量
    wmv = eval(wm[0])
    lt_wm = time.localtime(wmv)
    check('归档水位线对齐自然日 00:00',
          lt_wm.tm_hour == 0 and lt_wm.tm_min == 0 and lt_wm.tm_sec == 0,
          time.strftime('%Y-%m-%d %H:%M:%S', lt_wm))

    conn = store2._connect()
    try:
        buckets = {}
        for _d, _m, _v in conn.execute(
                "SELECT day, metric, value FROM daily_summary WHERE metric LIKE 'kwh@%'"):
            buckets.setdefault(_d, set()).add(_m)
    finally:
        conn.close()
    check('归档表里每个自然日的小时桶要么 0 个要么 24 个（无残缺半天）',
          all(len(s) in (0, 24) for s in buckets.values()),
          str({d: len(s) for d, s in buckets.items()}))

    # 多轮推进：模拟真实运行里每小时一次 cleanup
    store3, _n3 = build_store(points_per_day=48, days=10, watts=30.0)
    for _i in range(5):
        store3.cleanup(7)
    conn = store3._connect()
    try:
        arc3 = dict((r[0], r[1]) for r in conn.execute(
            "SELECT day, value FROM daily_summary WHERE metric='kwh'"))
        wm3 = conn.execute(
            "SELECT value FROM meta WHERE key='daily_archived_until'").fetchone()
    finally:
        conn.close()
    check('多轮 cleanup 后归档值不超量（不因重复归档而累加）',
          all(v <= 0.76 for v in arc3.values()), str(arc3))
    lt3 = time.localtime(eval(wm3[0]))
    check('多轮 cleanup 后水位线仍对齐自然日 00:00',
          lt3.tm_hour == 0 and lt3.tm_min == 0 and lt3.tm_sec == 0,
          time.strftime('%Y-%m-%d %H:%M:%S', lt3))
    # 关键不变量：删掉的原始点必须都已经进归档（归档线 ≤ 删除线）
    check('多轮 cleanup 后归档日都是完整日',
          all(abs(v - 0.72) < 0.05 for v in arc3.values()), str(arc3))

    # ⚠️ v1.1.62 新增：删除线收紧到 min(cutoff, 水位线) 后，
    #   必须确认「数据清理不会失效」—— 原始点数量要随时间推进而减少，
    #   不能因为删除线卡在水位线上而永远不删（存储无限增长）。
    store4, _n4 = build_store(points_per_day=48, days=20, watts=30.0)
    _orig_time = time.time
    counts = []
    for _i in range(4):
        _off = _i * 86400
        time.time = lambda: _orig_time() + _off
        try:
            store4.cleanup(7)
        finally:
            time.time = _orig_time
        _c = store4._connect()
        try:
            counts.append(_c.execute("SELECT COUNT(*) FROM metrics").fetchone()[0])
        finally:
            _c.close()
    check('原始点数量随时间推进而减少（删除线没卡死）',
          counts[-1] < counts[0], str(counts))
    check('原始点数量收敛到保留期附近（不无限增长）',
          counts[-1] <= counts[0] * 0.75, str(counts))

    # ⚠️ v1.1.62 新增：**老库升级自愈**回归。
    #   真实 v1.1.61 老库的水位线落在一天中间，归档表里那一天是残缺半天。
    #   升级到 v1.1.62 后必须能自动补齐 —— 这是本次修复最关键的升级路径，
    #   一旦回归，老用户升上来就会看到「某天用电只有一半」。
    print('\n=== 老库升级自愈（v1.1.61 半成品归档 → v1.1.62 完整日）===')
    store5, cut5 = build_legacy_store(points_per_day=48, days=12, watts=30.0)
    conn = store5._connect()
    try:
        legacy_kwh = dict((r[0], r[1]) for r in conn.execute(
            "SELECT day, value FROM daily_summary WHERE metric='kwh'"))
        legacy_wm = conn.execute(
            "SELECT value FROM meta WHERE key='daily_archived_until'").fetchone()
        legacy_buckets = {}
        for _d, _m in conn.execute(
                "SELECT day, metric FROM daily_summary WHERE metric LIKE 'kwh@%'"):
            legacy_buckets.setdefault(_d, set()).add(_m)
    finally:
        conn.close()
    lt_leg = time.localtime(eval(legacy_wm[0]))
    check('老库水位线确实落在一天中间（复现 v1.1.61 特征）',
          not (lt_leg.tm_hour == 0 and lt_leg.tm_min == 0 and lt_leg.tm_sec == 0),
          time.strftime('%Y-%m-%d %H:%M:%S', lt_leg))
    legacy_partial = [d for d, s in legacy_buckets.items() if len(s) not in (0, 24)]
    check('老库归档表里存在残缺半天（复现 v1.1.61 特征）',
          len(legacy_partial) > 0,
          '残缺日: %s' % legacy_partial)

    # 升级：跑几轮 cleanup（v1.1.62 的自然日对齐 + 累加补齐）
    for _i in range(3):
        store5.cleanup(7)
    conn = store5._connect()
    try:
        fixed_kwh = dict((r[0], r[1]) for r in conn.execute(
            "SELECT day, value FROM daily_summary WHERE metric='kwh'"))
        fixed_wm = conn.execute(
            "SELECT value FROM meta WHERE key='daily_archived_until'").fetchone()
        fixed_buckets = {}
        for _d, _m in conn.execute(
                "SELECT day, metric FROM daily_summary WHERE metric LIKE 'kwh@%'"):
            fixed_buckets.setdefault(_d, set()).add(_m)
    finally:
        conn.close()

    lt_fix = time.localtime(eval(fixed_wm[0]))
    check('老库升级后水位线对齐自然日 00:00',
          lt_fix.tm_hour == 0 and lt_fix.tm_min == 0 and lt_fix.tm_sec == 0,
          time.strftime('%Y-%m-%d %H:%M:%S', lt_fix))
    check('老库升级后归档表不再有残缺半天（全部 24 桶）',
          all(len(s) in (0, 24) for s in fixed_buckets.values()),
          str({d: len(s) for d, s in fixed_buckets.items()}))
    still_partial = [d for d, s in fixed_buckets.items() if len(s) not in (0, 24)]
    check('老库升级后原残缺日已补齐为完整日',
          len(still_partial) == 0 and len(fixed_buckets) > 0,
          '仍残缺: %s / 全部: %s' % (still_partial, sorted(fixed_buckets.keys())))
    check('老库升级后每个归档日都是整日电量',
          all(abs(v - 0.72) < 0.05 for v in fixed_kwh.values()), str(fixed_kwh))

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
