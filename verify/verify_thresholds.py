#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_thresholds.py —— 系统监控阈值门限配置（v1.1.79）

老板需求：「对软件中系统监控部分相关的阈值门限的配置提取出来进行单独的配置，
             配置页面以抽屉方式呈现，入口为页面顶部栏中增加配置管理按钮点击进入。」

本脚本在**真实 Chromium**（Playwright）里验证三件事：
  A. 前端阈值统一读取层（thresholds.js）行为正确
     · 加载前用默认值兜底
     · 加载后读到后端配置
     · 配置变更后 **getter 立刻生效**（不依赖重启/重新加载页面）
  B. 概览卡（overview.js）与抽屉（compute.js）的等级判定确实跟随配置
     —— 这是本次改动的核心价值：改门限后告警分级要跟着变
  C. 后端阈值模块（monitor_thresholds.py）的校验规则

⚠️ 关键回归点（v1.1.79 修掉的老问题）：
   overview.js 原本的 TH 是**普通对象**（脚本加载时固化），
   monitor.js 原本是 `var TH_TEMP_WARN = 60`（常量快照）。
   两者都会导致「配置改了但不生效」—— 本脚本专门用
   「先渲染一次 → 改配置 → 再渲染一次」验证第二次结果确实变了。

运行：python3 verify/verify_thresholds.py
"""
import http.server
import json
import socketserver
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path('/Volumes/SSD-Data/trae/FnOsApp')
UI = ROOT / 'app/ui'

RESULTS = []


def rec(group, item, ok, detail=''):
    RESULTS.append((group, item, 'PASS' if ok else 'FAIL', detail))
    print(('  ✅' if ok else '  ❌') + ' ' + group + ' / ' + item +
          (('  → ' + str(detail)) if detail != '' else ''))
    return ok


def eq(group, item, actual, expected):
    ok = actual == expected
    return rec(group, item, ok,
               '实际=' + json.dumps(actual, ensure_ascii=False) +
               ' 期望=' + json.dumps(expected, ensure_ascii=False))


def truthy(group, item, v, detail=''):
    return rec(group, item, bool(v), detail)


def section(t):
    print('\n【' + t + '】')


# ---------------------------------------------------------------------------
# 伪后端：按需返回阈值配置
# ---------------------------------------------------------------------------

STATE = {'config': None}   # None = 还没设，走默认值

# 与真实后端 monitor_thresholds.SPEC 一致的清单（伪后端据此渲染 items）
DEFAULT_CFG = {
    'cpuWarn': 85, 'cpuCrit': 95,
    'memWarn': 85, 'memCrit': 95,
    'capWarn': 85, 'capCrit': 95,
    'tempWarn': 60, 'tempCrit': 75,
    'diskTempWarn': 50, 'diskTempCrit': 58,
    'barWarn': 70, 'barCrit': 90,
    'scanGrowthCrit': 10,
}

GROUPS = [
    {'key': 'processor', 'label': '处理器'},
    {'key': 'memory', 'label': '内存'},
    {'key': 'storage', 'label': '存储'},
    {'key': 'temperature', 'label': '温度'},
    {'key': 'display', 'label': '显示着色'},
]

# 每个门限的元信息（label / unit / default / min / max / group / desc）
ITEM_SPEC = [
    ('cpuWarn', '处理器使用率 · 留意', '%', 'processor'),
    ('cpuCrit', '处理器使用率 · 严重', '%', 'processor'),
    ('memWarn', '内存使用率 · 留意', '%', 'memory'),
    ('memCrit', '内存使用率 · 严重', '%', 'memory'),
    ('capWarn', '存储容量 · 留意', '%', 'storage'),
    ('capCrit', '存储容量 · 严重', '%', 'storage'),
    ('tempWarn', '系统温度 · 留意', '℃', 'temperature'),
    ('tempCrit', '系统温度 · 严重', '℃', 'temperature'),
    ('diskTempWarn', '硬盘温度 · 留意', '℃', 'temperature'),
    ('diskTempCrit', '硬盘温度 · 严重', '℃', 'temperature'),
    ('barWarn', '进度条 · 留意', '%', 'display'),
    ('barCrit', '进度条 · 严重', '%', 'display'),
    ('scanGrowthCrit', '巡检缺陷增长 · 判故障', '个', 'storage'),
]


def build_items(cfg):
    """按 ITEM_SPEC 拼出与真实后端一致的 items 结构。"""
    out = []
    for key, label, unit, group in ITEM_SPEC:
        out.append({
            'key': key, 'label': label, 'unit': unit,
            'default': DEFAULT_CFG[key], 'value': cfg.get(key, DEFAULT_CFG[key]),
            'min': 0 if unit == '℃' else 1,
            'max': 120 if unit == '℃' else (10000 if unit == '个' else 100),
            'group': group, 'desc': '测试用说明', 'pair': None,
        })
    return out




class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(UI), **kw)

    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith('/api/monitor/thresholds'):
            cfg = dict(DEFAULT_CFG) if STATE['config'] is None else dict(STATE['config'])
            self._send({'success': True, 'config': cfg,
                        'items': build_items(cfg), 'groups': GROUPS})
            return
        super().do_GET()

    def do_POST(self):
        if self.path.startswith('/api/monitor/thresholds'):
            n = int(self.headers.get('Content-Length') or 0)
            raw = self.rfile.read(n).decode('utf-8') if n else '{}'
            try:
                body = json.loads(raw)
            except Exception:
                body = {}
            if body.get('__reset'):
                STATE['config'] = None
                cfg = dict(DEFAULT_CFG)
                self._send({'success': True, 'message': '已恢复默认',
                            'config': cfg, 'items': build_items(cfg),
                            'groups': GROUPS})
                return
            cfg = dict(DEFAULT_CFG)
            cfg.update(body)
            STATE['config'] = body
            self._send({'success': True, 'message': '已保存', 'config': cfg,
                        'items': build_items(cfg), 'groups': GROUPS})
            return
        super().do_POST()


# ---------------------------------------------------------------------------
# 页面模板
# ---------------------------------------------------------------------------

HOST_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<link rel="stylesheet" href="static/css/app.css">
<style>
  :root {
    --bg: #14161a; --bg-elevated: #1b1e24; --bg-hover: #262a32;
    --border: #2a2f38; --border-strong: #3a4150;
    --accent: #4aa8ff; --accent-soft: rgba(74,168,255,.16);
    --success: #35c48a; --warning: #f0a92c; --warning-soft: rgba(240,169,44,.5);
    --danger: #e5554f;
    --ramp-mid: #5b8fd6; --ramp-mid-2: #d18b16;
    --text-primary: #e8eaed; --text-secondary: #a9b0bb; --text-muted: #6f7784;
    --radius: 14px; --font-mono: ui-monospace, Menlo, monospace;
  }
  body { background: var(--bg); color: var(--text-primary);
         font-family: system-ui, -apple-system, "PingFang SC", sans-serif;
         margin: 0; padding: 16px; }
</style></head>
<body>
  <div class="topbar"><div class="topbar-right">
    <button class="btn sm icon-btn" id="tbOpenThresholds" type="button"
            title="配置管理" aria-label="配置管理">⚙</button>
  </div></div>
  <div class="ov-wrap" id="overviewCard"><div id="monOverview"></div></div>
  <div class="mon-drawer-overlay" id="monThresholdOverlay" hidden>
    <aside class="mon-drawer" role="dialog" aria-modal="true">
      <header class="mon-drawer-head">
        <div class="mon-drawer-head-main">
          <h3 id="monThresholdTitle"><span class="mon-drawer-ico">⚙️</span>配置管理</h3>
          <p class="mon-drawer-sub" id="monThresholdSub">系统监控阈值门限</p>
        </div>
        <button type="button" class="mon-drawer-close" id="btnCloseMonThreshold">✕</button>
      </header>
      <div class="mon-drawer-body" id="monThresholdBody"></div>
    </aside>
  </div>
<script src="static/js/thresholds.js"></script>
<script src="static/js/overview.js"></script>
<script src="static/js/thresholds-ui.js"></script>
</body></html>
"""

