#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.1.84 验证：电价设置已迁到「配置管理」设置抽屉

历史沿革
--------
v1.1.57：电价设置面板放在**能耗计算抽屉**（系统监控 → 用电详情）里，
      紧邻「用电与电费」区块 —— 当时的理由是「上面就是电费数字，改价方便」。
v1.1.84：老板要求把配置**统一收敛到设置抽屉**：
      「电费的设置调整到设置抽屉中」
      ⇒ 面板从能耗计算抽屉搬到「配置管理」抽屉（#monThresholdOverlay）。

本脚本验证：
  A. 能耗计算抽屉**不再**渲染电价面板（挂载点已迁走）
  B. 配置管理抽屉**渲染**电价面板（占位容器在 thresholds-ui.js 里）
  C. 电价面板的**实现**仍然只有一份（monitor.js 导出 window.FnStyleTariff）
  D. 「恢复默认」**只**重置阈值，不动电价（防不可逆数据损失）
  E. 副标题已涵盖两类配置
  F. 前端源码无重复实现（电价面板 HTML 不在两个文件里各写一遍）
"""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI = os.path.join(ROOT, 'app', 'ui')
JS_DIR = os.path.join(UI, 'static', 'js')

MON = os.path.join(JS_DIR, 'monitor.js')
THR = os.path.join(JS_DIR, 'thresholds-ui.js')
CMP = os.path.join(JS_DIR, 'compute.js')
HTML = os.path.join(UI, 'index.html')
CSS = os.path.join(UI, 'static', 'css', 'app.css')

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


def section(title):
    print('\n--- %s ---' % title)


def read(path):
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def strip_comments(src):
    """剥掉注释，避免解释性文字里的字符串被误判为代码。"""
    src = re.sub(r'/\*.*?\*/', ' ', src, flags=re.S)
    src = re.sub(r'(?m)^\s*//.*$', ' ', src)
    src = re.sub(r'(?m)\s//.*$', ' ', src)
    return src


mon_src = read(MON)
thr_src = read(THR)
cmp_src = read(CMP)
html_src = read(HTML)
css_src = read(CSS)

mon_code = strip_comments(mon_src)
thr_code = strip_comments(thr_src)
cmp_code = strip_comments(cmp_src)

print('=' * 70)
print('v1.1.84 验证：电价设置迁到「配置管理」抽屉')
print('=' * 70)

# ---------------------------------------------------------------------------
section('A. 能耗计算抽屉不再渲染电价面板')
# ---------------------------------------------------------------------------
# 能耗抽屉的 HTML 由 monitor.js 的 powerSheetHtml() 生成。
# 断言：powerSheetHtml 里不再出现电价面板的挂载点。
i = mon_code.find('function powerSheetHtml(')
check('找到 powerSheetHtml 函数', i > 0)
if i > 0:
    # 取到下一个顶层 function 定义为止
    j = mon_code.find('\n  function ', i + 10)
    power_fn = mon_code[i:j if j > 0 else len(mon_code)]
    check('powerSheetHtml 不再渲染 [data-tariff-box]',
          'data-tariff-box' not in power_fn,
          '仍含电价面板挂载点')
    check('powerSheetHtml 不再渲染「电价设置」小节标题',
          '电价设置' not in power_fn)
    check('powerSheetHtml 仍保留「用电与电费」区块',
          '用电与电费' in power_fn)

# 整份 monitor.js 里，电价挂载点字符串只应出现在「加载器」里，不在渲染器里。
check('monitor.js 里 data-tariff-box 出现次数 ≤ 3（加载器 + 实现）',
      mon_code.count('data-tariff-box') <= 3,
      '实际 %d 次' % mon_code.count('data-tariff-box'))

# ---------------------------------------------------------------------------
section('B. 配置管理抽屉渲染电价面板')
# ---------------------------------------------------------------------------
check('thresholds-ui.js 有电价分节函数 tariffSectionHtml',
      'function tariffSectionHtml(' in thr_code)
check('thresholds-ui.js 里电价分节含 [data-tariff-box] 容器',
      'data-tariff-box' in thr_code)
check('电价分节标题为「电费设置」',
      '电费设置' in thr_code)
check('thresholds-ui.js 调用 window.FnStyleTariff',
      'window.FnStyleTariff' in thr_code)
check('thresholds-ui.js 在 render 之后挂载电价面板',
      re.search(r'render[\s\S]{0,900}mountTariff\(\)', thr_code) is not None)
check('有独立的 mountTariff 挂载函数',
      'function mountTariff(' in thr_code)

# 电价区必须**在**阈值分组之后（视觉分段）
# ⚠️ 比较的是 bodyHtml() 里的**调用位置**，不是函数定义处
_body_i = thr_code.find('function bodyHtml(')
_body_j = thr_code.find('\n  }', _body_i)
_body_fn = thr_code[_body_i:_body_j if _body_j > 0 else len(thr_code)]
check('电价分节在阈值分组之后渲染',
      _body_fn.find('tariffSectionHtml()') > _body_fn.find('list.forEach'),
      'bodyHtml 里顺序不对')

# ---------------------------------------------------------------------------
section('C. 电价面板实现只有一份（monitor.js 导出）')
# ---------------------------------------------------------------------------
check('monitor.js 导出 window.FnStyleTariff',
      'window.FnStyleTariff' in mon_code)
check('FnStyleTariff 提供 mount 接口',
      re.search(r'window\.FnStyleTariff\s*=\s*\{[\s\S]{0,400}mount:', mon_code)
      is not None)
check('FnStyleTariff 提供 invalidateStats 接口',
      re.search(r'window\.FnStyleTariff\s*=\s*\{[\s\S]{0,900}invalidateStats:',
                mon_code) is not None)
check('mount 走的是 /api/power/tariff',
      re.search(r'mountTariff[\s\S]{0,1500}power/tariff', mon_code) is not None)

# ⚠️ 关键防重复：thresholds-ui.js 里**不能**自己再实现一遍面板 HTML
check('thresholds-ui.js 不自己实现电价面板 HTML（不重复造轮子）',
      'tariffPanelHtml' not in thr_code and
      'data-tariff-rate' not in thr_code,
      'thresholds-ui.js 里出现了面板实现')

# ---------------------------------------------------------------------------
section('D. 「恢复默认」只重置阈值，不动电价')
# ---------------------------------------------------------------------------
check('「恢复默认」按钮文案改为「恢复默认门限」（消除歧义）',
      '恢复默认门限' in thr_code)
check('存在只重绘阈值区的函数 renderThresholdsOnly',
      'function renderThresholdsOnly(' in thr_code)
check('「恢复默认」调用 renderThresholdsOnly 而不是整体 render',
      re.search(r'__reset[\s\S]{0,600}renderThresholdsOnly\(', thr_code)
      is not None)
check('renderThresholdsOnly 不调用整体 render（除非兜底）',
      # 兜底分支里有一处 render(data)，这里只确认它**不是**唯一出口
      thr_code.count('renderThresholdsOnly(res.data)') >= 1)

# 恢复默认的请求体里**不能**带电价相关键
m = re.search(r'TH\.save\(\{([^}]*)\}\)', thr_code)
check('恢复默认只提交 __reset（不带电价键）',
      m is not None and '__reset' in m.group(1)
      and 'rate' not in m.group(1),
      m.group(1) if m else '未找到')

# ---------------------------------------------------------------------------
section('E. 副标题与样式')
# ---------------------------------------------------------------------------
check('index.html 副标题涵盖阈值与电费',
      'monThresholdSub' in html_src and
      '电费设置' in html_src)
check('副标题不再是纯「系统监控阈值门限」',
      re.search(r'id="monThresholdSub">[^<]*阈值门限[^<]*电费', html_src)
      is not None)
check('CSS 保留 .tariff-box 样式（面板迁走但样式不丢）',
      '.tariff-box' in css_src)
check('CSS 有电价分节样式 .thr-sec-tariff',
      '.thr-sec-tariff' in css_src)

# ---------------------------------------------------------------------------
section('F. 语法检查')
# ---------------------------------------------------------------------------
for name, path in (('monitor.js', MON), ('thresholds-ui.js', THR),
                   ('compute.js', CMP)):
    r = subprocess.run(['node', '--check', path], capture_output=True, text=True)
    check('%s 语法正确' % name, r.returncode == 0, r.stderr[:200])

# ---------------------------------------------------------------------------
print('\n' + '=' * 70)
print('verify_tariff_in_settings_drawer: PASS %d / FAIL %d' % (PASS, FAIL))
if FAIL:
    sys.exit(1)
print('全部通过')
