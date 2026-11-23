#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.1.84 验证：超阈值告警引擎 + 通知推送配置

老板需求：
  「对于超阈值的生成告警消息，提供通过飞牛的通知消息Webhook进行推送的能力，
    同时提供通知消息的选择配置。」

本脚本验证：
  A. 告警判定口径与前端一致（>= crit 判严重、>= warn 判留意）
  B. 判定只看**数值本身**（0 是合法值、缺数据不当成正常）
  C. 缺数据的指标不产生告警（不静默漏报也不误报）
  D. 容量 / 温度取**最大值**不取平均（与 overview.js 口径一致）
  E. 通知配置：默认关闭、逐项校验、非法值被拒
  F. 选择配置：指标 / 级别 / 通道 三类可选项都能独立开关
  G. 去抖：边沿触发 + 静默期 + 等级升级立即发（不刷屏）
  H. 消息格式兼容飞书 / 钉钉 / 企业微信机器人
  I. 阈值键**真实存在**于 monitor_thresholds.SPEC（拼错 ⇒ 永不触发）
  J. 服务端接口齐备（GET/POST/test/status）
  K. 采集器已启动告警线程
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI = os.path.join(ROOT, 'app', 'ui')
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


def section(title):
    print('\n--- %s ---' % title)


def _strip_comments(src):
    """剥掉注释，只留真实代码。

    ⚠️ 断言源码时**必须**先剥注释 —— 解释性注释里会引用原代码字符串，
       直接 grep 会误判（比如注释里写了 `_mc.alert_status()`，
       断言「不该出现这个调用」就会假红）。
    """
    import re
    src = re.sub(r'/\*.*?\*/', ' ', src, flags=re.S)
    out = []
    for line in src.splitlines():
        s = line.strip()
        if s.startswith('#'):
            continue
        # 去掉行尾注释（注意不要误伤字符串里的 #）
        if ' #' in line:
            head, _, tail = line.partition(' #')
            if '"' not in tail and "'" not in tail:
                line = head
        out.append(line)
    return '\n'.join(out)


TMP = tempfile.mkdtemp(prefix='fnstyle_alerts_')
os.environ['FNSTYLE_DATA_DIR'] = TMP

import monitor_alerts as ma       # noqa: E402
import monitor_thresholds as mt   # noqa: E402

print('=' * 70)
print('v1.1.84 验证：超阈值告警引擎 + 通知推送配置')
print('=' * 70)

# ---------------------------------------------------------------------------
section('A. 告警判定口径（与前端 overview.js 一致）')
# ---------------------------------------------------------------------------
CPU_OVER = {
    'cpu': {'percent': 87},
    'mem': {'percent': 10},
}
alerts = ma.evaluate(CPU_OVER)
check('CPU 87% 触发告警', len(alerts) == 1, str(alerts))
if alerts:
    a = alerts[0]
    check('级别 = warn（85 ≤ 87 < 95）', a['level'] == 'warn', a.get('level'))
    check('指标 = cpu', a['metric'] == 'cpu', a.get('metric'))
    check('记录了触发门限 85', a.get('threshold') == 85, str(a.get('threshold')))
    check('文本含数值与门限', '87' in a['text'] and '85' in a['text'], a.get('text'))

alerts = ma.evaluate({'cpu': {'percent': 96}})
check('CPU 96% 判 crit', len(alerts) == 1 and alerts[0]['level'] == 'crit',
      str(alerts))

alerts = ma.evaluate({'cpu': {'percent': 84}})
check('CPU 84% 不告警（未达 85）', len(alerts) == 0, str(alerts))

# 边界：正好等于门限应告警（>= 语义，与前端一致）
alerts = ma.evaluate({'cpu': {'percent': 85}})
check('CPU 恰好 85% 告警（>= 边界）', len(alerts) == 1, str(alerts))
alerts = ma.evaluate({'cpu': {'percent': 95}})
check('CPU 恰好 95% 判 crit（>= 边界）',
      len(alerts) == 1 and alerts[0]['level'] == 'crit', str(alerts))

# ---------------------------------------------------------------------------
section('B. 判定只看数值本身（0 是合法值）')
# ---------------------------------------------------------------------------
alerts = ma.evaluate({'cpu': {'percent': 0}})
check('CPU 0% 不告警（0 是合法值，不是缺数据）', len(alerts) == 0, str(alerts))

alerts = ma.evaluate({'cpu': {'percent': None}})
check('CPU 无数据不告警', len(alerts) == 0, str(alerts))

alerts = ma.evaluate({'cpu': {'percent': '-'}})
check('CPU 值为「-」不告警', len(alerts) == 0, str(alerts))

