#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按官方《四川省电网居民生活电价表》(2026-07-01 起执行) 全面验证电价模型
（v1.1.59）

依据原文（PDF 提取 + 川发改价格〔2026〕255号 全文核对）：

  一户一表 居民生活用电
    · 夏季(7-9月)  ：第1档 0-260   第2档 261-460  第3档 460+
    · 其他月份     ：第1档 0-180   第2档 181-280  第3档 280+
    · 高峰平段(7:00-23:00) 不满1千伏 0.5224/0.6224/0.8224
                          1-10千伏  0.5124/0.6124/0.8124
                          35-110千伏 0.5124/0.6124/0.8124
    · 低谷(23:00-次日7:00) **三个电压等级完全相同**
                          丰水期(6-10月)   0.175/0.275/0.475
                          枯平水期(1-4,12;5,11) 0.2535/0.3535/0.5535
  合表居民生活用电  0.5464（不满1千伏）/ 0.5364（1-10、35-110千伏）
    → 不分时、不分阶梯
  注4：自2027年1月起，未使用完的第一、二档电量逐月滚动结转至
       **年内**后续月份对应阶梯档位，**年末清零**；
       「一户多人口」增加的阶梯电量纳入结转范围。

本文件验证：
  ① 三档电价与电压等级（高峰平段 / 低谷）
  ② 夏季 vs 非夏季档位门槛
  ③ 档位按**月总电量**判定（官方账单算例）
  ④ 低谷价不受电压等级影响
  ⑤ 合表居民单一电价
  ⑥ 结转：读上月台账 / 年末清零 / 台账写入口径
  ⑦ v1.1.62：「一户多人口」与「抄表周期」两个配置项**已删除**
     → 档位门槛只剩「基准档位 + 跨月结转」两环
