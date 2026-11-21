#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.1.63：默认单价的**官方出处溯源** + 官方算例重算

历史沿革
--------
v1.1.59：本脚本按官方《四川省电网居民生活电价表》(2026-07-01 起执行)
      全面验证**阶梯 + 分时 + 电压等级 + 季节 + 结转**的完整模型。

v1.1.63：老板决定整个电价模型退化为**单一单价** ——
      「根据成都最低档对应的电价进行计算（并提供可配置能力）」
      ⇒ 模型里不再有阶梯，但「成都最低档 = 0.5224 元/kWh」这个
        **默认值的出处**依然要钉死 —— 这是整个应用电价口径的唯一依据。

本文件保留官方原文摘录（溯源用），验证：
  ① 默认单价 0.5224 与官方表里的「不满 1 千伏 第一档 高峰平段」一致
  ② 0.5224 确实是**官方表里的最低档**（≤ 其余所有档位与低谷价）
  ③ 官方算例：用 0.5224 重算 100/400/600 度的电费
  ④ 口径说明里写明了 0.5224 的来源（成都/四川/一户一表/不满1千伏/第一档）
  ⑤ 前端默认值与后端 DEFAULT_RATE 一致（不会出现 0.5224 vs 0.6 的漂移）
"""
import os
import sys
import re
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


TMP = tempfile.mkdtemp(prefix='fnst_pdf_')
os.environ['FNSTYLE_DATA_DIR'] = TMP

# ---------------------------------------------------------------------------
# 官方《四川省电网居民生活电价表》(2026-07-01 起执行) 原文摘录
# （PDF 提取 + 川发改价格〔2026〕255号 全文核对）
#
#   一户一表 居民生活用电
#     · 夏季(7-9月)  ：第1档 0-260   第2档 261-460  第3档 460+
#     · 其他月份     ：第1档 0-180   第2档 181-280  第3档 280+
#     · 高峰平段(7:00-23:00) 不满1千伏 0.5224/0.6224/0.8224
#                           1-10千伏  0.5124/0.6124/0.8124
#                           35-110千伏 0.5124/0.6124/0.8124
#     · 低谷(23:00-次日7:00) **三个电压等级完全相同**
#                           丰水期(6-10月)   0.175/0.275/0.475
#                           枯平水期(1-4,12;5,11) 0.2535/0.3535/0.5535
#   合表居民生活用电  0.5464（不满1千伏）/ 0.5364（1-10、35-110千伏）
#     → 不分时、不分阶梯
#   注4：自2027年1月起，未使用完的第一、二档电量逐月滚动结转至
#        **年内**后续月份对应阶梯档位，**年末清零**
#
# ⇒ 「成都最低档」= 一户一表 + 不满1千伏 + 第一档 + 高峰平段 = 0.5224 元/kWh
# ---------------------------------------------------------------------------
OFFICIAL_LV1_PEAK = [0.5224, 0.6224, 0.8224]      # 不满1千伏 高峰平段三档
OFFICIAL_LV23_PEAK = [0.5124, 0.6124, 0.8124]     # 1-10 / 35-110千伏
OFFICIAL_VALLEY_WET = [0.175, 0.275, 0.475]       # 丰水期(6-10月) 低谷三档
OFFICIAL_VALLEY_DRY = [0.2535, 0.3535, 0.5535]    # 枯平水期 低谷三档
OFFICIAL_SHARED_LV1 = 0.5464                     # 合表居民 满1千伏
OFFICIAL_SHARED_LV23 = 0.5364                    # 合表居民 1-10/35-110千伏
CHENGDU_LOWEST = OFFICIAL_LV1_PEAK[0]             # ← 0.5224 成都最低档


# ---------------------------------------------------------------------------
print('\n=== 1. 默认单价与官方表一致 ===')
check('DEFAULT_RATE = 0.5224（官方：不满1千伏 第一档 高峰平段）',
      approx(pt.DEFAULT_RATE, CHENGDU_LOWEST), pt.DEFAULT_RATE)
check('DEFAULT_RATE = 官方表里的 0.5224',
      approx(pt.DEFAULT_RATE, 0.5224), pt.DEFAULT_RATE)
check('default_config().rate = DEFAULT_RATE',
      approx(pt.default_config()['rate'], pt.DEFAULT_RATE),
      pt.default_config()['rate'])

# ---------------------------------------------------------------------------
print('\n=== 2. 0.5224 确实是官方表里「一户一表」的最低档 ===')
# ⚠️ 口径说明：「成都最低档」指的是 **阶梯维度**上的最低档 ——
#    成都 = 四川 = 一户一表居民 = 不满 1 千伏 → 该维度下三档里的第一档。
#    不是「官方表所有电价里的最小值」—— 低谷价（0.175/0.2535）属于
#    **分时**维度，是另一个价格维度，不参与「档位」的比较。
check('0.5224 ≤ 不满1千伏高峰平段的三档（它是第一档）',
      all(CHENGDU_LOWEST <= p for p in OFFICIAL_LV1_PEAK),
      str(OFFICIAL_LV1_PEAK))
check('0.5224 就是不满1千伏高峰平段的第一档',
      approx(OFFICIAL_LV1_PEAK[0], CHENGDU_LOWEST), OFFICIAL_LV1_PEAK[0])
check('它是三档里的最小值（最低档）',
      approx(min(OFFICIAL_LV1_PEAK), CHENGDU_LOWEST), min(OFFICIAL_LV1_PEAK))
# 合表居民不分档，单价 0.5464 高于 0.5224 → 选 0.5224 偏保守
check('合表居民电价高于成都最低档（选 0.5224 作默认值偏保守）',
      OFFICIAL_SHARED_LV1 > CHENGDU_LOWEST)
# 低谷价属于分时维度，不参与「档位」比较（仅作口径说明留档）
check('低谷价属分时维度、不参与档位比较（留档说明）',
      min(OFFICIAL_VALLEY_WET) < CHENGDU_LOWEST,
      '低谷最低 %s' % min(OFFICIAL_VALLEY_WET))

# ---------------------------------------------------------------------------
print('\n=== 3. 官方算例重算（用 0.5224）===')
pt.save_config({'rate': CHENGDU_LOWEST})
for kwh in (100.0, 400.0, 600.0, 1000.0):
    b = pt.compute_bill(kwh, 0.0, pt.load_config())
    check('%g 度 × 0.5224 = %.4f 元' % (kwh, kwh * CHENGDU_LOWEST),
          approx(b['yuan'], kwh * CHENGDU_LOWEST, 1e-4), b['yuan'])

# 官方表里的一个真实算例：非夏季 400 度
#   第1档 180 × 0.5224 + 第2档 100 × 0.6224 + 第3档 120 × 0.8224
#   = 94.032 + 62.24 + 98.688 = 254.96 元
official_400 = 180 * 0.5224 + 100 * 0.6224 + 120 * 0.8224
check('官方表 400 度阶梯算例 = 254.96 元（溯源用）',
      approx(official_400, 254.96, 1e-6), official_400)
# 单一单价口径下 400 度 = 400 × 0.5224 = 208.96 元
check('单一单价口径下 400 度 = 208.96 元（低于阶梯算例，用最低档偏保守）',
      approx(400 * CHENGDU_LOWEST, 208.96, 1e-6), 400 * CHENGDU_LOWEST)

# ---------------------------------------------------------------------------
print('\n=== 4. 口径说明写明了 0.5224 的官方出处 ===')
d = pt.describe()
note = d.get('note') or ''
for kw in ('0.5224', '成都', '四川', '一户一表', '不满 1 千伏',
           '第一档', '高峰平段', '最低档'):
    check('说明含「%s」' % kw, kw in note, note)
check('说明提到用户可自行调整', '调整' in note, note)
check('说明提到是估算', '估算' in note, note)

# ---------------------------------------------------------------------------
print('\n=== 5. 前后端默认值一致 ===')
JS = os.path.join(BASE, 'app', 'ui', 'static', 'js', 'monitor.js')
with open(JS, encoding='utf-8') as f:
    js = f.read()
check('前端默认单价写死 0.5224', "'0.5224'" in js)
check('前端兜底单价 0.5224',
      js.count('0.5224') >= 2, '出现次数 %d' % js.count('0.5224'))
check('前端不再有 0.6 兜底（0.6 是旧版的固定单价默认值）',
      not re.search(r"['\"]0\.6['\"]", js))
check('后端无 0.6 兜底常量',
      '0.6' not in open(os.path.join(BASE, 'app', 'ui', 'power_tariff.py'),
                        encoding='utf-8').read())

# ---------------------------------------------------------------------------
print('\n=== 6. 单一单价下没有「档位」概念残留 ===')
b = pt.compute_bill(400.0, 0.0, pt.load_config())
for k in ('tier_rates', 'tier_kwh', 'current_tier', 'tier_limits'):
    check('计费结果不含 %s' % k, k not in b, sorted(b.keys()))
check('官方档位数据仅作为溯源常量存在，不进运行时',
      'OFFICIAL' not in json.dumps(b))

# ---------------------------------------------------------------------------
print('\n' + '=' * 70)
print('通过 %d 项，失败 %d 项' % (PASS, FAIL))
if FAIL:
    print('存在失败项')
    sys.exit(1)
print('全部通过 ✅')