alerts = ma.evaluate({'cpu': {'percent': float('inf')}})
check('CPU 值为 inf 不告警（不能当成超限）', len(alerts) == 0, str(alerts))

alerts = ma.evaluate({'cpu': {}})
check('cpu 缺 percent 不告警', len(alerts) == 0, str(alerts))

# ---------------------------------------------------------------------------
section('C. 各指标独立判定')
# ---------------------------------------------------------------------------
alerts = ma.evaluate({'mem': {'percent': 86}})
check('内存超限独立触发', len(alerts) == 1 and alerts[0]['metric'] == 'mem',
      str(alerts))

alerts = ma.evaluate({'temp': {'temps': [{'name': 'CPU', 'value': 61}]}})
check('温度超限触发', len(alerts) == 1 and alerts[0]['metric'] == 'temp',
      str(alerts))

alerts = ma.evaluate({'temp': {'temps': [{'name': 'CPU', 'value': 61}],
                               'disks': [{'name': 'sda', 'value': 51}]}})
check('温度与硬盘温度分别触发',
      len(alerts) == 2
      and set(x['metric'] for x in alerts) == {'temp', 'diskTemp'},
      str(alerts))

# ---------------------------------------------------------------------------
section('D. 容量 / 温度取最大值不取平均')
# ---------------------------------------------------------------------------
alerts = ma.evaluate({'storage': {
    'disks': [{'name': 'sda', 'percent': 30}, {'name': 'sdb', 'percent': 92}]}})
check('两块盘取最高的 92% 告警（不是平均 61%）',
      len(alerts) == 1 and alerts[0]['value'] == 92.0, str(alerts))
if alerts:
    check('告警文本里带出是哪块盘', 'sdb' in alerts[0]['text'],
          alerts[0]['text'])

alerts = ma.evaluate({'temp': {'temps': [
    {'name': 'CPU', 'value': 45}, {'name': '主板', 'value': 70}]}})
check('多个温度测点取最高的 70℃',
      len(alerts) == 1 and alerts[0]['value'] == 70.0, str(alerts))

# ---------------------------------------------------------------------------
section('E. 通知配置的读写与校验')
# ---------------------------------------------------------------------------
cfg = ma.load_config()
check('默认**不启用**推送（不能一上来就往外发消息）',
      cfg['enabled'] is False, str(cfg['enabled']))
check('默认指标全开（用户填地址就能用）',
      all(cfg['metrics'][m['key']] for m in ma.METRICS), str(cfg['metrics']))
check('默认级别含 warn 与 crit',
      set(cfg['levels']) == {'warn', 'crit'}, str(cfg['levels']))

ok, msg = ma.save_config({'enabled': True, 'webhook_url': 'https://example.com/hook'})
check('保存合法配置成功', ok is True, msg)
cfg = ma.load_config()
check('enabled 已保存', cfg['enabled'] is True)
check('webhook_url 已保存', cfg['webhook_url'] == 'https://example.com/hook')

ok, msg = ma.save_config({'webhook_url': 'ftp://bad'})
check('非 http/https 地址被拒', ok is False, msg)

ok, msg = ma.save_config({'enabled': True, 'webhook_url': ''})
check('启用但没地址被拒（不能假装生效）', ok is False, msg)

ok, msg = ma.save_config({'enabled': True, 'webhook_url': 'https://x.com/h',
                           'channels': []})
check('启用但没通道被拒', ok is False, msg)

ok, msg = ma.save_config({'enabled': True, 'webhook_url': 'https://x.com/h',
                           'levels': []})
check('启用但没级别被拒', ok is False, msg)

ok, msg = ma.save_config({'enabled': True, 'webhook_url': 'https://x.com/h',
                           'metrics': {m['key']: False for m in ma.METRICS}})
check('启用但没指标被拒', ok is False, msg)

ok, msg = ma.save_config({'cooldown': -1})
check('负静默期被拒', ok is False, msg)

ok, msg = ma.save_config({'cooldown': 999999})
check('超范围静默期被拒', ok is False, msg)

ok, msg = ma.save_config({'metrics': 'not-a-dict'})
check('指标非对象被拒', ok is False, msg)

# 未提交的键保持原值（部分提交语义）
ma.save_config({'webhook_url': 'https://keep.example.com/h', 'enabled': True})
ma.save_config({'cooldown': 120})
cfg = ma.load_config()
check('部分提交时 webhook_url 保持不变',
      cfg['webhook_url'] == 'https://keep.example.com/h', cfg['webhook_url'])
