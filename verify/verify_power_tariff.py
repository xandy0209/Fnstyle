#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.1.63：单一单价电价模型的单元测试

背景
----
老板原话：
  「电价的计费方式调整为：不使用阶梯电价计费方式，根据成都最低档对应的
    电价进行计算（并提供可配置能力），删除阶梯计费和档位信息等，
    也不区分区域，就是一个电费单价的配置，并增加对应的说明即可。」

模型退化为：**总电量 × 单一单价**
  · 默认单价 = 成都（四川）一户一表居民不满 1 千伏第一档高峰平段 0.5224 元/kWh
  · 不分地区 / 不分时段 / 不分档位 / 不分电压等级 / 无季节参数 / 无跨月结转
  · 保留一个「电费单价」配置项 + 一段口径说明

本脚本覆盖：
  A. 默认配置与常量
  B. 配置读写（保存 / 校验 / 边界）
  C. 废弃键在**读取层**被丢弃（旧库升级不残留）
  D. 计费：总电量 × 单价（分段、负值、空值、越界）
  E. describe() 契约
  F. 已删除的阶梯/分时/地区 API **确实不存在**（防回归）
"""
import os
import sys
import json
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
# 隔离：把配置写到临时目录，避免污染真实数据
# ---------------------------------------------------------------------------
TMP = tempfile.mkdtemp(prefix='fnst_tariff_')
os.environ['FNSTYLE_DATA_DIR'] = TMP

# ---------------------------------------------------------------------------
print('\n=== 1. 默认配置与常量 ===')
check('DEFAULT_RATE = 0.5224（成都最低档）',
      pt.DEFAULT_RATE == 0.5224, pt.DEFAULT_RATE)
cfg = pt.default_config()
check('默认配置只有 rate + updated',
      set(cfg.keys()) == {'rate', 'updated'}, sorted(cfg.keys()))
check('默认 rate = 0.5224', cfg['rate'] == 0.5224, cfg['rate'])
check('默认 updated = 0', cfg['updated'] == 0, cfg['updated'])
check('单价下限 = 0', pt._RATE_MIN == 0.0, pt._RATE_MIN)
check('单价上限 = 10', pt._RATE_MAX == 10.0, pt._RATE_MAX)

live = pt.load_config()
check('load_config 返回 rate=0.5224', live['rate'] == 0.5224, live['rate'])

# ---------------------------------------------------------------------------
print('\n=== 2. 配置读写 ===')
ok, msg = pt.save_config({'rate': 0.8888})
check('保存单价 0.8888 成功', ok and '已保存' in msg, msg)
back = pt.load_config()
check('读回单价 = 0.8888', back['rate'] == 0.8888, back['rate'])
check('保存后 updated 是时间戳', back['updated'] > 0, back['updated'])

ok, msg = pt.save_config({'rate': 'abc'})
check('非数字被拒', (not ok) and '数字' in msg, msg)
check('拒绝后原值未被改坏', pt.load_config()['rate'] == 0.8888,
      pt.load_config()['rate'])

ok, msg = pt.save_config({'rate': -1})
check('负数被拒', (not ok) and '之间' in msg, msg)
ok, msg = pt.save_config({'rate': 99})
check('超上限被拒', (not ok) and '之间' in msg, msg)
check('连续拒绝后原值仍在', pt.load_config()['rate'] == 0.8888,
      pt.load_config()['rate'])

ok, msg = pt.save_config({'rate': 0})
check('单价 0 合法（0-10 闭区间）', ok, msg)
check('0 被原样保留', pt.load_config()['rate'] == 0.0, pt.load_config()['rate'])
ok, msg = pt.save_config({'rate': 10})
check('单价 10 合法', ok, msg)

# 不提交 rate 时继承旧值（不被清零）
pt.save_config({'rate': 0.6})
ok, _ = pt.save_config({'updated': 12345})
check('未提交 rate 时继承旧值',
      ok and pt.load_config()['rate'] == 0.6, pt.load_config()['rate'])

# ---------------------------------------------------------------------------
print('\n=== 3. 废弃键在读取层被丢弃 ===')
legacy = {
    'rate': 0.6,
    'rate_override': 0.75,
    'proj_rate': 0.8,
    'region': 'sichuan',
    'voltage': 'lv2',
    'valley_start': 23,
    'valley_end': 7,
    'overrides': {'summer': {'months': [7, 8, 9]}},
    'household_people': 5,
    'meter_months': 2,
    'enabled': True,
}
with open(pt._cfg_path(), 'w', encoding='utf-8') as f:
    json.dump(legacy, f, ensure_ascii=False)
read = pt.load_config()
leaked = [k for k in pt._DEPRECATED_KEYS if k in read]
check('旧库升级后废弃键无残留', leaked == [], leaked)
check('旧库的 rate 被继承', read['rate'] == 0.6, read['rate'])
check('读出的配置只有 rate + updated',
      set(read.keys()) == {'rate', 'updated'}, sorted(read.keys()))

# 旧库只有 rate_override 时，单价继承过来（不回落默认）
with open(pt._cfg_path(), 'w', encoding='utf-8') as f:
    json.dump({'rate_override': 0.7777}, f, ensure_ascii=False)
read = pt.load_config()
check('旧库仅有 rate_override 时继承为 rate',
      read['rate'] == 0.7777, read['rate'])

# 配置文件损坏时回落默认
with open(pt._cfg_path(), 'w', encoding='utf-8') as f:
    f.write('{ not json')
read = pt.load_config()
check('配置文件损坏时回落默认', read['rate'] == 0.5224, read['rate'])

# ---------------------------------------------------------------------------
print('\n=== 4. 计费：总电量 × 单一单价 ===')
c = {'rate': 0.5224}
b = pt.compute_bill(10.0, 0.0, c)
check('10 kWh × 0.5224 = 5.224', approx(b['yuan'], 5.224), b['yuan'])
check('total_kwh = 10', approx(b['total_kwh'], 10.0), b['total_kwh'])
check('rate 回显 0.5224', approx(b['rate'], 0.5224), b['rate'])
check('eff_rate = rate（单一时恒等）', approx(b['eff_rate'], b['rate']), b['eff_rate'])
check('mode = fixed', b['mode'] == 'fixed', b['mode'])
check('无档位明细（detail 为空）', b['detail'] == [], b['detail'])

b = pt.compute_bill(3.0, 2.0, c)
check('平+谷相加得总量 5', approx(b['total_kwh'], 5.0), b['total_kwh'])
check('电量分段不影响总价', approx(b['yuan'], 5 * 0.5224), b['yuan'])
check('分段金额相加 = 总价',
      approx(b['flat_yuan'] + b['valley_yuan'], b['yuan']),
      (b['flat_yuan'], b['valley_yuan'], b['yuan']))

b = pt.compute_bill(0.0, 0.0, c)
check('零电量 → 零电费', approx(b['yuan'], 0.0), b['yuan'])

b = pt.compute_bill(-5.0, 0.0, c)
check('负电量按 0 计', approx(b['total_kwh'], 0.0) and approx(b['yuan'], 0.0),
      (b['total_kwh'], b['yuan']))

b = pt.compute_bill(None, None, c)
check('None 输入按 0 计', approx(b['yuan'], 0.0), b['yuan'])

b = pt.compute_bill(10.0, 0.0, {'rate': 1.0})
check('自定义单价 1.0 → 10 kWh = 10 元', approx(b['yuan'], 10.0), b['yuan'])

b = pt.compute_bill(10.0, 0.0, {'rate': 999})
check('越界单价回落默认',
      approx(b['rate'], pt.DEFAULT_RATE) and approx(b['yuan'], 5.224),
      (b['rate'], b['yuan']))

b = pt.compute_bill(10.0, 0.0, {})
check('缺 rate 时用默认单价', approx(b['rate'], pt.DEFAULT_RATE), b['rate'])

# 电量与单价相乘的精度：0.5224 × 3.7 = 1.93288
b = pt.compute_bill(3.7, 0.0, c)
check('浮点乘法精度（3.7 kWh）', approx(b['yuan'], 1.93288, 1e-4), b['yuan'])

# ---------------------------------------------------------------------------
print('\n=== 5. describe() 契约 ===')
d = pt.describe()
check('describe 含 config', 'config' in d, sorted(d.keys()))
check('describe 含 rate', 'rate' in d, sorted(d.keys()))
check('describe 含 default_rate', d.get('default_rate') == pt.DEFAULT_RATE,
      d.get('default_rate'))
check('describe 单位是 元/kWh', d.get('unit') == '元/kWh', d.get('unit'))
check('describe 含口径说明 note', bool(d.get('note')), d.get('note'))
note = d.get('note') or ''
for kw in ('用电量', '电费单价', '不区分阶梯档位', '不区分峰谷时段', '不区分地区',
           '0.5224'):
    check('说明含关键词「%s」' % kw, kw in note, note)
check('说明不含已废弃概念「档位上限」', '档位上限' not in note, note)
check('说明不含已废弃概念「电压等级」', '电压等级' not in note, note)

# ---------------------------------------------------------------------------
print('\n=== 6. 已删除的阶梯/分时/地区 API 确实不存在（防回归）===')
gone = [
    'PROFILES', 'OVERRIDABLE_FIELDS', 'profile_list', 'effective_profile',
    'tier_derivation', 'effective_tier_limits', 'normalize_overrides',
    'load_ledger', 'save_ledger', 'update_tier_ledger', 'carry_in_for',
    'resolve_voltage', '_effective_caps', '_split_kwh_by_tier',
    '_normalize_months', '_normalize_rates', '_normalize_tiers',
    '_month_key', '_prev_month_key', '_tier_index', '_tier_caps',
]
still = [g for g in gone if hasattr(pt, g)]
check('已删除的阶梯/分时/地区 API 全部不存在',
      still == [], '仍存在: %s' % still)

# 计费结果里不该再有阶梯字段
b = pt.compute_bill(10.0, 0.0, c)
for k in ('current_tier', 'tier_limits', 'tier_rates', 'tier_kwh',
          'carry_in', 'region', 'voltage', 'valley_label'):
    check('计费结果不含 %s' % k, k not in b, sorted(b.keys()))

# ---------------------------------------------------------------------------
print('\n' + '=' * 60)
print('通过 %d 项，失败 %d 项' % (PASS, FAIL))
print('=' * 60)
if FAIL:
    sys.exit(1)
print('🎉 单一单价电价模型测试全部通过')
