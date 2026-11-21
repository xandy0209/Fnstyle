#!/usr/bin/env node
/* ===========================================================================
   v1.1.57 回归测试：电价设置面板（地区切换 + 季节参数）

   用户反馈：
     ① 「为什么电价设置中的地区下拉选择后总是恢复到成都？」
     ② 「成都的电价不是区分丰水期、枯水期还有夏季吗？设置里面为什么没有？」

   ① 的根因：change 事件只改了内存对象 j.config.region，**没更新模块级
      tariffCache**；而抽屉每 3 秒 live 重建一次 → loadTariff 命中 60 秒
      缓存 → 用旧缓存（sichuan）重刷面板 → <select> 被重建 → 选中项回落。

   本测试跑真实链路：powerSheetHtml → loadTariff → 模拟 change →
   断言 tariffCache 已更新、面板重建后选中值写回、季节参数区块存在。
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

/* ================= 响应数据 ================= */
const PROFILES = [
  {
    id: 'sichuan', name: '四川省居民（成都）', note: '一户一表，不满1kV。',
    peak_rates: [0.5224, 0.6224, 0.8224],
    valley_rates_wet: [0.175, 0.275, 0.475],
    valley_rates_dry: [0.2535, 0.3535, 0.5535],
    tiers_summer: [260, 460, null], tiers_other: [180, 280, null],
    summer_months: [7, 8, 9], wet_months: [6, 7, 8, 9, 10],
    peak_hours: [[7, 23]], valley_hours: [[23, 7]],
    has_time_of_use: true, carry_over_since: '2027-01'
  },
  {
    id: 'tianjin', name: '天津市居民', note: '居民不执行峰谷分时。',
    peak_rates: [0.49, 0.54, 0.79],
    valley_rates_wet: [0.49, 0.54, 0.79],
    valley_rates_dry: [0.49, 0.54, 0.79],
    tiers_summer: [220, 400, null], tiers_other: [220, 400, null],
    summer_months: [], wet_months: [],
    peak_hours: [[0, 24]], valley_hours: [],
    has_time_of_use: false, carry_over_since: null
  },
  {
    id: 'flat', name: '固定单价（不分时不分阶梯）', note: '单一固定单价。',
    peak_rates: [0.6, 0.6, 0.6],
    valley_rates_wet: [0.6, 0.6, 0.6],
    valley_rates_dry: [0.6, 0.6, 0.6],
    tiers_summer: [null, null, null], tiers_other: [null, null, null],
    summer_months: [], wet_months: [],
    peak_hours: [[0, 24]], valley_hours: [],
    has_time_of_use: false, carry_over_since: null
  }
];

