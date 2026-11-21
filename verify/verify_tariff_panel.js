#!/usr/bin/env node
/* ===========================================================================
   v1.1.63 回归测试：电价设置面板（单一单价形态）

   历史沿革
   --------
   v1.1.57 用户反馈：
     ① 「为什么电价设置中的地区下拉选择后总是恢复到成都？」
     ② 「成都的电价不是区分丰水期、枯水期还有夏季吗？设置里面为什么没有？」

   ① 的根因：change 事件只改了内存对象 j.config.region，**没更新模块级
      tariffCache**；而抽屉每 3 秒 live 重建一次 → loadTariff 命中 60 秒
      缓存 → 用旧缓存（sichuan）重刷面板 → <select> 被重建 → 选中项回落。

   v1.1.63：整个电价模型退化为**单一单价** ——
     「删除阶梯计费和档位信息等，也不区分区域，就是一个电费单价的配置，
       并增加对应的说明即可」
     ⇒ 地区下拉、季节参数、价目表 **全部消失**
     ⇒ ① 的问题（地区切换弹回）**从根上不存在了** —— 没有地区可切

   本测试跑真实链路：powerSheetHtml → loadTariff →
   断言面板只含「单价输入框 + 口径说明 + 保存按钮」，且无任何下拉/档位信息。
   =========================================================================== */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const UI = path.join(__dirname, '..', 'app', 'ui');
const SRC = path.join(UI, 'static', 'js', 'monitor.js');
const RAW = fs.readFileSync(SRC, 'utf8');

let PASS = 0, FAIL = 0;
function check(label, ok, extra) {
  if (ok) { PASS++; console.log('  ✓ ' + label); }
  else { FAIL++; console.log('  ✗ ' + label + (extra ? '  → ' + extra : '')); }
}
function section(t) { console.log('\n--- ' + t + ' ---'); }

/* ================= 极简 DOM（带真实 select 语义） ================= */
function makeEl(tag) {
  const el = {
    tagName: (tag || 'div').toUpperCase(),
    children: [], _attrs: {}, style: {},
    classList: {
      _s: new Set(),
      add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); },
      contains(c) { return this._s.has(c); },
      toggle(c, f) {
        if (f === undefined) { this._s.has(c) ? this._s.delete(c) : this._s.add(c); }
        else if (f) this._s.add(c); else this._s.delete(c);
      }
    },
    textContent: '', innerHTML: '', value: '', checked: false, disabled: false,
    _listeners: {},
    get className() { return Array.from(this.classList._s).join(' '); },
    set className(v) { this.classList._s = new Set(String(v || '').split(/\s+/).filter(Boolean)); },
    setAttribute(k, v) { this._attrs[k] = String(v); },
    getAttribute(k) { return this._attrs[k] !== undefined ? this._attrs[k] : null; },
    hasAttribute(k) { return this._attrs[k] !== undefined; },
    removeAttribute(k) { delete this._attrs[k]; },
    addEventListener(t, fn) { (this._listeners[t] = this._listeners[t] || []).push(fn); },
    removeEventListener() {},
    dispatchEvent(e) {
      (this._listeners[e.type] || []).forEach(fn => fn(e));
      return true;
    },
    appendChild(c) { this.children.push(c); return c; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
  };
  return el;
}

/* ================= 响应数据（单一单价形态） ================= */
function tariffResponse() {
  return {
    success: true,
    config: { rate: 0.5224, updated: 0 },
    rate: 0.5224,
    default_rate: 0.5224,
    unit: '元/kWh',
    note: '电费按「用电量 × 电费单价」估算，不区分阶梯档位、不区分峰谷时段、不区分地区。'
        + '默认单价 0.5224 元/kWh，取成都（四川）一户一表居民不满 1 千伏第一档高峰平段电价'
        + '（成都居民电价的最低档），可按你家的实际电价自行调整。'
  };
}