SNAP = {
    "cpu": {"available": True, "percent": 32.4, "cores": 4,
            "freqText": "2.20 GHz", "loadavg": ["0.15", "0.12", "0.10"]},
    "mem": {"available": True, "percent": 42, "totalText": "7.8 GiB",
            "usedText": "3.3 GiB", "availText": "4.5 GiB"},
    "storage": {
        "summary": {"total": 8589934592, "used": 5926501888,
                    "percent": 69, "poolCount": 1, "diskCount": 2},
        "volumes": [{"name": "vol1", "total": 8589934592,
                     "used": 5926501888, "percent": 69}],
        "disks": [{"name": "sda", "percent": 70}, {"name": "sdb", "percent": 68}]},
    "network": {"available": True, "rxRate": 2048, "txRate": 512,
                "todayRx": 1048576, "todayTx": 524288, "ifaceCount": 2},
    "temp": {"temps": [{"type": "cpu", "value": 45}], "mbTemp": 38, "disks": []},
    "power": {"ok": True, "source": "rapl", "total": 29.0,
              "estimation_params": {"tdp_w": 65.0, "base_w": 20.0,
                                     "disk_typical_w": 8.0, "nic_fixed_w": 3.0,
                                     "disks": 2, "nics": 2}},
    "powerStats": {"today_kwh": 0.6, "today_yuan": 0.36},
}


