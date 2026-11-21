#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.1.63 验证：单一「电费单价」配置项（可调）+ 口径说明

历史沿革
--------
v1.1.62：老板提出两点 ——
  ① 「我没有输入电量，你怎么知道是按那个阶段进行计费呢？」
     → 判档靠 FnStyle 自测的本机月总电量，界面写明这一依据
  ② 「本月预计电费」不按阶梯、不分地区，统一用成都最低档 0.5224 元/kWh，
     并提供手动调整能力（当时是两个概念：计费阶梯 + 预计单价 proj_rate）

v1.1.63：两者**合并成一个**「电费单价」配置项 ——
  「根据成都最低档对应的电价进行计算（并提供可配置能力），
    删除阶梯计费和档位信息等，也不区分区域，就是一个电费单价的配置，
    并增加对应的说明即可」
  ⇒ 没有「阶段」可判 → 判档依据的疑问自然消解
  ⇒ proj_rate 与 rate_override 统一成一个 rate
  ⇒ 说明变成「用电量 × 电费单价」的口径说明

验证：
  A. 默认单价 = 0.5224（成都最低档）
  B. 单价可保存 / 可校验 / 非法值被拒 / 缺省时继承
  C. 旧配置里的 proj_rate / rate_override 迁移到 rate
  D. describe() 透出 rate 与 note
  E. history_collect 用同一个单价算已计电费与预计电费
  F. 前端源码约束：只有一个输入框 + 说明 + 提交