/* ================= 沙箱 ================= */
function makeSandbox() {
  const els = {};
  ['monPowerOverview', 'monPowerStats', 'monPowerDaily', 'monComputeBody',
    'monTempCharts', 'monPowerCharts'].forEach(id => { els[id] = makeEl('div'); });
  const fetchCalls = [];
  const sandbox = {
    console, setTimeout, clearTimeout, setInterval, clearInterval,
    Date, Math, JSON, Number, String, Array, Object, parseInt, parseFloat, isNaN,
    document: {
      querySelector(sel) { return els[sel.replace('#', '')] || null; },
      querySelectorAll() { return []; },
      createElement(t) { return makeEl(t); },
      body: makeEl('body'),
      addEventListener() {},
      readyState: 'complete',
    },
    window: {},
    fetch: function (url) {
      fetchCalls.push(url);
      return Promise.resolve({
        ok: true, json: () => Promise.resolve(tariffResponse())
      });
    },
    requestAnimationFrame(fn) { fn(); },
    navigator: { userAgent: 'node' },
    location: { href: 'http://localhost/', protocol: 'http:' },
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.__fetchCalls = fetchCalls;
  vm.createContext(sandbox);
  vm.runInContext(RAW, sandbox, { filename: 'monitor.js' });
  return sandbox;
}

(async function main() {
  console.log('\n========== v1.1.63 电价设置面板回归（单一单价）==========');

  const sandbox = makeSandbox();
  const M = sandbox.window.FnStyleMonitor;
  if (!M) { console.log('❌ FnStyleMonitor 未加载'); process.exit(1); }

  /* ---------- 源码级断言 ---------- */
  section('① 源码级：面板只剩一个单价输入框');
  check('存在 tariffPanelHtml 函数', /function tariffPanelHtml\(/.test(RAW));
  check('渲染 data-tariff-rate 输入框', /data-tariff-rate/.test(RAW));
  check('渲染 data-tariff-note 口径说明', /data-tariff-note/.test(RAW));
  check('渲染 data-tariff-save 保存按钮', /data-tariff-save/.test(RAW));
  check('读取后端下发的 note', /d && d\.note/.test(RAW));

  section('② 源码级：地区 / 季节 / 档位控件已删除');
  const gone = [
    'tariffSeasonHtml', 'tariffTableHtml', 'tariffDerivationHtml',
    'syncTariffVoltageTable', 'syncTariffFlatRate',
    'data-tariff-region', 'data-tariff-voltage', 'data-tariff-vstart',
    'data-tariff-vend', 'data-tariff-enabled', 'data-tariff-meter',
    'data-tariff-people', 'data-tariff-proj-rate', 'data-tariff-season',
    'data-tariff-months', 'data-tariff-flat-rate'
  ];
  gone.forEach(w => check('monitor.js 不含 ' + w, RAW.indexOf(w) < 0));

  check('无地区 change 事件绑定',
    !/region\.addEventListener\('change'/.test(RAW));
  check('无 payload.overrides 收集', !/payload\.overrides/.test(RAW));

  /* ---------- 渲染级：抽屉里的电价面板 ---------- */
  section('③ 渲染级：抽屉含电价设置容器');
  const POWER_DATA = {
    power: {
      ok: true, total: 30.2, source: 'rapl',
      package: 2.9, core: 2.1, uncore: 0.8, dram: 1.2,
      base: 8.0, disksW: 18.5, disksActive: 3, nics: 1,
      estimation_params: { tdp_w: 65, disk_typical_w: 5.5, nic_fixed_w: 2, base_w: 8 }
    },
    storage: { disks: [] }
  };
  const sheetHtml = M.powerSheetHtml(POWER_DATA);
  check('抽屉含电价设置容器', sheetHtml.indexOf('data-tariff-box') > -1);

  /* ---------- 端到端：loadTariff 渲染面板 ---------- */
  section('④ 端到端：面板渲染内容');
  const tariffBox = makeEl('div');
  tariffBox.setAttribute('data-tariff-box', '1');

  const body = makeEl('div');
  body.innerHTML = sheetHtml;
  body.querySelector = function (sel) {
    if (sel === '[data-tariff-box]') return tariffBox;
    return null;
  };
  tariffBox.querySelector = function (sel) { return null; };
  tariffBox.querySelectorAll = function (sel) { return []; };

  M.loadTariff(body);
  await new Promise(r => setTimeout(r, 40));

  check('面板已渲染（tariffBox 有内容）', tariffBox.innerHTML.length > 0);
  check('面板含单价输入框',
    tariffBox.innerHTML.indexOf('data-tariff-rate') > -1);
  check('面板含口径说明',
    tariffBox.innerHTML.indexOf('data-tariff-note') > -1);
  check('面板含保存按钮',
    tariffBox.innerHTML.indexOf('data-tariff-save') > -1);
  check('面板含默认值 0.5224',
    tariffBox.innerHTML.indexOf('0.5224') > -1);

  check('面板不含地区下拉',
    tariffBox.innerHTML.indexOf('data-tariff-region') < 0);
  check('面板不含电压下拉',
    tariffBox.innerHTML.indexOf('data-tariff-voltage') < 0);
  check('面板不含谷时段控件',
    tariffBox.innerHTML.indexOf('data-tariff-vstart') < 0);
  check('面板不含季节参数区块',
    tariffBox.innerHTML.indexOf('data-tariff-season') < 0);
  check('面板不含档位价目表',
    tariffBox.innerHTML.indexOf('tariff-table') < 0);
  check('面板不含 <select>', tariffBox.innerHTML.indexOf('<select') < 0);
  check('面板说明含「用电量 × 电费单价」口径',
    tariffBox.innerHTML.indexOf('用电量') > -1);

  /* 只有一个 input */
  const inputCount = (tariffBox.innerHTML.match(/<input/g) || []).length;
  check('面板只有 1 个输入框', inputCount === 1, '实际 ' + inputCount);

  console.log('\n' + '-'.repeat(60));
  console.log('通过 ' + PASS + ' 项，失败 ' + FAIL + ' 项');
  if (FAIL) { console.log('❌ 有失败项'); process.exit(1); }
  console.log('🎉 全部通过');
})();