"""

import os
import sys
import time
import json
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'app', 'ui'))

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


def eq_list(a, b, tol=1e-6):
    if len(a) != len(b):
        return False
    return all(approx(x, y, tol) for x, y in zip(a, b))


def tier_list_eq(a, b, tol=1e-6):
    """阶梯档位列表比较 —— 允许 None（三档 = 不限）。"""
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if x is None or y is None:
            if x is not y:
                return False
        elif not approx(x, y, tol):
            return False
    return True


TMP = tempfile.mkdtemp(prefix='fnst_pdf_')
os.environ['FNSTYLE_DATA_DIR'] = TMP

ts_mon = time.mktime((2026, 11, 10, 12, 0, 0, 0, 0, -1))   # 非夏季
ts_jul = time.mktime((2026, 7, 10, 12, 0, 0, 0, 0, -1))    # 夏季
ts_oct = time.mktime((2026, 10, 10, 12, 0, 0, 0, 0, -1))   # 丰水期
ts_jan = time.mktime((2026, 1, 10, 12, 0, 0, 0, 0, -1))    # 枯平水期


def cfg_of(region='sichuan', **kw):
    c = pt.default_config()
    c['enabled'] = True
    c['region'] = region
    c.update(kw)
    return c


# ---------------------------------------------------------------------------
print('\n=== 1. 三档高峰/平段电价（按电压等级） ===')
# 不满 1 千伏
c = cfg_of(voltage='lv1')
b = pt.compute_bill(100.0, 100.0, c, ts=ts_mon)
check('lv1 高峰平段三档价 0.5224/0.6224/0.8224',
      eq_list(b['tier_rates'], [0.5224, 0.6224, 0.8224]), b['tier_rates'])
# 1-10 千伏
c = cfg_of(voltage='lv2')
b2 = pt.compute_bill(100.0, 100.0, c, ts=ts_mon)
check('lv2 高峰平段三档价 0.5124/0.6124/0.8124',
      eq_list(b2['tier_rates'], [0.5124, 0.6124, 0.8124]), b2['tier_rates'])
# 35-110 千伏
c = cfg_of(voltage='lv3')
b3 = pt.compute_bill(100.0, 100.0, c, ts=ts_mon)
check('lv3 高峰平段三档价 0.5124/0.6124/0.8124',
      eq_list(b3['tier_rates'], [0.5124, 0.6124, 0.8124]), b3['tier_rates'])
check('返回体带电压等级名',
      b3.get('voltage') == 'lv3' and b3.get('voltage_name') == '35-110 千伏',
      '%s / %s' % (b3.get('voltage'), b3.get('voltage_name')))

# ---------------------------------------------------------------------------
print('\n=== 2. 低谷电价（三个电压等级完全相同） ===')
for vid, name in (('lv1', '不满1千伏'), ('lv2', '1-10千伏'), ('lv3', '35-110千伏')):
    c = cfg_of(voltage=vid)
    bb = pt.compute_bill(0.0, 400.0, c, ts=ts_oct)   # 丰水期
    rates = [d['valley_rate'] for d in bb['detail']]
    check('%s 丰水期低谷价 0.175/0.275/0.475' % name,
          eq_list(rates, [0.175, 0.275, 0.475]), rates)
for vid, name in (('lv1', '不满1千伏'), ('lv2', '1-10千伏'), ('lv3', '35-110千伏')):
    c = cfg_of(voltage=vid)
    bb = pt.compute_bill(0.0, 400.0, c, ts=ts_jan)   # 枯平水期
    rates = [d['valley_rate'] for d in bb['detail']]
    check('%s 枯平水期低谷价 0.2535/0.3535/0.5535' % name,
          eq_list(rates, [0.2535, 0.3535, 0.5535]), rates)

# ---------------------------------------------------------------------------
print('\n=== 3. 夏季 / 非夏季档位门槛 ===')
c = cfg_of()
b = pt.compute_bill(200.0, 200.0, c, ts=ts_mon)   # 400 度，11月
# 非夏季档位 0-180 / 181-280 / 280+ → 增量容量 180 / 100 / ∞
check('非夏季 400 度 → 一档180 二档100 三档120',
      approx(b['tier_kwh'][0], 180.0) and approx(b['tier_kwh'][1], 100.0) and
      approx(b['tier_kwh'][2], 120.0), b['tier_kwh'])
b = pt.compute_bill(200.0, 200.0, c, ts=ts_jul)    # 400 度，7月
check('夏季 400 度 → 一档260 二档140',
      approx(b['tier_kwh'][0], 260.0) and approx(b['tier_kwh'][1], 140.0),
      b['tier_kwh'])
b = pt.compute_bill(300.0, 300.0, c, ts=ts_jul)    # 600 度，7月
check('夏季 600 度 → 一档260 二档200 三档140',
      approx(b['tier_kwh'][0], 260.0) and approx(b['tier_kwh'][1], 200.0) and
      approx(b['tier_kwh'][2], 140.0), b['tier_kwh'])
b = pt.compute_bill(300.0, 300.0, c, ts=ts_mon)    # 600 度，11月
check('非夏季 600 度 → 一档180 二档100 三档320',
      approx(b['tier_kwh'][0], 180.0) and approx(b['tier_kwh'][1], 100.0) and
      approx(b['tier_kwh'][2], 320.0), b['tier_kwh'])

# ---------------------------------------------------------------------------
print('\n=== 4. 档位按月总电量判定（官方账单算例） ===')
# 宜宾叙州区政府官网账单算例 2025-02：
#   峰 100 + 平 150 + 谷 150 = 400 度 → 账单 214.625 元
#   按总电量判档重算 = 214.625 元（误差 0）
c = cfg_of()
b = pt.compute_bill(250.0, 150.0, c, ts=ts_mon)
# 400 度 → 按**总电量**判档：一档180 二档100 三档120
#   平段 250 度占比 0.625，谷段 150 度占比 0.375，各档按占比分摊
#   ⚠️ 每档的低谷价**不同**（0.2535 / 0.3535 / 0.5535）
#   一档: 平112.5×0.5224 + 谷 67.5×0.2535 = 58.77000 + 17.11125
#   二档: 平 62.5×0.6224 + 谷 37.5×0.3535 = 38.90000 + 13.25625
#   三档: 平 75.0×0.8224 + 谷 45.0×0.5535 = 61.68000 + 24.90750
exp = (112.5 * 0.5224 + 67.5 * 0.2535 +
       62.5 * 0.6224 + 37.5 * 0.3535 +
       75.0 * 0.8224 + 45.0 * 0.5535)
check('官方算例重算 = 214.625 元（误差 0）',
      approx(b['yuan'], 214.625, 1e-4) and approx(b['yuan'], exp, 1e-4),
      '%.4f vs %.4f' % (b['yuan'], exp))
check('档位按总电量判定（400 度 → 一档180 二档100 三档120）',
      approx(b['tier_kwh'][0], 180.0) and approx(b['tier_kwh'][1], 100.0) and
      approx(b['tier_kwh'][2], 120.0), b['tier_kwh'])

# ---------------------------------------------------------------------------
print('\n=== 5. 合表居民（不分时不分阶梯） ===')
for vid, exp_rate in (('lv1', 0.5464), ('lv2', 0.5364), ('lv3', 0.5364)):
    c = cfg_of(region='sichuan_shared', voltage=vid)
    bb = pt.compute_bill(150.0, 0.0, c, ts=ts_mon)
    check('合表 %s 电价 %.4f（%.4f 元）' % (vid, exp_rate, 150 * exp_rate),
          approx(bb['yuan'], 150 * exp_rate, 1e-6) and bb['mode'] == 'fixed',
          '%.6f / %s' % (bb['yuan'], bb['mode']))
c = cfg_of(region='sichuan_shared', voltage='lv1')
bb = pt.compute_bill(0.0, 150.0, c, ts=ts_mon)
check('合表不分时（谷段也按同一电价）',
      approx(bb['yuan'], 150 * 0.5464, 1e-6), bb['yuan'])
check('合表 has_time_of_use=False',
      bb.get('has_time_of_use') is False, bb.get('has_time_of_use'))

# ---------------------------------------------------------------------------
print('\n=== 6. v1.1.62：「一户多人口」「抄表周期」已移除 ===')
check('multi_person_extra 函数已删除',
      not hasattr(pt, 'multi_person_extra'))
check('_meter_months_of 函数已删除',
      not hasattr(pt, '_meter_months_of'))
check('PROFILES 不再有 supports_multi_person 开关',
      not any('supports_multi_person' in p for p in pt.PROFILES.values()))
check('PROFILES 不再有 supports_meter_cycle 开关',
      not any('supports_meter_cycle' in p for p in pt.PROFILES.values()))
# 旧配置里残留的键不再影响门槛
c = cfg_of(household_people=5, meter_months=2)
b = pt.compute_bill(400.0, 0.0, c, ts=ts_mon)
check('残留的 household_people/meter_months 不再放大门槛'
      '（400 度 → 一档180 二档100 三档120）',
      approx(b['tier_kwh'][0], 180.0) and approx(b['tier_kwh'][1], 100.0) and
      approx(b['tier_kwh'][2], 120.0), b['tier_kwh'])
check('返回体不再带 multi_person_extra',
      'multi_person_extra' not in b, sorted(b.keys()))
check('返回体不再带 meter_months',
      'meter_months' not in b, sorted(b.keys()))
# 门槛回到官方基准（无结转时）
lim, caps = pt.effective_tier_limits('sichuan', c, ts_mon)
check('生效门槛回到官方基准 180/280/不限',
      tier_list_eq(lim, [180.0, 280.0, None]), lim)

# ---------------------------------------------------------------------------
print('\n=== 8. 结转（官方注4：年内滚动、年末清零） ===')
mk_jan = '2027-01'
mk_feb = '2027-02'
ts_jan27 = time.mktime((2027, 1, 10, 12, 0, 0, 0, 0, -1))
ts_feb27 = time.mktime((2027, 2, 10, 12, 0, 0, 0, 0, -1))
ts_dec26 = time.mktime((2026, 12, 10, 12, 0, 0, 0, 0, -1))
c = cfg_of()
# 1 月剩一档 150 → 2 月一档可用 330
pt.save_ledger(mk_jan, 150.0, 0.0)
b = pt.compute_bill(50.0, 0.0, c, ts=ts_feb27)
check('结转读**上月**台账（carry_in = 150）',
      approx(b['carry_in'], 150.0), b['carry_in'])
check('结转扩容后 50 度全落一档',
      approx(b['tier_kwh'][0], 50.0) and b['tier_kwh'][1] == 0.0, b['tier_kwh'])
# 年末清零：2026-12 台账不结转到 2027-01
pt.save_ledger('2026-12', 999.0, 0.0)
b = pt.compute_bill(50.0, 0.0, c, ts=ts_jan27)
check('年末清零：2026-12 剩余不结转到 2027-01',
      approx(b['carry_in'], 0.0), b['carry_in'])
# 规则生效前不结转
b = pt.compute_bill(50.0, 0.0, c, ts=ts_dec26)
check('2026-12 不结转（规则自 2027-01 起）',
      approx(b['carry_in'], 0.0), b['carry_in'])
# 台账写入口径
led = pt.load_ledger(mk_jan)
check('台账字段为 tier1_remaining / tier2_remaining',
      'tier1_remaining' in led and 'tier2_remaining' in led, led)
check('台账不含旧字段 tier1_used', 'tier1_used' not in led, led)
# 台账写入与计费口径一致（用 update_tier_ledger 实写一次）
d2 = tempfile.mkdtemp()
os.environ['FNSTYLE_DATA_DIR'] = d2
c2 = cfg_of()
pt.save_config(c2)
pt.update_tier_ledger(250.0, 0.0, ts=ts_jan27)
led = pt.load_ledger(mk_jan)
check('台账写入：12月用250度 → 一档剩0 二档剩30',
      approx(led['tier1_remaining'], 0.0) and
      approx(led['tier2_remaining'], 30.0), led)
# 幂等：重复写不污染
pt.update_tier_ledger(250.0, 0.0, ts=ts_jan27)
led2 = pt.load_ledger(mk_jan)
check('台账写入幂等（重复调用结果一致）',
      approx(led2['tier1_remaining'], led['tier1_remaining']) and
      approx(led2['tier2_remaining'], led['tier2_remaining']), led2)

# ---------------------------------------------------------------------------
print('\n=== 9. 天津档位不受残留配置影响 ===')
# 天津按**年**周期阶梯 0-220 / 221-400 / 400+ → 增量 220 / 180 / ∞
c = cfg_of(region='tianjin', household_people=5, meter_months=2)
b = pt.compute_bill(400.0, 0.0, c, ts=ts_mon)
check('天津配了5人/双月也不生效（仍按 0-220/221-400）',
      approx(b['tier_kwh'][0], 220.0) and approx(b['tier_kwh'][1], 180.0),
      b['tier_kwh'])

# ---------------------------------------------------------------------------
print('\n=== 10. 配置保存校验 ===')
d3 = tempfile.mkdtemp()
os.environ['FNSTYLE_DATA_DIR'] = d3
ok, msg = pt.save_config({
    'region': 'sichuan', 'enabled': True, 'valley_start': 23, 'valley_end': 7,
    'rate_override': None, 'voltage': 'lv2',
    'household_people': 5, 'meter_months': 2})
check('合法配置保存成功', ok is True, msg)
c = pt.load_config()
check('读回 voltage = lv2', c.get('voltage') == 'lv2', c.get('voltage'))
check('旧配置里的 household_people 已被丢弃',
      'household_people' not in c, c.get('household_people'))
check('旧配置里的 meter_months 已被丢弃',
      'meter_months' not in c, c.get('meter_months'))
ok, msg = pt.save_config({'region': 'sichuan', 'valley_start': 23, 'valley_end': 7,
                          'household_people': 99})
check('人口数不再校验（直接忽略）', ok is True, msg)
ok, msg = pt.save_config({'region': 'sichuan', 'valley_start': 23, 'valley_end': 7,
                          'meter_months': 3})
check('抄表周期不再校验（直接忽略）', ok is True, msg)
ok, msg = pt.save_config({'region': 'sichuan', 'valley_start': 23, 'valley_end': 7,
                          'voltage': 'lv9'})
c = pt.load_config()
check('非法电压等级回落默认', c.get('voltage') == 'lv1', c.get('voltage'))
ok, msg = pt.save_config({'region': 'tianjin', 'valley_start': 23, 'valley_end': 7,
                          'voltage': 'lv2'})
c = pt.load_config()
# ⚠️ v1.1.61 行为变更：电压等级 id（lv1/lv2/lv3）是**跨地区通用**的，
#    天津虽不执行分档电价，但这里**保留**用户选的等级 ——
#    原来这里是 cfg.pop('voltage')，导致「在四川选了 1-10 千伏，
#    切天津保存一次，再切回四川」电压被重置回「不满 1 千伏」。
#    切回四川时后端会按四川的 levels 重新校验回落。
check('无电压等级的地区保留用户选的电压等级（不被清空）',
      c.get('voltage') == 'lv2', repr(c.get('voltage')))
# 天津本身不区分电压等级 → 解析出的生效电压为空
vid, vname, _p = pt.resolve_voltage('tianjin', c)
check('天津解析出的生效电压为空（该地区不区分电压）',
      vid == '', repr(vid))
check('天津峰价用档案默认 0.49/0.54/0.79',
      [abs(a - b) < 1e-9 for a, b in zip(_p, [0.49, 0.54, 0.79])], str(_p))

# ---------------------------------------------------------------------------
print('\n=== 11. describe() 透出新配置项 ===')
os.environ['FNSTYLE_DATA_DIR'] = tempfile.mkdtemp()
pt.save_config(cfg_of(region='sichuan', voltage='lv2'))
d = pt.describe()
check('profile.voltage_levels 非空', len(d['profile'].get('voltage_levels') or []) > 0)
check('profile.voltage 有值', bool(d['profile'].get('voltage')), d['profile'].get('voltage'))
check('profile 不再有 supports_multi_person（v1.1.62 已移除）',
      'supports_multi_person' not in d['profile'])
check('profile 不再有 supports_meter_cycle（v1.1.62 已移除）',
      'supports_meter_cycle' not in d['profile'])
byid = {x['id']: x for x in d['profiles']}
check('profiles 里 sichuan 有三个电压等级',
      len(byid['sichuan'].get('voltage_levels') or []) == 3)
check('profiles 里 tianjin 无电压等级',
      len(byid['tianjin'].get('voltage_levels') or []) == 0)

# ---------------------------------------------------------------------------
print('\n=== 12. describe() 的 active_tiers 必须反映实际生效容量 ===')
# ⚠️ v1.1.59 修复（真实缺陷）：原来 active_tiers 直接吐内置档位
#    [180, 280, None]，没反映抄表周期 ×2 / 一户多人口加成 / 跨月结转
#    → 前端价目表显示的门槛与真实计费口径对不上。
os.environ['FNSTYLE_DATA_DIR'] = tempfile.mkdtemp()
pt.save_config(cfg_of(region='sichuan'))
d = pt.describe()
check('默认（1人/每月）active_tiers = [180, 280, None]',
      tier_list_eq(d['active_tiers'], [180.0, 280.0, None]),
      d['active_tiers'])

# ⚠️ v1.1.62：抄表周期 ×2 与一户多人口加成**已移除**
#    → active_tiers 只受「基准档位 + 跨月结转」影响
os.environ['FNSTYLE_DATA_DIR'] = tempfile.mkdtemp()
pt.save_config(cfg_of(region='sichuan', meter_months=2, household_people=6))
d = pt.describe()
check('残留的抄表周期/人口数不再影响 active_tiers（仍为 [180, 280, None]）',
      tier_list_eq(d['active_tiers'], [180.0, 280.0, None]),
      d['active_tiers'])
check('describe 不再返回 multi_person_extra',
      'multi_person_extra' not in d, sorted(d.keys()))

# 天津的 active_tiers 保持 220/400
os.environ['FNSTYLE_DATA_DIR'] = tempfile.mkdtemp()
pt.save_config(cfg_of(region='tianjin', household_people=6, meter_months=2))
d = pt.describe()
check('天津 active_tiers 仍为 [220, 400, None]',
      tier_list_eq(d['active_tiers'], [220.0, 400.0, None]),
      d['active_tiers'])

# profiles 里每个地区都带 effective_tiers（前端切地区直接用）
os.environ['FNSTYLE_DATA_DIR'] = tempfile.mkdtemp()
pt.save_config(cfg_of(region='sichuan', meter_months=2, household_people=5))
d = pt.describe()
byid = {x['id']: x for x in d['profiles']}
check('profiles.sichuan.effective_tiers 非空',
      bool(byid['sichuan'].get('effective_tiers')),
      byid['sichuan'].get('effective_tiers'))
check('profiles.sichuan.effective_tiers 回到官方基准 [180, 280, None]',
      tier_list_eq(byid['sichuan']['effective_tiers'], [180.0, 280.0, None]),
      byid['sichuan']['effective_tiers'])
check('profiles.tianjin.effective_tiers 存在',
      'effective_tiers' in byid['tianjin'])

# effective_tier_limits 与 compute_bill 的分档口径必须一致
os.environ['FNSTYLE_DATA_DIR'] = tempfile.mkdtemp()
pt.save_config(cfg_of(region='sichuan', household_people=6, meter_months=2))
c = pt.load_config()
limits, caps = pt.effective_tier_limits('sichuan', c)
b = pt.compute_bill(600.0, 0.0, c, ts=ts_mon)
check('effective_tier_limits 的 caps[0] = 180',
      approx(caps[0], 180.0), caps)
check('compute_bill 一档电量 = 180（与 limits 口径一致）',
      approx(b['tier_kwh'][0], 180.0), b['tier_kwh'])
check('compute_bill 二档电量 = 100（280-180）',
      approx(b['tier_kwh'][1], 100.0), b['tier_kwh'])

# ---------------------------------------------------------------------------
print('\n=== 13. API 返回必须是**合法 JSON**（不能含 Infinity/NaN） ===')
# ⚠️ v1.1.59 修复（致命）：active_tier_caps 的第三档是 float('inf')，
#    json.dumps 默认 allow_nan=True → 输出裸的 `Infinity`
#    → 浏览器 JSON.parse 抛 SyntaxError → 电价面板整块显示「读取失败」
#    （实测：NAS 上真实复现）。
os.environ['FNSTYLE_DATA_DIR'] = tempfile.mkdtemp()
pt.save_config(cfg_of(region='sichuan', household_people=6, meter_months=2))
d = pt.describe()
raw = json.dumps(d, ensure_ascii=False)
check('describe() 序列化结果不含 Infinity', 'Infinity' not in raw, raw[:120])
check('describe() 序列化结果不含 NaN', 'NaN' not in raw, raw[:120])
check('active_tier_caps 第三档为 None（不限）',
      d['active_tier_caps'][2] is None, d['active_tier_caps'])
# 用严格解析器验证（Python 的 json 默认也接受 NaN/Infinity，要手工挡）
try:
    json.loads(raw, parse_constant=lambda x: (_ for _ in ()).throw(
        ValueError('非法 JSON 常量: ' + x)))
    ok = True
except ValueError as e:
    ok = False
    print('    解析失败:', e)
check('describe() 输出能被严格 JSON 解析器接受', ok is True)

# 兜底函数：fnstyle_server._json_safe
try:
    sys.path.insert(0, os.path.join(os.path.dirname(
        os.path.abspath(__file__)), '..', 'app', 'ui'))
    import fnstyle_server as fs
    safe = fs._json_safe({'a': float('inf'), 'b': float('nan'),
                          'c': [1.0, float('inf')], 'd': {'e': float('-inf')}})
    check('_json_safe 把 inf 换成 None', safe['a'] is None, safe)
    check('_json_safe 把 nan 换成 None', safe['b'] is None, safe)
    check('_json_safe 递归处理嵌套 list', safe['c'][1] is None, safe)
    check('_json_safe 递归处理嵌套 dict', safe['d']['e'] is None, safe)
    raw2 = json.dumps(safe, ensure_ascii=False, allow_nan=False)
    check('_json_safe 结果可严格序列化', 'Infinity' not in raw2, raw2)
except ImportError as e:
    check('能导入 fnstyle_server', False, str(e))

# ---------------------------------------------------------------------------
print('\n' + '-' * 56)
print('通过 %d 项，失败 %d 项' % (PASS, FAIL))
if FAIL:
    sys.exit(1)
print('全部通过')