function tariffResponse() {
  return {
    success: true,
    config: { region: 'sichuan', enabled: true, valley_start: 23, valley_end: 7, rate_override: null, overrides: {} },
    current_month: 11,
    active_tiers: [180, 280, null],
    active_valley_rates: [0.2535, 0.3535, 0.5535],
    is_summer_tier: false, is_wet_season: false,
    overrides: {},
    profile: PROFILES[0],
    profiles: PROFILES
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
  console.log('\n========== v1.1.57 电价设置面板回归 ==========');

  const sandbox = makeSandbox();
  const M = sandbox.window.FnStyleMonitor;
  if (!M) { console.log('❌ FnStyleMonitor 未加载'); process.exit(1); }

  /* ---------- 源码级断言（先做，最直接） ---------- */
  section('① 源码级：地区切换必须同步 tariffCache');
  /* 注意：change 回调到 tariffCache 赋值相隔 ~3000 字符
     （中间有价目表重算 + v1.1.61 新增的电压等级同步），
     正则窗口要放宽，否则会误报 */
  const changeBlock = (function () {
    const i = RAW.indexOf("region.addEventListener('change'");
    return i < 0 ? '' : RAW.slice(i, i + 4000);
  })();
  check('change 回调里更新了 tariffCache',
    /tariffCache = j/.test(changeBlock));
  check('change 回调里更新了 tariffAt',
    /tariffAt = Date\.now\(\)/.test(changeBlock));
  check('重建后把选中值写回 select',
    /applyTariff\(box, j\)[\s\S]{0,300}rg\.value = region\.value/.test(RAW));

  section('② 源码级：季节参数区块');
  check('存在 tariffSeasonHtml 函数', /function tariffSeasonHtml\(prof, d\)/.test(RAW));
  check('面板调用了 tariffSeasonHtml', /html \+= tariffSeasonHtml\(prof, d\)/.test(RAW));
  /* 这些属性是**动态拼接**的（kind 变量），源码里查不到字面量 "wet"，
     所以断言查拼接形式，而不是查渲染结果 */
  check('渲染丰水期/夏季月份选择（动态拼接 kind）',
    /data-tariff-months="\' \+ kind \+ \'"/.test(RAW) ||
    /monthBtns\('wet', wet\)/.test(RAW) && /monthBtns\('summer', summer\)/.test(RAW));
  check('渲染丰/枯谷价输入',
    /priceInputs\('wet', prof\.valley_rates_wet/.test(RAW) &&
    /priceInputs\('dry', prof\.valley_rates_dry/.test(RAW));
  check('渲染夏季/其他月档位输入',
    /tierInputs\('summer', prof\.tiers_summer\)/.test(RAW) &&
    /tierInputs\('other', prof\.tiers_other\)/.test(RAW));
  check('保存时收集 overrides',
    /payload\.overrides\[regId\] = ov/.test(RAW));
  check('月份按钮有点选事件', /classList\.toggle\('on'\)/.test(RAW));

  /* ---------- 渲染级：抽屉里的季节参数 ---------- */
  section('③ 渲染级：季节参数出现在面板里');
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

  /* ---------- 模拟真实 DOM：把 panel HTML 塞进 box，然后跑 loadTariff ---------- */
  section('④ 端到端：地区切换后不弹回');
  /* 构造一个能 querySelector 的 box，支持 [data-tariff-box] / [data-tariff-region] */
  const regEl = makeEl('select');
  regEl.value = 'sichuan';
  regEl.options = [];
  const tariffBox = makeEl('div');
  tariffBox.setAttribute('data-tariff-box', '1');

  const body = makeEl('div');
  body.innerHTML = sheetHtml;
  body.querySelector = function (sel) {
    if (sel === '[data-tariff-box]') return tariffBox;
    if (sel === '[data-tariff-region]') return regEl;
    return null;
  };
  tariffBox.querySelector = function (sel) {
    if (sel === '[data-tariff-region]') return regEl;
    return null;
  };
  tariffBox.querySelectorAll = function (sel) { return []; };

  /* loadTariff → applyTariff → tariffPanelHtml 会 innerHTML 覆盖 tariffBox */
  M.loadTariff(body);
  await new Promise(r => setTimeout(r, 40));

  check('面板已渲染（tariffBox 有内容）', tariffBox.innerHTML.length > 0);
  check('面板含季节参数区块',
    tariffBox.innerHTML.indexOf('data-tariff-season') > -1);
  check('面板含丰水期月份按钮',
    tariffBox.innerHTML.indexOf('data-tariff-months="wet"') > -1);
  check('面板含夏季月份按钮',
    tariffBox.innerHTML.indexOf('data-tariff-months="summer"') > -1);
  check('面板含当前季节说明',
    tariffBox.innerHTML.indexOf('丰水期') > -1 ||
    tariffBox.innerHTML.indexOf('枯平期') > -1);
  check('面板含「夏季档」说明',
    tariffBox.innerHTML.indexOf('夏季档') > -1);

  /* 地区切换：模拟 change 事件 */
  const before = sandbox.__fetchCalls.length;
  regEl.value = 'tianjin';
  regEl.dispatchEvent({ type: 'change', bubbles: true });
  await new Promise(r => setTimeout(r, 60));

  check('change 触发了重新拉取配置', sandbox.__fetchCalls.length > before);
  /* 关键：applyTariff 重建面板后，选中值必须被写回为 tianjin */
  check('地区切换后选中值仍是 tianjin（不再弹回成都）',
    regEl.value === 'tianjin', '当前 ' + regEl.value);
  check('缓存已同步（源码层 tar' +
    'iffCache = j 存在）',
    /tariffCache = j/.test(RAW));

  console.log('\n' + '-'.repeat(60));
  console.log('通过 ' + PASS + ' 项，失败 ' + FAIL + ' 项');
  if (FAIL) { console.log('❌ 有失败项'); process.exit(1); }
  console.log('🎉 全部通过');
})();