"""
import sys
import os
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


print('=' * 70)
print('v1.1.63 验证：单一「电费单价」配置项 + 口径说明')
print('=' * 70)

# ---------------------------------------------------------------------------
print('\n=== A. 默认单价 = 成都最低档 0.5224 ===')
check('DEFAULT_RATE = 0.5224', approx(pt.DEFAULT_RATE, 0.5224), pt.DEFAULT_RATE)
d = pt.default_config()
check('default_config 只有 rate + updated',
      set(d.keys()) == {'rate', 'updated'}, sorted(d.keys()))
check('default_config.rate = 0.5224', approx(d.get('rate'), 0.5224), d.get('rate'))
check('不再有独立的 proj_rate', 'proj_rate' not in d, sorted(d.keys()))
check('不再有独立的 rate_override', 'rate_override' not in d, sorted(d.keys()))

# ---------------------------------------------------------------------------
print('\n=== B. 单价可保存 / 可校验 ===')
os.environ['FNSTYLE_DATA_DIR'] = tempfile.mkdtemp()
ok, msg = pt.save_config({'rate': 0.61})
check('保存 rate=0.61 成功', ok is True, msg)
c2 = pt.load_config()
check('读回 rate = 0.61', approx(c2.get('rate'), 0.61), c2.get('rate'))

ok, msg = pt.save_config({'rate': 'abc'})
check('非法 rate 被拒绝', ok is False, msg)
ok, msg = pt.save_config({'rate': 99})
check('超范围 rate 被拒绝', ok is False, msg)
ok, msg = pt.save_config({'rate': -0.1})
check('负 rate 被拒绝', ok is False, msg)

print('\n--- 缺省时继承旧值（不被清零）---')
os.environ['FNSTYLE_DATA_DIR'] = tempfile.mkdtemp()
pt.save_config({'rate': 0.7})
pt.save_config({'updated': 1})  # 模拟前端没提交 rate
c3 = pt.load_config()
check('前端未提交时 rate 继承旧值 0.7',
      approx(c3.get('rate'), 0.7), c3.get('rate'))

# ---------------------------------------------------------------------------
print('\n=== C. 旧配置迁移 ===')
os.environ['FNSTYLE_DATA_DIR'] = tempfile.mkdtemp()
with open(pt._cfg_path(), 'w', encoding='utf-8') as f:
    json.dump({'proj_rate': 0.55, 'region': 'sichuan', 'enabled': True},
              f, ensure_ascii=False)
c4 = pt.load_config()
check('旧配置只有 proj_rate → 迁移为 rate',
      approx(c4.get('rate'), 0.55), c4.get('rate'))
check('旧配置里的 region 被丢弃', 'region' not in c4, sorted(c4.keys()))
check('旧配置里的 enabled 被丢弃', 'enabled' not in c4, sorted(c4.keys()))

os.environ['FNSTYLE_DATA_DIR'] = tempfile.mkdtemp()
with open(pt._cfg_path(), 'w', encoding='utf-8') as f:
    json.dump({'rate': 0.66, 'proj_rate': 0.55}, f, ensure_ascii=False)
c5 = pt.load_config()
check('新旧同时存在时以 rate 为准',
      approx(c5.get('rate'), 0.66), c5.get('rate'))

# ---------------------------------------------------------------------------
print('\n=== D. describe() 透出 rate 与 note ===')
os.environ['FNSTYLE_DATA_DIR'] = tempfile.mkdtemp()
pt.save_config({'rate': 0.5224})
desc = pt.describe()
check('describe.config.rate = 0.5224',
      approx((desc.get('config') or {}).get('rate'), 0.5224),
      (desc.get('config') or {}).get('rate'))
check('describe.rate = 0.5224', approx(desc.get('rate'), 0.5224), desc.get('rate'))
check('describe.default_rate = 0.5224',
      approx(desc.get('default_rate'), 0.5224), desc.get('default_rate'))
check('describe.unit = 元/kWh', desc.get('unit') == '元/kWh', desc.get('unit'))
check('describe.note 非空', bool(desc.get('note')))
raw = json.dumps(desc, ensure_ascii=False)
check('describe() 是合法 JSON', 'Infinity' not in raw and 'NaN' not in raw)
check('describe 不再透出 proj_rate', 'proj_rate' not in desc, sorted(desc.keys()))

# ---------------------------------------------------------------------------
print('\n=== E. history_collect 用同一个单价算电费 ===')
HC = os.path.join(BASE, 'app', 'ui', 'history_collect.py')
with open(HC, encoding='utf-8') as f:
    hc = f.read()
check('history_collect 输出 proj_rate_yuan_per_kwh（兼容字段）',
      'proj_rate_yuan_per_kwh' in hc)
check('history_collect 用配置的 rate 算预计电费',
      'result["month_proj_kwh"] * rate' in hc
      or 'month_proj_kwh"] * rate' in hc.replace('(', '').replace(')', ''),
      '未找到 rate 乘算')
check('history_collect 不再按均价倒推预计电费',
      'month_proj_kwh"] * eff' not in hc
      and 'month_proj_kwh * eff' not in hc.replace('"', ''),
      '仍见 eff 倒推')
check('history_collect 不再输出 current_tier',
      'result["current_tier"]' not in hc)
check('history_collect 不再输出 tier_limits',
      'result["tier_limits"]' not in hc)

# ---------------------------------------------------------------------------
print('\n=== F. 前端源码约束 ===')
JS = os.path.join(BASE, 'app', 'ui', 'static', 'js', 'monitor.js')
with open(JS, encoding='utf-8') as f:
    js = f.read()
CSS = os.path.join(BASE, 'app', 'ui', 'static', 'css', 'app.css')
with open(CSS, encoding='utf-8') as f:
    css = f.read()

check('前端有唯一的单价输入框 data-tariff-rate', 'data-tariff-rate' in js)
check('输入框是 number 类型', 'type="number"' in js)
check('输入框有 step=0.0001', 'step="0.0001"' in js)
check('输入框有 min=0 / max=10', 'min="0"' in js and 'max="10"' in js)
check('输入框带单位 元/kWh', 'tariff-unit' in js and '元/kWh' in js)
check('前端有口径说明容器 data-tariff-note', 'data-tariff-note' in js)
check('说明取自后端 d.note', 'd && d.note' in js or '(d && d.note)' in js)

check('无独立的预计单价输入项 data-tariff-proj-rate',
      'data-tariff-proj-rate' not in js)
check('无判档依据行 sub-tier', 'sub-tier' not in js)
check('capture 只采集 rate', 'rate:' in js and 'proj_rate:' not in js)
check('restore 只回填 rate', 'st.rate !== null' in js and 'st.proj_rate' not in js)
check('保存载荷只提交 rate', 'payload.rate' in js and 'payload.proj_rate' not in js)
check('前端默认单价 0.5224', "'0.5224'" in js)

check('CSS 有 .tariff-field 样式', '.tariff-field' in css)
check('CSS 有 .tariff-note 样式', '.tariff-note' in css)
check('CSS 有 .sub-rate 样式（能耗卡片显示单价）', '.sub-rate' in css)
check('CSS 有 .sub-proj-rate 样式（预计电费）', '.sub-proj-rate' in css)

print('\n' + '=' * 70)
print('通过 %d 项，失败 %d 项' % (PASS, FAIL))
if FAIL:
    print('存在失败项')
    sys.exit(1)
print('全部通过 ✅')