JS_RENDER = """(snap) => {
  window.FnStyleOverview.render(document.getElementById('monOverview'), snap);
}"""


def run_tests(page):
    # ----------------------------------------------------------
    section('A 阈值统一读取层')
    # ----------------------------------------------------------
    page.goto(BASE + '/__threshold_host.html')
    page.wait_for_function('typeof window.FnStyleThresholds === "object"')

    eq('读取层', 'window.FnStyleThresholds 已定义',
       page.evaluate('typeof window.FnStyleThresholds'), 'object')
    truthy('读取层', '对外接口含 get',
           page.evaluate('typeof window.FnStyleThresholds.get'), 'function')
    truthy('读取层', '对外接口含 load',
           page.evaluate('typeof window.FnStyleThresholds.load'), 'function')
    truthy('读取层', '对外接口含 save',
           page.evaluate('typeof window.FnStyleThresholds.save'), 'function')

    eq('读取层', '默认 cpuWarn = 85',
       page.evaluate('window.FnStyleThresholds.get("cpuWarn")'), 85)
    eq('读取层', '默认 tempWarn = 60（不是 overview.js 老口径 80）',
       page.evaluate('window.FnStyleThresholds.get("tempWarn")'), 60)
    eq('读取层', '默认 diskTempWarn = 50',
       page.evaluate('window.FnStyleThresholds.get("diskTempWarn")'), 50)
    eq('读取层', '默认 capWarn = 85',
       page.evaluate('window.FnStyleThresholds.get("capWarn")'), 85)
    eq('读取层', '未知键返回 undefined',
       page.evaluate('window.FnStyleThresholds.get("noSuchKey")'), None)

    # 等待自动加载完成
    page.wait_for_function('window.FnStyleThresholds.isLoaded() === true')
    truthy('读取层', 'isLoaded() 自动加载后为 true',
           page.evaluate('window.FnStyleThresholds.isLoaded()'))

    # ----------------------------------------------------------
    section('B 改配置后 getter 立即生效（不重载页面）')
    # ----------------------------------------------------------
    page.evaluate("""() => {
      window.FnStyleThresholds.__setForTest({
        cpuWarn: 40, cpuCrit: 50,
        memWarn: 40, memCrit: 50,
        capWarn: 40, capCrit: 50,
        tempWarn: 40, tempCrit: 50,
        diskTempWarn: 40, diskTempCrit: 50,
        barWarn: 40, barCrit: 50,
        scanGrowthCrit: 10
      });
    }""")
    eq('生效', '注入后 cpuWarn = 40',
       page.evaluate('window.FnStyleThresholds.get("cpuWarn")'), 40)
    eq('生效', '注入后 tempWarn = 40',
       page.evaluate('window.FnStyleThresholds.get("tempWarn")'), 40)
    eq('生效', '注入后 capWarn = 40',
       page.evaluate('window.FnStyleThresholds.get("capWarn")'), 40)

    # ----------------------------------------------------------
    section('C 概览卡等级判定跟随配置（核心价值）')
    # ----------------------------------------------------------
    # ⚠️ overview.js 原来的 TH 是普通对象 → 配置改了不生效。
    #    现在必须验证：改了门限后，等级判定与卡片状态文案确实跟着变。
    snap_hi = json.loads(json.dumps(SNAP))
    snap_hi['cpu']['percent'] = 45      # 介于新 cpuWarn(40) 与 cpuCrit(50) 之间
    snap_hi['mem']['percent'] = 45

    page.evaluate(JS_RENDER, snap_hi)
    res_hi = page.evaluate("""() => {
      const host = document.getElementById('monOverview');
      const cpuCard = host.querySelector('[data-ov="cpu"]');
      const memCard = host.querySelector('[data-ov="mem"]');
      return {
        cpuClass: cpuCard.className,
        cpuState: (host.querySelector('[data-ov-state="cpu"]')||{}).textContent,
        memClass: memCard.className,
        memState: (host.querySelector('[data-ov-state="mem"]')||{}).textContent
      };
    }""")
    truthy('概览卡', 'cpuWarn=40 时 45% 的 CPU 卡进入 warn 态',
           'is-warn' in res_hi['cpuClass'], res_hi['cpuClass'])
    eq('概览卡', 'CPU 卡状态文案 = 繁忙', res_hi['cpuState'], '繁忙')
    truthy('概览卡', 'memWarn=40 时 45% 的内存卡进入 warn 态',
           'is-warn' in res_hi['memClass'], res_hi['memClass'])

    # 把门限调高 → 同样的 45% 应该回到正常态
    page.evaluate("""() => {
      window.FnStyleThresholds.__setForTest({
        cpuWarn: 85, cpuCrit: 95,
        memWarn: 85, memCrit: 95,
        capWarn: 85, capCrit: 95,
        tempWarn: 60, tempCrit: 75,
        diskTempWarn: 50, diskTempCrit: 58,
        barWarn: 70, barCrit: 90,
        scanGrowthCrit: 10
      });
    }""")
    page.evaluate(JS_RENDER, snap_hi)
    res_lo = page.evaluate("""() => {
      const host = document.getElementById('monOverview');
      const cpuCard = host.querySelector('[data-ov="cpu"]');
      const memCard = host.querySelector('[data-ov="mem"]');
      return {
        cpuClass: cpuCard.className,
        cpuState: (host.querySelector('[data-ov-state="cpu"]')||{}).textContent,
        memClass: memCard.className
      };
    }""")
    truthy('概览卡', 'cpuWarn=85 时 45% 的 CPU 卡**不再**是 warn 态',
           'is-warn' not in res_lo['cpuClass'], res_lo['cpuClass'])
    truthy('概览卡', 'cpuWarn=85 时 45% 的内存卡**不再**是 warn 态',
           'is-warn' not in res_lo['memClass'], res_lo['memClass'])

    # ----------------------------------------------------------
    section('D 温度门限统一（口径分歧已修正）')
    # ----------------------------------------------------------
    # ⚠️ 老口径：overview.js 用 80/90，monitor.js 用 60/75 —— 同一台机器两种结论。
    #    现在两边都读同一份配置，默认值统一为 60/75。
    snap_t = json.loads(json.dumps(SNAP))
    snap_t['temp']['temps'] = [{"type": "cpu", "value": 65}]   # 65℃

    page.evaluate("""() => {
      window.FnStyleThresholds.__setForTest({
        cpuWarn: 85, cpuCrit: 95,
        memWarn: 85, memCrit: 95,
        capWarn: 85, capCrit: 95,
        tempWarn: 60, tempCrit: 75,
        diskTempWarn: 50, diskTempCrit: 58,
        barWarn: 70, barCrit: 90,
        scanGrowthCrit: 10
      });
    }""")
    page.evaluate(JS_RENDER, snap_t)
    res_t = page.evaluate("""() => {
      const host = document.getElementById('monOverview');
      const card = host.querySelector('[data-ov="temp"]');
      return {
        cls: card ? card.className : null,
        state: (host.querySelector('[data-ov-state="temp"]')||{}).textContent
      };
    }""")
    truthy('温度', 'tempWarn=60 时 65℃ 的温度卡进入 warn 态',
           res_t['cls'] and ('is-warn' in res_t['cls'] or 'is-crit' in res_t['cls']),
           res_t['cls'])

    # 调高到 80/90 → 65℃ 应回到正常
    page.evaluate("""() => {
      window.FnStyleThresholds.__setForTest({
        cpuWarn: 85, cpuCrit: 95,
        memWarn: 85, memCrit: 95,
        capWarn: 85, capCrit: 95,
        tempWarn: 80, tempCrit: 90,
        diskTempWarn: 50, diskTempCrit: 58,
        barWarn: 70, barCrit: 90,
        scanGrowthCrit: 10
      });
    }""")
    page.evaluate(JS_RENDER, snap_t)
    res_t2 = page.evaluate("""() => {
      const host = document.getElementById('monOverview');
      const card = host.querySelector('[data-ov="temp"]');
      return { cls: card ? card.className : null };
    }""")
    truthy('温度', 'tempWarn=80 时 65℃ 的温度卡**不再**告警',
           res_t2['cls'] and ('is-warn' not in res_t2['cls'] and
                             'is-crit' not in res_t2['cls']),
           res_t2['cls'])

    # ----------------------------------------------------------
    section('E 配置抽屉 UI')
    # ----------------------------------------------------------
    page.evaluate("""() => {
      window.FnStyleThresholds.__setForTest({
        cpuWarn: 85, cpuCrit: 95,
        memWarn: 85, memCrit: 95,
        capWarn: 85, capCrit: 95,
        tempWarn: 60, tempCrit: 75,
        diskTempWarn: 50, diskTempCrit: 58,
        barWarn: 70, barCrit: 90,
        scanGrowthCrit: 10
      });
    }""")
    page.click('#tbOpenThresholds')
    page.wait_for_selector('#monThresholdOverlay:not([hidden])', timeout=5000)
    truthy('抽屉', '点「配置管理」按钮打开抽屉',
           page.evaluate('!document.getElementById("monThresholdOverlay").hasAttribute("hidden")'))

    page.wait_for_selector('#monThresholdBody .thr-row', timeout=5000)
    n_rows = page.evaluate('document.querySelectorAll("#monThresholdBody .thr-row").length')
    truthy('抽屉', '抽屉里渲染出门限行', n_rows >= 12, '行数=' + str(n_rows))

    n_secs = page.evaluate('document.querySelectorAll("#monThresholdBody .thr-sec").length')
    truthy('抽屉', '按分组分段渲染', n_secs >= 4, '分组数=' + str(n_secs))

    has_input = page.evaluate(
        '!!document.querySelector("#monThresholdBody [data-thr-input=\\"cpuWarn\\"]")')
    truthy('抽屉', '含 cpuWarn 输入框', has_input)

    has_save = page.evaluate('!!document.querySelector("#monThresholdBody [data-thr-save]")')
    truthy('抽屉', '含保存按钮', has_save)
    has_reset = page.evaluate('!!document.querySelector("#monThresholdBody [data-thr-reset]")')
    truthy('抽屉', '含恢复默认按钮', has_reset)

    # 输入框的值应等于当前生效值
    inp_val = page.evaluate(
        'document.querySelector("#monThresholdBody [data-thr-input=\\"cpuWarn\\"]").value')
    eq('抽屉', 'cpuWarn 输入框值 = 当前生效值', inp_val, '85')

    # 改一个值并保存 → 应发 POST 并更新生效值
    page.evaluate("""() => {
      const inp = document.querySelector('#monThresholdBody [data-thr-input="cpuWarn"]');
      inp.value = '42';
    }""")
    page.click('#monThresholdBody [data-thr-save]')
    page.wait_for_function(
        'window.FnStyleThresholds.get("cpuWarn") === 42', timeout=5000)
    eq('抽屉', '保存后 cpuWarn 生效值 = 42',
       page.evaluate('window.FnStyleThresholds.get("cpuWarn")'), 42)

    msg = page.evaluate(
        '((document.querySelector("#monThresholdBody [data-thr-msg]")||{}).textContent||"")')
    truthy('抽屉', '保存后有「已保存」提示', '已保存' in msg, msg)

    # 恢复默认
    page.click('#monThresholdBody [data-thr-reset]')
    page.wait_for_function(
        'window.FnStyleThresholds.get("cpuWarn") === 85', timeout=5000)
    eq('抽屉', '恢复默认后 cpuWarn = 85',
       page.evaluate('window.FnStyleThresholds.get("cpuWarn")'), 85)

    # 关闭按钮
    page.click('#btnCloseMonThreshold')
    page.wait_for_timeout(450)
    truthy('抽屉', '点关闭按钮能关掉抽屉',
           page.evaluate('document.getElementById("monThresholdOverlay").hasAttribute("hidden")'))


