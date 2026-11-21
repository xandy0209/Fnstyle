#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.1.63：单一单价电价模型与历史库的集成测试

验证整条链路：
  造功率采样点 → 写入临时历史库 → 归档（产生 kwh@HH 小时分桶）
  → split_energy_by_period() 统计当月电量 → energy_stats() 按单一单价算电费

重点验证：
  · 电量统计口径正确（当月总量 = 分桶之和）
  · **不再按谷时段拆分**（valley 恒为 0，全部计入 flat）
  · month_yuan = month_kwh × 配置里的单一单价
  · 配置里的单价是权威值（覆盖入口参数 rate）
  · daily 接口的 cost 也按同一单价
  · 阶梯/分时/地区相关字段不再输出
"""
import os
import sys
import time
import json
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI = os.path.join(BASE, 'app', 'ui')
sys.path.insert(0, UI)

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


def approx(a, b, tol=1e-3):
    return abs(float(a) - float(b)) <= tol


TMP = tempfile.mkdtemp(prefix='fnst_tariff_int_')
os.environ['FNSTYLE_DATA_DIR'] = TMP

import storage_collect as sc   # noqa: E402
import history_collect as hc   # noqa: E402
import power_tariff as pt      # noqa: E402

# ---------------------------------------------------------------------------
print('\n=== 1. 构造含昼夜差异的历史库 ===')
# 造 3 天的点：白天 60W、夜里 20W，每 5 分钟一个点
db = os.path.join(TMP, 'storage.db')
store = sc.HistoryStore(db)
now = time.time()
lt = time.localtime(now - 2 * 86400)
start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
step = 300.0
rows = []
t = start
while t < now:
    hour = time.localtime(t).tm_hour
    w = 20.0 if (hour >= 23 or hour < 7) else 60.0
    rows.append((t, 'power', w))
    t += step
store.write(rows)
check('写入 %d 个采样点' % len(rows), len(rows) > 100)

# 归档（产生 kwh@HH 分桶）
conn = store._connect()
with conn:
    conn.execute("INSERT INTO meta(key,value) VALUES('daily_archived_until',?)",
                 (repr(start),))
conn.close()
n = store.archive_daily(now, default_dt=60)
check('归档写入 %d 行' % n, n > 0)

# 让 history_collect 用同一个库
hc._STORE = store

# ---------------------------------------------------------------------------
print('\n=== 2. 电量统计（不再分谷平）===')
split = hc.split_energy_by_period()
check('统计结果非空', split.get('total', 0) > 0, split.get('total'))
check('flat = total（全部电量计入 flat）',
      approx(split['flat'], split['total'], 1e-6),
      '%.4f vs %.4f' % (split['flat'], split['total']))
check('valley 恒为 0（不再分谷段）',
      approx(split['valley'], 0.0, 1e-9), split['valley'])
check('total = flat + valley',
      approx(split['total'], split['flat'] + split['valley'], 1e-6))
check('by_hour 分桶保留（柱状图要用）',
      isinstance(split.get('by_hour'), dict) and len(split['by_hour']) > 0,
      len(split.get('by_hour') or {}))
print('    当月总电量 %.4f kWh' % split['total'])

# ---------------------------------------------------------------------------
print('\n=== 3. 默认单价下的电费 ===')
pt.save_config({'rate': 0.5224})
st = hc.energy_stats(0.6, None)
check('tariff_mode = fixed', st.get('tariff_mode') == 'fixed',
      st.get('tariff_mode'))
check('配置里的 rate 是权威值（覆盖入口 0.6）',
      approx(st['rate_yuan_per_kwh'], 0.5224, 1e-6),
      st['rate_yuan_per_kwh'])
check('month_yuan = month_kwh × 0.5224',
      approx(st['month_yuan'], st['month_kwh'] * 0.5224, 0.02),
      '%.4f vs %.4f' % (st['month_yuan'], st['month_kwh'] * 0.5224))
check('tariff_rate 与 rate_yuan_per_kwh 一致',
      approx(st.get('tariff_rate'), st['rate_yuan_per_kwh'], 1e-6),
      (st.get('tariff_rate'), st['rate_yuan_per_kwh']))
check('today_yuan 也按同一单价',
      approx(st['today_yuan'], st['today_kwh'] * 0.5224, 0.02),
      '%.4f vs %.4f' % (st['today_yuan'], st['today_kwh'] * 0.5224))
check('month_kwh 与 split 的总量一致',
      approx(st['month_kwh'], split['total'], 1e-3),
      '%.4f vs %.4f' % (st['month_kwh'], split['total']))
check('综合单价 eff_rate = rate（单一时恒等）',
      approx(st['eff_rate_yuan_per_kwh'], 0.5224, 1e-3),
      st['eff_rate_yuan_per_kwh'])
check('预计单价 proj_rate = 同一单价',
      approx(st['proj_rate_yuan_per_kwh'], 0.5224, 1e-3),
      st['proj_rate_yuan_per_kwh'])

# ---------------------------------------------------------------------------
print('\n=== 4. 阶梯/分时/地区字段不再输出 ===')
for k in ('tier_kwh', 'tier_rates', 'current_tier', 'tier_limits',
          'tariff_detail', 'valley_kwh', 'valley_label', 'carry_in',
          'tariff_region', 'tariff_voltage'):
    check('energy_stats 不再输出 %s' % k, k not in st,
          '仍存在: %s' % sorted(st.keys()))

# ---------------------------------------------------------------------------
print('\n=== 5. 改单价 → 电费随之变化 ===')
pt.save_config({'rate': 0.6})
st2 = hc.energy_stats(0.6, None)
check('单价改成 0.6 后 rate 生效',
      approx(st2['rate_yuan_per_kwh'], 0.6, 1e-6),
      st2['rate_yuan_per_kwh'])
check('month_yuan 随之重算',
      approx(st2['month_yuan'], st2['month_kwh'] * 0.6, 0.02),
      '%.4f vs %.4f' % (st2['month_yuan'], st2['month_kwh'] * 0.6))
check('电量本身不受单价影响',
      approx(st2['month_kwh'], st['month_kwh'], 1e-3),
      '%.4f vs %.4f' % (st2['month_kwh'], st['month_kwh']))

pt.save_config({'rate': 1.0})
st3 = hc.energy_stats(0.6, None)
check('单价 1.0 时 month_yuan = month_kwh',
      approx(st3['month_yuan'], st3['month_kwh'], 0.05),
      '%.4f vs %.4f' % (st3['month_yuan'], st3['month_kwh']))

# ---------------------------------------------------------------------------
print('\n=== 6. 预计电费随单价变化 ===')
pt.save_config({'rate': 0.5224})
st4 = hc.energy_stats(0.6, None)
check('month_proj_yuan 有值', st4.get('month_proj_yuan') is not None)
check('month_proj_yuan > month_yuan（本月已用 < 全月预计）',
      st4['month_proj_yuan'] > st4['month_yuan'],
      '%.2f vs %.2f' % (st4['month_proj_yuan'], st4['month_yuan']))
check('预计电费也按同一单价',
      approx(st4['month_proj_yuan'], st4['month_proj_kwh'] * 0.5224, 0.05),
      '%.2f vs %.2f' % (st4['month_proj_yuan'], st4['month_proj_kwh'] * 0.5224))

# ---------------------------------------------------------------------------
print('\n=== 7. daily 接口的 cost ===')
# ⚠️ daily_energy 的单价来自 eff_rate 入参 —— 服务端 /api/power/daily
#    会把配置里的 rate 作为 eff_rate 传进来（见 fnstyle_server._handle_power_daily）
d = hc.daily_energy(0.6, 7, eff_rate=0.5224)
days_with_data = [x for x in d['days'] if x.get('kwh') is not None]
check('daily 有数据的天', len(days_with_data) > 0, len(days_with_data))
if days_with_data:
    x = days_with_data[0]
    check('daily cost = kwh × rate（统一单价口径）',
          approx(x['cost'], x['kwh'] * d['rate_yuan_per_kwh'], 0.02),
          '%.4f vs %.4f' % (x['cost'], x['kwh'] * d['rate_yuan_per_kwh']))
check('daily 的 rate 来自 eff_rate（单一单价）',
      approx(d['rate_yuan_per_kwh'], 0.5224, 1e-6),
      d['rate_yuan_per_kwh'])

# ---------------------------------------------------------------------------
print('\n=== 8. 阶梯台账已废弃（不再写）===')
hc.update_tier_ledger(50.0, 20.0)
check('update_tier_ledger 返回 False（空实现）',
      hc.update_tier_ledger(50.0, 20.0) is False)

# ---------------------------------------------------------------------------
print('\n=== 9. 旧配置升级：废弃键不影响计算 ===')
with open(pt._cfg_path(), 'w', encoding='utf-8') as f:
    json.dump({'rate': 0.7, 'region': 'tianjin', 'enabled': True,
               'overrides': {'x': 1}}, f, ensure_ascii=False)
st5 = hc.energy_stats(0.6, None)
check('旧库升级后 rate 仍生效',
      approx(st5['rate_yuan_per_kwh'], 0.7, 1e-6),
      st5['rate_yuan_per_kwh'])
check('旧库升级后仍无阶梯字段',
      'current_tier' not in st5 and 'tier_kwh' not in st5)

# ---------------------------------------------------------------------------
print('\n%s' % ('-' * 56))
print('通过 %d 项，失败 %d 项' % (PASS, FAIL))
if FAIL:
    sys.exit(1)
print('全部通过')
