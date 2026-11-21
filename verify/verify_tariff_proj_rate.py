#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.1.62 验证：判档依据可见 + 预计电费单价可调（不按阶梯、不分地区）

两条需求（老板原话）：
  ① 「我没有输入电量，你怎么知道是按那个阶段进行计费呢？」
     → 判档靠 **FnStyle 自测的本机月总电量**，界面必须写明这一依据。
  ② 「本月预计电费」不按阶梯算、不区分地区，统一用成都最低档单价
     （0.5224 元/kWh），并提供手动调整的能力。

验证：
  A. compute_bill 输出 current_tier（当前第几档）与 tier_limits（各档上限）
  B. current_tier 与 tier_kwh 的实际落档一致（多档场景逐个验）
  C. proj_rate 配置：默认 0.5224、可保存、可校验、非法值被拒
  D. 旧配置残留不影响 proj_rate
  E. history_collect 用 proj_rate 算预计电费（不是均价倒推）
  F. 前端源码约束：判档依据说明行 + 预计单价输入项 + 提交
"""
import os
import sys
import json
import re
import time
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


def cfg_of(region='sichuan', **kw):
    c = pt.default_config()
    c['region'] = region
    # ⚠️ 必须 enabled=True，否则 compute_bill 走「单一固定单价」分支，
    #    根本不会分档（current_tier 自然为 None）。
    c['enabled'] = True
    c.update(kw)
    return c


ts_mon = time.mktime((2026, 11, 10, 12, 0, 0, 0, 0, -1))

print('=' * 70)
print('v1.1.62 验证：判档依据可见 + 预计电费单价可调')
print('=' * 70)

# ---------------------------------------------------------------------------
print('\n=== A. compute_bill 输出判档结果 ===')
c = cfg_of()
b = pt.compute_bill(50.0, 0.0, c, ts=ts_mon)
check('返回体带 current_tier', 'current_tier' in b, sorted(b.keys()))
check('返回体带 tier_limits', 'tier_limits' in b, sorted(b.keys()))
check('50 度 → 第 1 档', b.get('current_tier') == 1, b.get('current_tier'))
check('tier_limits = [180, 280, None]',
      b.get('tier_limits') == [180.0, 280.0, None], b.get('tier_limits'))

# ---------------------------------------------------------------------------
print('\n=== B. current_tier 与实际落档一致 ===')
for kwh, exp in ((50.0, 1), (200.0, 2), (500.0, 3)):
    bb = pt.compute_bill(kwh, 0.0, c, ts=ts_mon)
    check('%g 度 → 第 %d 档' % (kwh, exp), bb.get('current_tier') == exp,
          bb.get('current_tier'))
    tk = bb.get('tier_kwh') or []
    # 实际有电量的最高档
    real = 0
    for i, v in enumerate(tk):
        if v > 1e-9:
            real = i + 1
    check('%g 度的 current_tier 与 tier_kwh 一致' % kwh, real == exp,
          'current=%s tier_kwh=%s' % (bb.get('current_tier'), tk))

# 谷段电量也占档位（官方口径：按月总电量判档）
bb = pt.compute_bill(0.0, 400.0, c, ts=ts_mon)
check('纯谷段 400 度 → 第 3 档（谷段也占档位）',
      bb.get('current_tier') == 3, bb.get('current_tier'))

# ---------------------------------------------------------------------------
print('\n=== C. proj_rate 配置 ===')
check('DEFAULT_PROJ_RATE = 0.5224', approx(pt.DEFAULT_PROJ_RATE, 0.5224),
      pt.DEFAULT_PROJ_RATE)
d = pt.default_config()
check('default_config 带 proj_rate = 0.5224',
      approx(d.get('proj_rate'), 0.5224), d.get('proj_rate'))

os.environ['FNSTYLE_DATA_DIR'] = tempfile.mkdtemp()
ok, msg = pt.save_config({'region': 'sichuan', 'enabled': True,
                           'valley_start': 23, 'valley_end': 7,
                           'proj_rate': 0.61})
check('保存 proj_rate=0.61 成功', ok is True, msg)
c2 = pt.load_config()
check('读回 proj_rate = 0.61', approx(c2.get('proj_rate'), 0.61),
      c2.get('proj_rate'))

ok, msg = pt.save_config({'region': 'sichuan', 'valley_start': 23,
                           'valley_end': 7, 'proj_rate': 'abc'})
check('非法 proj_rate 被拒绝', ok is False, msg)
ok, msg = pt.save_config({'region': 'sichuan', 'valley_start': 23,
                           'valley_end': 7, 'proj_rate': 99})
check('超范围 proj_rate 被拒绝', ok is False, msg)

# ---------------------------------------------------------------------------
print('\n=== D. 缺省时继承旧值（不被清零）===')
os.environ['FNSTYLE_DATA_DIR'] = tempfile.mkdtemp()
pt.save_config({'region': 'sichuan', 'valley_start': 23, 'valley_end': 7,
                'proj_rate': 0.7})
# 模拟前端没提交 proj_rate（老面板 / 其它地区）
pt.save_config({'region': 'tianjin', 'valley_start': 23, 'valley_end': 7})
c3 = pt.load_config()
check('前端未提交时 proj_rate 继承旧值 0.7',
      approx(c3.get('proj_rate'), 0.7), c3.get('proj_rate'))

# ---------------------------------------------------------------------------
print('\n=== E. describe() 透出 proj_rate ===')
os.environ['FNSTYLE_DATA_DIR'] = tempfile.mkdtemp()
pt.save_config({'region': 'sichuan', 'valley_start': 23, 'valley_end': 7,
                'proj_rate': 0.55})
desc = pt.describe()
check('describe.config.proj_rate = 0.55',
      approx((desc.get('config') or {}).get('proj_rate'), 0.55),
      (desc.get('config') or {}).get('proj_rate'))
raw = json.dumps(desc, ensure_ascii=False)
check('describe() 是合法 JSON', 'Infinity' not in raw and 'NaN' not in raw)

# ---------------------------------------------------------------------------
print('\n=== F. history_collect 用 proj_rate 算预计电费 ===')
HC = os.path.join(BASE, 'app', 'ui', 'history_collect.py')
with open(HC, encoding='utf-8') as f:
    hc = f.read()
check('history_collect 有 DEFAULT_PROJ_RATE 常量',
      'DEFAULT_PROJ_RATE = 0.5224' in hc)
check('history_collect 用 proj_rate 算预计电费',
      'month_proj_kwh"] * proj_rate' in hc.replace('(', '').replace(')', '')
      or 'result["month_proj_kwh"] * proj_rate' in hc
      or 'month_proj_kwh * proj_rate' in hc.replace('"', ''),
      '未找到 proj_rate 乘算')
check('history_collect 不再按均价倒推预计电费',
      'month_proj_kwh"] * eff' not in hc
      and 'month_proj_kwh * eff' not in hc.replace('"', ''),
      '仍见 eff 倒推')
check('history_collect 输出 proj_rate_yuan_per_kwh',
      'proj_rate_yuan_per_kwh' in hc)
check('history_collect 输出 current_tier',
      'result["current_tier"]' in hc)
check('history_collect 输出 tier_limits',
      'result["tier_limits"]' in hc)

# ---------------------------------------------------------------------------
print('\n=== G. 前端源码约束 ===')
JS = os.path.join(BASE, 'app', 'ui', 'static', 'js', 'monitor.js')
with open(JS, encoding='utf-8') as f:
    js = f.read()
CSS = os.path.join(BASE, 'app', 'ui', 'static', 'css', 'app.css')
with open(CSS, encoding='utf-8') as f:
    css = f.read()

check('前端有判档依据说明行（sub-tier）', 'sub-tier' in js)
check('判档说明含「判档」二字', '判档：' in js)
check('判档说明含「本机」口径', '本机' in js)
check('判档说明读 current_tier', 'current_tier' in js)
check('判档说明读 tier_limits', 'tier_limits' in js)
check('CSS 有 .sub-tier 样式', '.stat-cell .sub-tier' in css)

check('前端有预计电费单价输入项', 'data-tariff-proj-rate' in js)
check('前端有 sub-proj-rate 说明行', 'sub-proj-rate' in js)
check('CSS 有 .sub-proj-rate 样式', '.stat-cell .sub-proj-rate' in css)
check('capture 采集 proj_rate', 'proj_rate:' in js)
check('restore 回填 proj_rate', 'st.proj_rate !== null' in js)
check('保存载荷提交 proj_rate', 'payload.proj_rate' in js)
check('前端默认单价 0.5224', "'0.5224'" in js)

# ---- 判档区间口径（v1.1.62 bug 修复）----
# tier_limits 是「各档累计上限」[180, 280, null]
#   → 第 N 档下界 = 第 N-1 档上限（第 1 档 = 0），上界 = tier_limits[N-1]
# 原实现把本档上界当下界 → 第 1 档渲染成「181–180 度」
_seg = None
for _m in re.finditer(r'var lo = 0;', js):
    _seg = js[_m.start():_m.start() + 700]
    if 'tLimits' in _seg:
        break
check('判档区间下界循环只到 curTier-1（不含本档）',
      _seg is not None and 'ti < curTier' in _seg, (_seg or '')[:120])
check('判档区间不再用「本档上限当下界」的旧写法',
      _seg is not None and 'ti <= curTier' not in _seg, (_seg or '')[:120])
check('判档区间第 1 档下界为 0（渲染成 0–180）',
      _seg is not None and "lo > 0 ? (lo + 1)" in _seg, (_seg or '')[:120])

print('\n' + '=' * 70)
print('通过 %d 项，失败 %d 项' % (PASS, FAIL))
if FAIL:
    print('存在失败项')
    sys.exit(1)
print('全部通过 ✅')
