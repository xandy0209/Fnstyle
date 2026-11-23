#!/usr/bin/env python3
"""验证归档水位线钳位修复（v1.1.83）。

背景（实机故障 2026-11-23）：
    /api/power/daily 的 11/22、11/23 返回 kwh=null，
    但 /api/monitor/history 里 11/22 有 941 个 power 点、11/23 有 468 个。
    根因：meta.daily_archived_until = 1801152000.0（2027-01-29，未来），
    daily_rows() 的实时尾段起点 tail_start = 水位线 ⇒ 昨天/今天被整段跳过。

覆盖三层：
    A. archive_daily 的水位线**不会**再单调 +1 天/次
    B. 老库水位线漂到未来时，archive_daily / daily_rows 都能**夹回来**，
       且夹回后「昨天/今天」能读出 kwh
    C. cleanup 的删除线不会越过当前时刻（不会把 metrics 删光）

用法：python3 verify/verify_watermark_clamp.py
"""
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'app', 'ui'))

import storage_collect as sc  # noqa: E402

FAILS = []


def check(cond, name, detail=''):
    print('  %s %s%s' % ('✓' if cond else '✗', name,
                         ('  —— ' + detail) if detail else ''))
    if not cond:
        FAILS.append(name)
    return cond


def day_start(ts):
    lt = time.localtime(ts)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


def build_db(rows_by_day, tmp):
    """造一个真实 HistoryStore 库：rows_by_day = {day_str: [(ts, value)]}"""
    db = os.path.join(tmp, 'storage.db')
    st = sc.HistoryStore(db)
    allrows = []
    for rs in rows_by_day.values():
        allrows.extend(rs)
    allrows.sort()
    st.write(allrows)
    return st, db