check('部分提交时 cooldown 已更新', cfg['cooldown'] == 120, str(cfg['cooldown']))

# 损坏文件 → 回落默认，不抛异常
with open(os.path.join(TMP, 'monitor_alerts.json'), 'w', encoding='utf-8') as f:
    f.write('{ 这不是合法 JSON')
try:
    cfg = ma.load_config()
    check('配置文件损坏时回落默认值不抛异常', cfg is not None)
    check('损坏时 enabled = False', cfg['enabled'] is False)
except Exception as e:
    check('配置文件损坏时回落默认值不抛异常', False, str(e))

# ---------------------------------------------------------------------------
section('F. 选择配置：指标 / 级别 / 通道 可独立开关')
# ---------------------------------------------------------------------------
ma.reset_config()
ma.save_config({'enabled': True, 'webhook_url': 'https://x.com/h',
                'metrics': {'cpu': True, 'mem': False, 'cap': False,
                            'temp': True, 'diskTemp': False,
                            'diskHealth': False}})
cfg = ma.load_config()
check('只勾选的指标为 true', cfg['metrics']['cpu'] is True
      and cfg['metrics']['mem'] is False, str(cfg['metrics']))

ma.save_config({'levels': ['crit']})
cfg = ma.load_config()
check('只勾 crit 级别', cfg['levels'] == ['crit'], str(cfg['levels']))

ma.save_config({'channels': ['fnos']})
cfg = ma.load_config()
check('只勾 fnos 通道', cfg['channels'] == ['fnos'], str(cfg['channels']))

ma.save_config({'channels': ['webhook', 'fnos']})
cfg = ma.load_config()
check('两个通道都能选', set(cfg['channels']) == {'webhook', 'fnos'},
      str(cfg['channels']))

# 非法通道被丢弃
ma.save_config({'channels': ['webhook', 'bogus']})
cfg = ma.load_config()
check('非法通道被丢弃', cfg['channels'] == ['webhook'], str(cfg['channels']))

d = ma.describe()
check('describe 返回指标清单', len(d['metrics']) == len(ma.METRICS))
check('describe 返回级别清单',
      [x['key'] for x in d['levels']] == ['warn', 'crit'],
      str(d['levels']))
check('describe 返回通道清单', len(d['channels']) == 2)

# ---------------------------------------------------------------------------
section('G. 去抖：边沿触发 + 静默期 + 等级升级立即发')
# ---------------------------------------------------------------------------
st = ma.AlertState()
now = 1000.0
check('首次超限 → 发', st.should_send('cpu', 'warn', now, 300) is True)
check('同级别重复（静默期内）→ 不发',
      st.should_send('cpu', 'warn', now + 10, 300) is False)
check('静默期过后再超限 → 发',
      st.should_send('cpu', 'warn', now + 301, 300) is True)

st2 = ma.AlertState()
st2.should_send('cpu', 'warn', 1000.0, 300)
check('等级升级 warn → crit 立即发（不受静默期限制）',
      st2.should_send('cpu', 'crit', 1001.0, 300) is True)

st3 = ma.AlertState()
st3.should_send('cpu', 'warn', 1000.0, 300)
st3.update_normal('cpu')
check('回到正常后再超限 → 重新触发',
      st3.should_send('cpu', 'warn', 1001.0, 300) is True)

st4 = ma.AlertState()
st4.should_send('cpu', 'warn', 1000.0, 300)
st4.mark_level('cpu', 'warn')
check('mark_level 不触发推送（未启用时不刷旧告警）',
      st4.should_send('cpu', 'warn', 1001.0, 300) is False)

# ---------------------------------------------------------------------------
section('H. 消息格式兼容飞书 / 钉钉 / 企业微信')
# ---------------------------------------------------------------------------
payload = ma.build_message([{
    'metric': 'cpu', 'label': '处理器使用率', 'level': 'warn',
    'value': 87.0, 'unit': '%', 'threshold': 85,
    'text': '处理器使用率 87.0%，已达留意门限 85%',
}], hostname='MyNAS')
check('msgtype = text', payload.get('msgtype') == 'text', str(payload.get('msgtype')))
check('有 text.content 字段',
      isinstance(payload.get('text', {}).get('content'), str)
      and payload['text']['content'], str(payload.get('text')))
content = payload['text']['content']
check('内容含主机名', 'MyNAS' in content, content[:80])
check('内容含告警文本', '87.0' in content, content[:80])
check('内容含级别标记', '留意' in content, content[:80])
check('消息可被 JSON 序列化', isinstance(json.dumps(payload, ensure_ascii=False), str))
check('空告警不生成消息', ma.build_message([]) is None)

