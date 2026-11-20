#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.1.57：电价模型与历史库的集成测试

验证整条链路：
  造功率采样点 → 写入临时历史库 → 归档（产生 kwh@HH 小时分桶）
  → split_energy_by_period() 拆谷/平 → energy_stats() 算阶梯电费

重点验证：
  · 拆分出的谷+平 = 总电量（与 energy_stats 的 month_kwh 一致）
  · 谷段电量确实落在低谷时段
  · 阶梯模式下 month_yuan 不再是「总量 × 单一单价」
  · 未启用时行为与 v1.1.56 完全一致（向后兼容）
"""
import os
import sys
import time
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
print('\n=== 1. 构造含谷/平差异的历史库 ===')
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
print('\n=== 2. 谷/平拆分 ===')
split = hc.split_energy_by_period()
check('拆分结果非空', split.get('total', 0) > 0, split.get('total'))
check('谷 + 平 = 总电量',
      approx(split['flat'] + split['valley'], split['total'], 1e-3),
      '%.4f + %.4f vs %.4f' % (split['flat'], split['valley'], split['total']))
check('谷段电量 > 0', split['valley'] > 0, split['valley'])
check('平段电量 > 0', split['flat'] > 0, split['flat'])
# 夜里功率低 → 谷段电量应显著少于平段（谷段 8h/天 vs 平段 16h/天）
ratio = split['valley'] / max(1e-9, split['flat'])
check('谷段电量显著少于平段（夜间低功耗）ratio=%.3f' % ratio, ratio < 0.5, ratio)
print('    平段 %.4f kWh / 谷段 %.4f kWh' % (split['flat'], split['valley']))

# ---------------------------------------------------------------------------
print('\n=== 3. 未启用：与 v1.1.56 行为一致（向后兼容） ===')
cfg = pt.default_config()
cfg['enabled'] = False
pt.save_config(cfg)
st = hc.energy_stats(0.6, None)
check('tariff_mode 不存在或为 fixed',
      st.get('tariff_mode') in (None, 'fixed'), st.get('tariff_mode'))
check('month_yuan = month_kwh × 0.6',
      approx(st['month_yuan'], st['month_kwh'] * 0.6, 0.02),
      '%.4f vs %.4f' % (st['month_yuan'], st['month_kwh'] * 0.6))
check('无 valIey_kwh 字段（未拆分）', 'valley_kwh' not in st or st.get('valley_kwh') is None)

# ---------------------------------------------------------------------------
print('\n=== 4. 启用阶梯：电费按谷/平 + 阶梯算 ===')
cfg = pt.default_config()
cfg['enabled'] = True
pt.save_config(cfg)
st = hc.energy_stats(0.6, None)
check('tariff_mode = tiered', st.get('tariff_mode') == 'tiered', st.get('tariff_mode'))
check('tariff_region = sichuan', st.get('tariff_region') == 'sichuan',
      st.get('tariff_region'))
check('返回谷段电量', st.get('valley_kwh') is not None and st.get('valley_kwh') > 0,
      st.get('valley_kwh'))
check('返回平段电量', st.get('flat_kwh') is not None and st.get('flat_kwh') > 0,
      st.get('flat_kwh'))
check('谷+平 ≈ month_kwh',
      approx(st['valley_kwh'] + st['flat_kwh'], st['month_kwh'], 1e-3),
      '%.4f vs %.4f' % (st['valley_kwh'] + st['flat_kwh'], st['month_kwh']))

# 电费必须小于「总量 × 0.6」（因为有低谷优惠）
naive = st['month_kwh'] * 0.6
check('阶梯电费 < 总量×0.6（低谷拉低）%.4f < %.4f' % (st['month_yuan'], naive),
      st['month_yuan'] < naive, '%.4f vs %.4f' % (st['month_yuan'], naive))
check('电费 > 0', st['month_yuan'] > 0, st['month_yuan'])
check('有阶梯明细', isinstance(st.get('tariff_detail'), list) and
      len(st['tariff_detail']) == 3, st.get('tariff_detail'))
check('有阶梯档位电量', isinstance(st.get('tier_kwh'), list) and
      len(st['tier_kwh']) == 3, st.get('tier_kwh'))
check('三档电量之和 = 本月总电量',
      approx(sum(st['tier_kwh']), st['month_kwh'], 1e-3),
      '%.4f vs %.4f' % (sum(st['tier_kwh']), st['month_kwh']))
check('综合单价 = month_yuan / month_kwh',
      approx(st['eff_rate_yuan_per_kwh'],
             st['month_yuan'] / st['month_kwh'], 1e-2),
      st['eff_rate_yuan_per_kwh'])

# ---------------------------------------------------------------------------
print('\n=== 5. 阶梯模式下预计电费随之变化 ===')
st2 = hc.energy_stats(0.6, None)
check('month_proj_yuan 有值', st2.get('month_proj_yuan') is not None)
check('month_proj_yuan > month_yuan（本月已用 < 全月预计）',
      st2['month_proj_yuan'] > st2['month_yuan'],
      '%.2f vs %.2f' % (st2['month_proj_yuan'], st2['month_yuan']))

# ---------------------------------------------------------------------------
print('\n=== 6. daily 接口在阶梯模式下的 cost ===')
d = hc.daily_energy(0.6, 7)
days_with_data = [x for x in d['days'] if x.get('kwh') is not None]
check('daily 有数据的天', len(days_with_data) > 0, len(days_with_data))
if days_with_data:
    x = days_with_data[0]
    check('daily cost = kwh × rate（统一单价口径）',
          approx(x['cost'], x['kwh'] * d['rate_yuan_per_kwh'], 0.02),
          '%.4f vs %.4f' % (x['cost'], x['kwh'] * d['rate_yuan_per_kwh']))

# ---------------------------------------------------------------------------
print('\n=== 7. 切换地区 → 行为变化 ===')
cfg2 = pt.default_config()
cfg2['enabled'] = True
cfg2['region'] = 'tianjin'
pt.save_config(cfg2)
st3 = hc.energy_stats(0.6, None)
check('地区切到天津', st3.get('tariff_region') == 'tianjin', st3.get('tariff_region'))
# 天津居民不执行峰谷分时 → 谷段电费应为 0
check('天津无低谷优惠（谷段电费为 0）',
      st3.get('valley_yuan') is not None and st3['valley_yuan'] == 0.0,
      st3.get('valley_yuan'))

cfg3 = pt.default_config()
cfg3['enabled'] = True
cfg3['region'] = 'flat'
pt.save_config(cfg3)
st4 = hc.energy_stats(0.6, None)
check('固定单价地区电费 = 总量×0.6',
      approx(st4['month_yuan'], st4['month_kwh'] * 0.6, 0.02),
      '%.4f' % st4['month_yuan'])

# ---------------------------------------------------------------------------
print('\n=== 8. 阶梯台账写入 ===')
pt.save_config(pt.default_config() | {'enabled': True, 'region': 'sichuan'})
hc.update_tier_ledger(50.0, 20.0)
mk = pt._month_key()
led = pt.load_ledger(mk)
# 2026 年（现在）不满足 carry_over_since=2027-01 → 不应写台账
check('2026 年不写台账（结转规则自 2027-01 起）',
      led.get('tier1_used', 0) == 0.0, led)

# ---------------------------------------------------------------------------
print('\n%s' % ('-' * 56))
print('通过 %d 项，失败 %d 项' % (PASS, FAIL))
if FAIL:
    sys.exit(1)
print('全部通过')
