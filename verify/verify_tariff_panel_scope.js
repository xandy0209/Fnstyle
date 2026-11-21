#!/usr/bin/env node
/* ===========================================================================
   v1.1.63 回归测试：电价面板不得覆盖能耗抽屉的原有内容

   ⚠️ v1.1.57 初版 bug：
      loadTariff(body) 里直接执行 box.innerHTML = tariffPanelHtml(...)，
      而调用方传进来的 body 是**整个抽屉容器**。
      结果：实时功耗 / 堆叠条 / 趋势图 / 部件明细 / 用电与电费 / 估算参数
      全被电价面板覆盖掉 —— 用户反馈「我之前能耗的抽屉内容呢？给我删了？」。

   本测试在 Node vm + 极简 DOM 里跑一遍真实链路：
     ① 渲染抽屉 HTML（powerSheetHtml，真实导出函数）
     ② 挂进 body
     ③ 调用真实导出的 loadTariff(body)（fetch 被桩成返回 tariff 配置）
     ④ 断言抽屉原有内容**全部还在**，且电价面板已就位
     ⑤ 元检查：把源码改回 buggy 写法后重跑，必须 FAIL
        —— 否则说明这个测试本身抓不住 bug，是无效测试
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

/* ================= 极简 DOM ================= */
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
    get className() { return Array.from(this.classList._s).join(' '); },
    set className(v) { this.classList._s = new Set(String(v || '').split(/\s+/).filter(Boolean)); },
    setAttribute(k, v) { this._attrs[k] = String(v); },
    getAttribute(k) { return this._attrs[k] !== undefined ? this._attrs[k] : null; },
    hasAttribute(k) { return this._attrs[k] !== undefined; },
    removeAttribute(k) { delete this._attrs[k]; },
    addEventListener() {}, removeEventListener() {},
    appendChild(c) { this.children.push(c); return c; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
  };
  return el;
}

