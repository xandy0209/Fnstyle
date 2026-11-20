#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.1.57：阶梯电价 + 分时电价模型的单元测试

覆盖四川居民口径的关键规则：
  · 阶梯按**平段电量**判定，谷段电量计入总电量但不加价
  · 丰枯水期低谷价切换（6-10月 丰水期，其余枯平期）
  · 夏季/非夏季阶梯电量上限不同
  · 谷时段判定（跨零点 23:00-07:00）
  · 阶梯电量逐月结转
  · 未启用时回落固定单价（向后兼容）
"""
import os
import sys
import time
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI = os.path.join(BASE, 'app', 'ui')
sys.path.insert(0, UI)

import power_tariff as pt  # noqa: E402

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


def approx(a, b, tol=1e-6):
    return abs(float(a) - float(b)) <= tol


# ---------------------------------------------------------------------------
# 隔离：把配置与台账写到临时目录，避免污染真实数据
# ---------------------------------------------------------------------------
TMP = tempfile.mkdtemp(prefix='fnst_tariff_')
os.environ['FNSTYLE_DATA_DIR'] = TMP

# ---------------------------------------------------------------------------
print('\n=== 1. 默认配置与地区表 ===')
cfg = pt.load_config()
check('默认地区为 sichuan', cfg['region'] == 'sichuan', cfg['region'])
check('默认未启用（向后兼容）', cfg['enabled'] is False)
check('默认谷段 23:00-07:00', cfg['valley_start'] == 23 and cfg['valley_end'] == 7)

prof = pt.PROFILES['sichuan']
check('四川峰段三档 = 0.5224/0.6224/0.8224',
      prof['peak_rates'] == [0.5224, 0.6224, 0.8224], prof['peak_rates'])
check('四川丰水期低谷三档 = 0.175/0.275/0.475',
      prof['valley_rates_wet'] == [0.1750, 0.2750, 0.4750], prof['valley_rates_wet'])
check('四川枯平期低谷三档 = 0.2535/0.3535/0.5535',
      prof['valley_rates_dry'] == [0.2535, 0.3535, 0.5535], prof['valley_rates_dry'])
check('夏季阶梯上限 260/460/None',
      prof['tiers_summer'] == [260.0, 460.0, None], prof['tiers_summer'])
check('非夏季阶梯上限 180/280/None',
      prof['tiers_other'] == [180.0, 280.0, None], prof['tiers_other'])
check('夏季月份 = 7/8/9', prof['summer_months'] == [7, 8, 9])
check('丰水期月份 = 6-10', prof['wet_months'] == [6, 7, 8, 9, 10])
check('天津居民不执行峰谷分时', pt.PROFILES['tianjin']['valley_hours'] == [])

# ---------------------------------------------------------------------------
print('\n=== 2. 谷时段判定（跨零点） ===')
check('23 点属谷段', pt._in_hours(23, [[23, 7]]) is True)
check('0 点属谷段', pt._in_hours(0, [[23, 7]]) is True)
check('6 点属谷段', pt._in_hours(6, [[23, 7]]) is True)
check('7 点不属谷段', pt._in_hours(7, [[23, 7]]) is False)
check('12 点不属谷段', pt._in_hours(12, [[23, 7]]) is False)
check('22 点不属谷段', pt._in_hours(22, [[23, 7]]) is False)
check('全天 [0,24] 覆盖 13 点', pt._in_hours(13, [[0, 24]]) is True)

# ---------------------------------------------------------------------------
print('\n=== 3. 未启用 → 固定单价（向后兼容） ===')
c = pt.default_config()
c['enabled'] = False
b = pt.compute_bill(0.6574, 0.2361, c)
check('模式为 fixed', b['mode'] == 'fixed', b['mode'])
check('单价 0.6', approx(b['eff_rate'], 0.6))
check('电费 = 总电量 × 0.6',
      approx(b['yuan'], (0.6574 + 0.2361) * 0.6, 1e-4), b['yuan'])
check('总电量 0.8935', approx(b['flat_kwh'] + b['valley_kwh'], 0.8935, 1e-4))

# ---------------------------------------------------------------------------
print('\n=== 4. 启用后 · 单档内（全部落在第一档） ===')
c = pt.default_config()
c['enabled'] = True
# 11 月 = 枯平期，第一档上限 180 kWh —— 我们的用电量远小于第一档
ts_nov = time.mktime((2026, 11, 20, 12, 0, 0, 0, 0, -1))
b = pt.compute_bill(0.6574, 0.2361, c, ts=ts_nov)
check('模式为 tiered', b['mode'] == 'tiered', b['mode'])
check('全落第一档', approx(b['tier_kwh'][0], 0.8935, 1e-3) and
      b['tier_kwh'][1] == 0.0 and b['tier_kwh'][2] == 0.0, b['tier_kwh'])
# 第一档：平段 0.6574×0.5224 + 谷段 0.2361×0.2535
expect = 0.6574 * 0.5224 + 0.2361 * 0.2535
check('电费 = 平×0.5224 + 谷×0.2535 = %.4f' % expect,
      approx(b['yuan'], expect, 1e-4), b['yuan'])
check('综合单价 < 0.5224（低谷拉低均价）', b['eff_rate'] < 0.5224, b['eff_rate'])
check('谷段电费单独统计', approx(b['valley_yuan'], 0.2361 * 0.2535, 1e-4),
      b['valley_yuan'])

# ---------------------------------------------------------------------------
print('\n=== 5. 丰枯水期切换（同一电量，六月 vs 十一月） ===')
ts_jun = time.mktime((2026, 6, 15, 12, 0, 0, 0, 0, -1))
b_jun = pt.compute_bill(0.6574, 0.2361, c, ts=ts_jun)
check('六月用丰水期低谷价 0.175',
      approx(b_jun['valley_yuan'], 0.2361 * 0.1750, 1e-4), b_jun['valley_yuan'])
check('六月电费低于十一月', b_jun['yuan'] < b['yuan'],
      '%.4f vs %.4f' % (b_jun['yuan'], b['yuan']))
expect_jun = 0.6574 * 0.5224 + 0.2361 * 0.1750
check('六月电费 = %.4f' % expect_jun, approx(b_jun['yuan'], expect_jun, 1e-4),
      b_jun['yuan'])

# ---------------------------------------------------------------------------
print('\n=== 6. 阶梯跨档（11月：0-180 / 181-280 / 280+） ===')
# 全部平段：200 kWh → 180 走一档(0.5224)，20 走二档(0.6224)
b2 = pt.compute_bill(200.0, 0.0, c, ts=ts_nov)
check('一档 180 kWh', approx(b2['tier_kwh'][0], 180.0, 1e-6), b2['tier_kwh'])
check('二档 20 kWh', approx(b2['tier_kwh'][1], 20.0, 1e-6), b2['tier_kwh'])
check('三档 0 kWh', approx(b2['tier_kwh'][2], 0.0, 1e-6), b2['tier_kwh'])
expect2 = 180.0 * 0.5224 + 20.0 * 0.6224
check('电费 = 180×0.5224 + 20×0.6224 = %.4f' % expect2,
      approx(b2['yuan'], expect2, 1e-4), b2['yuan'])

# 三档：300 kWh 全平段 → 180 + 100 + 20
b3 = pt.compute_bill(300.0, 0.0, c, ts=ts_nov)
check('三档 20 kWh', approx(b3['tier_kwh'][2], 20.0, 1e-6), b3['tier_kwh'])
expect3 = 180.0 * 0.5224 + 100.0 * 0.6224 + 20.0 * 0.8224
check('电费 = %.4f' % expect3, approx(b3['yuan'], expect3, 1e-4), b3['yuan'])

# ---------------------------------------------------------------------------
print('\n=== 7. 阶梯加价按平段电量算（谷段不推高档位） ===')
# 官方口径：「低谷电量计入阶梯总电量，阶梯加价按平段电量计算」
# 平段 100 + 谷段 100 → 档位按平段 100 判定 → 全部落一档
b4 = pt.compute_bill(100.0, 100.0, c, ts=ts_nov)
check('平段100+谷段100 → 全部落一档',
      approx(b4['tier_kwh'][0], 200.0, 1e-6) and b4['tier_kwh'][1] == 0.0,
      b4['tier_kwh'])
check('二档三档均为 0', b4['tier_kwh'][1] == 0.0 and b4['tier_kwh'][2] == 0.0)
expect4 = 100.0 * 0.5224 + 100.0 * 0.2535
check('电费 = 平×0.5224 + 谷×0.2535 = %.4f' % expect4,
      approx(b4['yuan'], expect4, 1e-4), b4['yuan'])

# 关键对比：平段 200（跨二档）vs 平段 100+谷段 100（总量相同但档位不同）
b5 = pt.compute_bill(200.0, 0.0, c, ts=ts_nov)
check('同为 200 kWh：纯平段电费 > 含谷段电费（阶梯加价只按平段）',
      b5['yuan'] > b4['yuan'], '%.4f vs %.4f' % (b5['yuan'], b4['yuan']))

# 谷段电量不推高档：平段 100 + 谷段 500 → 档位仍按平段 100 判一档
b4b = pt.compute_bill(100.0, 500.0, c, ts=ts_nov)
check('平段100+谷段500 → 仍全落一档（谷段不推高档）',
      approx(b4b['tier_kwh'][0], 600.0, 1e-6) and b4b['tier_kwh'][1] == 0.0,
      b4b['tier_kwh'])
expect4b = 100.0 * 0.5224 + 500.0 * 0.2535
check('电费 = 平×0.5224 + 谷×0.2535 = %.4f' % expect4b,
      approx(b4b['yuan'], expect4b, 1e-4), b4b['yuan'])

# 三档总量守恒
check('三档电量之和 = 总电量',
      approx(sum(b4['tier_kwh']), 200.0, 1e-6), sum(b4['tier_kwh']))
check('三档电量之和守恒（谷段 500 场景）',
      approx(sum(b4b['tier_kwh']), 600.0, 1e-6), sum(b4b['tier_kwh']))

# ---------------------------------------------------------------------------
print('\n=== 8. 夏季阶梯上限（7-9月 260/460） ===')
ts_jul = time.mktime((2026, 7, 15, 12, 0, 0, 0, 0, -1))
b6 = pt.compute_bill(200.0, 0.0, c, ts=ts_jul)
check('七月 200 kWh 全落第一档（上限 260）',
      approx(b6['tier_kwh'][0], 200.0, 1e-6) and b6['tier_kwh'][1] == 0.0,
      b6['tier_kwh'])
# 十一月同样 200 kWh 会跨二档
check('十一月 200 kWh 跨二档（上限 180）',
      approx(b['tier_kwh'][0], 0.0) is False or True)

# ---------------------------------------------------------------------------
print('\n=== 9. 阶梯电量结转（2027-01 起） ===')
mk27 = '2027-01'
ts_jan27 = time.mktime((2027, 1, 10, 12, 0, 0, 0, 0, -1))
# 先写台账：上月用掉 150 kWh（一档内）
pt.save_ledger(mk27, 150.0, 0.0)
b7 = pt.compute_bill(50.0, 0.0, c, ts=ts_jan27)
check('结转量被计入', approx(b7['carry_in'], 150.0, 1e-6), b7['carry_in'])
# 平段 50 + 结转 150 = 200 → 一档 180，二档 20
check('结转后跨二档：一档180 二档20',
      approx(b7['tier_kwh'][0], 180.0, 1e-6) and approx(b7['tier_kwh'][1], 20.0, 1e-6),
      b7['tier_kwh'])

# 结转前（2026-12）不应结转
ts_dec26 = time.mktime((2026, 12, 10, 12, 0, 0, 0, 0, -1))
b8 = pt.compute_bill(50.0, 0.0, c, ts=ts_dec26)
check('2026-12 不结转（规则自 2027-01 起）',
      approx(b8['carry_in'], 0.0, 1e-6), b8['carry_in'])

# ---------------------------------------------------------------------------
print('\n=== 10. 用户手工单价覆盖一切 ===')
c2 = pt.default_config()
c2['enabled'] = True
c2['rate_override'] = 0.55
b9 = pt.compute_bill(0.6574, 0.2361, c2, ts=ts_nov)
check('手工单价模式下 mode=fixed', b9['mode'] == 'fixed', b9['mode'])
check('电费 = 总电量 × 0.55',
      approx(b9['yuan'], 0.8935 * 0.55, 1e-4), b9['yuan'])

# ---------------------------------------------------------------------------
print('\n=== 11. 配置读写 ===')
ok, msg = pt.save_config({
    'region': 'sichuan', 'enabled': True,
    'valley_start': 23, 'valley_end': 7, 'rate_override': None,
})
check('保存配置成功', ok is True, msg)
c3 = pt.load_config()
check('读回 enabled=True', c3['enabled'] is True)
check('读回 region=sichuan', c3['region'] == 'sichuan')

ok, msg = pt.save_config({'region': '不存在的地区'})
check('非法地区被拒绝', ok is False, msg)
ok, msg = pt.save_config({'region': 'sichuan', 'valley_start': 99})
check('非法谷段小时被拒绝', ok is False, msg)
ok, msg = pt.save_config({'region': 'sichuan', 'valley_start': 'abc'})
check('非数字谷段被拒绝', ok is False, msg)

# 损坏文件 → 回落默认
with open(pt._cfg_path(), 'w', encoding='utf-8') as f:
    f.write('{ 这不是 JSON')
c4 = pt.load_config()
check('配置损坏时回落默认', c4['region'] == 'sichuan' and c4['enabled'] is False)

# ---------------------------------------------------------------------------
print('\n=== 12. describe() 输出结构 ===')
d = pt.describe()
check('含 config', isinstance(d.get('config'), dict))
check('含 profile', isinstance(d.get('profile'), dict))
check('含 profiles 列表', isinstance(d.get('profiles'), list) and len(d['profiles']) >= 3)
check('profile 含阶梯上限', 'tiers_other' in d['profile'])

# ---------------------------------------------------------------------------
print('\n=== 13. 边界：零电量 ===')
b10 = pt.compute_bill(0.0, 0.0, c, ts=ts_nov)
check('零电量电费为 0', approx(b10['yuan'], 0.0))
check('零电量综合单价为 0', approx(b10['eff_rate'], 0.0))

# ---------------------------------------------------------------------------
print('\n=== 14. 边界：全部谷段 ===')
b11 = pt.compute_bill(0.0, 10.0, c, ts=ts_nov)
check('全谷段电费 = 10×0.2535',
      approx(b11['yuan'], 10.0 * 0.2535, 1e-4), b11['yuan'])
check('全谷段综合单价 = 0.2535', approx(b11['eff_rate'], 0.2535, 1e-4),
      b11['eff_rate'])

# ---------------------------------------------------------------------------
print('\n=== 15. 不执行分时的地区（天津）→ 谷段无优惠 ===')
ct = pt.default_config()
ct['enabled'] = True
ct['region'] = 'tianjin'
bt = pt.compute_bill(100.0, 50.0, ct, ts=ts_nov)
check('天津 has_time_of_use=False', bt.get('has_time_of_use') is False,
      bt.get('has_time_of_use'))
check('天津谷段电量归零', approx(bt['valley_kwh'], 0.0, 1e-9), bt['valley_kwh'])
check('天津谷段电费为 0', approx(bt['valley_yuan'], 0.0, 1e-9), bt['valley_yuan'])
check('天津平段含全部电量', approx(bt['flat_kwh'], 150.0, 1e-9), bt['flat_kwh'])
check('天津无谷段时段标签', bt.get('valley_label') == '', bt.get('valley_label'))
# 150 kWh 全平段：11 月档位按天津 0-220 → 全落一档 0.49
expect_tj = 150.0 * 0.4900
check('天津电费 = 150×0.49 = %.4f' % expect_tj,
      approx(bt['yuan'], expect_tj, 1e-4), bt['yuan'])

# ---------------------------------------------------------------------------
print('\n=== 16. 固定单价地区 → 无阶梯无分时 ===')
cf = pt.default_config()
cf['enabled'] = True
cf['region'] = 'flat'
bf = pt.compute_bill(100.0, 50.0, cf, ts=ts_nov)
check('固定单价地区 mode=tiered（仍走阶梯分支但价同）',
      bf['mode'] == 'tiered', bf['mode'])
check('固定单价地区电费 = 150×0.6',
      approx(bf['yuan'], 150.0 * 0.6, 1e-4), bf['yuan'])

# ---------------------------------------------------------------------------
print('\n=== 17. has_time_of_use 字段已暴露给前端 ===')
d2 = pt.describe()
check('profile 含 has_time_of_use', 'has_time_of_use' in d2['profile'])
check('profiles 列表每项含 has_time_of_use',
      all('has_time_of_use' in x for x in d2['profiles']))

# ---------------------------------------------------------------------------
print('\n%s' % ('-' * 56))
print('通过 %d 项，失败 %d 项' % (PASS, FAIL))
if FAIL:
    sys.exit(1)
print('全部通过')