# ---------------------------------------------------------------------------
section('I. 阈值键真实存在于 monitor_thresholds.SPEC')
# ---------------------------------------------------------------------------
spec_keys = set(mt.SPEC.keys())
for m in ma.METRICS:
    for k in (m.get('warn_key'), m.get('crit_key')):
        if k:
            check('%s 的门限键 %s 存在于 SPEC' % (m['key'], k), k in spec_keys)

# ---------------------------------------------------------------------------
section('J. 服务端接口齐备')
# ---------------------------------------------------------------------------
srv = open(os.path.join(UI, 'fnstyle_server.py'), encoding='utf-8').read()
for p in ('/api/monitor/alerts', '/api/monitor/alerts/test',
          '/api/monitor/alerts/status'):
    check('路由含 %s' % p, 'p == "%s"' % p in srv)
for h in ('_handle_alerts_get', '_handle_alerts_save', '_handle_alerts_test',
          '_handle_alerts_status'):
    check('有 handler %s' % h, ('def %s(' % h) in srv)

cgi = open(os.path.join(UI, 'index.cgi'), encoding='utf-8').read()
check('index.cgi 放行了告警接口', '/api/monitor/alerts' in cgi)

# ⚠️ v1.1.84 实测踩坑：`_mc` 是**模块** monitor_collect，`_MONITOR` 才是
#    MonitorCollector 实例。实例方法写成 `_mc.alert_status()` ⇒
#    运行期报 "module 'monitor_collect' has no attribute 'alert_status'"。
#    「路由存在 + handler 存在」这种源码断言**抓不到**这种错，
#    必须断言**调用对象**是谁。
check('告警状态调用的是实例 _MONITOR 而非模块 _mc',
      '_MONITOR.alert_status()' in srv)
# ⚠️ 别用 `'x' not in src` 做这种断言 —— 注释里写了同样的字符串就会误判。
#    正解：剥掉注释后只查**真实代码行**。
_code = _strip_comments(srv)
check('未把实例方法错写成模块级调用',
      '_mc.alert_status()' not in _code,
      [l.strip() for l in _code.splitlines() if '_mc.alert_status' in l][:3])

# 端到端：真的起一个采集器实例，确认 alert_status 可调用
try:
    import importlib
    _m = importlib.import_module('monitor_collect')
    _inst = _m.MonitorCollector(interval=3)
    _st = _inst.alert_status()
    check('MonitorCollector 实例能调 alert_status()', isinstance(_st, dict), str(_st)[:120])
    check('alert_status 返回含告警状态字段', 'state' in _st, str(list(_st.keys())))
except Exception as e:
    check('MonitorCollector 实例能调 alert_status()', False, repr(e))

# ---------------------------------------------------------------------------
section('K. 采集器启动了告警线程')
# ---------------------------------------------------------------------------
mc = open(os.path.join(UI, 'monitor_collect.py'), encoding='utf-8').read()
check('采集器有 _alert_loop', 'def _alert_loop(' in mc)
check('采集器启动告警线程', '_start_alerts()' in mc)
check('告警线程是独立线程', 'threading.Thread(target=self._alert_loop' in mc)
check('有 ALERT_INTERVAL 常量', 'ALERT_INTERVAL' in mc)
check('COLLECT_VERSION 已升到 1.1.84', 'COLLECT_VERSION = "1.1.84"' in mc)

# ---------------------------------------------------------------------------
section('L. 语法检查')
# ---------------------------------------------------------------------------
for name in ('monitor_alerts.py', 'fnos_notify.py', 'monitor_collect.py',
             'fnstyle_server.py'):
    r = subprocess.run([sys.executable, '-m', 'py_compile',
                        os.path.join(UI, name)], capture_output=True, text=True)
    check('%s 语法正确' % name, r.returncode == 0, r.stderr[:200])

r = subprocess.run(['bash', '-n', os.path.join(UI, 'index.cgi')],
                   capture_output=True, text=True)
check('index.cgi 语法正确', r.returncode == 0, r.stderr[:200])

# ---------------------------------------------------------------------------
section('M. 推送不会因为代理环境变量而失败')
# ---------------------------------------------------------------------------
check('push 用了绕过代理的 opener', 'ProxyHandler({})' in
      open(os.path.join(UI, 'monitor_alerts.py'), encoding='utf-8').read())

# ---------------------------------------------------------------------------
shutil.rmtree(TMP, ignore_errors=True)
print('\n' + '=' * 70)
print('verify_alerts_notify: PASS %d / FAIL %d' % (PASS, FAIL))
if FAIL:
    sys.exit(1)
print('全部通过')