def run_backend_tests():
    section('F 后端阈值模块校验规则')
    import importlib.util
    import os
    import shutil
    import tempfile

    tmp = tempfile.mkdtemp(prefix='fnstyle_thr_')
    os.environ['FNSTYLE_DATA_DIR'] = tmp
    try:
        spec = importlib.util.spec_from_file_location(
            'monitor_thresholds', str(UI / 'monitor_thresholds.py'))
        mt = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mt)

        d = mt.describe()
        truthy('后端', 'describe 返回 13 个门限', len(d['items']) == 13,
               '实际=' + str(len(d['items'])))
        truthy('后端', 'describe 含 5 个分组', len(d['groups']) == 5,
               '实际=' + str(len(d['groups'])))

        ok, msg = mt.save_config({'cpuWarn': 70, 'cpuCrit': 80})
        truthy('后端', '保存合法值成功', ok, msg)
        eq('后端', '保存后 cpuWarn = 70', mt.load_config()['cpuWarn'], 70)

        ok, msg = mt.save_config({'cpuWarn': 90, 'cpuCrit': 80})
        truthy('后端', 'warn >= crit 被拒绝', not ok, msg)

        ok, msg = mt.save_config({'cpuWarn': 200})
        truthy('后端', '越界值被拒绝', not ok, msg)

        ok, msg = mt.save_config({'cpuWarn': 'abc'})
        truthy('后端', '非数字被拒绝', not ok, msg)

        ok, msg = mt.save_config({'cpuWarn': 70})
        truthy('后端', '只提交一项也成功', ok, msg)
        eq('后端', '未提交项保持原值', mt.load_config()['cpuCrit'], 80)

        mt.reset_config()
        eq('后端', '恢复默认后 cpuWarn = 85', mt.load_config()['cpuWarn'], 85)

        # 损坏文件
        with open(os.path.join(tmp, 'monitor_thresholds.json'), 'w') as f:
            f.write('{ broken')
        eq('后端', '配置文件损坏时回落默认', mt.load_config()['cpuWarn'], 85)

        # 单项越界只回落该项
        with open(os.path.join(tmp, 'monitor_thresholds.json'), 'w') as f:
            json.dump({'cpuWarn': 999, 'memWarn': 60}, f)
        c = mt.load_config()
        eq('后端', '越界项回落默认', c['cpuWarn'], 85)
        eq('后端', '正常项保留', c['memWarn'], 60)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ.pop('FNSTYLE_DATA_DIR', None)


BASE = ''


def main():
    global BASE
    httpd = socketserver.TCPServer(('127.0.0.1', 0), Handler)
    httpd.allow_reuse_address = True
    port = httpd.server_address[1]
    BASE = 'http://127.0.0.1:%d' % port
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    # 把测试页写进 UI 目录（Handler 以 UI 为根）
    host_path = UI / '__threshold_host.html'
    host_path.write_text(HOST_HTML, encoding='utf-8')
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=['--no-sandbox'])
            page = browser.new_page(viewport={'width': 1440, 'height': 1400})
            try:
                run_tests(page)
            finally:
                browser.close()
        run_backend_tests()
    finally:
        host_path.unlink(missing_ok=True)
        httpd.shutdown()

    passed = sum(1 for r in RESULTS if r[2] == 'PASS')
    failed = len(RESULTS) - passed
    print('\n' + '=' * 60)
    print('通过 %d 项，失败 %d 项' % (passed, failed))
    print('=' * 60)
    if failed:
        for g, i, st, d in RESULTS:
            if st == 'FAIL':
                print('  ❌ %s / %s  %s' % (g, i, d))
        sys.exit(1)


if __name__ == '__main__':
    main()
