#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.1.43：每日用电聚合与月/年推算的单元测试

用假的历史点验证 daily_energy / avg_power / energy_stats 的口径，
对齐 fnmonitor 的 monthProj / yearProj 公式。
"""
import os
import sys
import time
import types

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI = os.path.join(BASE, 'app', 'ui')
sys.path.insert(0, UI)

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


def fake_points():
    """构造 3 天、每 30 分钟一个点的功率历史（恒定 30W）。"""
    now = time.time()
    day = 86400
    pts = []
    # 从 2 天前 00:00 开始，每 30 分钟一点
    lt = time.localtime(now - 2 * day)
    start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    t = start
    while t < now:
        pts.append([t, 30.0])
        t += 1800
    return pts


def main():
    print('\n=== 每日用电聚合（daily_energy）===')

    pts = fake_points()
    orig = hc.query_history

    hc.query_history = lambda metric, range_='1h': pts if metric == 'power' else []

    d = hc.daily_energy(0.6, 30)
    check('返回 success', d.get('success') is True)
    check('返回 days 列表', isinstance(d.get('days'), list))
    check('至少 3 天数据', len(d['days']) >= 3, '实际 %d' % len(d['days']))

    # 恒定 30W 跑满一整天 = 30W × 24h / 1000 = 0.72 kWh
    for x in d['days']:
        if x['power_h'] > 23:
            check('整日用电 ≈ 0.72 kWh（30W × 24h）',
                  abs(x['kwh'] - 0.72) < 0.05, '实际 %.4f' % x['kwh'])
            check('整日采集小时数 ≈ 24h',
                  abs(x['power_h'] - 24) < 1.0, '实际 %.2f' % x['power_h'])
            check('电费 = kWh × 0.6',
                  abs(x['cost'] - round(x['kwh'] * 0.6, 2)) < 0.011,
                  '实际 %s' % x['cost'])
            break
    else:
        check('存在采集满整日的天', False, '没有 power_h>23 的天')

    # 日期升序
    keys = [x['day'] for x in d['days']]
    check('日期按升序排列', keys == sorted(keys))
    check('日期格式 YYYY-MM-DD',
          all(len(k) == 10 and k[4] == '-' and k[7] == '-' for k in keys))

    # max_days 截断
    d2 = hc.daily_energy(0.6, 2)
    check('maxDays 生效（截断到 2 天）', len(d2['days']) <= 2,
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
    else:
        check('本月预计有值', False, 'month_proj_kwh 为空（本月已用为 0 时不推算）')

    print('\n=== 空数据兜底 ===')
    hc.query_history = lambda metric, range_='1h': []
    d3 = hc.daily_energy(0.6, 30)
    check('无历史时 days 为空列表', d3.get('days') == [])
    check('无历史时 success 仍为 True', d3.get('success') is True)
    ap2 = hc.avg_power()
    check('无历史时 avg_power 返回 None', ap2 is None)
    s2 = hc.energy_stats(0.6, None)
    check('无历史时各值不报错', s2.get('success') is None and
          s2.get('today_kwh') == 0.0)

    hc.query_history = orig

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