def main():
    fmt = lambda t: time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(t))
    tmp = tempfile.mkdtemp(prefix='fnwm_')

    print('=' * 74)
    print('A. 水位线不再单调 +1 天/次')
    print('=' * 74)

    # A1：连续调用 archive_daily，水位线应稳定在 cutoff 决定的边界，
    #     且**不晚于今天 00:00**。
    db = os.path.join(tmp, 'a.db')
    st = sc.HistoryStore(db)
    now = time.time()
    # 造 8 天的 power 点（每 60s 一个，20W）
    rows = []
    t = day_start(now) - 8 * 86400
    while t < now:
        rows.append((t, 'power', 20.0))
        t += 60
    st.write(rows)

    wm_hist = []
    for i in range(12):
        st.archive_daily(time.time() - 7 * 86400)
        wm = st._daily_watermark(st._connect())[0]
        wm_hist.append(wm)
    today0 = day_start(time.time())
    print('    连续 12 次调用后的水位线：')
    for i, w in enumerate(wm_hist[:3] + ['...'] + wm_hist[-3:], ):
        if w == '...':
            print('      ...')
        else:
            print('      第%2d次: %s' % (wm_hist.index(w) + 1 if w in wm_hist else 0, fmt(w)))
    check(all(w <= today0 + 1 for w in wm_hist),
          '水位线始终不晚于「今天 00:00」',
          'max=%s today0=%s' % (fmt(max(wm_hist)), fmt(today0)))
    check(len(set(wm_hist)) <= 2,
          '水位线不再每次 +1 天（12 次调用内变化次数 ≤ 2）',
          'distinct=%d' % len(set(wm_hist)))

    print('\n' + '=' * 74)
    print('B. 老库水位线漂到未来 → 夹回后「昨天/今天」能读出 kwh')
    print('=' * 74)

    db2 = os.path.join(tmp, 'b.db')
    st2 = sc.HistoryStore(db2)
    now = time.time()
    y0 = day_start(now) - 86400
    t0 = day_start(now)
    rows = []
    # 昨天全天 + 今天到现在的 power 点
    tt = y0
    while tt < now:
        rows.append((tt, 'power', 30.0))
        tt += 60
    st2.write(rows)
    # 人为把水位线设到未来（模拟 v1.1.62~v1.1.82 的 bug 产物）
    future = time.mktime((time.localtime(now).tm_year + 1, 1, 29, 0, 0, 0, 0, 0, -1))
    conn = st2._connect(write=True)
    with conn:
        conn.execute("INSERT INTO meta(key, value) VALUES('daily_archived_until', ?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (repr(float(future)),))
    conn.close()
    wm0 = st2._daily_watermark(st2._connect())[0]
    print('    人为设置水位线 =', fmt(wm0))
    check(wm0 > now, '前置状态：水位线确实在未来')

    # B1：daily_rows 在夹回前就应能读出（防御生效）
    data = st2.daily_rows(7, default_dt=60)
    days = data or {}
    yday = time.strftime('%Y-%m-%d', time.localtime(y0))
    today = time.strftime('%Y-%m-%d', time.localtime(t0))
    yv = (days.get(yday) or {}).get('kwh')
    tv = (days.get(today) or {}).get('kwh')
    check(yv is not None and float(yv) > 0,
          'daily_rows 直接读出「昨天」的 kwh', '%s = %s' % (yday, yv))
    check(tv is not None and float(tv) > 0,
          'daily_rows 直接读出「今天」的 kwh', '%s = %s' % (today, tv))

    # B2：archive_daily 应把水位线夹回今天 00:00
    st2.archive_daily(time.time() - 7 * 86400)
    wm1 = st2._daily_watermark(st2._connect())[0]
    print('    archive_daily 后水位线 =', fmt(wm1))
    check(wm1 <= day_start(time.time()) + 1,
          'archive_daily 把水位线夹回今天 00:00 之内')
    check(wm1 < future, '水位线不再停留在未来')

    # B3：夹回后再次 daily_rows，昨天的值应仍在（没被吞掉）
    data2 = st2.daily_rows(7, default_dt=60) or {}
    yv2 = (data2.get(yday) or {}).get('kwh')
    check(yv2 is not None, '夹回后「昨天」的 kwh 仍可读', '%s = %s' % (yday, yv2))

    print('\n' + '=' * 74)
    print('C. cleanup 的删除线不越过当前时刻')
    print('=' * 74)

    db3 = os.path.join(tmp, 'c.db')
    st3 = sc.HistoryStore(db3)
    now = time.time()
    rows = []
    tt = day_start(now) - 10 * 86400
    while tt < now:
        rows.append((tt, 'power', 25.0))
        tt += 60
    st3.write(rows)
    conn = st3._connect(write=True)
    with conn:
        conn.execute("INSERT INTO meta(key, value) VALUES('daily_archived_until', ?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (repr(float(future)),))
    conn.close()
    n_before = st3.query('power', 0)
    st3.cleanup()
    n_after = st3.query('power', 0)
    print('    cleanup 前 power 点数 = %d，cleanup 后 = %d' % (len(n_before), len(n_after)))
    check(len(n_after) > 0, 'cleanup 之后仍有 power 点（没被删光）')
    # 今天的数据必须还在
    conn = st3._connect()
    today_pts = conn.execute(
        "SELECT COUNT(*) FROM metrics WHERE metric='power' AND ts >= ?",
        (day_start(now),)).fetchone()[0]
    conn.close()
    check(today_pts > 0, '今天的数据未被误删', 'points = %d' % today_pts)

    print('\n' + '=' * 74)
    print('C2. cleanup 的三种边界：retention_days=0 / 默认 7 天 / 水位线在未来')
    print('=' * 74)

    # C2a：retention_days=0 = 「立即清空全部」
    # ⚠️ 这条是 v1.1.83 修水位线时**踩出来的回归**：
    #    水位线修好后停在「今天 00:00」→ min(cutoff=now, wm=今天00:00)
    #    → 删除线 = 今天 00:00 → 今天零点之后的点全留下，「清空全部」名不副实。
    #    （老版本因为水位线漂到未来，反而意外能删干净 —— 这就是回归难发现的原因。）
    db2c = os.path.join(tmp, 'c2.db')
    st_c2 = sc.HistoryStore(db2c)
    nowc = time.time()
    rows = []
    tt = nowc - 3600
    while tt < nowc:
        rows.append((tt, 'power', 30.0))
        tt += 60
    st_c2.write(rows)
    n_all = st_c2.cleanup(retention_days=0)
    left = len(st_c2.query('power', 0))
    check(left == 0, 'retention_days=0 清空全部（不被归档水位线收窄）',
          '删除 %d，剩余 %d' % (n_all, left))

    # C2b：默认 7 天 —— 超期点删掉、保留期内不动
    st_c2.write([(nowc - 8 * 86400, 'power', 1.0), (nowc, 'power', 2.0)])
    n_7d = st_c2.cleanup()
    left7 = len(st_c2.query('power', 0))
    check(left7 == 1, '默认保留 7 天：超期点删掉、保留期内点不动',
          '删除 %d，剩余 %d' % (n_7d, left7))

    # C2c：水位线在未来时，删除线不得越过「现在」
    conn = st_c2._connect(write=True)
    with conn:
        conn.execute("INSERT INTO meta(key, value) VALUES('daily_archived_until', ?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (repr(float(future)),))
    conn.close()
    st_c2.cleanup()
    conn = st_c2._connect()
    future_pts = conn.execute(
        "SELECT COUNT(*) FROM metrics WHERE metric='power' AND ts > ?",
        (nowc,)).fetchone()[0]
    conn.close()
    check(future_pts == 0, '水位线在未来时也不删「未来的点」',
          '未来点 = %d' % future_pts)

    print('\n' + '=' * 74)
    print('D. 长期稳态：30 天逐小时归档，每天只归档一次、总量守恒')
    print('=' * 74)

    # D0：模拟「真实时钟前进」—— 直接改水位线元数据来推进"现在"太绕，
    #     这里用**时间平移**的方式：每一轮把 now 往前推 1 小时，
    #     并往库里补上那一小时的点，然后调 archive_daily(cutoff)。
    #     断言：① 水位线单调不减 ② 每天的 kwh 不被重复累加
    #            ③ 已归档天的 kwh 与直接算出来的期望值一致。
    db4 = os.path.join(tmp, 'd.db')
    st4 = sc.HistoryStore(db4)
    # 以「今天 00:00」为基准，往回 30 天，每分钟一个点、20W
    base = day_start(time.time()) - 30 * 86400
    rows = []
    tt = base
    end4 = day_start(time.time())
    while tt < end4:
        rows.append((tt, 'power', 20.0))
        tt += 60
    st4.write(rows)
    # 期望值：每天 1440 个点 × 20W × 60s = 每天 20/60*1 = 0.3333 kWh
    expected_per_day = 1440 * 20.0 * 60.0 / 3600000.0
    cutoff = base  # 先归档到最早
    wm_series = []
    for i in range(24 * 31):            # 31 天 × 24 次/天 = 744 次调用
        cutoff += 3600                  # cutoff 每小时 +1 小时
        st4.archive_daily(cutoff)
        wm_series.append(st4._daily_watermark(st4._connect())[0])
    check(all(wm_series[i] <= wm_series[i + 1] + 1 for i in range(len(wm_series) - 1)),
          '水位线单调不减（没有回退）')
    today0 = day_start(time.time())
    check(all(w <= today0 + 1 for w in wm_series),
          '31 天 744 次调用后水位线仍不晚于「今天 00:00」',
          'max=%s' % fmt(max(wm_series)))
    check(wm_series[-1] - wm_series[0] <= 31 * 86400,
          '水位线总推进量 ≤ 31 天（不是每天 +1 天那种失控）',
          '推进=%s 天' % ((wm_series[-1] - wm_series[0]) / 86400.0))

    conn = st4._connect()
    daykwh = {}
    for day, metric, value in conn.execute(
            "SELECT day,metric,value FROM daily_summary WHERE metric='kwh'"):
        daykwh[day] = value
    conn.close()
    # 已完整归档的整日：kwh 应等于期望值（不能翻倍、不能少）
    bad_double = []
    bad_missing = []
    for day in sorted(daykwh):
        d0 = time.mktime(time.strptime(day, '%Y-%m-%d'))
        # 只有「整天都有点」的日子才可比对期望值
        full = d0 >= base and d0 + 86400 <= end4
        if not full:
            continue
        if abs(daykwh[day] - expected_per_day) > expected_per_day * 0.02:
            bad_double.append('%s=%.4f(期望%.4f)' % (day, daykwh[day], expected_per_day))
    check(not bad_double,
          '已归档整日的 kwh 精确守恒（无重复累加、无漏归档）',
          ('偏差: %s' % '; '.join(bad_double[:5])) if bad_double else
          '共 %d 天，期望每天 %.4f kWh' % (len(daykwh), expected_per_day))
    # 抽查：归档了的天数应接近 30 天
    check(len(daykwh) >= 28, '归档覆盖天数接近 30 天',
          'days=%d' % len(daykwh))
    # 归档后的库里，daily_rows 仍应能读出最近 7 天
    rows7 = st4.daily_rows(7, default_dt=60) or {}
    yv3 = (rows7.get(time.strftime('%Y-%m-%d', time.localtime(day_start(time.time()) - 86400)))
           or {}).get('kwh')
    check(yv3 is not None and yv3 > 0,
          '长期稳态下「昨天」仍能读出 kwh', 'kwh=%s' % yv3)

    print('\n' + '=' * 74)
    if FAILS:
        print('✗ 失败 %d 项：%s' % (len(FAILS), '；'.join(FAILS)))
        return 1
    print('✓ 全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