/* ================= 沙箱 ================= */
function makeSandbox(src) {
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
    fetch: function (url, opts) {
      fetchCalls.push({ url: url, opts: opts });
      return Promise.resolve({
        ok: true,
        json: function () { return Promise.resolve(TARIFF_RESPONSE()); }
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
  vm.runInContext(src, sandbox, { filename: 'monitor.js' });
  return sandbox;
}

/* ================= 假数据 ================= */
/* ⚠️ v1.1.63：电价模型退化为**单一单价** —— 响应里没有 profile / profiles /
   地区 / 电压 / 档位 / 季节，只有 rate + note。 */
const TARIFF_RESPONSE = () => ({
  success: true,
  config: { rate: 0.5224, updated: 0 },
  rate: 0.5224,
  default_rate: 0.5224,
  unit: '元/kWh',
  note: '电费按「用电量 × 电费单价」估算，不区分阶梯档位、不区分峰谷时段、不区分地区。'
      + '默认单价 0.5224 元/kWh，取成都（四川）一户一表居民不满 1 千伏第一档高峰平段电价'
      + '（成都居民电价的最低档），可按你家的实际电价自行调整。'
});

const POWER_DATA = {
  power: {
    ok: true, total: 30.2, source: 'rapl',
    package: 2.9, core: 2.1, uncore: 0.8, dram: 1.2,
    base: 8.0, disksW: 18.5, disksActive: 3, nics: 1,
    estimation_params: { tdp_w: 65, disk_typical_w: 5.5, nic_fixed_w: 2, base_w: 8 }
  },
  storage: { disks: [] }
};

/* 抽屉原有内容必须保留的特征串 */
const KEEP_MARKERS = [
  ['实时功耗 hero', 'mon-hero'],
  ['功耗堆叠条', 'mon-stack-wrap'],
  ['部件明细', '部件明细'],
  ['用电与电费区', 'data-pwr-energy'],
  ['估算参数', '估算参数'],
  ['电价设置区块', 'data-tariff-box']
];

/* ================= 跑链路 ================= */
function runChain(src) {
  const sandbox = makeSandbox(src);
  const M = sandbox.window.FnStyleMonitor;
  if (!M) return { err: 'FnStyleMonitor 未加载' };

  const sheetHtml = M.powerSheetHtml(POWER_DATA);

  /* 抽屉 body —— querySelector('[data-tariff-box]') 必须能找到面板 */
  const tariffBox = makeEl('div');
  tariffBox.setAttribute('data-tariff-box', '1');
  const body = makeEl('div');
  body.innerHTML = sheetHtml;
  body.querySelector = function (sel) {
    if (sel === '[data-tariff-box]') return tariffBox;
    return null;
  };

  M.loadTariff(body);

  /* loadTariff 走 fetch → Promise，等微任务队列排空 */
  return new Promise(function (resolve) {
    setTimeout(function () {
      resolve({
        M: M, sheetHtml: sheetHtml, body: body, tariffBox: tariffBox,
        fetchCalls: sandbox.__fetchCalls
      });
    }, 30);
  });
}

(async function main() {
  console.log('\n========== v1.1.63 回归：电价面板不得覆盖抽屉内容 ==========');

  /* ---------- ① 正常链路 ---------- */
  section('① 正常链路：loadTariff(body)');
  const r = await runChain(RAW);
  if (r.err) { console.log('  ✗ ' + r.err); FAIL++; }
  else {
    check('loadTariff 发起了 /api/power/tariff 请求',
      r.fetchCalls.length === 1 && /power\/tariff/.test(r.fetchCalls[0].url),
      JSON.stringify(r.fetchCalls.map(c => c.url)));
    check('抽屉原本含全部内容区块', KEEP_MARKERS.every(m => r.sheetHtml.indexOf(m[1]) > -1),
      KEEP_MARKERS.filter(m => r.sheetHtml.indexOf(m[1]) === -1).map(m => m[0]).join(', '));

    /* 关键：body.innerHTML 不能被覆盖 */
    check('body.innerHTML 未被清空（内容长度一致）',
      r.body.innerHTML === r.sheetHtml,
      '当前 ' + r.body.innerHTML.length + ' vs 原 ' + r.sheetHtml.length);
    KEEP_MARKERS.forEach(m => {
      check('抽屉原有内容仍在：「' + m[0] + '」',
        r.body.innerHTML.indexOf(m[1]) > -1);
    });
    /* 电价面板内容确实渲染进面板容器了 */
    check('电价面板已渲染进 [data-tariff-box]',
      r.tariffBox.innerHTML.indexOf('tariff-field') > -1 &&
      r.tariffBox.innerHTML.indexOf('data-tariff-save') > -1,
      r.tariffBox.innerHTML.slice(0, 120));
    /* v1.1.63：面板只剩「单价输入框 + 口径说明」 */
    check('电价面板含单价输入框与口径说明',
      r.tariffBox.innerHTML.indexOf('data-tariff-rate') > -1 &&
      r.tariffBox.innerHTML.indexOf('data-tariff-note') > -1 &&
      r.tariffBox.innerHTML.indexOf('tariff-field') > -1);
    check('电价面板不含地区/电压/谷时段/档位表',
      r.tariffBox.innerHTML.indexOf('data-tariff-region') === -1 &&
      r.tariffBox.innerHTML.indexOf('data-tariff-voltage') === -1 &&
      r.tariffBox.innerHTML.indexOf('data-tariff-vstart') === -1 &&
      r.tariffBox.innerHTML.indexOf('tariff-table') === -1);
    check('电价面板只有一个输入框',
      (r.tariffBox.innerHTML.match(/<input/g) || []).length === 1,
      '实际 ' + (r.tariffBox.innerHTML.match(/<input/g) || []).length);
    check('body 上未出现电价面板内容（面板只写在子容器）',
      r.body.innerHTML.indexOf('tariff-field') === -1);
  }

  /* ---------- ② 元检查：把源码改回 buggy 写法，测试必须失败 ---------- */
  section('② 元检查：回退到 buggy 写法应被测出');
  const BUGGY = RAW.replace(
    'var tbox = scope.querySelector ? scope.querySelector(\'[data-tariff-box]\') : null;\n    if (!tbox) return;',
    'var tbox = scope; // BUG: 误把整个抽屉 body 当成面板容器\n    if (!tbox) return;'
  );
  if (BUGGY === RAW) {
    check('元检查：能在源码里定位到 buggy 注入点', false, 'replace 未命中，源码可能已改写');
  } else {
    const r2 = await runChain(BUGGY);
    const broken = r2.body && r2.body.innerHTML !== r2.sheetHtml;
    check('元检查：buggy 版本确实会覆盖抽屉内容（测试有效）', broken === true,
      'buggy 版本 body 长度 ' + (r2.body ? r2.body.innerHTML.length : '?') +
      ' vs 原 ' + (r2.sheetHtml ? r2.sheetHtml.length : '?'));
    check('元检查：修复后版本不会覆盖（回归已被守住）',
      !!(r && r.body && r.body.innerHTML === r.sheetHtml));
  }

  /* ---------- ③ 源码级断言 ---------- */
  section('③ 源码级断言');
  check('loadTariff 签名为 scope',
    /function loadTariff\(scope\)/.test(RAW));
  check('loadTariff 内部先 querySelector(\'[data-tariff-box]\')',
    /function loadTariff\(scope\)[\s\S]{0,300}querySelector\('\[data-tariff-box\]'\)/.test(RAW));
  check('loadTariff 内无 scope.innerHTML 直接赋值',
    /function loadTariff\(scope\)[\s\S]{0,600}scope\.innerHTML\s*=/.test(RAW) === false);
  check('applyTariff 只写 tbox',
    /function applyTariff\(tbox, d\)/.test(RAW) &&
    /function applyTariff\(tbox, d\) \{[\s\S]{0,900}tbox\.innerHTML/.test(RAW));
  check('compute.js 两处调用 loadTariff(body) 传的是抽屉 body（配合上面的定位逻辑）',
    /loadTariff\(body\)/.test(fs.readFileSync(path.join(UI, 'static', 'js', 'compute.js'), 'utf8')));

  console.log('\n' + '-'.repeat(60));
  console.log('通过 ' + PASS + ' 项，失败 ' + FAIL + ' 项');
  if (FAIL) { console.log('❌ 有失败项'); process.exit(1); }
  console.log('🎉 全部通过');
})();
